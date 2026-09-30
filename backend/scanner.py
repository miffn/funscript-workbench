from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import re

from .config import Config, Root, normalize_id, parse_folder
from .store import Store, now

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

    def scan(self, roots: tuple[Root, ...] | None = None) -> dict:
        roots = self.config.roots if roots is None else roots
        self.import_history()
        observed: list[dict] = []
        reachable = []
        unavailable = []
        scan_issues = []
        unnumbered_count = 0
        for root in roots:
            try:
                children = sorted(root.path.iterdir(), key=lambda item: item.name.lower())
                # Listing must succeed before any existing directories may be marked absent.
            except OSError as error:
                unavailable.append(str(root.path))
                scan_issues.append(("root_unavailable", f"库存根目录暂时不可访问：{error.strerror or error}", None, None, [root.windows_path]))
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
                if not parsed:
                    if root.label.lower() == "workspace":
                        unnumbered.append(root.windows_directory(directory))
                    continue
                script_id, title = parsed
                assets = []
                errors = []
                try:
                    directory.resolve().relative_to(root.path.resolve())
                    for parent, folders, filenames in os.walk(directory, followlinks=False, onerror=errors.append):
                        folders[:] = [name for name in folders if not (Path(parent) / name).is_symlink() and not self.excluded_output(Path(parent) / name)]
                        for filename in sorted(filenames):
                            file = Path(parent) / filename
                            if file.is_symlink():
                                continue
                            try:
                                file.resolve().relative_to(directory.resolve())
                                stat = file.stat()
                                if not file.is_file():
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
            affected = {row[0] for row in db.execute("SELECT work_id,root_path FROM directories") if row[1] in selected_paths}
            # Keep diagnostics belonging to unchecked roots. Legacy issues store
            # Windows (and, for unsafe links, Linux) paths rather than a root ID.
            def selected_issue_path(value):
                return any(PureWindowsPath(value).is_relative_to(PureWindowsPath(root.windows_path))
                           or Path(value).is_relative_to(root.path) for root in roots)

            for issue in db.execute("SELECT * FROM issues WHERE type!='cover_failed'").fetchall():
                paths = json.loads(issue["paths"])
                if issue["type"] == "history_unmatched" or (
                    issue["type"] == "duplicate_identifier" and issue["work_id"] in affected
                ) or (paths and all(selected_issue_path(path) for path in paths)):
                    db.execute("DELETE FROM issues WHERE id=?", (issue["id"],))
            for kind, message, script_id, work_id, paths in scan_issues:
                self.store.issue(db, kind, message, script_id, work_id, paths)
            for root_path in reachable:
                db.execute("UPDATE directories SET available=0 WHERE root_path=?", (root_path,))
            for item in observed:
                script_id = item["script_id"]
                history = db.execute("SELECT * FROM history WHERE script_id=?", (script_id,)).fetchone()
                metadata = json.loads(history["metadata"]) if history else {}
                work = db.execute("SELECT * FROM works WHERE script_id=?", (script_id,)).fetchone()
                if work is None:
                    cursor = db.execute("INSERT INTO works(script_id,title,status,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                                        (script_id, metadata.get("title") or item["title"],
                                         history["status"] if history else "pending", json.dumps(metadata, ensure_ascii=False), timestamp, timestamp))
                    work_id = cursor.lastrowid
                else:
                    work_id = work["id"]
                    # Never derive manual title or status from a subsequent filesystem scan.
                    if history and not history["applied"]:
                        manual = json.loads(work["manual_fields"])
                        merged = {**metadata, **json.loads(work["metadata"])}
                        db.execute("UPDATE works SET title=?,status=?,metadata=?,updated_at=? WHERE id=?",
                                   (work["title"] if "title" in manual else metadata.get("title") or work["title"],
                                    work["status"] if "status" in manual else history["status"],
                                    json.dumps(merged, ensure_ascii=False), timestamp, work_id))
                affected.add(work_id)
                if history:
                    db.execute("UPDATE history SET applied=1 WHERE script_id=?", (script_id,))
                previous = db.execute("SELECT d.*,w.script_id FROM directories d JOIN works w ON w.id=d.work_id WHERE d.path=? AND d.work_id!=?",
                                      (item["path"], work_id)).fetchall()
                for old in previous:
                    self.store.issue(db, "identifier_changed", f"路径曾关联 {old['script_id']}，现在识别为 {script_id}；请核对", script_id, work_id, [item["windows_path"]])
                db.execute("INSERT INTO directories(work_id,path,windows_path,root_path,name,available,last_seen) VALUES(?,?,?,?,?,1,?) ON CONFLICT(work_id,path) DO UPDATE SET available=1,last_seen=excluded.last_seen,windows_path=excluded.windows_path",
                           (work_id, item["path"], item["windows_path"], item["root_path"], item["name"], timestamp))
                directory_id = db.execute("SELECT id FROM directories WHERE work_id=? AND path=?", (work_id, item["path"])).fetchone()[0]
                if item["errors"]:
                    self.store.issue(db, "directory_unreadable", "目录中部分素材无法读取，保留此前素材记录；下次扫描重试", script_id, work_id, [item["windows_path"]])
                else:
                    current_paths = {asset["relative_path"] for asset in item["assets"]}
                    old_assets = db.execute("SELECT id,relative_path FROM assets WHERE directory_id=?", (directory_id,)).fetchall()
                    for old in old_assets:
                        if old["relative_path"] not in current_paths:
                            db.execute("DELETE FROM assets WHERE id=?", (old["id"],))
                for asset in item["assets"]:
                    db.execute("INSERT INTO assets(directory_id,name,relative_path,kind,axis,size,mtime_ns) VALUES(?,?,?,?,?,?,?) ON CONFLICT(directory_id,relative_path) DO UPDATE SET name=excluded.name,kind=excluded.kind,axis=excluded.axis,size=excluded.size,mtime_ns=excluded.mtime_ns",
                               (directory_id, asset["name"], asset["relative_path"], asset["kind"], asset["axis"], asset["size"], asset["mtime_ns"]))
            for work in db.execute("SELECT id,script_id FROM works").fetchall():
                if work["id"] not in affected:
                    continue
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
            unmatched = db.execute("SELECT h.script_id FROM history h LEFT JOIN works w ON w.script_id=h.script_id WHERE w.id IS NULL").fetchall()
            for row in unmatched:
                self.store.issue(db, "history_unmatched", "历史资料尚未匹配到完整编号文件夹", row["script_id"])
            previous_scan = db.execute("SELECT value FROM settings WHERE key='last_scan'").fetchone()
            previous_unavailable = json.loads(previous_scan[0]).get("unavailable_roots", []) if previous_scan else []
            retained_unavailable = [path for path in previous_unavailable if path not in selected_paths]
            summary = {"works": db.execute("SELECT count(*) FROM works").fetchone()[0],
                       "directories": len(observed), "assets": sum(len(item["assets"]) for item in observed),
                       "unnumbered": unnumbered_count, "unavailable_roots": retained_unavailable + unavailable,
                       "scanned_roots": [str(root.path) for root in roots]}
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('last_scan',?)", (json.dumps({"at": timestamp, **summary}, ensure_ascii=False),))
        return summary

    def excluded_output(self, path: Path) -> bool:
        try:
            return path.absolute().is_relative_to(self.config.preview_output_root.absolute()) or path.resolve().is_relative_to(self.config.preview_output_root.resolve())
        except OSError:
            return False
