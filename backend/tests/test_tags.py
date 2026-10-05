from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, PROJECT_DIR, Root
from backend.import_tags import import_tags
from backend.main import create_app
from backend.scanner import Scanner
from backend.store import Store
from backend.tags import TagService, TagError


@pytest.fixture
def inventory(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    for name in ("S071_one", "S072_two", "S025_parent", "S025_001_child"):
        directory = root / name
        directory.mkdir()
        (directory / "main.mp4").write_bytes(b"example")
        (directory / "main.funscript").write_text('{"actions":[]}')
    config = Config(data_dir=tmp_path / "data", roots=(Root(root, "D:\\Media\\workspace", "workspace"),), preview_output_root=root / "预览")
    store = Store(config.data_dir)
    scanner = Scanner(store, config)
    scanner.scan()
    with store.connection() as db:
        # These tests start from an intentionally unclassified tag catalog.
        # Automatic axis classification is covered separately in test_axis_tags.py.
        db.execute("DELETE FROM work_tags")
        db.execute("DELETE FROM work_tag_state")
        db.execute("DELETE FROM tags")
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('axis_tags_initialized','true')")
        works = {row["script_id"]: row["id"] for row in db.execute("SELECT id,script_id FROM works")}
        db.execute("UPDATE works SET title='保留标题',notes='保留备注',status='published',metadata=? WHERE id=?", (json.dumps({"video_type": "3DCG", "es_url": "https://example.com/old-post"}), works["S071"]))
    return config, store, scanner, works


def snapshots():
    monthly = {"rows": [
        {"Script ID": "S071", "Video Type": "Real", "Release Type": "Paid", "Slot Type": "Main Tier", "Status": "Ready", "Release Title": "不能覆盖人工标题"},
        {"Script ID": "S025_001", "Video Type": "Anime", "Release Type": "Free Sample", "Slot Type": "Free"},
        {"Script ID": "S025_002", "Video Type": "VAM", "Release Type": "Paid", "Slot Type": "Extra Tier"},
    ]}
    master = {"rows": [
        {"Script ID": "S071", "Creator": "Example Author", "Support Creator URL": "https://example.com/example-author", "Video Type": "3DCG", "Release Type": "Free Sample"},
        {"Script ID": "S001", "Creator": "example author", "Support Creator URL": "https://example.com/another"},
        {"Script ID": "S025_001", "Creator": "SampleCreator", "Support Creator URL": "https://example.com/sample-creator"},
        {"Script ID": "S025_002", "Creator": "samplecreator", "Support Creator URL": "https://example.com/sample-creator"},
        {"Script ID": "S035", "Creator": "Another", "Support Creator URL": "https://discuss.eroscripts.com/t/other-work/1"},
        {"Script ID": "S072", "Creator": "TBD", "Video Type": "TBD", "Release Type": " ", "Support Creator URL": "TBD"},
    ]}
    return monthly, master


def current_works(store):
    with store.connection() as db:
        return [dict(row) for row in db.execute("SELECT * FROM works ORDER BY id")]


def test_api_tags_shared_author_url_and_optimistic_revision(inventory):
    config, store, scanner, works = inventory
    with TestClient(create_app(config, start_worker=False)) as client:
        created = client.post("/api/tags", json={"category": "author", "name": "Sample Author", "support_url": "https://example.com/sample-author"})
        assert created.status_code == 201
        author = created.json()
        assert author["support_status"] == "url" and author["revision"] == 1
        for work_id in (works["S071"], works["S072"]):
            bound = client.put(f"/api/works/{work_id}/tags", json={"tag_ids": [author["id"]], "expected_revision": 0})
            assert bound.status_code == 200 and bound.json()["tags_revision"] == 1
        changed = client.patch(f"/api/tags/{author['id']}", json={"expected_revision": 1, "name": "Sample Author updated", "support_url": "https://example.com/new"})
        assert changed.status_code == 200 and changed.json()["revision"] == 2
        assert changed.json()["usage_count"] == 2
        for work_id in (works["S071"], works["S072"]):
            detail = client.get(f"/api/works/{work_id}").json()
            assert detail["tags"][0]["name"] == "Sample Author updated"
            assert detail["tags"][0]["support_url"] == "https://example.com/new"
        stale = client.patch(f"/api/tags/{author['id']}", json={"expected_revision": 1, "support_status": "none"})
        assert stale.status_code == 409
        assert client.get("/api/tags").json()["items"][0]["support_status"] == "url"


@pytest.mark.parametrize("body", [
    {"category": "author", "name": "  "},
    {"category": "author", "name": "bad\nname"},
    {"category": "invalid", "name": "x"},
    {"category": "release_type", "name": "Other"},
    {"category": "tier", "name": "Premium"},
    {"category": "author", "name": "x", "support_url": "file:///etc/passwd"},
    {"category": "author", "name": "x", "support_url": "https://user:secret@example.com"},
    {"category": "author", "name": "x", "support_url": "https://example.com:999999"},
    {"category": "author", "name": "x", "support_url": "https://example.com/a b"},
    {"category": "author", "name": "x", "support_url": "https://example.com", "support_status": "none"},
    {"category": "author", "name": "x", "support_url": "https://example.com", "support_status": "unknown"},
    {"category": "author", "name": "x", "support_status": "url"},
    {"category": "author", "name": "x", "support_status": None},
    {"category": "custom", "name": "x", "support_status": "none"},
    {"category": "video_type", "name": "VAM", "support_url": "https://example.com"},
])
def test_invalid_tag_input_rejected(inventory, body):
    config, store, scanner, works = inventory
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.post("/api/tags", json=body).status_code == 422
        assert client.get("/api/tags").json()["items"] == []


def test_names_casefold_uniqueness_and_categories_are_independent(inventory):
    config, store, scanner, works = inventory
    service = TagService(store)
    service.create({"category": "author", "name": " Straße "})
    with pytest.raises(TagError) as error:
        service.create({"category": "author", "name": "STRASSE"})
    assert error.value.status_code == 409
    service.create({"category": "custom", "name": "STRASSE"})
    assert len(service.catalog()["items"]) == 2


def test_selection_singletons_custom_multi_and_release_combo(inventory):
    config, store, scanner, works = inventory
    service = TagService(store)
    created = {}
    for category, name in (("author", "A"), ("author", "B"), ("video_type", "Real"), ("video_type", "VAM"),
                           ("release_type", "Free Sample"), ("release_type", "Paid"), ("tier", "Free"), ("tier", "Main Tier"),
                           ("custom", "One"), ("custom", "Two")):
        created[name] = service.create({"category": category, "name": name})["id"]
    invalid = [[created["A"], created["B"]], [created["Real"], created["VAM"]], [created["Free Sample"], created["Paid"]],
               [created["Free"], created["Main Tier"]], [created["Free Sample"], created["Main Tier"]], [created["Paid"], created["Free"]],
               [created["One"], created["One"]]]
    for values in invalid:
        with pytest.raises(TagError):
            service.replace_work(works["S071"], values, 0)
    accepted = service.replace_work(works["S071"], [created["Paid"], created["Main Tier"], created["One"], created["Two"]], 0)
    assert len(accepted["tags"]) == 4 and accepted["tags_revision"] == 1
    missing = service.replace_work(works["S072"], [created["Paid"]], 0)
    assert len(missing["tags"]) == 1
    with pytest.raises(TagError):
        service.update(created["Main Tier"], {"expected_revision": 1, "name": "Free"})


def test_api_work_revision_strict_ids_and_invalid_selection_atomic(inventory):
    config, store, scanner, works = inventory
    service = TagService(store)
    custom = service.create({"category": "custom", "name": "One"})
    with TestClient(create_app(config, start_worker=False)) as client:
        url = f"/api/works/{works['S071']}/tags"
        for body in ({"tag_ids": [True], "expected_revision": 0}, {"tag_ids": ["1"], "expected_revision": 0},
                     {"tag_ids": [-1], "expected_revision": 0}, {"tag_ids": [], "expected_revision": True}):
            assert client.put(url, json=body).status_code == 422
        assert client.put(url, json={"tag_ids": [999999], "expected_revision": 0}).status_code == 404
        assert client.get(url).json()["tags_revision"] == 0
        assert client.put(url, json={"tag_ids": [custom["id"]], "expected_revision": 0}).status_code == 200
        assert client.put(url, json={"tag_ids": [], "expected_revision": 0}).status_code == 409
        assert client.get(url).json()["tags"][0]["id"] == custom["id"]
        assert client.put(url, headers={"Origin": "http://evil.example"}, json={"tag_ids": [], "expected_revision": 1}).status_code == 403


def test_parallel_tag_and_work_edits_have_one_winner(inventory):
    config, store, scanner, works = inventory
    service = TagService(store)
    tag = service.create({"category": "custom", "name": "First"})

    def edit(name):
        try:
            return service.update(tag["id"], {"expected_revision": 1, "name": name})
        except TagError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ["Second", "Third"]))
    assert sum(isinstance(result, dict) for result in results) == 1 and 409 in results
    other = service.create({"category": "custom", "name": "Other"})

    def bind(tag_id):
        try:
            return service.replace_work(works["S071"], [tag_id], 0)
        except TagError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(bind, [tag["id"], other["id"]]))
    assert sum(isinstance(result, dict) for result in results) == 1 and 409 in results


def test_import_priorities_full_ids_conflicts_url_candidates_and_no_metadata_changes(inventory):
    config, store, scanner, works = inventory
    monthly, master = snapshots()
    before = current_works(store)
    report = import_tags(config.data_dir, monthly, master)
    assert report["matched"] == 3
    assert report["matched_by_source"] == {"monthly": 2, "master": 3}
    assert report["bindings"] == 8
    assert any(item["type"] == "support_url_conflict" and item["name"] == "Example Author" for item in report["conflicts"])
    assert sum(item["type"] == "field_conflict" for item in report["conflicts"]) == 2
    assert any(item["type"] == "support_url_review" for item in report["warnings"])
    service = TagService(store)
    catalog = service.catalog()
    example_author = next(tag for tag in catalog["items"] if tag["name"] == "Example Author")
    assert example_author["support_status"] == "unknown" and example_author["support_url"] is None
    assert example_author["support_candidates"] == ["https://example.com/another", "https://example.com/example-author"]
    assert len([tag for tag in catalog["items"] if tag["name"].casefold() == "samplecreator"]) == 1
    assert not any(tag["name"] == "TBD" for tag in catalog["items"])
    with store.connection() as db:
        first = service.work_state(db, works["S071"])
        assert {tag["category"]: tag["name"] for tag in first["tags"]} == {"author": "Example Author", "video_type": "Real", "release_type": "Paid", "tier": "Main Tier"}
        assert service.work_state(db, works["S025"])["tags"] == []
        assert service.work_state(db, works["S025_001"])["tags_revision"] == 1
        assert db.execute("SELECT count(*) FROM works").fetchone()[0] == 4
    assert current_works(store) == before
    assert catalog["import_report"]["matched"] == 3


def test_repeated_import_idempotent_manual_clear_and_shared_url_preserved(inventory):
    config, store, scanner, works = inventory
    monthly, master = snapshots()
    first = import_tags(config.data_dir, monthly, master)
    service = TagService(store)
    with store.connection() as db:
        child = service.work_state(db, works["S025_001"])
    author = next(tag for tag in child["tags"] if tag["category"] == "author")
    service.update(author["id"], {"expected_revision": author["revision"], "support_status": "none"})
    cleared = service.replace_work(works["S025_001"], [], child["tags_revision"])
    before_catalog = service.catalog()["items"]
    second = import_tags(config.data_dir, monthly, master)
    assert second["created"] == 0 and second["bindings"] == 0
    assert service.catalog()["items"] == before_catalog
    with store.connection() as db:
        assert service.work_state(db, works["S025_001"]) == cleared
    assert any(plan["action"] == "skip_manual" and plan["script_id"] == "S025_001" for plan in second["plans"])
    assert next(tag for tag in service.catalog()["items"] if tag["id"] == author["id"])["support_status"] == "none"


def test_shared_manual_rename_protects_bindings_and_invalidates_old_editor(inventory):
    config, store, scanner, works = inventory
    monthly, master = snapshots()
    import_tags(config.data_dir, monthly, master)
    service = TagService(store)
    with store.connection() as db:
        original = service.work_state(db, works["S025_001"])
    author = next(tag for tag in original["tags"] if tag["category"] == "author")
    renamed = service.update(author["id"], {"expected_revision": author["revision"], "name": "User canonical author"})
    assert renamed["id"] == author["id"]
    with pytest.raises(TagError) as error:
        service.replace_work(works["S025_001"], [], original["tags_revision"])
    assert error.value.status_code == 409
    report = import_tags(config.data_dir, monthly, master)
    with store.connection() as db:
        current = service.work_state(db, works["S025_001"])
    assert current["tags_revision"] == original["tags_revision"] + 1
    assert any(tag["id"] == author["id"] and tag["name"] == "User canonical author" for tag in current["tags"])
    assert not any(tag["name"] == "SampleCreator" for tag in current["tags"])
    assert any(plan["action"] == "skip_manual" and plan["script_id"] == "S025_001" for plan in report["plans"])
    assert any(tag["name"] == "SampleCreator" and tag["usage_count"] == 0 for tag in service.catalog()["items"])


def test_scanning_and_move_keep_labels_and_clear_legacy_video_type(inventory):
    config, store, scanner, works = inventory
    service = TagService(store)
    custom = service.create({"category": "custom", "name": "待分类"})
    service.replace_work(works["S071"], [custom["id"]], 0)
    before = current_works(store)
    (config.roots[0].path / "S071_one").rename(config.roots[0].path / "S071_renamed")
    scanner.scan()
    after = current_works(store)
    derived = {'association_revision', 'association_missing', 'preview_key', 'preview_stale'}
    assert [{field: value for field, value in work.items() if field not in derived} for work in after] == [
        {field: value for field, value in work.items() if field not in derived} for work in before]
    moved = next(work for work in after if work['id'] == works['S071'])
    original = next(work for work in before if work['id'] == works['S071'])
    assert moved['association_revision'] == original['association_revision'] + 1
    assert moved['preview_key'] == 'S071' and moved['preview_stale'] == 1 and moved['association_missing'] == 0
    with TestClient(create_app(config, start_worker=False)) as client:
        detail = client.get(f"/api/works/{works['S071']}").json()
        assert detail["video_type"] == ""
        assert detail["metadata"]["video_type"] == "3DCG"
        assert detail["tags"][0]["id"] == custom["id"]
        clear = client.put(f"/api/works/{works['S071']}/tags", json={"tag_ids": [], "expected_revision": 1})
        assert clear.status_code == 200
        assert client.get(f"/api/works/{works['S071']}").json()["video_type"] == ""
        import_tags(config.data_dir, *snapshots())
        assert client.get(f"/api/works/{works['S071']}/tags").json()["tags"] == []


def test_search_filter_current_video_type_and_untagged(inventory):
    config, store, scanner, works = inventory
    service = TagService(store)
    author = service.create({"category": "author", "name": "Straße"})
    video = service.create({"category": "video_type", "name": "VAM"})
    special = service.create({"category": "custom", "name": "标签%_字"})
    service.replace_work(works["S071"], [author["id"], video["id"], special["id"]], 0)
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.get("/api/works", params={"q": "STRASSE"}).json()["total"] == 1
        assert client.get("/api/works", params={"q": "%_"}).json()["total"] == 1
        assert client.get("/api/works", params={"tag_id": author["id"]}).json()["total"] == 1
        assert client.get("/api/works", params={"untagged_only": "true"}).json()["total"] == 3
        assert client.get("/api/works", params={"untagged_only": "yes"}).status_code == 422
        assert client.get("/api/works", params={"tag_id": -1}).status_code == 422
        assert client.get("/api/works", params={"tag_id": "invalid"}).status_code == 422
        assert client.get("/api/works", params={"tag_id": 999999}).status_code == 404
        assert client.get("/api/works", params={"tag_id": author["id"], "untagged_only": "true"}).status_code == 422
        listed = client.get("/api/works", params={"tag_id": video["id"]}).json()["items"][0]
        assert listed["video_type"] == "VAM" and listed["tags_revision"] == 1


def test_dry_run_old_schema_is_read_only_and_cli_report(inventory, tmp_path):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    database = legacy_dir / "workbench.sqlite3"
    db = sqlite3.connect(database)
    db.execute("CREATE TABLE works(id INTEGER PRIMARY KEY,script_id TEXT)")
    db.execute("INSERT INTO works(id,script_id) VALUES(1,'S071')")
    db.commit()
    db.close()
    before = database.read_bytes()
    monthly, master = snapshots()
    report = import_tags(legacy_dir, monthly, master, dry_run=True)
    assert report["matched"] == 1 and report["bindings"] == 4
    assert report["dry_run"] is True and database.read_bytes() == before
    db = sqlite3.connect(database)
    assert {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {"works"}
    db.close()
    monthly_path = tmp_path / "monthly.json"
    master_path = tmp_path / "master.json"
    report_path = tmp_path / "plan.json"
    monthly_path.write_text(json.dumps(monthly))
    master_path.write_text(json.dumps(master))
    completed = subprocess.run([str(PROJECT_DIR / ".venv/bin/python"), "-m", "backend.import_tags", "--monthly", str(monthly_path),
        "--master", str(master_path), "--data-dir", str(legacy_dir), "--dry-run", "--report", str(report_path)], cwd=PROJECT_DIR, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(report_path.read_text())["bindings"] == 4
    assert database.read_bytes() == before


def test_invalid_import_combination_does_not_guess_other_tier(inventory):
    config, store, scanner, works = inventory
    monthly = {"rows": [{"Script ID": "S071", "Video Type": "Real", "Release Type": "Free Sample", "Slot Type": "Main Tier"}]}
    report = import_tags(config.data_dir, monthly, {})
    assert any(conflict["type"] == "invalid_combination" for conflict in report["conflicts"])
    with store.connection() as db:
        state = TagService(store).work_state(db, works["S071"])
    assert {tag["category"] for tag in state["tags"]} == {"video_type"}


def test_within_preferred_source_conflict_skips_field_and_empty_tbd_not_tags(inventory):
    config, store, scanner, works = inventory
    monthly = {"rows": [{"Script ID": "S071", "Video Type": "Real"}, {"Script ID": "S071", "Video Type": "VAM"}, {"Script ID": "S072", "Video Type": "TBD"}]}
    report = import_tags(config.data_dir, monthly, {})
    assert any(conflict["type"] == "field_conflict" for conflict in report["conflicts"])
    assert report["bindings"] == 0
    assert TagService(store).catalog()["items"] == []
