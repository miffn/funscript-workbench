import json
import sqlite3

from fastapi.testclient import TestClient

from backend.config import Config, Root
from backend.main import create_app
from backend.scanner import Scanner
from backend.store import SCHEMA, Store
from backend.tags import TagService


def test_legacy_schema_migration_preserves_ids_bindings_and_manual_fields(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    legacy = SCHEMA.replace("'video_type','axis_type'", "'video_type'")
    with sqlite3.connect(data / "workbench.sqlite3") as db:
        db.executescript(legacy)
        db.execute("INSERT INTO works(id,script_id,title,status,notes,created_at,updated_at) VALUES(1,'S001','人工标题','published','人工备注','2026','2026')")
        db.execute("INSERT INTO tags(id,category,name,name_key,revision,support_status,support_url) VALUES(42,'author','Example Author','example author',7,'url','https://example.com/support')")
        db.execute("INSERT INTO work_tags(work_id,tag_id,source) VALUES(1,42,'manual')")
        db.execute("INSERT INTO work_tag_state VALUES(1,9,1)")
    store = Store(data)
    service = TagService(store)
    axis = service.create({"category": "axis_type", "name": "多轴"})
    with store.connection() as db:
        state = service.work_state(db, 1)
        assert state["tags_revision"] == 9 and state["tags"][0]["id"] == 42
        assert state["tags"][0]["revision"] == 7
        assert state["tags"][0]["support_url"] == "https://example.com/support"
        assert tuple(db.execute("SELECT title,status,notes FROM works").fetchone()) == ("人工标题", "published", "人工备注")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    service.replace_work(1, [42, axis["id"]], 9)
    assert Store(data).path == store.path  # Reopening a migrated database is safe.


def test_detected_axes_filtering_and_manual_clearing_survive_scan_and_restart(tmp_path):
    root = tmp_path / "workspace"
    for number in ("S001", "S002"):
        folder = root / number
        folder.mkdir(parents=True)
        (folder / "video.mp4").write_bytes(b"video")
        (folder / "video.funscript").write_text('{"actions":[]}')
    # Multiple stroke files still constitute a single axis.
    (root / "S001" / "other.funscript").write_text('{"actions":[]}')
    (root / "S002" / "video.pitch.funscript").write_text('{"actions":[]}')
    config = Config(data_dir=tmp_path / "data", roots=(Root(root, "D:\\workspace", "workspace"),))
    store = Store(config.data_dir)
    scanner = Scanner(store, config)
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        items = {work["script_id"]: work for work in client.get('/api/works').json()["items"]}
        assert items["S001"]["axis_type"] == "单轴"
        assert items["S002"]["axis_type"] == "多轴"
        assert client.get('/api/works', params={"untagged_only": "true"}).json()["total"] == 2
        multi = next(tag for tag in client.get('/api/tags').json()["items"] if tag["name"] == "多轴")
        assert [work["script_id"] for work in client.get('/api/works', params={"tag_id": multi["id"]}).json()["items"]] == ["S002"]
        work = items["S002"]
        axis_id = items["S001"]["tags"][0]["id"]
        bad = client.put(f'/api/works/{work["id"]}/tags', json={"tag_ids": [axis_id, multi["id"]], "expected_revision": work["tags_revision"]})
        assert bad.status_code == 422
        cleared = client.put(f'/api/works/{work["id"]}/tags', json={"tag_ids": [], "expected_revision": work["tags_revision"]})
        assert cleared.status_code == 200
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        result = client.get(f'/api/works/{work["id"]}').json()
        assert result["tags"] == [] and result["axis_type"] == ""
    # Unedited classifications update when the actual set of axes changes.
    (root / "S001" / "video.roll.funscript").write_text('{"actions":[]}')
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.get(f'/api/works/{items["S001"]["id"]}').json()["axis_type"] == "多轴"


def test_first_upgrade_backfills_axes_without_touching_manual_author_selection(tmp_path):
    store = Store(tmp_path)
    with store.connection() as db:
        db.execute("INSERT INTO works(id,script_id,title,metadata,created_at,updated_at) VALUES(1,'S001','title',?,'2026','2026')", (json.dumps({"Axis Type": "Multi-axis"}),))
        db.execute("INSERT INTO tags(id,category,name,name_key) VALUES(1,'author','Author','author')")
        db.execute("INSERT INTO work_tags VALUES(1,1,'manual','[]')")
        db.execute("INSERT INTO work_tag_state VALUES(1,4,1)")
    service = TagService(store)
    with store.connection() as db:
        state = service.work_state(db, 1)
        assert {tag["category"] for tag in state["tags"]} == {"author", "axis_type"}
        assert state["tags_revision"] == 5
    # Startup must not overwrite a later deliberate manual removal.
    service.replace_work(1, [1], 5)
    TagService(Store(tmp_path))
    with store.connection() as db:
        assert [tag["id"] for tag in service.work_state(db, 1)["tags"]] == [1]
