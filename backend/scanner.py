from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path, PureWindowsPath
import re
import stat as stat_module

from .config import Config, Root, normalize_id, parse_folder
from .store import Store, now
from .tags import sync_axis_tag
from .production import require_production_confirmation

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".wmv", ".ts", ".mpg", ".mpeg"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
HISTORY_FIELDS = {
    "title": ("Release Title", "Video Title", "title", "标题"),
    "video_type": ("Video Type", "video_type", "视频类型"),
    "axis_type": ("Axis Type", "axis_type", "轴类型"),
    "length": ("Length", "Duration", "时长"),
    "es_url": ("ES Link", "ES URL", "ES Post URL"),
    "patreon_url": ("Patreon post ink", "Patreon post link", "Patreon Link", "Patreon Post URL"),
    "video_url": ("Video Link", "Video URL"),
    "creator_url": ("Support Creator URL", "Creator URL"),
    "planned_date": ("Planned Date", "Planned Release Date"),
    "actual_date": ("Actual Release Date", "Actual Date"),
    "historical_notes": ("Notes", "备注"),
}


def asset_kind(path: Path) -> str:
    extension = path.suffix.lower()
    if extension == ".funscript":
        return "script"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in {".ofs", ".ofsp"}:
        return "project"
    return "other"


def script_axis(path: Path) -> str | None:
    if path.suffix.lower() != ".funscript":
        return None
    match = re.search(r"(?:[._ -])(pitch|roll|twist|sway|surge|heave|suck|vib|valve)(?:[._ -]|$)", path.stem, re.I)
    return match[1].lower() if match else "stroke"


def rows_of(document: object) -> list[dict]:
    if isinstance(document, list):
        rows = document
    elif isinstance(document, dict):
        rows = document.get("rows", document.get("records", document.get("items", document.get("tableData", []))))
    else:
        return []
    if not isinstance(rows, list):
        return []
    if rows and isinstance(rows[0], list):
        headers = rows[0]
        return [dict(zip(headers, row)) for row in rows[1:] if isinstance(row, list)]
    return [row for row in rows if isinstance(row, dict)]


def pick(row: dict, *names: str):
    values = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        value = values.get(name.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def enrich_metadata(metadata: dict) -> dict:
    """Expose aliases for older stored imports without re-reading or resyncing a source."""
    result = dict(metadata)
    for key, names in HISTORY_FIELDS.items():
        if not result.get(key):
            value = pick(metadata, *names)
            if value:
                result[key] = value
    return result


class Scanner:
    def __init__(self, store: Store, config: Config):
        self.store = store
        self.config = config

    def import_history(self):
        with self.store.connection() as db:
            if db.execute("SELECT 1 FROM settings WHERE key='history_imported'").fetchone():
                return
        records: dict[str, dict] = {}
        sources = []
        for filename in ("master-pipeline.json", "monthly-release-plan.json"):
            path = self.config.data_dir / "import" / filename
            if not path.exists():
                continue
            document = json.loads(path.read_text(encoding="utf-8-sig"))
            sources.append(filename)
            for row in rows_of(document):
                script_id = normalize_id(pick(row, "Script ID", "script_id", "编号", "ID"))
                if not script_id:
                    continue
                record = records.setdefault(script_id, {"sources": [], "source_rows": {}, "status": "pending"})
                record["sources"].append(filename)
                record["source_rows"][filename] = row
                # Keep all nonempty original fields; monthly-release-plan has priority.
                record.update({key: value for key, value in row.items() if key and value not in ("", None)})
                for key, names in HISTORY_FIELDS.items():
                    value = pick(row, *names)
                    if value:
                        record[key] = value
                status = pick(row, "Status", "Stauts", "发布状态").lower()
                if status in {"published", "已发布"}:
                    record["status"] = "published"
        with self.store.connection() as db:
            for script_id, record in records.items():
                db.execute("INSERT OR IGNORE INTO history(script_id,metadata,status) VALUES(?,?,?)",
                           (script_id, json.dumps(record, ensure_ascii=False), record["status"]))
            # If snapshots aren't available yet, permit importing them on a later scan.
            if sources:
                db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('history_imported',?)",
                           (json.dumps({"at": now(), "sources": sources, "records": len(records)}, ensure_ascii=False),))

    def scan(self, roots: tuple[Root, ...] | None = None, target_id: str | None = None, target_work_id: int | None = None) -> dict:
        if roots is None:
            from .scan_roots import ScanRoots
            roots = ScanRoots(self.store, self.config).roots()
        scoped = target_id is not None or target_work_id is not None
        from .work_directory import current_directory, association_signature, invalidate_association
        with self.store.connection() as db:
            target = db.execute('SELECT * FROM works WHERE id=?', (target_work_id,)).fetchone() if target_work_id is not None else db.execute('SELECT * FROM works WHERE script_id=?', (target_id,)).fetchone() if target_id else None
            if target:
                target_work_id = target['id']
                target_id = target['script_id']
            bound_paths = {}
            for work_row in db.execute('SELECT id FROM works').fetchall():
                directory = current_directory(db, work_row['id'])
                if directory:
                    bound_paths[directory['path']] = work_row['id']
        if not scoped:
            self.import_history()
        observed: list[dict] = []
        reachable = []
        unavailable = []
        scan_issues = []
        unnumbered_count = 0
        for root in roots:
            try:
                from .scan_roots import no_link_components, ScanRootsError
                no_link_components(root.path)
                children = sorted(root.path.iterdir(), key=lambda item: item.name.lower())
                # Listing must succeed before any existing directories may be marked absent.
            except (OSError, ScanRootsError) as error:
                unavailable.append(str(root.path))
                scan_issues.append(("root_unavailable", f"库存根目录暂时不可访问：{getattr(error, 'strerror', None) or error}", None, None, [root.windows_path]))
                continue
            reachable.append(str(root.path))
            unnumbered = []
            for directory in children:
                if self.excluded_output(directory):
                    continue
                if directory.name.startswith("."):
                    continue
                if directory.is_symlink():
                    scan_issues.append(("unsafe_link", "跳过符号链接，避免访问库存目录之外的文件", None, None, [str(directory)]))
                    continue
                parsed = parse_folder(directory.name) if directory.is_dir() else None
                path_owner = existing_path_owner(directory, bound_paths)
                if not directory.is_dir() or (not parsed and root.identification != 'folder' and path_owner is None):
                    if not scoped and root.label.lower() == "workspace":
                        unnumbered.append(root.windows_directory(directory))
                    continue
                script_id, title = parsed if parsed else (None, directory.name)
                if scoped and not ((script_id is not None and script_id == target_id) or path_owner == target_work_id):
                    continue
                assets = []
                errors = []
                try:
                    directory_resolved = directory.resolve()
                    directory_resolved.relative_to(root.path.resolve())
                    for parent, folders, filenames in os.walk(directory, followlinks=False, onerror=errors.append):
                        folders[:] = [name for name in folders if not name.startswith('.') and not (Path(parent) / name).is_symlink() and not self.excluded_output(Path(parent) / name)]
                        for filename in sorted(filenames):
                            file = Path(parent) / filename
                            if file.is_symlink():
                                continue
                            try:
                                file.resolve().relative_to(directory_resolved)
                                stat = file.stat()
                                if not stat_module.S_ISREG(stat.st_mode):
                                    continue
                                assets.append({"name": file.name, "relative_path": str(file.relative_to(directory)),
                                               "kind": asset_kind(file), "axis": script_axis(file),
                                               "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
                            except (OSError, ValueError) as error:
                                errors.append(error)
                except (OSError, ValueError) as error:
                    errors.append(error)
                observed.append({"script_id": script_id, "title": title, "path": str(directory),
                                 "windows_path": root.windows_directory(directory), "root_path": str(root.path),
                                 "name": directory.name, "assets": assets, "errors": errors})
            unnumbered_count += len(unnumbered)
            if unnumbered:
                scan_issues.append(("unnumbered_material", f"workspace 中有 {len(unnumbered)} 项未编号素材，不计入完成库存", None, None, unnumbered))
        timestamp = now()
        with self.store.connection() as db:
            selected_paths = {str(root.path) for root in roots}
            before = {row['id']: association_signature(db, row['id']) for row in db.execute('SELECT id FROM works').fetchall()}
            affected = {row[0] for row in db.execute("SELECT work_id,root_path FROM directories") if row[1] in selected_paths}
            target_work = db.execute('SELECT id FROM works WHERE id=?', (target_work_id,)).fetchone() if target_work_id else None
            if scoped:
                affected = {target_work_id} if target_work else set()
            # Keep diagnostics belonging to unchecked roots. Legacy issues store
            # Windows (and, for unsafe links, Linux) paths rather than a root ID.
            def selected_issue_path(value):
                return any(PureWindowsPath(value).is_relative_to(PureWindowsPath(root.windows_path))
                           or Path(value).is_relative_to(root.path) for root in roots)

            for issue in db.execute("SELECT * FROM issues WHERE type!='cover_failed'").fetchall():
                paths = json.loads(issue["paths"])
                if scoped:
                    if issue["work_id"] in affected:
                        db.execute("DELETE FROM issues WHERE id=?", (issue["id"],))
                    continue
                if issue["type"] == "history_unmatched" or (
                    issue["type"] == "duplicate_identifier" and issue["work_id"] in affected
                ) or (paths and all(selected_issue_path(path) for path in paths)):
                    db.execute("DELETE FROM issues WHERE id=?", (issue["id"],))
            for kind, message, script_id, work_id, paths in scan_issues:
                if not scoped:
                    self.store.issue(db, kind, message, script_id, work_id, paths)
            for root_path in reachable:
                if not scoped:
                    db.execute("UPDATE directories SET available=0 WHERE root_path=?", (root_path,))
                elif target_work:
                    db.execute("UPDATE directories SET available=0 WHERE root_path=? AND work_id=?", (root_path, target_work[0]))
            # Mark absent only after a successful root listing. Apply recognized paths/codes
            # before deciding whether a brand-new folder could be a renamed missing work.
            unresolved = []
            for item in observed:
                code_work = db.execute('SELECT * FROM works WHERE script_id=?', (item['script_id'],)).fetchone() if item['script_id'] else None
                path_owner = existing_path_owner(Path(item['path']), bound_paths)
                path_work = db.execute('SELECT * FROM works WHERE id=?', (path_owner,)).fetchone() if path_owner else None
                if code_work is None and path_work is None:
                    unresolved.append(item)
                    continue
                if code_work is not None and path_work is not None and code_work['id'] != path_work['id']:
                    self.store.issue(db, 'identifier_conflict', '目录已关联其他作品，不能按编号覆盖关联', item['script_id'], path_work['id'], [item['windows_path']])
                    continue
                work = code_work or path_work
                if work['script_id'] != item['script_id']:
                    invalidate_association(db, work['id'])
                    db.execute('UPDATE works SET script_id=? WHERE id=?', (item['script_id'], work['id']))
                    if item['script_id']:
                        db.execute('UPDATE history SET applied=1 WHERE script_id=?', (item['script_id'],))
                elif not scoped and item['script_id']:
                    history = db.execute('SELECT * FROM history WHERE script_id=? AND applied=0', (item['script_id'],)).fetchone()
                    if history:
                        from .work_links import validate_link, WorkLinksError
                        metadata = json.loads(history['metadata'])
                        manual = set(json.loads(work['manual_fields']))
                        historical_published = history['status'] == 'published'
                        try:
                            historical_es = historical_published or bool(validate_link(pick(metadata, 'es_url', *HISTORY_FIELDS['es_url'])))
                        except WorkLinksError:
                            historical_es = historical_published
                        es = work['es_published'] if manual & {'status', 'es_published'} else historical_es
                        patreon = work['patreon_published'] if manual & {'status', 'patreon_published'} else historical_published
                        db.execute('UPDATE works SET title=?,metadata=?,es_published=?,patreon_published=?,status=?,updated_at=? WHERE id=?',
                                   (work['title'] if 'title' in manual else metadata.get('title') or work['title'],
                                    json.dumps({**metadata, **json.loads(work['metadata'])}, ensure_ascii=False), es, patreon,
                                    'published' if es and patreon else 'pending', timestamp, work['id']))
                        db.execute('UPDATE history SET applied=1 WHERE script_id=?', (item['script_id'],))
                self.bind_item(db, item, work['id'], timestamp)
                affected.add(work['id'])
            confirmed_missing = []
            for row in db.execute('SELECT id,script_id,association_missing FROM works').fetchall():
                if row['id'] not in affected:
                    continue
                directory = current_directory(db, row['id'])
                active = db.execute('SELECT 1 FROM directories WHERE work_id=? AND available=1', (row['id'],)).fetchone()
                if directory and directory['root_path'] in reachable:
                    missing = not active and not Path(directory['path']).is_dir()
                    db.execute('UPDATE works SET association_missing=? WHERE id=?', (int(missing), row['id']))
                elif active:
                    db.execute('UPDATE works SET association_missing=0 WHERE id=?', (row['id'],))
                if db.execute('SELECT association_missing FROM works WHERE id=?', (row['id'],)).fetchone()[0]:
                    if directory and directory['root_path'] in reachable and not active:
                        confirmed_missing.append(dict(row))
            for item in unresolved:
                candidates_for = [row['id'] for row in confirmed_missing if item['script_id'] is None or row['script_id'] is None]
                pending = db.execute("SELECT * FROM scan_candidates WHERE path=? AND status='pending'", (item['path'],)).fetchone()
                if not scoped and (candidates_for or pending):
                    self.save_candidate(db, item, candidates_for or json.loads(pending['missing_work_ids']), timestamp)
                    continue
                if scoped:
                    continue
                script_id = item["script_id"]
                history = db.execute("SELECT * FROM history WHERE script_id=?", (script_id,)).fetchone()
                if history and history['applied']:
                    history = None
                metadata = json.loads(history["metadata"]) if history else {}
                work = db.execute("SELECT * FROM works WHERE script_id=?", (script_id,)).fetchone()
                historical_published = bool(history and history['status'] == 'published')
                # An ES post link establishes ES publication independently of Patreon.
                from .work_links import validate_link, WorkLinksError
                try:
                    historical_es = historical_published or bool(validate_link(pick(metadata, 'es_url', *HISTORY_FIELDS['es_url'])))
                except WorkLinksError:
                    historical_es = historical_published
                if work is None:
                    cursor = db.execute("INSERT INTO works(script_id,title,status,es_published,patreon_published,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                        (script_id, metadata.get("title") or item["title"],
                                         'published' if historical_es and historical_published else 'pending', historical_es, historical_published,
                                         json.dumps(metadata, ensure_ascii=False), timestamp, timestamp))
                    work_id = cursor.lastrowid
                else:
                    work_id = work["id"]
                    # Never derive manual title or status from a subsequent filesystem scan.
                    if not scoped and history and not history["applied"]:
                        manual = json.loads(work["manual_fields"])
                        merged = {**metadata, **json.loads(work["metadata"])}
                        es = work['es_published'] if 'status' in manual or 'es_published' in manual else historical_es
                        patreon = work['patreon_published'] if 'status' in manual or 'patreon_published' in manual else historical_published
                        db.execute("UPDATE works SET title=?,status=?,es_published=?,patreon_published=?,metadata=?,updated_at=? WHERE id=?",
                                   (work["title"] if "title" in manual else metadata.get("title") or work["title"],
                                    'published' if es and patreon else 'pending', es, patreon,
                                    json.dumps(merged, ensure_ascii=False), timestamp, work_id))
                affected.add(work_id)
                if history and not scoped:
                    db.execute("UPDATE history SET applied=1 WHERE script_id=?", (script_id,))
                previous = db.execute("SELECT d.*,w.script_id FROM directories d JOIN works w ON w.id=d.work_id WHERE d.path=? AND d.work_id!=?",
                                      (item["path"], work_id)).fetchall()
                for old in previous:
                    self.store.issue(db, "identifier_changed", f"路径曾关联 {old['script_id']}，现在识别为 {script_id}；请核对", script_id, work_id, [item["windows_path"]])
                self.bind_item(db, item, work_id, timestamp)
            # A uniquely observed full code establishes its current readable location.
            # Offline/removed roots contain cached history, not a second observed copy.
            by_code = {}
            for item in observed:
                if item['script_id'] is not None:
                    by_code.setdefault(item['script_id'], []).append(item)
            from .scan_roots import ScanRoots
            registered_paths = {str(root.path) for root in ScanRoots(self.store, self.config).roots(db)}
            for code, items in by_code.items():
                if len(items) != 1 or items[0]['errors']:
                    continue
                row = db.execute('SELECT id FROM works WHERE script_id=?', (code,)).fetchone()
                if row is None or not db.execute('SELECT 1 FROM directories WHERE work_id=? AND path=? AND available=1', (row['id'], items[0]['path'])).fetchone():
                    continue  # A pending recovery candidate does not establish a binding.
                for directory in db.execute('SELECT * FROM directories WHERE work_id=? AND available=1 AND path!=?', (row['id'], items[0]['path'])).fetchall():
                    inactive_root = directory['root_path'] in unavailable or directory['root_path'] not in registered_paths
                    if not inactive_root and directory['root_path'] not in selected_paths:
                        # A scoped rematch may only scan the new root. Probe cached roots
                        # for access without inferring anything about uncoded works.
                        try:
                            no_link_components(Path(directory['root_path']))
                            with os.scandir(directory['root_path']) as children:
                                next(children, None)
                        except (OSError, ScanRootsError):
                            inactive_root = True
                    if inactive_root:
                        db.execute('UPDATE directories SET available=0 WHERE id=?', (directory['id'],))
            for work in db.execute("SELECT id,script_id FROM works").fetchall():
                if work["id"] not in affected:
                    continue
                after = association_signature(db, work['id'])
                if before.get(work['id']) != after:
                    if work['id'] in before:
                        invalidate_association(db, work['id'])
                    db.execute('UPDATE works SET association_revision=association_revision+1 WHERE id=?', (work['id'],))
                sync_axis_tag(db, work["id"])
                require_production_confirmation(db, work['id'])
                directories = db.execute("SELECT * FROM directories WHERE work_id=?", (work["id"],)).fetchall()
                active = [directory for directory in directories if directory["available"]]
                verified_active = [directory for directory in active if directory["root_path"] in reachable]
                if len(active) > 1:
                    db.execute("DELETE FROM issues WHERE work_id=? AND type='duplicate_identifier'", (work["id"],))
                    self.store.issue(db, "duplicate_identifier", "同一编号对应多个文件夹，请处理编号冲突", work["script_id"], work["id"], [directory["windows_path"] for directory in active])
                missing = [directory for directory in directories if not directory["available"] and directory["root_path"] in reachable]
                if missing:
                    # A moved directory is harmless once another directory with the same ID is observed.
                    if not active:
                        self.store.issue(db, "directory_missing", "原关联文件夹已不在可访问的库存根目录中，保留发布状态和历史关联", work["script_id"], work["id"], [directory["windows_path"] for directory in missing])
                if verified_active:
                    kinds = {row[0] for row in db.execute("SELECT a.kind FROM assets a JOIN directories d ON d.id=a.directory_id WHERE d.work_id=? AND d.available=1", (work["id"],))}
                    missing_kinds = [label for kind, label in (("video", "视频"), ("script", ".funscript 脚本")) if kind not in kinds]
                    if missing_kinds:
                        self.store.issue(db, "missing_assets", "缺少" + "、".join(missing_kinds), work["script_id"], work["id"], [directory["windows_path"] for directory in verified_active])
                for directory in active:
                    if directory["root_path"] in unavailable:
                        self.store.issue(db, "root_unavailable", "库存根目录暂时不可访问，当前展示此前扫描结果", work["script_id"], work["id"], [directory["windows_path"]])
            for candidate in db.execute("SELECT * FROM scan_candidates WHERE status='pending'").fetchall():
                if candidate['root_path'] not in reachable or scoped:
                    continue
                found = next((item for item in observed if item['path'] == candidate['path']), None)
                if found is None and candidate['available']:
                    db.execute('UPDATE scan_candidates SET available=0,revision=revision+1,updated_at=? WHERE id=?', (timestamp, candidate['id']))
            unmatched = db.execute("SELECT h.script_id FROM history h LEFT JOIN works w ON w.script_id=h.script_id WHERE w.id IS NULL AND h.applied=0").fetchall()
            for row in unmatched if not scoped else []:
                self.store.issue(db, "history_unmatched", "历史资料尚未匹配到完整编号文件夹", row["script_id"])
            previous_scan = db.execute("SELECT value FROM settings WHERE key='last_scan'").fetchone()
            previous_unavailable = json.loads(previous_scan[0]).get("unavailable_roots", []) if previous_scan else []
            retained_unavailable = [path for path in previous_unavailable if path not in selected_paths]
            summary = {"works": db.execute("SELECT count(*) FROM works").fetchone()[0],
                       "directories": len(observed), "assets": sum(len(item["assets"]) for item in observed),
                       "unnumbered": unnumbered_count, "unavailable_roots": retained_unavailable + unavailable,
                       "scanned_roots": [str(root.path) for root in roots]}
            summary['candidates'] = db.execute("SELECT count(*) FROM scan_candidates WHERE status='pending' AND available=1").fetchone()[0]
            if not scoped:
                db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('last_scan',?)", (json.dumps({"at": timestamp, **summary}, ensure_ascii=False),))
        from .durations import refresh_all_duration
        summary['durations'] = refresh_all_duration(self.store, self.config, sorted(affected))
        return summary

    def bind_item(self, db, item, work_id, timestamp):
        db.execute("INSERT INTO directories(work_id,path,windows_path,root_path,name,available,last_seen) VALUES(?,?,?,?,?,1,?) ON CONFLICT(work_id,path) DO UPDATE SET available=1,last_seen=excluded.last_seen,windows_path=excluded.windows_path,name=excluded.name",
                   (work_id, item['path'], item['windows_path'], item['root_path'], item['name'], timestamp))
        directory_id = db.execute('SELECT id FROM directories WHERE work_id=? AND path=?', (work_id, item['path'])).fetchone()[0]
        if not item['errors']:
            current_paths = {asset['relative_path'] for asset in item['assets']}
            for asset in db.execute('SELECT id,relative_path FROM assets WHERE directory_id=?', (directory_id,)).fetchall():
                if asset['relative_path'] not in current_paths:
                    db.execute('DELETE FROM assets WHERE id=?', (asset['id'],))
        else:
            self.store.issue(db, 'directory_unreadable', '目录中部分素材无法读取，保留此前素材记录；下次扫描重试', item['script_id'], work_id, [item['windows_path']])
        for asset in item['assets']:
            db.execute("INSERT INTO assets(directory_id,name,relative_path,kind,axis,size,mtime_ns) VALUES(?,?,?,?,?,?,?) ON CONFLICT(directory_id,relative_path) DO UPDATE SET name=excluded.name,kind=excluded.kind,axis=excluded.axis,size=excluded.size,mtime_ns=excluded.mtime_ns",
                       (directory_id, asset['name'], asset['relative_path'], asset['kind'], asset['axis'], asset['size'], asset['mtime_ns']))
        db.execute("UPDATE scan_candidates SET status='resolved',revision=revision+1,updated_at=? WHERE path=? AND status='pending'", (timestamp, item['path']))

    def save_candidate(self, db, item, work_ids, timestamp):
        fingerprint = item_fingerprint(item)
        previous = db.execute('SELECT * FROM scan_candidates WHERE path=?', (item['path'],)).fetchone()
        revision = previous['revision'] + int(previous['fingerprint'] != fingerprint or not previous['available'] or previous['status'] != 'pending' or json.loads(previous['missing_work_ids']) != work_ids) if previous else 1
        db.execute("INSERT INTO scan_candidates(path,root_path,windows_path,name,script_id,fingerprint,missing_work_ids,video_count,script_count,discovered_at,updated_at,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET root_path=excluded.root_path,windows_path=excluded.windows_path,name=excluded.name,script_id=excluded.script_id,fingerprint=excluded.fingerprint,missing_work_ids=excluded.missing_work_ids,video_count=excluded.video_count,script_count=excluded.script_count,updated_at=excluded.updated_at,revision=excluded.revision,available=1,status='pending'",
                   (item['path'], item['root_path'], item['windows_path'], item['name'], item['script_id'], fingerprint, json.dumps(work_ids),
                    sum(asset['kind'] == 'video' for asset in item['assets']), sum(asset['kind'] == 'script' for asset in item['assets']), timestamp, timestamp, revision))

    def excluded_output(self, path: Path) -> bool:
        try:
            return path.absolute().is_relative_to(self.config.preview_output_root.absolute()) or path.resolve().is_relative_to(self.config.preview_output_root.resolve())
        except OSError:
            return False


def item_fingerprint(item):
    return hashlib.sha256(json.dumps([item['path'], item['script_id'], sorted((asset['relative_path'], asset['size'], asset['mtime_ns']) for asset in item['assets'])], ensure_ascii=False).encode()).hexdigest()


def existing_path_owner(path, bound_paths):
    """Resolve live filesystem aliases only; never infer ownership of a missing path."""
    if str(path) in bound_paths:
        return bound_paths[str(path)]
    owners = set()
    spelling = str(path).casefold()
    from .scan_roots import no_link_components, ScanRootsError
    for original, owner in bound_paths.items():
        # Only case-only spellings can be live aliases. Other moves use the
        # full code or explicit recovery; do not stat every old path over SMB.
        if original.casefold() != spelling:
            continue
        try:
            original = Path(original)
            no_link_components(original)
            if original.is_dir() and path.is_dir() and original.samefile(path):
                owners.add(owner)
        except (OSError, ScanRootsError):
            continue
    return next(iter(owners)) if len(owners) == 1 else None
