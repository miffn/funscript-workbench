from __future__ import annotations

import json
import os
import threading

from .config import Config
from .covers import CoverGenerator
from .scanner import Scanner
from .previews import PreviewService, PreviewError, GenerationCancelled
from .store import Store, now
from .scan_roots import ScanRoots, ScanRootsError


class JobWorker:
    """One persistent, coalescing scan/cover queue; browsers never own its lifetime."""

    def __init__(self, store: Store, config: Config):
        self.store = store
        self.config = config
        self.scanner = Scanner(store, config)
        self.covers = CoverGenerator(store, config)
        self.previews = PreviewService(store, config)
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.preview_wake_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.preview_thread: threading.Thread | None = None

    def enqueue(self, trigger="manual", refresh_covers=False) -> dict:
        with self.store.connection() as db:
            # BEGIN IMMEDIATE serializes simultaneous requests before coalescing.
            db.execute("BEGIN IMMEDIATE")
            selection = ScanRoots(self.store, self.config).state(db)
            if not selection["enabled_paths"]:
                raise ScanRootsError("请至少选择一个扫描目录")
            pending = db.execute("SELECT id,status,result,inputs FROM jobs WHERE type='scan' AND status IN ('queued','running') ORDER BY id").fetchall()
            # Coalesce only requests for the same root snapshot. Changing a setting
            # must not redirect a queued/running manual scan.
            pending = [row for row in pending if json.loads(row["inputs"] or "{}").get("enabled_paths", selection["enabled_paths"]) == selection["enabled_paths"]]
            selected = next((row for row in pending if row["status"] == "queued"), None) if refresh_covers else (pending[0] if pending else None)
            if refresh_covers and not selected:
                selected = next((row for row in pending if bool(json.loads(row["result"] or "{}").get("refresh_covers"))), None)
            if selected:
                job_id = selected["id"]
                if refresh_covers and selected["status"] == "queued":
                    result = json.loads(selected["result"] or "{}")
                    result["refresh_covers"] = True
                    db.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(result), job_id))
            else:
                job_id = db.execute("INSERT INTO jobs(status,trigger,created_at,message,result,inputs) VALUES('queued',?,?,?,?,?)",
                                    (trigger, now(), "等待扫描", json.dumps({"refresh_covers": refresh_covers}),
                                     json.dumps({"enabled_paths": selection["enabled_paths"], "scan_roots_revision": selection["revision"]}))).lastrowid
        self.wake_event.set()
        return self.store.job(job_id)

    def active_preview(self, work_id: int) -> dict | None:
        with self.store.connection() as db:
            row = db.execute("SELECT id FROM jobs WHERE type='preview' AND status IN ('queued','running') AND json_extract(inputs,'$.work_id')=? ORDER BY id LIMIT 1", (work_id,)).fetchone()
        return self.store.job(row[0]) if row else None

    def enqueue_preview(self, inputs: dict) -> dict:
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE type='rematch' AND status IN ('queued','running') AND json_extract(inputs,'$.work_id')=?", (inputs['work_id'],)).fetchone():
                raise PreviewError('作品正在重新匹配文件，请等待完成', 409)
            row = db.execute("SELECT id,inputs FROM jobs WHERE type='preview' AND status IN ('queued','running') AND json_extract(inputs,'$.work_id')=? ORDER BY id LIMIT 1", (inputs["work_id"],)).fetchone()
            if row:
                if json.loads(row["inputs"])["video_asset_id"] != inputs["video_asset_id"]:
                    raise PreviewError("该作品已有其他视频正在生成，请等待当前任务完成", 409)
                job_id = row[0]
            else:
                selection = {key: inputs[key] for key in ("work_id", "script_id", "video_asset_id")}
                job_id = db.execute("INSERT INTO jobs(type,status,trigger,created_at,message,inputs,result) VALUES('preview','queued','manual',?,?,?,?)",
                                    (now(), "等待生成预览", json.dumps(inputs, ensure_ascii=False), json.dumps(selection))).lastrowid
        self.preview_wake_event.set()
        return self.store.job(job_id)

    def enqueue_rematch(self, work_id: int) -> dict:
        work = self.previews.work(work_id)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            active = self.previews.matching.active(db, work_id)
            if active:
                job = db.execute('SELECT type FROM jobs WHERE id=?', (active[0],)).fetchone()
                if job[0] != 'rematch':
                    raise PreviewError('作品正在生成预览，请等待完成后重新匹配', 409)
                job_id = active[0]
            else:
                selection = ScanRoots(self.store, self.config).state(db)
                if not selection['enabled_paths']:
                    raise PreviewError('请至少启用一个扫描目录')
                inputs = {'work_id': work_id, 'script_id': work['script_id'], 'enabled_paths': selection['enabled_paths']}
                job_id = db.execute("INSERT INTO jobs(type,status,trigger,created_at,message,inputs) VALUES('rematch','queued','manual',?,?,?)", (now(), '等待重新匹配文件', json.dumps(inputs))).lastrowid
        self.wake_event.set()
        return self.store.job(job_id)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        with self.store.connection() as db:
            # Retire unfinished scans created by the previous automatic scheduler.
            db.execute("UPDATE jobs SET status='cancelled',finished_at=?,message='已改为手动扫描，取消自动任务' WHERE type='scan' AND trigger IN ('startup','periodic') AND status IN ('queued','running')", (now(),))
            db.execute("UPDATE jobs SET status='queued',started_at=NULL,finished_at=NULL,progress=0,message=CASE type WHEN 'preview' THEN '服务重启后恢复预览生成' ELSE '服务重启后恢复扫描' END WHERE status='running'")
        self.thread = threading.Thread(target=self.run, name="workbench-worker", daemon=True)
        self.thread.start()
        self.preview_thread = threading.Thread(target=self.run_previews, name="workbench-preview-worker", daemon=True)
        self.preview_thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        self.preview_wake_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.preview_thread:
            self.preview_thread.join(timeout=5)

    def run(self):
        while not self.stop_event.is_set():
            with self.store.connection() as db:
                queued = db.execute("SELECT id FROM jobs WHERE type IN ('scan','rematch') AND status='queued' ORDER BY id LIMIT 1").fetchone()
            if queued:
                self.perform(queued[0])
                continue
            self.wake_event.wait(timeout=5)
            self.wake_event.clear()

    def run_previews(self):
        while not self.stop_event.is_set():
            with self.store.connection() as db:
                row = db.execute("SELECT id FROM jobs WHERE type='preview' AND status='queued' ORDER BY id LIMIT 1").fetchone()
            if row:
                self.perform_preview(row[0])
                continue
            self.preview_wake_event.wait(timeout=2)
            self.preview_wake_event.clear()

    def perform_preview(self, job_id: int):
        job = self.store.job(job_id)
        inputs = dict(job["inputs"])
        inputs["_recovery_pid"] = (job.get("result") or {}).get("generator_pid")
        selection = {key: inputs.get(key) for key in ("work_id", "script_id", "video_asset_id")}
        selection["generator_pid"] = os.getpid()
        with self.store.connection() as db:
            db.execute("UPDATE jobs SET status='running',started_at=?,message='检查原视频及全部关联轴脚本',progress=0,error=NULL,result=? WHERE id=?", (now(), json.dumps(selection), job_id))
        stages = {"check": "检查素材与工具", "probe": "检查视频", "fingerprint": "校验输入与缓存", "tools": "检查生成工具",
                  "render": "渲染模拟器", "encode_video": "压制预览视频", "encode_gif": "压制 GIF", "saved": "保存成品",
                  "reuse": "复用已有成品", "completed": "预览生成完成"}

        def progress(event):
            value = max(0, min(99, int(float(event.get("progress", 0)) * 100)))
            stage = event.get("stage", "generate")
            message = stages.get(stage, "生成预览")
            if event.get("clip_index"):
                message += f" · 片段 {event['clip_index']}"
            result = {**selection, "stage": stage, "elapsed_seconds": event.get("elapsed_seconds", 0)}
            if event.get("clip_index"):
                result["clip_index"] = event["clip_index"]
            with self.store.connection() as db:
                db.execute("UPDATE jobs SET progress=max(progress,?),message=?,result=? WHERE id=?", (value, message, json.dumps(result), job_id))

        try:
            manifest = self.previews.generate(inputs, on_progress=progress, cancel_event=self.stop_event)
            result = {**selection, "manifest": manifest, "output_dir": str(self.config.preview_output_root / inputs["script_id"]),
                      "files": self.previews.state(inputs["work_id"])["files"]}
            with self.store.connection() as db:
                db.execute("UPDATE jobs SET status='completed',finished_at=?,progress=100,message='预览生成完成',result=?,error=NULL WHERE id=?", (now(), json.dumps(result, ensure_ascii=False), job_id))
        except GenerationCancelled:
            if not self.stop_event.is_set():
                with self.store.connection() as db:
                    db.execute("UPDATE jobs SET status='failed',finished_at=?,message='预览生成中止',error='生成任务被中止' WHERE id=?", (now(), job_id))
            # Shutdown leaves running persisted; startup restores the original inputs.
        except Exception as error:
            with self.store.connection() as db:
                db.execute("UPDATE jobs SET status='failed',finished_at=?,message='预览生成失败',error=? WHERE id=?", (now(), f"生成预览失败：{error}", job_id))

    def perform(self, job_id: int):
        job = self.store.job(job_id)
        if job['type'] == 'rematch':
            return self.perform_rematch(job_id)
        force = bool((job.get("result") or {}).get("refresh_covers"))
        with self.store.connection() as db:
            db.execute("UPDATE jobs SET status='running',started_at=?,message='扫描库存目录',progress=5 WHERE id=?", (now(), job_id))
        try:
            enabled_paths = job["inputs"].get("enabled_paths")
            if enabled_paths is None:
                with self.store.connection() as db:
                    enabled_paths = ScanRoots(self.store, self.config).state(db)["enabled_paths"]
            roots = tuple(root for root in self.config.roots if str(root.path) in enabled_paths)
            if not roots:
                raise ScanRootsError("任务的扫描目录已不在当前配置中，请重新选择目录并扫描")
            result = self.scanner.scan(roots)
            result.update(covers_generated=0, covers_failed=0, refresh_covers=force)
            with self.store.connection() as db:
                paths = [str(root.path) for root in roots]
                work_ids = [row[0] for row in db.execute(f"SELECT DISTINCT work_id FROM directories WHERE root_path IN ({','.join('?' for _ in paths)}) ORDER BY work_id", paths)]
                db.execute("UPDATE jobs SET result=?,progress=30,message='库存扫描完成，更新封面缓存' WHERE id=?", (json.dumps(result), job_id))
            for index, work_id in enumerate(work_ids):
                if self.stop_event.is_set():
                    # Leave running persisted so next startup restores this idempotent job.
                    return
                outcome = self.covers.generate(work_id, force=force, root_paths=paths)
                if outcome == "generated":
                    result["covers_generated"] += 1
                elif outcome == "failed":
                    result["covers_failed"] += 1
                with self.store.connection() as db:
                    db.execute("UPDATE jobs SET progress=?,message=?,result=? WHERE id=?",
                               (30 + int(65 * (index + 1) / max(1, len(work_ids))), f"更新封面 {index + 1}/{len(work_ids)}", json.dumps(result), job_id))
            with self.store.connection() as db:
                db.execute("UPDATE jobs SET status='completed',finished_at=?,progress=100,message='库存与封面更新完成',result=?,error=NULL WHERE id=?", (now(), json.dumps(result), job_id))
        except Exception as error:
            with self.store.connection() as db:
                db.execute("UPDATE jobs SET status='failed',finished_at=?,message='扫描失败',error=? WHERE id=?", (now(), str(error), job_id))

    def perform_rematch(self, job_id: int):
        job = self.store.job(job_id)
        inputs = job['inputs']
        with self.store.connection() as db:
            db.execute("UPDATE jobs SET status='running',started_at=?,progress=5,message='查找当前完整编号的素材目录',error=NULL WHERE id=?", (now(), job_id))
        try:
            roots = tuple(root for root in self.config.roots if str(root.path) in inputs['enabled_paths'])
            if not roots:
                raise PreviewError('扫描目录已不在配置中，请重新选择目录')
            result = self.scanner.scan(roots, target_id=inputs['script_id'])
            with self.store.connection() as db:
                db.execute("UPDATE jobs SET progress=75,message='更新当前作品的素材与封面' WHERE id=?", (job_id,))
            result['cover'] = self.covers.generate(inputs['work_id'], force=True, root_paths=[str(root.path) for root in roots])
            result.update(work_id=inputs['work_id'], script_id=inputs['script_id'])
            with self.store.connection() as db:
                db.execute("UPDATE jobs SET status='completed',finished_at=?,progress=100,message='文件匹配已刷新，请检查源视频与各轴脚本',result=? WHERE id=?", (now(), json.dumps(result, ensure_ascii=False), job_id))
        except Exception as error:
            with self.store.connection() as db:
                db.execute("UPDATE jobs SET status='failed',finished_at=?,message='重新匹配文件失败',error=? WHERE id=?", (now(), str(error), job_id))
