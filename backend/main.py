from __future__ import annotations

import base64
from contextlib import asynccontextmanager
import hmac
import json
from pathlib import Path, PureWindowsPath
import subprocess
from urllib.parse import urlparse
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import Config
from .jobs import JobWorker
from .scanner import enrich_metadata
from .previews import MEDIA_FILES, PreviewError
from .store import Store, now
from .tags import TagService, TagError
from .scan_roots import ScanRoots, ScanRootsError
from .work_links import WorkLinks, WorkLinksError


class WorkEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=500)
    status: str | None = None
    notes: str | None = Field(default=None, max_length=20000)


class OpenFolder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directory_id: int | None = None


class ScanOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_covers: bool = False


class PreviewOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_asset_id: int | None = Field(default=None, gt=0, strict=True)
    force: bool = Field(default=False, strict=True)


class PreviewMatchingEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_asset_id: int = Field(gt=0, strict=True)
    script_asset_ids: dict[str, Annotated[int, Field(strict=True, gt=0)]] = Field(max_length=6)
    expected_revision: int = Field(ge=0, strict=True)


class ScanRootsEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled_paths: list[Annotated[str, Field(strict=True, min_length=1)]]
    expected_revision: int = Field(strict=True, ge=0)


class TagCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["author", "video_type", "axis_type", "release_type", "tier", "custom"]
    name: str = Field(strict=True, min_length=1, max_length=120)
    support_url: str | None = Field(default=None, strict=True, max_length=2000)
    support_status: Literal["unknown", "none", "url"] | None = None


class TagEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1)
    name: str | None = Field(default=None, strict=True, min_length=1, max_length=120)
    support_url: str | None = Field(default=None, strict=True, max_length=2000)
    support_status: Literal["unknown", "none", "url"] | None = None


class WorkTagsEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tag_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(max_length=100)
    expected_revision: int = Field(strict=True, ge=0)


class WorkLinksEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    links: dict[Literal['patreon', 'video', 'script', 'es'], Annotated[str, Field(strict=True, max_length=4000)]] = Field(min_length=1, max_length=4)
    expected_revision: int = Field(strict=True, ge=0)


def create_app(config: Config | None = None, start_worker: bool = True) -> FastAPI:
    config = config or Config.from_environment()
    store = Store(config.data_dir)
    worker = JobWorker(store, config)
    tags = TagService(store)
    scan_roots = ScanRoots(store, config)
    links = WorkLinks(store)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            worker.start()
        yield
        if start_worker:
            worker.stop()

    app = FastAPI(title="Funscript 工作台", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.worker = worker
    app.state.config = config
    app.state.tags = tags

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                parsed = urlparse(origin)
                if parsed.scheme != "http" or parsed.netloc.lower() != request.headers.get("host", "").lower():
                    return JSONResponse({"detail": "不允许跨站修改工作台数据"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def last_scan(db) -> dict | None:
        row = db.execute("SELECT value FROM settings WHERE key='last_scan'").fetchone()
        return json.loads(row[0]) if row else None

    def details(db, work, include_assets=False) -> dict:
        record = dict(work)
        metadata = enrich_metadata(json.loads(record["metadata"]))
        record.pop("manual_fields", None)
        record["metadata"] = metadata
        link_state = links.state(db, record["id"])
        record["links"] = link_state["links"]
        record["links_revision"] = link_state["links_revision"]
        tag_state = tags.work_state(db, record["id"])
        record["tags"] = tag_state["tags"]
        record["tags_revision"] = tag_state["tags_revision"]
        selected_type = next((tag["name"] for tag in record["tags"] if tag["category"] == "video_type"), None)
        manual_tags = db.execute("SELECT manual_edited FROM work_tag_state WHERE work_id=?", (record["id"],)).fetchone()
        record["video_type"] = selected_type if selected_type is not None else "" if manual_tags and manual_tags[0] else metadata.get("video_type") or metadata.get("Video Type") or ""
        record["axis_type"] = next((tag["name"] for tag in record["tags"] if tag["category"] == "axis_type"), "")
        latest = last_scan(db) or {}
        unavailable_roots = latest.get("unavailable_roots", [])
        directories = [dict(row) for row in db.execute("SELECT * FROM directories WHERE work_id=? ORDER BY available DESC,id", (record["id"],))]
        for directory in directories:
            directory["available"] = bool(directory["available"]) and directory["root_path"] not in unavailable_roots
        record["directories"] = directories
        assets = [dict(row) for row in db.execute("SELECT a.* FROM assets a JOIN directories d ON d.id=a.directory_id WHERE d.work_id=? ORDER BY a.kind,a.relative_path COLLATE NOCASE", (record["id"],))]
        active_ids = {directory["id"] for directory in directories if directory["available"]}
        # Unreachable root keeps prior inventory counts; vanished directory is historical only.
        counted_ids = active_ids | {directory["id"] for directory in directories if directory["root_path"] in unavailable_roots}
        record["video_count"] = sum(asset["kind"] == "video" and asset["directory_id"] in counted_ids for asset in assets)
        record["script_count"] = sum(asset["kind"] == "script" and asset["directory_id"] in counted_ids for asset in assets)
        record["issues"] = [{"type": row["type"], "message": row["message"]} for row in db.execute("SELECT type,message FROM issues WHERE work_id=? ORDER BY id", (record["id"],))]
        cover = db.execute("SELECT * FROM covers WHERE work_id=?", (record["id"],)).fetchone()
        record["cover_url"] = f"/api/covers/{record['id']}?v={cover['fingerprint'][:20]}" if cover and cover["path"] and Path(cover["path"]).is_file() else None
        if include_assets:
            record["assets"] = assets
        return record

    def require_work(db, work_id):
        work = db.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
        if work is None:
            raise HTTPException(404, "库存编号不存在")
        return work

    def host_capability(request: Request) -> bool:
        if request.headers.get("host", "").lower() not in {"localhost:8788", "127.0.0.1:8788"}:
            return False
        key_file = config.host_key_file or config.data_dir / "host.key"
        try:
            expected = key_file.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        supplied = request.headers.get("x-workbench-host-key", "")
        return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())

    def require_host_origin(request: Request):
        if not host_capability(request):
            raise HTTPException(403, "不支持打开，仅素材所在主机可用")
        origin = request.headers.get("origin", "")
        if origin not in {"http://localhost:8788", "http://127.0.0.1:8788"} or urlparse(origin).netloc.lower() != request.headers.get("host", "").lower():
            raise HTTPException(403, "目录打开仅允许素材主机网页的同源请求")

    def send_open_request(windows_path: str):
        if config.open_mode == "gateway":
            encoded = base64.urlsafe_b64encode(windows_path.encode("utf-8")).decode("ascii")
            return JSONResponse({"message": "已发送打开请求"}, headers={"X-Workbench-Open-Folder": encoded})
        try:
            subprocess.Popen(["/mnt/c/Windows/explorer.exe", windows_path], shell=False,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as error:
            raise HTTPException(502, f"无法发送资源管理器打开请求：{error.strerror or error}")
        return {"message": "已发送打开请求"}

    def validated_directory(directory: dict) -> str:
        if not directory["available"]:
            raise HTTPException(404, "目录不存在或当前不可访问")
        path = Path(directory["path"])
        root = next((root for root in config.roots if str(root.path) == directory["root_path"]), None)
        if not root:
            raise HTTPException(403, "目录不在配置的库存根目录中")
        try:
            relative = path.resolve().relative_to(root.path.resolve())
            if not relative.parts or path.is_symlink() or not path.is_dir():
                raise HTTPException(404, "目录不存在或当前不可访问")
            expected_windows = root.windows_directory(path)
            if directory["windows_path"] != expected_windows or not PureWindowsPath(expected_windows).is_absolute():
                raise HTTPException(403, "目录路径映射校验失败")
            # Reject a linked component, not just a symlink as final component.
            candidate = root.path
            for part in relative.parts:
                candidate = candidate / part
                if candidate.is_symlink():
                    raise HTTPException(403, "不允许打开符号链接目录")
        except (OSError, ValueError):
            raise HTTPException(403, "目录不在配置的库存根目录中")
        return expected_windows

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "script-workbench"}

    @app.get("/api/works")
    def works(q: str = Query(default="", max_length=300), status: str = "all", issues_only: bool = False,
              tag_id: int | None = Query(default=None, gt=0), untagged_only: str = Query(default="false", pattern="^(true|false)$"),
              page: int = Query(default=1, ge=1), page_size: int = Query(default=24, ge=1, le=100)):
        if status not in {"all", "pending", "published"}:
            raise HTTPException(422, "发布状态应为 pending、published 或 all")
        clauses, values = [], []
        if q.strip():
            # Literal search: % and _ in user text are not wildcard operators.
            term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(script_id LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM work_tags wt JOIN tags t ON t.id=wt.tag_id WHERE wt.work_id=works.id AND t.name_key LIKE ? ESCAPE '\\'))")
            values.extend([f"%{term}%", f"%{term}%", f"%{term.casefold()}%"])
        if status != "all":
            clauses.append("status=?")
            values.append(status)
        if issues_only:
            clauses.append("EXISTS(SELECT 1 FROM issues WHERE issues.work_id=works.id)")
        if tag_id is not None:
            clauses.append("EXISTS(SELECT 1 FROM work_tags WHERE work_tags.work_id=works.id AND work_tags.tag_id=?)")
            values.append(tag_id)
        if untagged_only == "true":
            if tag_id is not None:
                raise HTTPException(422, "按标签筛选不能同时只看无标签库存")
            clauses.append("NOT EXISTS(SELECT 1 FROM work_tags wt JOIN tags t ON t.id=wt.tag_id WHERE wt.work_id=works.id AND (t.category!='axis_type' OR wt.source!='scan'))")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with store.connection() as db:
            if tag_id is not None and not db.execute("SELECT 1 FROM tags WHERE id=?", (tag_id,)).fetchone():
                raise HTTPException(404, "筛选标签不存在")
            total = db.execute("SELECT count(*) FROM works" + where, values).fetchone()[0]
            rows = db.execute("SELECT * FROM works" + where + " ORDER BY script_id DESC LIMIT ? OFFSET ?", [*values, page_size, (page - 1) * page_size]).fetchall()
            stats = {"total": db.execute("SELECT count(*) FROM works").fetchone()[0],
                     "pending": db.execute("SELECT count(*) FROM works WHERE status='pending'").fetchone()[0],
                     "published": db.execute("SELECT count(*) FROM works WHERE status='published'").fetchone()[0],
                     "issues": db.execute("SELECT count(DISTINCT work_id) FROM issues WHERE work_id IS NOT NULL").fetchone()[0]}
            return {"items": [details(db, row) for row in rows], "total": total, "page": page,
                    "page_size": page_size, "stats": stats, "last_scan": last_scan(db)}

    @app.get("/api/works/{work_id}")
    def work_detail(work_id: int):
        with store.connection() as db:
            return details(db, require_work(db, work_id), include_assets=True)

    @app.get("/api/tags")
    def tag_catalog():
        return tags.catalog()

    @app.get('/api/works/{work_id}/links')
    def work_links(work_id: int):
        try:
            with store.connection() as db:
                return links.state(db, work_id)
        except WorkLinksError as error:
            raise HTTPException(error.status_code, str(error))

    @app.patch('/api/works/{work_id}/links')
    def update_work_links(work_id: int, options: WorkLinksEdit):
        try:
            return links.update(work_id, options.links, options.expected_revision)
        except WorkLinksError as error:
            raise HTTPException(error.status_code, str(error))

    @app.post("/api/tags", status_code=201)
    def create_tag(options: TagCreate):
        try:
            return tags.create(options.model_dump(exclude_unset=True))
        except TagError as error:
            raise HTTPException(error.status_code, str(error))

    @app.patch("/api/tags/{tag_id}")
    def edit_tag(tag_id: int, options: TagEdit):
        try:
            return tags.update(tag_id, options.model_dump(exclude_unset=True))
        except TagError as error:
            raise HTTPException(error.status_code, str(error))

    @app.get("/api/works/{work_id}/tags")
    def work_tags(work_id: int):
        try:
            with store.connection() as db:
                return tags.work_state(db, work_id)
        except TagError as error:
            raise HTTPException(error.status_code, str(error))

    @app.put("/api/works/{work_id}/tags")
    def replace_work_tags(work_id: int, options: WorkTagsEdit):
        try:
            return tags.replace_work(work_id, options.tag_ids, options.expected_revision)
        except TagError as error:
            raise HTTPException(error.status_code, str(error))

    @app.patch("/api/works/{work_id}")
    def edit_work(work_id: int, change: WorkEdit):
        changes = change.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise HTTPException(422, "作品字段不能为 null")
        if "status" in changes and changes["status"] not in {"pending", "published"}:
            raise HTTPException(422, "发布状态应为 pending 或 published")
        if "title" in changes:
            changes["title"] = changes["title"].strip()
            if not changes["title"]:
                raise HTTPException(422, "标题不能为空")
        with store.connection() as db:
            current = require_work(db, work_id)
            if changes:
                manual_fields = set(json.loads(current["manual_fields"])) | set(changes)
                assignments = ",".join(f"{field}=?" for field in changes)
                db.execute(f"UPDATE works SET {assignments},manual_fields=?,updated_at=? WHERE id=?", [*changes.values(), json.dumps(sorted(manual_fields)), now(), work_id])
            return details(db, require_work(db, work_id), include_assets=True)

    @app.get("/api/issues")
    def issues():
        with store.connection() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM issues ORDER BY CASE type WHEN 'duplicate_identifier' THEN 0 WHEN 'root_unavailable' THEN 1 WHEN 'history_unmatched' THEN 3 ELSE 2 END,script_id,id")]
        for row in rows:
            row["paths"] = json.loads(row["paths"])
        return {"items": rows, "total": len(rows)}

    @app.get("/api/jobs")
    def jobs():
        with store.connection() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM jobs ORDER BY id DESC LIMIT 50")]
        return {"items": [store.job(job_id) for job_id in ids]}

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: int):
        job = store.job(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在")
        return job

    @app.post("/api/scans", status_code=202)
    def request_scan(options: ScanOptions | None = None):
        try:
            return worker.enqueue("manual", bool(options and options.refresh_covers))
        except ScanRootsError as error:
            raise HTTPException(error.status_code, str(error))

    @app.get("/api/settings")
    def settings():
        with store.connection() as db:
            imported = db.execute("SELECT value FROM settings WHERE key='history_imported'").fetchone()
            selection = scan_roots.state(db)
        return {"roots": [{"path": str(root.path), "windows_path": root.windows_path, "label": root.label,
                            "available": root.path.is_dir(), "enabled": str(root.path) in selection["enabled_paths"]} for root in config.roots],
                "scan_roots_revision": selection["revision"],
                "scan_mode": "manual", "scan_interval_seconds": 0,
                "database": "SQLite", "history_import": json.loads(imported[0]) if imported else None,
                "folder_identifier_rule": "S029、S025_001；以编号文件夹为准", "host_access_url": "http://localhost:8788/"}

    @app.put("/api/settings/scan-roots")
    def update_scan_roots(options: ScanRootsEdit):
        try:
            scan_roots.update(options.enabled_paths, options.expected_revision)
        except ScanRootsError as error:
            raise HTTPException(error.status_code, str(error))
        return settings()

    @app.get("/api/capabilities")
    def capabilities(request: Request):
        supported = host_capability(request)
        return {"can_open_folder": supported,
                "reason": "在素材主机资源管理器中打开" if supported else "不支持打开，仅素材所在主机可用"}

    @app.post("/api/works/{work_id}/open-folder")
    def open_folder(work_id: int, request: Request, options: OpenFolder | None = None):
        require_host_origin(request)
        with store.connection() as db:
            require_work(db, work_id)
            if options and options.directory_id is not None:
                rows = db.execute("SELECT * FROM directories WHERE work_id=? AND id=?", (work_id, options.directory_id)).fetchall()
            else:
                rows = db.execute("SELECT * FROM directories WHERE work_id=? AND available=1", (work_id,)).fetchall()
            if not rows:
                raise HTTPException(404, "目录不存在或当前不可访问")
            if len(rows) > 1:
                raise HTTPException(409, "编号有多个关联目录，请先选择具体路径")
            windows_path = validated_directory(dict(rows[0]))
        return send_open_request(windows_path)

    @app.get("/api/works/{work_id}/preview")
    def preview_state(work_id: int):
        try:
            return worker.previews.state(work_id)
        except PreviewError as error:
            raise HTTPException(error.status_code, str(error))

    @app.get('/api/works/{work_id}/preview-matching')
    def preview_matching(work_id: int, video_asset_id: int | None = Query(default=None, gt=0)):
        try:
            return worker.previews.matching.state(work_id, video_asset_id)
        except PreviewError as error:
            raise HTTPException(error.status_code, str(error))

    @app.put('/api/works/{work_id}/preview-matching')
    def save_preview_matching(work_id: int, options: PreviewMatchingEdit):
        try:
            return worker.previews.matching.save(work_id, options.video_asset_id, options.script_asset_ids, options.expected_revision)
        except PreviewError as error:
            raise HTTPException(error.status_code, str(error))

    @app.post('/api/works/{work_id}/rematch', status_code=202)
    def rematch_files(work_id: int):
        try:
            return worker.enqueue_rematch(work_id)
        except PreviewError as error:
            raise HTTPException(error.status_code, str(error))

    @app.post("/api/works/{work_id}/preview", status_code=202)
    def generate_preview(work_id: int, options: PreviewOptions | None = None):
        try:
            worker.previews.work(work_id)
            # An active request is idempotent even if the user re-clicks after files change.
            active = worker.active_preview(work_id)
            if active:
                if options and options.video_asset_id is not None:
                    worker.previews.assert_video_asset(work_id, options.video_asset_id)
                    if options.video_asset_id != active["inputs"]["video_asset_id"]:
                        raise PreviewError("该作品已有其他视频正在生成，请等待当前任务完成", 409)
                return active
            inputs = worker.previews.select_inputs(work_id, options.video_asset_id if options else None)
            inputs['force'] = bool(options and options.force)
            return worker.enqueue_preview(inputs)
        except PreviewError as error:
            raise HTTPException(error.status_code, str(error))

    @app.get("/api/works/{work_id}/preview/files/{filename}")
    def preview_media(work_id: int, filename: str):
        try:
            path = worker.previews.media_path(work_id, filename)
        except PreviewError as error:
            raise HTTPException(error.status_code, str(error))
        kind = MEDIA_FILES[filename][0]
        return FileResponse(path, media_type={'video': 'video/webm', 'gif': 'image/gif', 'heatmap': 'image/png'}[kind], filename=filename,
                            content_disposition_type="inline", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/api/works/{work_id}/preview/open-folder")
    def open_preview_folder(work_id: int, request: Request):
        require_host_origin(request)
        try:
            work = worker.previews.work(work_id)
            output = worker.previews.output_directory(work["script_id"])
            if not output.is_dir():
                raise PreviewError("预览输出目录尚不存在，请先生成预览", 404)
            configured_root = next((root for root in config.roots if output.resolve().is_relative_to(root.path.resolve())), None)
            if not configured_root:
                raise PreviewError("预览目录无法映射到素材主机的库存根目录", 403)
            windows_path = configured_root.windows_directory(output)
            if not PureWindowsPath(windows_path).is_absolute():
                raise PreviewError("预览目录的 Windows 路径映射无效", 403)
        except PreviewError as error:
            raise HTTPException(error.status_code, str(error))
        return send_open_request(windows_path)

    @app.get("/api/covers/{work_id}")
    def cover_file(work_id: int):
        with store.connection() as db:
            cover = db.execute("SELECT * FROM covers WHERE work_id=?", (work_id,)).fetchone()
        if not cover or not cover["path"]:
            raise HTTPException(404, "封面暂不可用")
        path = Path(cover["path"])
        try:
            path.resolve().relative_to((config.data_dir / "covers").resolve())
            if path.is_symlink() or not path.is_file():
                raise HTTPException(404, "封面暂不可用")
        except ValueError:
            raise HTTPException(403, "无效封面路径")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600", "ETag": f'"{cover["fingerprint"]}"'})

    @app.get("/{frontend_path:path}", include_in_schema=False)
    def frontend(frontend_path: str):
        if frontend_path.startswith("api/") or frontend_path == "api":
            raise HTTPException(404, "接口不存在")
        dist = config.frontend_dist.resolve()
        candidate = dist / frontend_path
        try:
            candidate.resolve().relative_to(dist)
        except ValueError:
            raise HTTPException(404)
        if candidate.is_file():
            return FileResponse(candidate)
        index = dist / "index.html"
        if not index.is_file():
            raise HTTPException(503, "前端尚未构建，请先执行生产构建")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
