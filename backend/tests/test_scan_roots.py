from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.covers import CoverGenerator
from backend.jobs import JobWorker
from backend.main import create_app
from backend.scan_roots import ScanRoots
from backend.scanner import Scanner
from backend.store import Store, now


@pytest.fixture
def inventory(tmp_path):
    roots = tuple(Root(tmp_path / name, f"D:\\assets\\{name}", name) for name in ("2026", "workspace"))
    for root in roots:
        root.path.mkdir()
    config = Config(data_dir=tmp_path / "data", roots=roots, ffmpeg="missing-ffmpeg", ffprobe="missing-ffprobe")
    store = Store(config.data_dir)
    return config, store, Scanner(store, config)


def folder(root: Root, name: str, assets=True):
    path = root.path / name
    path.mkdir()
    if assets:
        (path / "video.mp4").write_bytes(b"not-a-real-video")
        (path / "video.funscript").write_text('{"actions":[]}')
    return path


def table(store, name):
    with store.connection() as db:
        return [dict(row) for row in db.execute(f"SELECT * FROM {name} ORDER BY rowid")]


def select(config, store, index, revision=0):
    return ScanRoots(store, config).update([str(config.roots[index].path)], revision)


def test_settings_default_saved_restart_and_no_implicit_scan(inventory):
    config, store, _ = inventory
    with TestClient(create_app(config, start_worker=False)) as client:
        original = client.get("/api/settings").json()
        assert original["scan_roots_revision"] == 0
        assert [root["enabled"] for root in original["roots"]] == [True, True]
        changed = client.put("/api/settings/scan-roots", json={
            "enabled_paths": [str(config.roots[1].path)] * 2, "expected_revision": 0})
        assert changed.status_code == 200
        assert changed.json()["scan_roots_revision"] == 1
        assert [root["enabled"] for root in changed.json()["roots"]] == [False, True]
        assert changed.json()["scan_mode"] == "manual"
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.get("/api/settings").json() == changed.json()
    assert table(Store(config.data_dir), "jobs") == []


@pytest.mark.parametrize("payload", [
    {"enabled_paths": [], "expected_revision": 0},
    {"enabled_paths": ["D:\\assets\\2026"], "expected_revision": 0},
    {"enabled_paths": ["/mnt/d/unknown"], "expected_revision": 0},
    {"enabled_paths": [123], "expected_revision": 0},
    {"enabled_paths": [], "expected_revision": True},
])
def test_settings_invalid_request_does_not_write(inventory, payload):
    config, store, _ = inventory
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.put("/api/settings/scan-roots", json=payload).status_code == 422
        assert client.get("/api/settings").json()["scan_roots_revision"] == 0
    assert table(store, "settings") == []


def test_settings_conflict_and_same_origin(inventory):
    config, store, _ = inventory
    select(config, store, 1)
    with TestClient(create_app(config, start_worker=False)) as client:
        body = {"enabled_paths": [str(config.roots[0].path)], "expected_revision": 0}
        assert client.put("/api/settings/scan-roots", json=body).status_code == 409
        body["expected_revision"] = 1
        assert client.put("/api/settings/scan-roots", json=body, headers={"Origin": "http://evil.example"}).status_code == 403
        assert [root["enabled"] for root in client.get("/api/settings").json()["roots"]] == [False, True]


def test_partial_scan_preserves_unselected_inventory_tags_issues_and_covers(inventory):
    config, store, scanner = inventory
    folder(config.roots[0], "S080")
    second = folder(config.roots[1], "S081", assets=False)
    (config.roots[1].path / "raw.txt").write_text("raw")
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        works = client.get("/api/works").json()["items"]
        work = next(row for row in works if row["script_id"] == "S081")
        client.patch(f"/api/works/{work['id']}", json={"title": "手动", "notes": "保存", "status": "published"})
        tag = client.post("/api/tags", json={"category": "custom", "name": "保留标签"}).json()
        client.put(f"/api/works/{work['id']}/tags", json={"tag_ids": [tag["id"]], "expected_revision": 0})
    with store.connection() as db:
        db.execute("INSERT INTO covers(work_id,fingerprint,path,source_path,updated_at) VALUES(?,?,?,?,?)",
                   (work["id"], "fixture", "cache", "unselected-video", now()))
    before = {name: table(store, name) for name in ("works", "directories", "assets", "work_tags", "work_tag_state", "covers")}
    retained_issues = [row for row in table(store, "issues") if row["work_id"] == work["id"] or row["type"] == "unnumbered_material"]
    second.rename(config.roots[1].path / "removed-on-disk")
    folder(config.roots[1], "S083")
    folder(config.roots[0], "S082")
    scanner.scan((config.roots[0],))
    for name, rows in before.items():
        after = table(store, name)
        for row in rows:
            if name == "directories" and row["root_path"] == str(config.roots[0].path):
                continue
            assert row in after
    assert {row["script_id"] for row in table(store, "works")} == {"S080", "S081", "S082"}
    assert all(row in table(store, "issues") for row in retained_issues)
    scanner.scan((config.roots[1],))
    assert "S083" in {row["script_id"] for row in table(store, "works")}
    assert any(row["type"] == "directory_missing" and row["work_id"] == work["id"] for row in table(store, "issues"))


def test_unavailable_unselected_root_keeps_diagnostic_and_api_availability(inventory):
    config, store, scanner = inventory
    folder(config.roots[1], "S080")
    scanner.scan()
    config.roots[1].path.rename(config.roots[1].path.with_name("unmounted"))
    scanner.scan()
    scanner.scan((config.roots[0],))
    with TestClient(create_app(config, start_worker=False)) as client:
        work = client.get("/api/works").json()["items"][0]
        assert not work["directories"][0]["available"]
        assert work["video_count"] == 1
        assert any(issue["type"] == "root_unavailable" for issue in work["issues"])


def test_jobs_snapshot_coalescing_restart_and_cover_scope(inventory, monkeypatch):
    config, store, scanner = inventory
    folder(config.roots[0], "S080")
    folder(config.roots[1], "S081")
    scanner.scan()
    select(config, store, 0)
    worker = JobWorker(store, config)
    first = worker.enqueue()
    assert first["inputs"]["enabled_paths"] == [str(config.roots[0].path)]
    assert worker.enqueue()["id"] == first["id"]
    select(config, store, 1, revision=1)
    second = worker.enqueue()
    assert second["id"] != first["id"]
    assert worker.enqueue()["id"] == second["id"]
    restarted = JobWorker(Store(config.data_dir), config)
    covered = []
    monkeypatch.setattr(restarted.covers, "generate", lambda work_id, **kwargs: covered.append((work_id, kwargs["root_paths"])) or "skipped")
    folder(config.roots[0], "S082")
    folder(config.roots[1], "S083")
    restarted.perform(first["id"])
    assert store.job(first["id"])["status"] == "completed"
    assert store.job(first["id"])["result"]["scanned_roots"] == [str(config.roots[0].path)]
    works = {row["id"]: row["script_id"] for row in table(store, "works")}
    assert {works[item[0]] for item in covered} == {"S080", "S082"}
    assert all(item[1] == [str(config.roots[0].path)] for item in covered)
    assert "S083" not in works.values()
    restarted.perform(second["id"])
    assert "S083" in {row["script_id"] for row in table(store, "works")}


def test_duplicate_cover_never_reads_unselected_root(inventory, monkeypatch):
    config, store, scanner = inventory
    first = folder(config.roots[0], "S080")
    second = folder(config.roots[1], "S080")
    scanner.scan()
    work_id = table(store, "works")[0]["id"]
    generator = CoverGenerator(store, config)
    assert generator.source(work_id, [str(config.roots[1].path)]) == second / "video.mp4"
    first.rename(config.roots[0].path / "unselected-disappeared")
    scanner.scan((config.roots[1],))
    assert any(row["type"] == "duplicate_identifier" for row in table(store, "issues"))
    assert generator.source(work_id, [str(config.roots[1].path)]) == second / "video.mp4"
    (second / "video.mp4").unlink()
    scanner.scan((config.roots[1],))
    with store.connection() as db:
        db.execute("INSERT INTO covers(work_id,fingerprint,path,source_path,updated_at) VALUES(?,?,?,?,?)",
                   (work_id, "fixture", "cache", str(first / "video.mp4"), now()))
    assert generator.generate(work_id, root_paths=[str(config.roots[1].path)]) == "skipped"
    assert table(store, "covers")[0]["fingerprint"] == "fixture"


def test_no_enabled_config_rejects_scan_without_job_and_legacy_job_fallback(inventory):
    config, store, _ = inventory
    with store.connection() as db:
        db.execute("INSERT INTO settings(key,value) VALUES('scan_roots',?)", (json.dumps({"enabled_paths": ["/removed/config/root"], "revision": 1}),))
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.post("/api/scans").status_code == 422
    assert table(store, "jobs") == []
    select(config, store, 1, revision=1)
    folder(config.roots[0], "S080")
    folder(config.roots[1], "S081")
    with store.connection() as db:
        job_id = db.execute("INSERT INTO jobs(type,status,trigger,created_at,inputs) VALUES('scan','queued','manual',?,'{}')", (now(),)).lastrowid
    JobWorker(store, config).perform(job_id)
    assert store.job(job_id)["status"] == "completed"
    assert {row["script_id"] for row in table(store, "works")} == {"S081"}


def test_shared_cover_failure_preserves_unselected_valid_cache(inventory, monkeypatch):
    config, store, scanner = inventory
    first = folder(config.roots[0], "S080")
    second = folder(config.roots[1], "S080")
    scanner.scan()
    work_id = table(store, "works")[0]["id"]
    generator = CoverGenerator(store, config)
    old_cache = generator.cache_dir / f"{work_id}-existing.jpg"
    old_cache.write_bytes(b"valid-previous-cache")
    with store.connection() as db:
        db.execute("INSERT INTO covers(work_id,fingerprint,path,source_path,updated_at) VALUES(?,?,?,?,?)",
                   (work_id, "old-fingerprint", str(old_cache), str(first / "video.mp4"), now()))
    before = table(store, "covers")
    attempts = []

    def fail(argv, **kwargs):
        attempts.append(argv)
        raise FileNotFoundError("missing ffmpeg")

    monkeypatch.setattr(subprocess, "run", fail)
    assert generator.generate(work_id, root_paths=[str(config.roots[1].path)]) == "failed"
    assert table(store, "covers") == before
    assert old_cache.read_bytes() == b"valid-previous-cache"
    assert all(str(second / "video.mp4") in argv for argv in attempts)
    assert any(issue["type"] == "cover_failed" for issue in table(store, "issues"))


def test_running_scan_restart_preserves_original_root_snapshot(inventory, monkeypatch):
    config, store, _ = inventory
    folder(config.roots[0], "S080")
    folder(config.roots[1], "S081")
    select(config, store, 0)
    job = JobWorker(store, config).enqueue()
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
    select(config, store, 1, revision=1)
    restarted = JobWorker(Store(config.data_dir), config)
    monkeypatch.setattr(restarted.covers, "generate", lambda *args, **kwargs: "skipped")
    restarted.start()
    try:
        deadline = time.monotonic() + 3
        while store.job(job["id"])["status"] != "completed" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert store.job(job["id"])["status"] == "completed"
        assert {row["script_id"] for row in table(store, "works")} == {"S080"}
        assert store.job(job["id"])["inputs"] == job["inputs"]
    finally:
        restarted.stop()
