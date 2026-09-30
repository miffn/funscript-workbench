from __future__ import annotations

import base64
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import threading
import time

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root, normalize_id, parse_folder
from backend.covers import CoverGenerator
from backend.jobs import JobWorker
from backend.main import create_app
from backend.scanner import Scanner
from backend.store import Store, now


@pytest.fixture
def inventory(tmp_path):
    first = tmp_path / "2026"
    second = tmp_path / "workspace"
    first.mkdir()
    second.mkdir()
    config = Config(data_dir=tmp_path / "data", roots=(Root(first, "D:\\Media\\2026", "2026"), Root(second, "D:\\Media\\workspace", "workspace")),
                    ffmpeg="definitely-missing-ffmpeg", ffprobe="definitely-missing-ffprobe", open_mode="gateway")
    store = Store(config.data_dir)
    scanner = Scanner(store, config)
    return config, store, scanner


def add_work(root: Path, name: str, assets=True):
    folder = root / name
    folder.mkdir()
    if assets:
        (folder / "main.mp4").write_bytes(b"sample-not-real-video")
        (folder / "main.funscript").write_text('{"actions":[]}', encoding="utf-8")
    return folder


def work_rows(store):
    with store.connection() as db:
        return [dict(row) for row in db.execute("SELECT * FROM works ORDER BY script_id")]


@pytest.mark.parametrize("value,expected", [("S29", "S029"), ("s025_2", "S025_002"), ("S025_001", "S025_001"), ("S025", "S025"), ("S025_title", None), ("S025_001_bad", None), ("S25x", None)])
def test_exact_identifier_normalization(value, expected):
    assert normalize_id(value) == expected


@pytest.mark.parametrize("name,expected", [("S29_title", ("S029", "title")), ("S025_001_Title", ("S025_001", "Title")), ("S064", ("S064", "S064")), ("S029x", None), ("S029_001x", None), ("Preview", None)])
def test_folder_identification(name, expected):
    assert parse_folder(name) == expected


def test_child_ids_raw_material_and_missing_assets(inventory):
    config, store, scanner = inventory
    add_work(config.roots[0].path, "S025_001_a")
    add_work(config.roots[0].path, "S025_002_b", assets=False)
    (config.roots[1].path / "raw.mp4").write_bytes(b"x")
    scanner.scan()
    assert [row["script_id"] for row in work_rows(store)] == ["S025_001", "S025_002"]
    app = create_app(config, start_worker=False)
    with TestClient(app) as client:
        response = client.get("/api/works").json()
        assert response["stats"]["total"] == 2
        assert response["last_scan"]["unnumbered"] == 1
        issues = client.get("/api/issues").json()["items"]
        assert any(issue["type"] == "missing_assets" and issue["script_id"] == "S025_002" for issue in issues)
        assert any(issue["type"] == "unnumbered_material" and issue["work_id"] is None for issue in issues)


def test_duplicates_not_double_counted_and_move_keeps_manual_state(inventory):
    config, store, scanner = inventory
    folder = add_work(config.roots[0].path, "S29_original")
    scanner.scan()
    app = create_app(config, start_worker=False)
    with TestClient(app) as client:
        row = client.get("/api/works").json()["items"][0]
        edited = client.patch(f"/api/works/{row['id']}", json={"title": "人工标题", "status": "published", "notes": "发布备注"})
        assert edited.status_code == 200
        add_work(config.roots[1].path, "S029_copy")
        scanner.scan()
        duplicate = client.get("/api/works").json()
        assert duplicate["total"] == 1
        assert len([directory for directory in duplicate["items"][0]["directories"] if directory["available"]]) == 2
        assert any(issue["type"] == "duplicate_identifier" for issue in duplicate["items"][0]["issues"])
        duplicate_folder = config.roots[1].path / "S029_copy"
        duplicate_folder.rename(config.roots[1].path / "untracked_copy")
        folder.rename(config.roots[1].path / "S029_moved")
        scanner.scan()
        moved = client.get(f"/api/works/{row['id']}").json()
        assert moved["title"] == "人工标题"
        assert moved["status"] == "published"
        assert moved["notes"] == "发布备注"
        assert not any(issue["type"] in {"duplicate_identifier", "directory_missing"} for issue in moved["issues"])
        assert sum(directory["available"] for directory in moved["directories"]) == 1


def test_unavailable_root_preserves_assets_and_excludes_missing_claims(inventory):
    config, store, scanner = inventory
    add_work(config.roots[0].path, "S070_a")
    scanner.scan()
    config.roots[0].path.rename(config.roots[0].path.with_name("temporarily-unmounted"))
    scanner.scan()
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM assets").fetchone()[0] == 2
        assert db.execute("SELECT available FROM directories").fetchone()[0] == 1
        kinds = {row[0] for row in db.execute("SELECT type FROM issues WHERE work_id IS NOT NULL")}
        assert "root_unavailable" in kinds
        assert "missing_assets" not in kinds and "directory_missing" not in kinds
    with TestClient(create_app(config, start_worker=False)) as client:
        row = client.get("/api/works").json()["items"][0]
        assert row["video_count"] == row["script_count"] == 1
        assert not row["directories"][0]["available"]


def test_history_full_id_and_explicit_status_and_no_resync(inventory):
    config, store, scanner = inventory
    imports = config.data_dir / "import"
    imports.mkdir()
    rows = [{"Script ID": "S025_001", "Status": "Published", "Release Title": "child title"},
            {"Script ID": "S052", "Status": "Published", "Release Title": "historical title", "Planned Date": "2030-01-01", "ES Link": "https://example.com/post"},
            {"Script ID": "S058", "Stauts": "Published", "Actual Release Date": ""}]
    (imports / "monthly-release-plan.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    for name in ("S025_parent", "S052_local", "S058_a", "S063_a"):
        add_work(config.roots[0].path, name)
    scanner.scan()
    works = {row["script_id"]: row for row in work_rows(store)}
    assert works["S025"]["status"] == "pending"
    assert works["S052"]["status"] == "published"
    assert works["S052"]["title"] == "historical title"
    assert works["S058"]["status"] == "published"
    assert works["S063"]["status"] == "pending"
    assert "actual_date" not in json.loads(works["S058"]["metadata"])
    with store.connection() as db:
        assert db.execute("SELECT 1 FROM issues WHERE type='history_unmatched' AND script_id='S025_001'").fetchone()
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.patch(f"/api/works/{works['S052']['id']}", json={"status": "pending"}).status_code == 200
    (imports / "monthly-release-plan.json").write_text(json.dumps({"rows": [{"Script ID": "S052", "Stauts": "Published", "Release Title": "CHANGED REMOTE TITLE"}]}))
    scanner.scan()
    changed = next(row for row in work_rows(store) if row["script_id"] == "S052")
    assert changed["status"] == "pending" and changed["title"] == "historical title"


def host_headers(config):
    (config.data_dir / "host.key").write_text("test-key-never-for-production")
    return {"Host": "localhost:8788", "Origin": "http://localhost:8788", "X-Workbench-Host-Key": "test-key-never-for-production"}


def test_host_open_denied_without_gateway_key_and_origin(inventory):
    config, store, scanner = inventory
    add_work(config.roots[0].path, "S070_a")
    scanner.scan()
    work = work_rows(store)[0]
    headers = host_headers(config)
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.get("/api/capabilities").json()["can_open_folder"] is False
        assert client.get("/api/capabilities", headers=headers).json()["can_open_folder"] is True
        assert client.post(f"/api/works/{work['id']}/open-folder").status_code == 403
        for replacement in ({"X-Workbench-Host-Key": "wrong"}, {"Host": "192.0.2.6:8787", "Origin": "http://192.0.2.6:8787"}, {"Origin": "http://evil.example"}):
            assert client.post(f"/api/works/{work['id']}/open-folder", headers={**headers, **replacement}).status_code == 403
        without_origin = {key: value for key, value in headers.items() if key != "Origin"}
        assert client.post(f"/api/works/{work['id']}/open-folder", headers=without_origin).status_code == 403
        accepted = client.post(f"/api/works/{work['id']}/open-folder", headers=headers)
        assert accepted.status_code == 200
        assert base64.urlsafe_b64decode(accepted.headers["X-Workbench-Open-Folder"]).decode() == "D:\\Media\\2026\\S070_a"


def test_open_path_validation_and_missing_directory(inventory):
    config, store, scanner = inventory
    folder = add_work(config.roots[0].path, "S070_a")
    scanner.scan()
    work = work_rows(store)[0]
    headers = host_headers(config)
    with TestClient(create_app(config, start_worker=False)) as client:
        with store.connection() as db:
            db.execute("UPDATE directories SET windows_path='C:\\Windows' WHERE work_id=?", (work["id"],))
        assert client.post(f"/api/works/{work['id']}/open-folder", headers=headers).status_code == 403
        scanner.scan()
        folder.rename(config.roots[0].path / "gone")
        assert client.post(f"/api/works/{work['id']}/open-folder", headers=headers).status_code == 404


def test_symlinks_outside_roots_never_scanned_or_opened(inventory, tmp_path):
    config, store, scanner = inventory
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.mp4").write_bytes(b"no")
    (config.roots[0].path / "S070_link").symlink_to(outside, target_is_directory=True)
    folder = add_work(config.roots[0].path, "S071_safe")
    (folder / "escape").symlink_to(outside, target_is_directory=True)
    scanner.scan()
    assert [row["script_id"] for row in work_rows(store)] == ["S071"]
    with store.connection() as db:
        assert not db.execute("SELECT 1 FROM assets WHERE name='secret.mp4'").fetchone()
        work = work_rows(store)[0]
        db.execute("UPDATE directories SET path=?,windows_path=? WHERE work_id=?", (str(config.roots[0].path / "S070_link"), "D:\\Media\\2026\\S070_link", work["id"]))
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.post(f"/api/works/{work['id']}/open-folder", headers=host_headers(config)).status_code == 403


def test_search_and_edit_validation_same_origin(inventory):
    config, store, scanner = inventory
    add_work(config.roots[0].path, "S025_001_a")
    add_work(config.roots[0].path, "S025_002_b")
    scanner.scan()
    work = work_rows(store)[0]
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.get("/api/works", params={"q": "S025_001"}).json()["total"] == 1
        assert client.get("/api/works", params={"status": "unknown"}).status_code == 422
        assert client.patch(f"/api/works/{work['id']}", json={"status": "制作中"}).status_code == 422
        assert client.patch(f"/api/works/{work['id']}", json={"title": "  "}).status_code == 422
        assert client.patch(f"/api/works/{work['id']}", json={"windows_path": "C:\\Windows"}).status_code == 422
        assert client.patch(f"/api/works/{work['id']}", headers={"Origin": "http://evil.example"}, json={"status": "published"}).status_code == 403


def test_cover_failure_cached_and_input_changes_retry(inventory, monkeypatch):
    config, store, scanner = inventory
    folder = add_work(config.roots[0].path, "S070_a")
    scanner.scan()
    work = work_rows(store)[0]
    generator = CoverGenerator(store, config)
    calls = []

    def failed_process(argv, **kwargs):
        calls.append(argv[0])
        raise FileNotFoundError("missing ffmpeg")

    monkeypatch.setattr(subprocess, "run", failed_process)
    assert generator.generate(work["id"]) == "failed"
    assert len(calls) == 2
    assert generator.generate(work["id"]) == "cached"
    assert len(calls) == 2
    (folder / "main.mp4").write_bytes(b"different-size-video")
    scanner.scan()
    assert generator.generate(work["id"]) == "failed"
    assert len(calls) == 4
    assert generator.generate(work["id"], force=True) == "failed"
    assert len(calls) == 6
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM issues WHERE type='cover_failed'").fetchone()[0] == 1


def test_video_choice_excludes_previews_and_prefers_direct(inventory):
    config, store, scanner = inventory
    folder = add_work(config.roots[0].path, "S070_a")
    (folder / "Preview").mkdir()
    (folder / "Preview" / "aaa.mp4").write_bytes(b"preview")
    (folder / "nested").mkdir()
    (folder / "nested" / "aaa.mp4").write_bytes(b"main2")
    scanner.scan()
    generator = CoverGenerator(store, config)
    assert generator.source(work_rows(store)[0]["id"]) == folder / "main.mp4"


def test_restart_persistence_and_interrupted_job_recovery(inventory):
    config, store, scanner = inventory
    add_work(config.roots[0].path, "S070_a", assets=False)
    scanner.scan()
    work = work_rows(store)[0]
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.patch(f"/api/works/{work['id']}", json={"status": "published", "notes": "persistent"}).status_code == 200
    worker = JobWorker(store, config)
    queued = worker.enqueue("manual")
    assert worker.enqueue("manual")["id"] == queued["id"]
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='running',started_at=? WHERE id=?", (now(), queued["id"]))
    restarted_store = Store(config.data_dir)
    worker = JobWorker(restarted_store, config)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while restarted_store.job(queued["id"])["status"] != "completed" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert restarted_store.job(queued["id"])["status"] == "completed"
        assert restarted_store.job(queued["id"])["result"]["works"] == 1
    finally:
        worker.stop()
    with TestClient(create_app(config, start_worker=False)) as client:
        row = client.get(f"/api/works/{work['id']}").json()
        assert row["status"] == "published" and row["notes"] == "persistent"


def test_manual_scanning_has_no_startup_or_timer_jobs(inventory, monkeypatch):
    config, store, _ = inventory
    monkeypatch.setenv("WORKBENCH_SCAN_INTERVAL", "10")
    with TestClient(create_app(config, start_worker=False)) as client:
        settings = client.get("/api/settings").json()
        assert settings["scan_mode"] == "manual"
        assert settings["scan_interval_seconds"] == 0
    worker = JobWorker(store, config)
    waiting = threading.Event()
    original_wait = worker.wake_event.wait

    def observe_idle(timeout=None):
        waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(worker.wake_event, "wait", observe_idle)
    worker.start()
    try:
        assert waiting.wait(timeout=2)
        with store.connection() as db:
            assert db.execute("SELECT count(*) FROM jobs WHERE type='scan'").fetchone()[0] == 0
        add_work(config.roots[0].path, "S070_manual", assets=False)
        # Simulate several worker wakeups; these must not create periodic jobs.
        for _ in range(3):
            waiting.clear()
            worker.wake_event.set()
            assert waiting.wait(timeout=2)
        with store.connection() as db:
            assert db.execute("SELECT count(*) FROM jobs WHERE type='scan'").fetchone()[0] == 0
        with TestClient(create_app(config, start_worker=False)) as client:
            response = client.post("/api/scans")
            assert response.status_code == 202
        job_id = response.json()["id"]
        worker.wake_event.set()
        deadline = time.monotonic() + 5
        while store.job(job_id)["status"] != "completed" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert store.job(job_id)["status"] == "completed"
        assert store.job(job_id)["trigger"] == "manual"
        assert [row["script_id"] for row in work_rows(store)] == ["S070"]
    finally:
        worker.stop()


def test_manual_mode_retires_legacy_automatic_scans(inventory):
    config, store, _ = inventory
    worker = JobWorker(store, config)
    automatic = worker.enqueue("startup")
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (automatic["id"],))
        periodic_id = db.execute("INSERT INTO jobs(type,status,trigger,created_at) VALUES('scan','queued','periodic',?)", (now(),)).lastrowid
    worker.start()
    try:
        assert store.job(automatic["id"])["status"] == "cancelled"
        assert store.job(periodic_id)["status"] == "cancelled"
        with store.connection() as db:
            assert db.execute("SELECT count(*) FROM jobs WHERE type='scan' AND status IN ('queued','running')").fetchone()[0] == 0
    finally:
        worker.stop()


def test_unavailable_root_retains_cached_cover(inventory):
    config, store, scanner = inventory
    add_work(config.roots[0].path, "S070_a")
    scanner.scan()
    work = work_rows(store)[0]
    cache = config.data_dir / "covers"
    cache.mkdir()
    image = cache / "fixture.jpg"
    image.write_bytes(b"cached-image")
    with store.connection() as db:
        db.execute("INSERT INTO covers(work_id,fingerprint,path,source_path,updated_at) VALUES(?,?,?,?,?)", (work["id"], "fingerprint", str(image), "source", now()))
    config.roots[0].path.rename(config.roots[0].path.with_name("unmounted"))
    scanner.scan()
    assert CoverGenerator(store, config).generate(work["id"]) == "skipped"
    with store.connection() as db:
        assert db.execute("SELECT path FROM covers WHERE work_id=?", (work["id"],)).fetchone()[0] == str(image)


def test_force_cover_refresh_promotes_queued_and_follows_running(inventory):
    config, store, scanner = inventory
    worker = JobWorker(store, config)
    first = worker.enqueue("manual")
    promoted = worker.enqueue("manual", refresh_covers=True)
    assert promoted["id"] == first["id"]
    assert promoted["result"]["refresh_covers"] is True
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='running',result=? WHERE id=?", (json.dumps({"refresh_covers": False}), first["id"]))
    followup = worker.enqueue("manual", refresh_covers=True)
    assert followup["id"] != first["id"]
    assert followup["status"] == "queued" and followup["result"]["refresh_covers"]
    assert worker.enqueue("manual", refresh_covers=True)["id"] == followup["id"]
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0] == 1
    worker.perform(followup["id"])
    assert store.job(followup["id"])["result"]["refresh_covers"] is True
