from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
from urllib.parse import quote

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, PROJECT_DIR, Root
from backend.jobs import JobWorker
from backend.main import create_app
from backend.previews import MEDIA_FILES, PreviewError, PreviewService
from backend.scanner import Scanner
from backend.store import Store, now
import backend.previews as preview_module
from preview_generator import GenerationCancelled


@pytest.fixture
def previews(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    config = Config(data_dir=tmp_path / "data", roots=(Root(root, "D:\\Media\\workspace", "workspace"),),
                    preview_output_root=root / "预览", open_mode="gateway",
                    ffmpeg="missing-ffmpeg-for-covers", ffprobe="missing-ffprobe-for-covers")
    store = Store(config.data_dir)
    return config, store, Scanner(store, config)


def source(root: Path, name="S070_source", stem="main", folder=None):
    folder = folder or root / name
    folder.mkdir(exist_ok=True)
    video = folder / f"{stem}.mp4"
    video.write_bytes(b"video-test")
    (folder / f"{stem}.funscript").write_text(json.dumps({"actions": [{"at": 0, "pos": 0}, {"at": 10000, "pos": 100}]}))
    return folder, video


def first_work(store):
    with store.connection() as db:
        return dict(db.execute("SELECT * FROM works ORDER BY id LIMIT 1").fetchone())


def video_id(store, name="main.mp4"):
    with store.connection() as db:
        return db.execute("SELECT id FROM assets WHERE kind='video' AND name=?", (name,)).fetchone()[0]


def wait_job(store, job_id, status="completed", timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.job(job_id)
        if job["status"] == status:
            return job
        if job["status"] == "failed" and status != "failed":
            pytest.fail(job["error"])
        time.sleep(0.02)
    pytest.fail(f"Job did not become {status}: {store.job(job_id)}")


def make_manifest(output: Path, script_id="S070"):
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for filename, (kind, index) in MEDIA_FILES.items():
        data = f"{script_id}:{filename}".encode()
        path = output / filename
        path.write_bytes(data)
        records.append({"filename": filename, "kind": kind, "clip_index": index, "width": 1920 if kind == "video" else 192,
                        "height": 1080 if kind == "video" else 108, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "path": str(path)})
    manifest = {"schema_version": 1, "status": "completed", "work_id": script_id, "outputs": records}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest


def fake_generate(config, on_progress=None, cancel_event=None):
    assert config.model == "builtin" and config.show_axis_hud is True
    assert config.percentages == (0.2, 0.4, 0.6, 0.8) and config.clip_seconds == 10
    assert config.width == 1920 and config.height == 1080
    assert config.gif_width == 192 and config.gif_height == 108
    if on_progress:
        on_progress({"progress": 0.6, "stage": "encode_video", "clip_index": 3})
    if cancel_event and cancel_event.is_set():
        raise GenerationCancelled("cancelled")
    manifest = make_manifest(Path(config.output_dir), config.work_id)
    if on_progress:
        on_progress({"progress": 1.0, "stage": "completed"})
    return manifest


def test_preview_api_generate_persists_and_serves_fixed_media(previews, monkeypatch):
    config, store, scanner = previews
    folder, video = source(config.roots[0].path)
    (folder / "main.pitch.funscript").write_text((folder / "main.funscript").read_text())
    (folder / "unrelated.roll.funscript").write_text("not-a-valid-script")
    scanner.scan()
    app = create_app(config, start_worker=False)
    worker = app.state.worker
    seen = []

    def generate(config, **kwargs):
        seen.append(config)
        return fake_generate(config, **kwargs)

    monkeypatch.setattr(preview_module, "generate", generate)
    with TestClient(app) as client:
        work = first_work(store)
        original = client.patch(f"/api/works/{work['id']}", json={"title": "人工标题", "notes": "原发布备注", "status": "pending"}).json()
        initial = client.get(f"/api/works/{work['id']}/preview").json()
        assert initial["files"] == [] and initial["job"] is None
        response = client.post(f"/api/works/{work['id']}/preview")
        assert response.status_code == 202
        job = response.json()
        assert job["type"] == "preview" and job["result"]["video_asset_id"] == video_id(store)
        worker.perform_preview(job["id"])
        finished = client.get(f"/api/jobs/{job['id']}").json()
        assert finished["status"] == "completed" and finished["progress"] == 100
        assert finished["result"]["work_id"] == work["id"]
        assert set(seen[0].scripts) == {"stroke", "pitch"}
        state = client.get(f"/api/works/{work['id']}/preview").json()
        assert len(state["files"]) == 8 and state["error"] is None
        assert state["windows_path"].endswith("预览\\S070")
        for media in state["files"]:
            fetched = client.get(media["url"])
            assert fetched.status_code == 200 and len(fetched.content) == media["size"]
            assert fetched.headers["content-type"] == ("video/webm" if media["kind"] == "video" else "image/gif")
        assert client.get(f"/api/works/{work['id']}/preview/files/manifest.json").status_code == 404
        assert client.get(f"/api/works/{work['id']}/preview/files/{quote('main.mp4')}").status_code == 404
        assert client.get(f"/api/works/{work['id']}/preview/files/%2e%2e%2fmain.mp4").status_code == 404
        after = client.get(f"/api/works/{work['id']}").json()
        assert {field: after[field] for field in ("title", "notes", "status", "updated_at")} == {field: original[field] for field in ("title", "notes", "status", "updated_at")}


def test_multi_video_selection_active_conflict_and_foreign_id(previews):
    config, store, scanner = previews
    folder, _ = source(config.roots[0].path)
    source(config.roots[0].path, stem="other", folder=folder)
    source(config.roots[0].path, name="S071_foreign", stem="foreign")
    scanner.scan()
    work = first_work(store)
    endpoint = f"/api/works/{work['id']}/preview"
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.post(endpoint).status_code == 409
        first = client.post(endpoint, json={"video_asset_id": video_id(store)}).json()
        assert client.post(endpoint).json()["id"] == first["id"]
        assert client.post(endpoint, json={"video_asset_id": video_id(store)}).json()["id"] == first["id"]
        assert client.post(endpoint, json={"video_asset_id": video_id(store, "other.mp4")}).status_code == 409
        assert client.post(endpoint, json={"video_asset_id": video_id(store, "foreign.mp4")}).status_code == 404
        assert client.post(endpoint, json={"video_asset_id": 9999}).status_code == 404
        assert client.post(endpoint, json={"video_path": "/etc/passwd"}).status_code == 422
        assert client.post(endpoint, json={"video_asset_id": True}).status_code == 422


def test_concurrent_preview_requests_deduplicate_and_scan_has_own_job(previews):
    config, store, scanner = previews
    source(config.roots[0].path)
    scanner.scan()
    worker = JobWorker(store, config)
    inputs = worker.previews.select_inputs(first_work(store)["id"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda _: worker.enqueue_preview(inputs), range(12)))
    assert len({job["id"] for job in jobs}) == 1
    assert worker.enqueue()["id"] != jobs[0]["id"]
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE type='preview'").fetchone()[0] == 1


def test_concurrent_different_video_selections_return_conflict_at_transaction(previews):
    config, store, scanner = previews
    folder, _ = source(config.roots[0].path)
    source(config.roots[0].path, stem="other", folder=folder)
    scanner.scan()
    worker = JobWorker(store, config)
    work = first_work(store)
    # Both clients select inputs before either one sees an active job.
    first = worker.previews.select_inputs(work["id"], video_id(store))
    second = worker.previews.select_inputs(work["id"], video_id(store, "other.mp4"))
    start = threading.Barrier(2)

    def submit(inputs):
        start.wait(timeout=2)
        try:
            return worker.enqueue_preview(inputs)
        except PreviewError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [first, second]))
    assert sum(isinstance(result, dict) for result in results) == 1
    conflicts = [result for result in results if isinstance(result, PreviewError)]
    assert len(conflicts) == 1 and conflicts[0].status_code == 409
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE type='preview'").fetchone()[0] == 1


def test_scan_and_web_requests_continue_during_preview(previews, monkeypatch):
    config, store, scanner = previews
    source(config.roots[0].path)
    scanner.scan()
    app = create_app(config, start_worker=False)
    worker = app.state.worker
    started, release = threading.Event(), threading.Event()

    def slow_generate(config, on_progress=None, cancel_event=None):
        on_progress({"progress": 0.4, "stage": "render"})
        started.set()
        assert release.wait(timeout=5)
        return fake_generate(config, on_progress=on_progress, cancel_event=cancel_event)

    monkeypatch.setattr(preview_module, "generate", slow_generate)
    preview = worker.enqueue_preview(worker.previews.select_inputs(first_work(store)["id"]))
    worker.start()
    try:
        assert started.wait(timeout=3)
        source(config.roots[0].path, name="S072_new")
        scan = worker.enqueue()
        wait_job(store, scan["id"])
        assert store.job(preview["id"])["status"] == "running"
        assert store.job(preview["id"])["progress"] == 40
        with TestClient(app) as client:
            begin = time.monotonic()
            assert client.get("/api/health").status_code == 200
            assert client.get("/api/works").json()["total"] == 2
            assert time.monotonic() - begin < 1
        release.set()
        wait_job(store, preview["id"])
    finally:
        release.set()
        worker.stop()


def test_resume_keeps_preview_type_and_original_inputs(previews, monkeypatch):
    config, store, scanner = previews
    source(config.roots[0].path)
    scanner.scan()
    first = JobWorker(store, config)
    inputs = first.previews.select_inputs(first_work(store)["id"])
    queued = first.enqueue_preview(inputs)
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='running',progress=40,result=? WHERE id=?", (json.dumps({"generator_pid": 987654321, "video_asset_id": inputs["video_asset_id"]}), queued["id"]))
    seen = []
    restarted = JobWorker(Store(config.data_dir), config)

    def resumed(config, on_progress=None, cancel_event=None):
        seen.append(config.video)
        return fake_generate(config, on_progress=on_progress, cancel_event=cancel_event)

    monkeypatch.setattr(preview_module, "generate", resumed)
    restarted.start()
    try:
        completed = wait_job(store, queued["id"])
        assert completed["type"] == "preview" and completed["inputs"] == inputs
        assert seen == [inputs["video_path"]]
    finally:
        restarted.stop()


def test_failure_readable_and_previous_outputs_retained(previews, monkeypatch):
    config, store, scanner = previews
    source(config.roots[0].path)
    scanner.scan()
    work = first_work(store)
    output = config.preview_output_root / work["script_id"]
    old = make_manifest(output, work["script_id"])
    worker = JobWorker(store, config)
    queued = worker.enqueue_preview(worker.previews.select_inputs(work["id"]))

    def fail(config, on_progress=None, cancel_event=None):
        on_progress({"progress": 0.3, "stage": "encode_video"})
        (Path(config.output_dir) / "manifest.json").write_text(json.dumps({"work_id": config.work_id, "outputs": [], "status": "failed", "error": "编码失败"}))
        raise RuntimeError("测试编码失败")

    monkeypatch.setattr(preview_module, "generate", fail)
    worker.perform_preview(queued["id"])
    failed = store.job(queued["id"])
    assert failed["status"] == "failed" and "测试编码失败" in failed["error"]
    assert failed["result"]["work_id"] == work["id"] and failed["progress"] == 30
    state = worker.previews.state(work["id"])
    assert len(state["files"]) == 8 and "测试编码失败" in state["error"]
    retried = worker.enqueue_preview(worker.previews.select_inputs(work["id"]))
    assert retried["id"] != queued["id"]
    assert worker.previews.state(work["id"])["error"] is None
    assert len(worker.previews.state(work["id"])["files"]) == 8


@pytest.mark.parametrize("fault", ["missing", "empty", "duplicate", "symlink"])
def test_exact_scripts_invalid_or_missing_rejected_before_queue(previews, fault):
    config, store, scanner = previews
    folder, _ = source(config.roots[0].path)
    main = folder / "main.funscript"
    if fault == "missing":
        main.rename(folder / "unrelated.funscript")
    elif fault == "empty":
        main.write_text('{"actions":[]}')
    elif fault == "duplicate":
        (folder / "MAIN.FUNSCRIPT").write_text(main.read_text())
    else:
        main.rename(folder / "real.funscript")
        main.symlink_to(folder / "real.funscript")
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        response = client.post(f"/api/works/{first_work(store)['id']}/preview")
        assert response.status_code == (403 if fault == "symlink" else 422)
        assert response.json()["detail"]
        assert client.get("/api/jobs").json()["items"] == []


def test_source_asset_path_escape_and_source_change_after_queue(previews, monkeypatch):
    config, store, scanner = previews
    folder, _ = source(config.roots[0].path)
    scanner.scan()
    worker = JobWorker(store, config)
    work = first_work(store)
    inputs = worker.previews.select_inputs(work["id"])
    queued = worker.enqueue_preview(inputs)
    with store.connection() as db:
        db.execute("UPDATE assets SET relative_path='../outside.mp4' WHERE id=?", (inputs["video_asset_id"],))
    with pytest.raises(PreviewError, match="超出库存目录"):
        worker.previews.select_inputs(work["id"], inputs["video_asset_id"])
    worker.perform_preview(queued["id"])
    assert store.job(queued["id"])["status"] == "failed"
    assert "超出库存目录" in store.job(queued["id"])["error"]


def test_scan_excludes_preview_tree_from_unnumbered_and_assets(previews):
    config, store, scanner = previews
    folder, _ = source(config.roots[0].path)
    make_manifest(config.preview_output_root / "S070")
    source(config.preview_output_root, name="S099_generated")
    scanner.scan()
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM works").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM assets").fetchone()[0] == 2
        assert not db.execute("SELECT 1 FROM issues WHERE type='unnumbered_material'").fetchone()
    nested = replace(config, preview_output_root=folder / "预览")
    make_manifest(nested.preview_output_root / "S070")
    Scanner(store, nested).scan()
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM assets").fetchone()[0] == 2


def test_existing_cli_manifest_is_read_without_job_and_wrong_manifest_ignored(previews):
    config, store, scanner = previews
    source(config.roots[0].path, name="S064_source")
    scanner.scan()
    work = first_work(store)
    output = config.preview_output_root / "S064"
    manifest = make_manifest(output, "S064")
    service = PreviewService(store, config)
    assert service.state(work["id"])["job"] is None
    assert len(service.state(work["id"])["files"]) == 8
    manifest["work_id"] = "S064_001"
    (output / "manifest.json").write_text(json.dumps(manifest))
    assert service.state(work["id"])["files"] == []


@pytest.mark.parametrize("fault", ["file_symlink", "output_symlink", "root_symlink", "checksum", "extra_name"])
def test_media_and_manifest_path_permissions(previews, fault, tmp_path):
    config, store, scanner = previews
    source(config.roots[0].path)
    scanner.scan()
    work = first_work(store)
    output = config.preview_output_root / "S070"
    manifest = make_manifest(output)
    filename = "预览视频1.webm"
    if fault == "file_symlink":
        media = output / filename
        media.rename(tmp_path / filename)
        media.symlink_to(tmp_path / filename)
    elif fault in {"output_symlink", "root_symlink"}:
        linked = output if fault == "output_symlink" else config.preview_output_root
        linked.rename(tmp_path / "actual-output")
        linked.symlink_to(tmp_path / "actual-output", target_is_directory=True)
    elif fault == "checksum":
        media = output / filename
        data = media.read_bytes()
        media.write_bytes(b"X" * len(data))
    else:
        (output / "secret.txt").write_bytes(b"secret")
        manifest["outputs"].append({"filename": "../secret.txt", "kind": "video", "clip_index": 1, "width": 1920, "height": 1080,
                                    "sha256": hashlib.sha256(b"secret").hexdigest(), "size": 6})
        (output / "manifest.json").write_text(json.dumps(manifest))
    with TestClient(create_app(config, start_worker=False)) as client:
        fetched = client.get(f"/api/works/{work['id']}/preview/files/{quote(filename)}")
        assert fetched.status_code == (200 if fault == "extra_name" else 403 if fault in {"output_symlink", "root_symlink"} else 404)
        assert client.get(f"/api/works/{work['id']}/preview/files/secret.txt").status_code == 404


def test_preview_open_folder_requires_host_origin_and_configured_mapping(previews, tmp_path):
    config, store, scanner = previews
    source(config.roots[0].path)
    scanner.scan()
    work = first_work(store)
    endpoint = f"/api/works/{work['id']}/preview/open-folder"
    (config.data_dir / "host.key").write_text("a-long-test-secret-key-not-for-production")
    headers = {"Host": "localhost:8788", "Origin": "http://localhost:8788", "X-Workbench-Host-Key": "a-long-test-secret-key-not-for-production"}
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.post(endpoint).status_code == 403
        assert client.post(endpoint, headers=headers).status_code == 404
        make_manifest(config.preview_output_root / "S070")
        response = client.post(endpoint, headers=headers)
        assert response.status_code == 200
        assert base64.urlsafe_b64decode(response.headers["X-Workbench-Open-Folder"]).decode() == "D:\\Media\\workspace\\预览\\S070"
        assert client.post(endpoint, headers={**headers, "Origin": "http://evil.example"}).status_code == 403
        assert client.post(endpoint, headers={**headers, "Host": "192.0.2.6:8787", "Origin": "http://192.0.2.6:8787"}).status_code == 403
        assert client.post(endpoint, headers={key: value for key, value in headers.items() if key != "Origin"}).status_code == 403
    unmapped = replace(config, preview_output_root=tmp_path / "unmapped")
    make_manifest(unmapped.preview_output_root / "S070")
    with TestClient(create_app(unmapped, start_worker=False)) as client:
        assert client.post(endpoint, headers=headers).status_code == 403


def test_only_owned_dead_pid_lock_is_recovered(previews, monkeypatch):
    config, store, scanner = previews
    service = PreviewService(store, config)
    output = config.preview_output_root / "S070"
    output.mkdir(parents=True)
    lock = output / ".preview-generator.lock"
    lock.write_text(json.dumps({"pid": 123456789}))

    def gone(pid, signal):
        raise ProcessLookupError()

    monkeypatch.setattr(preview_module.os, "kill", gone)
    service.recover_stale_lock(output, None)
    assert lock.exists()
    service.recover_stale_lock(output, 1234)
    assert lock.exists()
    service.recover_stale_lock(output, 123456789)
    assert not lock.exists()
    lock.write_text(json.dumps({"pid": 123456789}))
    monkeypatch.setattr(preview_module.os, "kill", lambda pid, signal: None)
    service.recover_stale_lock(output, 123456789)
    assert lock.exists()


def test_generator_cancel_on_shutdown_leaves_recoverable_inputs(previews, monkeypatch):
    config, store, scanner = previews
    source(config.roots[0].path)
    scanner.scan()
    worker = JobWorker(store, config)
    inputs = worker.previews.select_inputs(first_work(store)["id"])
    queued = worker.enqueue_preview(inputs)
    started = threading.Event()

    def cancellable(config, on_progress=None, cancel_event=None):
        on_progress({"progress": 0.2, "stage": "render"})
        started.set()
        assert cancel_event.wait(timeout=5)
        raise GenerationCancelled("service stopping")

    monkeypatch.setattr(preview_module, "generate", cancellable)
    worker.start()
    assert started.wait(timeout=3)
    worker.stop()
    interrupted = store.job(queued["id"])
    assert interrupted["status"] == "running" and interrupted["inputs"] == inputs
    assert not worker.preview_thread.is_alive()


def test_backend_preview_real_tiny_egl_integration(previews, monkeypatch):
    config, store, scanner = previews
    renderer = PROJECT_DIR / "preview_generator" / "build" / "ofs-preview-renderer"
    if not renderer.is_file() or not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Real FFmpeg/EGL renderer unavailable")
    folder, video = source(config.roots[0].path)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=4",
                    "-t", "8", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True, timeout=30)
    config = replace(config, ffmpeg="ffmpeg", ffprobe="ffprobe", preview_renderer=renderer)
    Scanner(store, config).scan()
    original_generate = preview_module.generate

    def tiny(generator_config, **kwargs):
        assert generator_config.clip_seconds == 10 and generator_config.percentages == (0.2, 0.4, 0.6, 0.8)
        test_config = replace(generator_config, width=160, height=90, fps=4, gif_width=80, gif_height=46, gif_fps=4,
                              simulator_width=64, simulator_height=64, margin=4, clip_seconds=1, include_audio=False, threads=1)
        return original_generate(test_config, **kwargs)

    monkeypatch.setattr(preview_module, "generate", tiny)
    app = create_app(config, start_worker=False)
    work = first_work(store)
    with TestClient(app) as client:
        before_video = video.read_bytes()
        response = client.post(f"/api/works/{work['id']}/preview")
        assert response.status_code == 202
        app.state.worker.perform_preview(response.json()["id"])
        job = client.get(f"/api/jobs/{response.json()['id']}").json()
        assert job["status"] == "completed", job.get("error")
        state = client.get(f"/api/works/{work['id']}/preview").json()
        assert len(state["files"]) == 8
        assert {file["kind"] for file in state["files"]} == {"video", "gif"}
        for file in state["files"]:
            assert client.get(file["url"]).status_code == 200
        assert video.read_bytes() == before_video
        assert client.get(f"/api/works/{work['id']}").json()["status"] == "pending"
