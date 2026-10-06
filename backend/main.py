from __future__ import annotations

import base64
from contextlib import asynccontextmanager
import hmac
import hashlib
import json
from pathlib import Path, PureWindowsPath
from urllib.parse import urlparse
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictInt, model_validator

from .config import Config
from .jobs import JobWorker
from .scanner import enrich_metadata
from .previews import MEDIA_FILES, PreviewError
from .store import Store, now, inventory_revision
from .inventory_cache import InventoryCache, InventoryCacheError
from .tags import TagService, TagError
from .durations import duration_fields
from .scan_roots import ScanRoots, ScanRootsError
from .work_links import WorkLinks, WorkLinksError, PUBLICATION_FIELDS
from .release_dates import RELEASE_DATE_FIELDS, validate_release_date, release_today
from .profile import register_profile_routes
from .timezone import register_timezone_routes
from .work_media import register_media_routes
from .language import register_language_routes
from .es_posts import register_es_post_routes
from .release_calendar import register_release_calendar_routes
from .work_directory import current_directory, directory_status
from .production import reset_production
from .scan_candidates import ScanCandidates, ScanCandidatesError
from .mcp_server import create_workbench_mcp, mcp_http_app
from .mcp_auth import MCPAuth, register_mcp_auth_routes


ReleaseDate = Annotated[str | None, BeforeValidator(validate_release_date)]


class WorkEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=500)
    status: str | None = None
    es_published: bool | None = Field(default=None, strict=True)
    patreon_published: bool | None = Field(default=None, strict=True)
    es_published_date: ReleaseDate = None
    patreon_published_date: ReleaseDate = None
    notes: str | None = Field(default=None, max_length=20000)
    expected_revision: str | None = Field(default=None, strict=True, pattern=r'^[a-f0-9]{64}$')


def work_data_revision(work) -> str:
    """Fingerprint raw persisted fields, independent of derived UI fields."""
    return hashlib.sha256(json.dumps(dict(work), sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


class OpenFolder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directory_id: int | None = None
    asset_id: Annotated[StrictInt, Field(gt=0)] | None = None


class ProductionConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: StrictInt = Field(ge=0)


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


class ScanRootDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(strict=True, min_length=1, max_length=2000)
    label: str = Field(default='', strict=True, max_length=120)
    enabled: bool = Field(strict=True)
    identification: Literal['numbered', 'folder'] = 'folder'


class CandidateResolution(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['create', 'associate']
    expected_revision: StrictInt = Field(ge=1)
    work_id: StrictInt | None = Field(default=None, gt=0)
    expected_work_revision: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def association_target(self):
        if self.action == 'associate' and (self.work_id is None or self.expected_work_revision is None):
            raise ValueError('关联已有作品需要作品 ID 和关联版本')
        if self.action == 'create' and (self.work_id is not None or self.expected_work_revision is not None):
            raise ValueError('创建新作品不能指定已有作品')
        return self


class ScanRootsEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled_paths: list[Annotated[str, Field(strict=True, min_length=1)]] | None = None
    roots: list[ScanRootDefinition] | None = Field(default=None, max_length=100)
    expected_revision: int = Field(strict=True, ge=0)

    @model_validator(mode='after')
    def exactly_one_mode(self):
        if len(self.model_fields_set & {'roots', 'enabled_paths'}) != 1 or (self.roots is None and self.enabled_paths is None):
            raise ValueError('必须且只能指定 roots 或 enabled_paths')
        return self


class TagCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["author", "video_type", "axis_type", "release_type", "tier", "duration", "custom"]
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
    links: dict[Literal['patreon', 'video', 'script', 'es'], Annotated[str, Field(strict=True, max_length=4000)]] = Field(default_factory=dict, max_length=4)
    expected_revision: int = Field(strict=True, ge=0)
    es_published_date: ReleaseDate = None
    patreon_published_date: ReleaseDate = None
    es_published: bool | None = Field(default=None, strict=True)
    patreon_published: bool | None = Field(default=None, strict=True)
    es_planned_date: ReleaseDate = None
    patreon_planned_date: ReleaseDate = None
    expected_publication_revision: str | None = Field(default=None, strict=True, pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def has_changes(self):
        if not self.links and not self.model_fields_set.intersection((*RELEASE_DATE_FIELDS, *PUBLICATION_FIELDS)):
            raise ValueError('请至少填写一种链接或发布日期')
        if any(getattr(self, field) is None for field in ('es_published', 'patreon_published') if field in self.model_fields_set):
            raise ValueError('平台发布状态不能为 null')
        return self


def create_app(config: Config | None = None, start_worker: bool = True) -> FastAPI:
    config = config or Config.from_environment()
    store = Store(config.data_dir)
    worker = JobWorker(store, config)
    tags = TagService(store)
    scan_roots = ScanRoots(store, config)
    with store.connection() as db:
        scan_roots.state(db)
    links = WorkLinks(store)
    inventory_cache = InventoryCache()

    @asynccontextmanager
    async def lifespan(app):
        async with app.state.mcp.session_manager.run():
            if start_worker:
                worker.start()
            try:
                yield
            finally:
                if start_worker:
                    worker.stop()

    app = FastAPI(title="Funscript 工作台", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.worker = worker
    app.state.config = config
    app.state.tags = tags
    app.state.inventory_cache = inventory_cache
    register_profile_routes(app, store)
    register_timezone_routes(app, store)
    register_language_routes(app, store)
    register_es_post_routes(app, store, config, worker.previews)
    register_release_calendar_routes(app, store)

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

    def details(db, work, include_assets=False, *, check_filesystem=True) -> dict:
        record = dict(work)
        record['data_revision'] = work_data_revision(work)
        record['es_published'] = bool(record['es_published'])
        record['patreon_published'] = bool(record['patreon_published'])
        record['production_required'] = bool(record['production_required'])
        record['preview_stale'] = bool(record['preview_stale'])
        record['status'] = 'published' if record['es_published'] and record['patreon_published'] else 'pending'
        metadata = enrich_metadata(json.loads(record["metadata"]))
        record.pop("manual_fields", None)
        record["metadata"] = metadata
        link_state = links.state(db, record["id"])
        record["links"] = link_state["links"]
        record["links_revision"] = link_state["links_revision"]
        record.update({field: link_state[field] for field in ('es_planned_date', 'patreon_planned_date', 'publication_revision')})
        tag_state = tags.work_state(db, record["id"])
        record["tags"] = tag_state["tags"]
        record["tags_revision"] = tag_state["tags_revision"]
        record.update(duration_fields(db, record['id']))
        selected_type = next((tag["name"] for tag in record["tags"] if tag["category"] == "video_type"), None)
        manual_tags = db.execute("SELECT manual_edited FROM work_tag_state WHERE work_id=?", (record["id"],)).fetchone()
        record["video_type"] = selected_type if selected_type is not None else "" if manual_tags and manual_tags[0] else metadata.get("video_type") or metadata.get("Video Type") or ""
        record["axis_type"] = next((tag["name"] for tag in record["tags"] if tag["category"] == "axis_type"), "")
        latest = last_scan(db) or {}
        roots = scan_roots.roots(db)
        registered_roots = {str(root.path) for root in roots}
        unavailable_roots = latest.get("unavailable_roots", [])
        directory = current_directory(db, record['id'])
        record['association_status'] = directory_status(db, record['id'], roots,
            check_filesystem=check_filesystem, unavailable_roots=unavailable_roots)
        directories = [dict(directory)] if directory is not None else []
        for directory in directories:
            directory["available"] = bool(directory["available"]) and record['association_status'] == 'available' and directory["root_path"] not in unavailable_roots and directory['root_path'] in registered_roots
        record["directories"] = directories
        assets = [dict(row) for row in db.execute("SELECT * FROM assets WHERE directory_id=? ORDER BY kind,relative_path COLLATE NOCASE", (directory['id'],))] if directory is not None else []
        active_ids = {directory["id"] for directory in directories if directory["available"]}
        # Unreachable root keeps prior inventory counts; vanished directory is historical only.
        counted_ids = active_ids | {directory["id"] for directory in directories if record['association_status'] == 'unavailable' or directory["root_path"] in unavailable_roots or directory['root_path'] not in registered_roots}
        record["video_count"] = sum(asset["kind"] == "video" and asset["directory_id"] in counted_ids for asset in assets)
        record["script_count"] = sum(asset["kind"] == "script" and asset["directory_id"] in counted_ids for asset in assets)
        record["issues"] = [{"type": row["type"], "message": row["message"]} for row in db.execute("SELECT type,message FROM issues WHERE work_id=? ORDER BY id", (record["id"],))]
        cover = db.execute("SELECT * FROM covers WHERE work_id=?", (record["id"],)).fetchone()
        record['cover_mode'] = cover['mode'] if cover else 'automatic'
        record['cover_revision'] = cover['revision'] if cover else 0
        record["cover_url"] = f"/api/covers/{record['id']}?v={cover['fingerprint'][:20]}" if cover and cover["path"] and (not check_filesystem or Path(cover["path"]).is_file()) else None
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

    mcp_auth = MCPAuth(store)
    register_mcp_auth_routes(app, mcp_auth, host_capability)
    app.state.media = register_media_routes(app, store, config, host_capability, require_host_origin)

    def send_open_request(windows_path: str, windows_root: str):
        encoded = base64.urlsafe_b64encode(windows_path.encode("utf-8")).decode("ascii")
        encoded_root = base64.urlsafe_b64encode(windows_root.encode("utf-8")).decode("ascii")
        return JSONResponse({"message": "已发送打开请求"}, headers={"X-Workbench-Open-Folder": encoded, "X-Workbench-Folder-Root": encoded_root})

    def validated_directory(directory: dict, db) -> tuple[str, str]:
        if not directory["available"]:
            raise HTTPException(404, "目录不存在或当前不可访问")
        path = Path(directory["path"])
        root = next((root for root in scan_roots.roots(db) if str(root.path) == directory["root_path"]), None)
        if not root:
            raise HTTPException(403, "目录不在配置的库存根目录中")
        try:
            relative = path.resolve().relative_to(root.path.resolve())
            from .scan_roots import no_link_components
            no_link_components(root.path)
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
        except (OSError, ValueError, ScanRootsError):
            raise HTTPException(403, "目录不在配置的库存根目录中")
        return expected_windows, root.windows_path

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "script-workbench"}

    def script_groups(db):
        # Classification uses persisted scan results, never probes material storage.
        registered = {str(root.path) for root in scan_roots.roots(db)}
        unavailable = set((last_scan(db) or {}).get('unavailable_roots', []))
        script_directories = {row[0] for row in db.execute("SELECT DISTINCT directory_id FROM assets WHERE kind='script'")}
        required = {row[0] for row in db.execute('SELECT id FROM works WHERE production_required=1')}
        roots = scan_roots.roots(db)
        ready, to_make = set(), set()
        for row in db.execute('SELECT DISTINCT work_id FROM directories').fetchall():
            directory = current_directory(db, row[0])
            if directory is None:
                continue
            association = directory_status(db, row[0], roots,
                check_filesystem=False, unavailable_roots=unavailable)
            if association in {'missing', 'conflict', 'unlinked'}:
                continue
            if directory['available'] or association == 'unavailable' or directory['root_path'] in unavailable or directory['root_path'] not in registered:
                (ready if directory['id'] in script_directories and row[0] not in required else to_make).add(row[0])
        return ready, to_make

    @app.get("/api/works")
    def works(q: str = Query(default="", max_length=300), status: str = "all", issues_only: bool = False,
              tag_id: int | None = Query(default=None, gt=0), untagged_only: str = Query(default="false", pattern="^(true|false)$"),
              tag_ids: str = Query(default="", max_length=1200),
              sort_platform: Literal['es', 'patreon'] = 'es', sort_direction: Literal['asc', 'desc'] = 'desc',
              page: int = Query(default=1, ge=1), page_size: int = Query(default=24, ge=1, le=100),
              snapshot_id: str | None = Query(default=None, max_length=100)):
        status_filters = {'pending': '(es_published=0 OR patreon_published=0)',
                          'published': '(es_published=1 AND patreon_published=1)',
                          'es_published': 'es_published=1', 'patreon_published': 'patreon_published=1'}
        if status != 'all' and status != 'to_make' and status not in status_filters:
            raise HTTPException(422, "库存分类应为 to_make、pending、published、es_published、patreon_published 或 all")
        clauses, values = [], []
        selected_tags = set()
        if tag_ids:
            parts = tag_ids.split(',')
            if len(parts) > 100 or any(not part.isdecimal() or int(part) <= 0 for part in parts):
                raise HTTPException(422, '筛选标签应为最多 100 个正整数 ID，以逗号分隔')
            selected_tags.update(int(part) for part in parts)
        if tag_id is not None:
            selected_tags.add(tag_id)
        if q.strip():
            # Literal search: % and _ in user text are not wildcard operators.
            term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(script_id LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM work_tags wt JOIN tags t ON t.id=wt.tag_id WHERE wt.work_id=works.id AND t.name_key LIKE ? ESCAPE '\\'))")
            values.extend([f"%{term}%", f"%{term}%", f"%{term.casefold()}%"])
        if status in status_filters:
            clauses.append(status_filters[status])
        if issues_only:
            clauses.append("EXISTS(SELECT 1 FROM issues WHERE issues.work_id=works.id)")
        if untagged_only == "true":
            if selected_tags:
                raise HTTPException(422, "按标签筛选不能同时只看无标签库存")
            clauses.append("NOT EXISTS(SELECT 1 FROM work_tags wt JOIN tags t ON t.id=wt.tag_id WHERE wt.work_id=works.id AND t.category!='duration' AND (t.category!='axis_type' OR wt.source!='scan'))")
        query_key = (q.strip(), status, issues_only, tuple(sorted(selected_tags)),
                     untagged_only == 'true', sort_platform, sort_direction)
        if snapshot_id is not None:
            try:
                return inventory_cache.get(snapshot_id, query_key).page(page, page_size)
            except InventoryCacheError as error:
                raise HTTPException(error.status_code, str(error))

        def build(db):
            if selected_tags:
                selected = sorted(selected_tags)
                catalog = db.execute(f"SELECT id,category FROM tags WHERE id IN ({','.join('?' for _ in selected)})", selected).fetchall()
                if len(catalog) != len(selected):
                    raise HTTPException(404, '筛选标签不存在')
                categories = {}
                for tag in catalog:
                    categories.setdefault(tag['category'], []).append(tag['id'])
                for ids in categories.values():
                    clauses.append(f"EXISTS(SELECT 1 FROM work_tags WHERE work_tags.work_id=works.id AND work_tags.tag_id IN ({','.join('?' for _ in ids)}))")
                    values.extend(ids)
            ready_ids, to_make_ids = script_groups(db)
            if status in {'pending', 'to_make'}:
                ids = sorted(ready_ids if status == 'pending' else to_make_ids)
                clauses.append(f"id IN ({','.join('?' for _ in ids)})" if ids else '0')
                values.extend(ids)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            date_field = f'{sort_platform}_published_date'
            order = f' ORDER BY ({date_field} IS NULL OR {date_field}=\'\') ASC,{date_field} {sort_direction.upper()},script_id DESC,id DESC'
            rows = db.execute("SELECT * FROM works" + where + order, values).fetchall()
            stats = {"total": db.execute("SELECT count(*) FROM works").fetchone()[0],
                     "pending": sum(row['id'] in ready_ids for row in db.execute("SELECT id FROM works WHERE es_published=0 OR patreon_published=0")),
                     "to_make": len(to_make_ids),
                     "published": db.execute("SELECT count(*) FROM works WHERE es_published=1 AND patreon_published=1").fetchone()[0],
                     "es_published": db.execute("SELECT count(*) FROM works WHERE es_published=1").fetchone()[0],
                     "patreon_published": db.execute("SELECT count(*) FROM works WHERE patreon_published=1").fetchone()[0],
                     "issues": db.execute("SELECT count(DISTINCT work_id) FROM issues WHERE work_id IS NOT NULL").fetchone()[0]}
            return {"items": [details(db, row, check_filesystem=False) for row in rows],
                    "stats": stats, "last_scan": last_scan(db)}

        with store.connection() as db:
            db.execute('BEGIN')
            revision = inventory_revision(db)
            return inventory_cache.get_or_create(query_key, revision, lambda: build(db)).page(page, page_size)

    @app.get('/api/inventory-revision')
    def current_inventory_revision():
        with store.connection() as db:
            db.execute('BEGIN')
            revision = inventory_revision(db)
            scan_active = db.execute("SELECT 1 FROM jobs WHERE type='scan' "
                                     "AND status IN ('queued','pending','running') LIMIT 1").fetchone() is not None
            return {'inventory_revision': revision, 'scan_active': scan_active}

    @app.get("/api/works/{work_id}")
    def work_detail(work_id: int):
        with store.connection() as db:
            return details(db, require_work(db, work_id), include_assets=True)

    @app.get('/api/scan-candidates')
    def scan_candidates():
        return ScanCandidates(store, config).catalog()

    @app.post('/api/scan-candidates/{candidate_id}/resolve')
    def resolve_candidate(candidate_id: int, options: CandidateResolution):
        try:
            result = ScanCandidates(store, config).resolve(candidate_id, options.action, options.expected_revision,
                options.work_id, options.expected_work_revision)
            with store.connection() as db:
                work = details(db, require_work(db, result['work_id']), include_assets=True)
            return {'candidate_id': result['candidate_id'], 'action': result['action'], 'work': work}
        except ScanCandidatesError as error:
            raise HTTPException(error.status_code, str(error))

    @app.post('/api/works/{work_id}/production/reset')
    def return_to_production(work_id: int, options: ProductionConfirmation):
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            reset_production(db, work_id, options.expected_revision)
            return details(db, require_work(db, work_id), include_assets=True)

    @app.post('/api/works/{work_id}/production/confirm')
    def confirm_production(work_id: int, options: ProductionConfirmation):
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            work = require_work(db, work_id)
            if work['production_revision'] != options.expected_revision:
                raise HTTPException(409, '制作确认状态已变化，请刷新作品后重试')
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') AND (type='scan' OR json_extract(inputs,'$.work_id')=?)", (work_id,)).fetchone():
                raise HTTPException(409, '素材任务正在运行，请等待完成后确认制作')
            directory = current_directory(db, work_id)
            if directory is None:
                raise HTTPException(409, '尚无唯一的作品目录，请处理冲突后重新匹配文件')
            scripts = db.execute("SELECT a.*,d.path,d.root_path,d.available FROM assets a JOIN directories d ON d.id=a.directory_id WHERE d.id=? AND a.kind='script'", (directory['id'],)).fetchall()
            available = False
            for script in scripts:
                try:
                    worker.previews.valid_source(dict(script))
                    available = True
                    break
                except PreviewError:
                    continue
            if not available:
                raise HTTPException(422, '没有可用脚本，添加脚本后请先扫描或重新匹配文件')
            if work['production_required']:
                db.execute('UPDATE works SET production_required=0,production_confirmed_at=?,production_revision=production_revision+1 WHERE id=?', (now(), work_id))
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
            dates = {field: getattr(options, field) for field in RELEASE_DATE_FIELDS if field in options.model_fields_set}
            publication = {field: getattr(options, field) for field in PUBLICATION_FIELDS if field in options.model_fields_set}
            return links.update(work_id, options.links, options.expected_revision, dates, publication, options.expected_publication_revision)
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
        expected_revision = changes.pop('expected_revision', None)
        if any(value is None for field, value in changes.items() if field not in RELEASE_DATE_FIELDS):
            raise HTTPException(422, "作品字段不能为 null")
        if "status" in changes and changes["status"] not in {"pending", "published"}:
            raise HTTPException(422, "发布状态应为 pending 或 published")
        if 'status' in changes and {'es_published', 'patreon_published'} & changes.keys():
            raise HTTPException(422, '不能同时填写旧发布状态与平台发布状态')
        if 'status' in changes:
            changes['es_published'] = changes['patreon_published'] = changes['status'] == 'published'
        if "title" in changes:
            changes["title"] = changes["title"].strip()
            if not changes["title"]:
                raise HTTPException(422, "标题不能为空")
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = require_work(db, work_id)
            if expected_revision is not None and expected_revision != work_data_revision(current):
                raise HTTPException(409, '作品资料已被其他操作修改，请重新读取后再保存')
            if changes:
                for platform in ('es', 'patreon'):
                    date_field = f'{platform}_published_date'
                    if changes.get(f'{platform}_published') is True and not current[f'{platform}_published'] and not current[date_field] and date_field not in changes:
                        changes[date_field] = release_today(store)
                manual_fields = set(json.loads(current["manual_fields"])) | set(changes)
                if {'es_published', 'patreon_published'} & changes.keys():
                    es = changes.get('es_published', bool(current['es_published']))
                    patreon = changes.get('patreon_published', bool(current['patreon_published']))
                    changes['status'] = 'published' if es and patreon else 'pending'
                assignments = ",".join(f"{field}=?" for field in changes)
                db.execute(f"UPDATE works SET {assignments},manual_fields=?,updated_at=? WHERE id=?", [*changes.values(), json.dumps(sorted(manual_fields)), now(), work_id])
                if set(changes).intersection(RELEASE_DATE_FIELDS):
                    db.execute('INSERT INTO work_links(work_id,revision) VALUES(?,1) ON CONFLICT(work_id) DO UPDATE SET revision=work_links.revision+1', (work_id,))
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
        return {"roots": [{**root, "available": Path(root['path']).is_dir()} for root in selection['roots']],
                "scan_roots_revision": selection["revision"],
                "scan_mode": "manual", "scan_interval_seconds": 0,
                "database": "SQLite", "history_import": json.loads(imported[0]) if imported else None,
                "folder_identifier_rule": "按编号识别：S029、S025_001；按文件夹识别：每个直属文件夹一个作品，编号可选", "host_access_url": "http://localhost:8788/"}

    @app.put("/api/settings/scan-roots")
    def update_scan_roots(options: ScanRootsEdit):
        try:
            if options.roots is not None:
                scan_roots.replace([root.model_dump(exclude_unset=True) for root in options.roots], options.expected_revision)
            else:
                scan_roots.update(options.enabled_paths, options.expected_revision)
        except ScanRootsError as error:
            raise HTTPException(error.status_code, str(error))
        return settings()

    @app.get("/api/capabilities")
    def capabilities(request: Request):
        supported = host_capability(request)
        return {"can_open_folder": supported, "can_play_video": supported, "can_edit_cover": supported,
                "reason": "在素材主机资源管理器中打开" if supported else "不支持打开，仅素材所在主机可用"}

    @app.post("/api/works/{work_id}/open-folder")
    def open_folder(work_id: int, request: Request, options: OpenFolder | None = None):
        require_host_origin(request)
        with store.connection() as db:
            require_work(db, work_id)
            directory = current_directory(db, work_id)
            if directory is None and db.execute('SELECT count(*) FROM directories WHERE work_id=? AND available=1', (work_id,)).fetchone()[0] > 1:
                raise HTTPException(409, "编号存在目录冲突，请处理冲突后重新匹配文件")
            if directory is None or (options and options.directory_id is not None and options.directory_id != directory['id']):
                raise HTTPException(404, "目录不存在或当前不可访问")
            windows_path, windows_root = validated_directory(dict(directory), db)
            if options and options.asset_id is not None:
                asset = db.execute('SELECT * FROM assets WHERE id=? AND directory_id=?', (options.asset_id, directory['id'])).fetchone()
                if asset is None:
                    raise HTTPException(404, '素材不属于当前作品目录')
                candidate = Path(directory['path']) / asset['relative_path']
                try:
                    from .scan_roots import no_link_components
                    no_link_components(candidate)
                    candidate.resolve().relative_to(Path(directory['path']).resolve())
                    if not candidate.is_file():
                        raise HTTPException(404, '素材当前不可访问')
                    root = next(root for root in scan_roots.roots(db) if str(root.path) == directory['root_path'])
                    windows_path = root.windows_directory(candidate.parent)
                except (ValueError, OSError, ScanRootsError, StopIteration):
                    raise HTTPException(403, '素材路径不在当前安全目录中') from None
        return send_open_request(windows_path, windows_root)

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
    def preview_media(work_id: int, filename: str, inline: bool = False):
        try:
            path = worker.previews.media_path(work_id, filename)
        except PreviewError as error:
            raise HTTPException(error.status_code, str(error))
        kind = MEDIA_FILES[filename][0]
        return FileResponse(path, media_type={'video': 'video/webm', 'gif': 'image/gif', 'heatmap': 'image/png'}[kind], filename=filename,
                            content_disposition_type="inline" if inline else "attachment", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/api/works/{work_id}/preview/open-folder")
    def open_preview_folder(work_id: int, request: Request):
        require_host_origin(request)
        try:
            work = worker.previews.work(work_id)
            output = worker.previews.output_directory(worker.previews.preview_key(work))
            if not output.is_dir():
                raise PreviewError("预览输出目录尚不存在，请先生成预览", 404)
            windows_root = worker.previews.windows_path(config.preview_output_root)
            windows_path = str(PureWindowsPath(windows_root) / output.name)
            if not PureWindowsPath(windows_path).is_absolute():
                raise PreviewError("预览目录的 Windows 路径映射无效", 403)
        except PreviewError as error:
            raise HTTPException(error.status_code, str(error))
        return send_open_request(windows_path, windows_root)

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

    app.state.mcp = create_workbench_mcp(app, {
        'work': WorkEdit, 'tag_create': TagCreate, 'tag_edit': TagEdit,
        'work_tags': WorkTagsEdit, 'work_links': WorkLinksEdit,
    })
    app.add_route("/mcp", mcp_http_app(app.state.mcp, mcp_auth), methods=["GET", "POST", "DELETE"])

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
