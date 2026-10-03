from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from .config import Config
from .store import Store, now
from .scan_roots import ScanRoots, ScanRootsError, no_link_components
from .work_directory import current_directory


class CoverGenerator:
    def __init__(self, store: Store, config: Config):
        self.store = store
        self.config = config
        self.cache_dir = config.data_dir / "covers"
        self.cache_dir.mkdir(exist_ok=True)

    def source(self, work_id: int, root_paths: list[str] | None = None) -> Path | None:
        with self.store.connection() as db:
            registered_roots = {str(root.path): root for root in ScanRoots(self.store, self.config).roots(db)}
            directory = current_directory(db, work_id)
            candidates = db.execute("SELECT a.*,d.path,d.root_path FROM assets a JOIN directories d ON d.id=a.directory_id WHERE d.id=? AND d.available=1 AND a.kind='video'", (directory['id'],)).fetchall() if directory is not None else []
        videos = []
        for row in candidates:
            if row['root_path'] not in registered_roots:
                continue
            if root_paths is not None and row["root_path"] not in root_paths:
                continue
            relative = Path(row["relative_path"])
            if any(word in str(relative).lower() for word in ("preview", "review", "teaser", "预览")):
                continue
            source = Path(row["path"]) / relative
            try:
                no_link_components(source)
                source.resolve().relative_to(Path(row["root_path"]).resolve())
                if source.resolve().is_relative_to(self.config.preview_output_root.resolve()):
                    continue
                if source.is_symlink() or not source.is_file():
                    continue
            except (ValueError, OSError, ScanRootsError):
                continue
            videos.append((len(relative.parts), str(source).lower(), source))
        return min(videos, key=lambda item: item[:2])[2] if videos else None

    def existing_cover(self, video: Path) -> Path | None:
        # Reuse only images that are explicitly identifiable as cover/poster files.
        try:
            for image in sorted(video.parent.iterdir(), key=lambda item: item.name.lower()):
                if image.is_symlink() or image.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                    continue
                if image.stem.lower() in {"cover", "poster", "thumbnail", "封面", video.stem.lower()}:
                    return image
        except OSError:
            pass
        return None

    def generate(self, work_id: int, force: bool = False, root_paths: list[str] | None = None) -> str:
        video = self.source(work_id, root_paths)
        if not video:
            with self.store.connection() as db:
                latest = db.execute("SELECT value FROM settings WHERE key='last_scan'").fetchone()
                unavailable = json.loads(latest[0]).get("unavailable_roots", []) if latest else []
                directory = current_directory(db, work_id)
                if directory is None:
                    return 'skipped'
                directories = [directory]
                registered = {str(root.path) for root in ScanRoots(self.store, self.config).roots(db)}
                if any(directory['root_path'] not in registered for directory in directories):
                    return 'skipped'
                directories = [directory for directory in directories if directory['available']]
                if root_paths is not None and any(directory['root_path'] not in root_paths for directory in directories):
                    # An unscanned source is not evidence that its cached cover vanished.
                    return "skipped"
                if any(directory['root_path'] in unavailable for directory in directories):
                    # An unmounted drive is not evidence that a cached cover should disappear.
                    return "skipped"
                db.execute("DELETE FROM covers WHERE work_id=?", (work_id,))
                db.execute("DELETE FROM issues WHERE work_id=? AND type='cover_failed'", (work_id,))
            return "skipped"
        source = self.existing_cover(video) or video
        try:
            stat = source.stat()
        except OSError:
            return "skipped"
        fingerprint = hashlib.sha256(f"cover-v1:{source}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
        with self.store.connection() as db:
            old = db.execute("SELECT * FROM covers WHERE work_id=?", (work_id,)).fetchone()
        if old and old["fingerprint"] == fingerprint and not force:
            if old["error"] or (old["path"] and Path(old["path"]).is_file()):
                return "cached"
        final = self.cache_dir / f"{work_id}-{fingerprint[:20]}.jpg"
        temporary = self.cache_dir / f".{work_id}-pending.jpg"
        error_message = None
        try:
            seek = []
            if source == video:
                try:
                    probe = subprocess.run([self.config.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source)],
                                           capture_output=True, text=True, timeout=15, check=True)
                    duration = float(json.loads(probe.stdout)["format"]["duration"])
                    seek = ["-ss", str(max(0, min(10, duration * 0.1)))]
                except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError):
                    seek = ["-ss", "0"]
            subprocess.run([self.config.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *seek,
                            "-i", str(source), "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "3", "-threads", "1", str(temporary)],
                           capture_output=True, timeout=45, check=True)
            if not temporary.is_file() or not temporary.stat().st_size:
                raise RuntimeError("未提取到有效视频帧")
            temporary.replace(final)
        except subprocess.TimeoutExpired:
            error_message = "封面提取超时，源文件变化或手动刷新封面后重试"
        except FileNotFoundError:
            error_message = "未找到 FFmpeg，安装后可手动刷新封面"
        except subprocess.CalledProcessError:
            error_message = "视频无法解码或没有有效画面，源文件变化或手动刷新封面后重试"
        except (OSError, RuntimeError) as error:
            error_message = f"封面生成失败：{error}"
        finally:
            temporary.unlink(missing_ok=True)
        preserve_unselected = bool(error_message and old and old["path"] and Path(old["path"]).is_file()
                                   and root_paths is not None and any(
                                       str(root.path) not in root_paths
                                       and Path(old["source_path"]).is_relative_to(root.path)
                                       for root in ScanRoots(self.store, self.config).roots()))
        with self.store.connection() as db:
            db.execute("DELETE FROM issues WHERE work_id=? AND type='cover_failed'", (work_id,))
            if not preserve_unselected:
                db.execute("INSERT INTO covers(work_id,fingerprint,path,error,source_path,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(work_id) DO UPDATE SET fingerprint=excluded.fingerprint,path=excluded.path,error=excluded.error,source_path=excluded.source_path,updated_at=excluded.updated_at",
                           (work_id, fingerprint, None if error_message else str(final), error_message, str(source), now()))
            if error_message:
                script_id = db.execute("SELECT script_id FROM works WHERE id=?", (work_id,)).fetchone()[0]
                self.store.issue(db, "cover_failed", error_message, script_id, work_id)
        # Delete only old cache files created for this inventory record.
        if old and old["path"] and old["path"] != str(final) and not preserve_unselected:
            old_path = Path(old["path"])
            if old_path.parent == self.cache_dir and old_path.name.startswith(f"{work_id}-"):
                old_path.unlink(missing_ok=True)
        return "failed" if error_message else "generated"
