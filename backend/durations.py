"""Read-only source duration probes and persistent automatic minute classifications."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path
import re
import subprocess

from .previews import PreviewError, PreviewService
from .store import now
from .work_directory import current_directory


def duration_fields(db, work_id: int) -> dict:
    row = db.execute('SELECT * FROM work_durations WHERE work_id=?', (work_id,)).fetchone()
    return {'duration_seconds': row['total_seconds'] if row else None,
            'duration_minutes': row['duration_minutes'] if row else None,
            'duration_status': row['status'] if row else 'unknown',
            'duration_error': row['error'] if row else None,
            'duration_last_known_seconds': row['last_good_seconds'] if row else None,
            'duration_last_known_minutes': row['last_good_minutes'] if row else None}


def round_minutes(total_seconds: Decimal | str | int) -> int:
    return int((Decimal(total_seconds) / Decimal(60)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


class DurationError(ValueError):
    pass


def is_source_video(relative_path: str) -> bool:
    """Exclude only known generated/temporary material; leave inventory records intact."""
    path = Path(relative_path)
    if any(part.casefold() in {'preview', '预览'} for part in path.parts[:-1]):
        return False
    if re.search(r'_preview_\d+-\d+\.webm$', path.name, re.I):
        return False
    if path.stem.casefold().endswith('.partial'):
        return False
    return True


class DurationService:
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.previews = PreviewService(store, config)

    def directories(self, db, work_id: int):
        from .scan_roots import ScanRoots, ScanRootsError, no_link_components
        roots = ScanRoots(self.store, self.config).roots(db)
        registered = {str(root.path) for root in roots}
        scan = db.execute("SELECT value FROM settings WHERE key='last_scan'").fetchone()
        unavailable = set(json.loads(scan[0]).get('unavailable_roots', [])) if scan else set()
        for root in roots:
            try:
                no_link_components(root.path)
                if not root.path.is_dir():
                    unavailable.add(str(root.path))
            except (OSError, ScanRootsError):
                unavailable.add(str(root.path))
        # Match inventory counting: a confirmed vanished historical directory in a
        # configured readable root is no longer a current source. An unreachable
        # or removed root is uncertain, so retain those assets and report staleness.
        directory = current_directory(db, work_id)
        rows = [directory] if directory is not None else []
        return [dict(row) for row in rows if row['available'] or row['root_path'] in unavailable or row['root_path'] not in registered]

    def assets(self, db, work_id: int):
        counted_ids = {directory['id'] for directory in self.directories(db, work_id)}
        rows = db.execute(
            "SELECT a.*,d.work_id,d.path,d.windows_path,d.root_path,d.available FROM assets a JOIN directories d ON d.id=a.directory_id WHERE d.work_id=? AND a.kind='video' ORDER BY a.id", (work_id,))
        return [dict(row) for row in rows if row['directory_id'] in counted_ids and is_source_video(row['relative_path'])]

    def signature(self, db, work_id: int):
        roots = db.execute("SELECT value FROM settings WHERE key='root_catalog'").fetchone()
        return json.dumps({'roots': roots[0] if roots else None, 'assets': self.assets(db, work_id)}, sort_keys=True)

    def probe(self, path: Path) -> Decimal:
        try:
            result = subprocess.run([self.config.ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                                     '-of', 'json', str(path)], capture_output=True, text=True, timeout=10, check=True)
            value = json.loads(result.stdout)['format']['duration']
            if isinstance(value, bool):
                raise ValueError
            seconds = Decimal(str(value))
            if not seconds.is_finite() or not Decimal(0) < seconds <= Decimal('1000000000000'):
                raise ValueError
            return seconds
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, InvalidOperation) as error:
            # ffprobe stderr may contain full private paths; expose only a clear local status.
            raise DurationError('无法读取视频时长，请检查文件是否完整、可访问以及 ffprobe 是否可用') from error

    def cached_duration(self, asset: dict) -> tuple[str, Decimal]:
        try:
            path = self.previews.valid_source(asset)
            resolved = str(path.resolve())
            before = path.stat()
        except (PreviewError, OSError) as error:
            raise DurationError('视频当前不可访问或不在已配置的安全素材目录中') from error
        with self.store.connection() as db:
            cached = db.execute('SELECT * FROM video_duration_cache WHERE path=? AND size=? AND mtime_ns=?',
                                (resolved, before.st_size, before.st_mtime_ns)).fetchone()
        if cached:
            try:
                seconds = Decimal(cached['seconds'])
                if not seconds.is_finite() or seconds <= 0:
                    raise ValueError
                return resolved, seconds
            except (InvalidOperation, ValueError):
                pass
        seconds = self.probe(path)  # Never hold SQLite's write lock while a subprocess runs.
        try:
            # Revalidate authorization and the actual file after a potentially slow probe.
            self.previews.valid_source(asset)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) != (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev):
                raise DurationError('读取期间视频发生变化，请重新扫描')
        except (PreviewError, OSError) as error:
            raise DurationError('读取期间视频目录发生变化，请重新扫描') from error
        with self.store.connection() as db:
            db.execute('INSERT INTO video_duration_cache(path,size,mtime_ns,seconds,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,seconds=excluded.seconds,updated_at=excluded.updated_at',
                       (resolved, before.st_size, before.st_mtime_ns, str(seconds), now()))
        return resolved, seconds

    def refresh(self, work_id: int) -> dict:
        # Ensure the existing catalog is initialized outside any write transaction.
        from .scan_roots import ScanRoots
        ScanRoots(self.store, self.config).roots()
        with self.store.connection() as db:
            work = db.execute('SELECT id,script_id FROM works WHERE id=?', (work_id,)).fetchone()
            if work is None:
                raise DurationError('库存编号不存在')
            script_id = work['script_id']
            assets = self.assets(db, work_id)
            initial_signature = self.signature(db, work_id)
            prior = db.execute('SELECT * FROM work_durations WHERE work_id=?', (work_id,)).fetchone()
            conflict = db.execute('SELECT count(*) FROM directories WHERE work_id=? AND available=1', (work_id,)).fetchone()[0] > 1
        active = [asset for asset in assets if asset['available']]
        successes, errors = {}, ['编号存在目录冲突，请处理冲突后重新匹配文件'] if conflict else []
        for asset in active:
            try:
                path, seconds = self.cached_duration(asset)
                successes[path] = seconds  # A canonical physical path is counted once per work.
            except DurationError as error:
                errors.append(f'{asset["name"]}：{error}')
        if len(active) < len(assets):
            errors.append('视频所在目录当前失联，保留此前成功读取的时长记录')
        # Scanned assets retained after a partial directory read cannot establish a complete sum.
        with self.store.connection() as db:
            counted_directories = self.directories(db, work_id)
            active_paths = {directory['path'] for directory in counted_directories} | {directory['windows_path'] for directory in counted_directories}
            unreadable = db.execute("SELECT paths FROM issues WHERE work_id=? AND type='directory_unreadable'", (work_id,)).fetchall()
            if any(not json.loads(issue[0]) or active_paths.intersection(json.loads(issue[0])) for issue in unreadable):
                errors.append('编号文件夹中有素材无法读取，暂不能确认完整视频总时长')
        last_seconds = prior['last_good_seconds'] if prior else None
        last_minutes = prior['last_good_minutes'] if prior else None
        seconds = minutes = None
        if errors:
            status = 'partial' if successes or last_seconds is None else 'stale'
        elif successes:
            total = sum(successes.values(), Decimal(0))
            seconds, minutes = float(total), round_minutes(total)
            last_seconds, last_minutes = seconds, minutes
            status = 'ready'
        else:
            status = 'stale' if last_seconds is not None else 'unknown'
            if status == 'stale':
                errors.append('当前未找到源视频，保留此前成功读取的时长记录')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.signature(db, work_id) != initial_signature:
                # Avoid publishing a result for an inventory that changed while ffprobe ran.
                seconds = minutes = None
                status = 'stale' if prior and prior['last_good_seconds'] is not None else 'partial'
                last_seconds = prior['last_good_seconds'] if prior else None
                last_minutes = prior['last_good_minutes'] if prior else None
                errors = ['读取期间库存或扫描目录配置发生变化，请重新扫描']
            error = '\n'.join(dict.fromkeys(errors)) or None
            db.execute('INSERT INTO work_durations(work_id,total_seconds,duration_minutes,status,last_good_seconds,last_good_minutes,error,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(work_id) DO UPDATE SET total_seconds=excluded.total_seconds,duration_minutes=excluded.duration_minutes,status=excluded.status,last_good_seconds=excluded.last_good_seconds,last_good_minutes=excluded.last_good_minutes,error=excluded.error,updated_at=excluded.updated_at',
                       (work_id, seconds, minutes, status, last_seconds, last_minutes, error, now()))
            self.sync_tag(db, work_id, minutes if status == 'ready' else None)
            db.execute("DELETE FROM issues WHERE work_id=? AND type='duration_unavailable'", (work_id,))
            if error:
                self.store.issue(db, 'duration_unavailable', error, script_id, work_id)
            return {'work_id': work_id, **duration_fields(db, work_id)}

    def sync_tag(self, db, work_id: int, minutes: int | None):
        current = [row[0] for row in db.execute("SELECT wt.tag_id FROM work_tags wt JOIN tags t ON t.id=wt.tag_id WHERE wt.work_id=? AND t.category='duration'", (work_id,))]
        target = []
        if minutes is not None:
            name = f'{minutes} 分钟'
            db.execute("INSERT OR IGNORE INTO tags(category,name,name_key) VALUES('duration',?,?)", (name, name.casefold()))
            tag_id = db.execute("SELECT id FROM tags WHERE category='duration' AND name_key=?", (name.casefold(),)).fetchone()[0]
            target = [tag_id]
        if current == target:
            return
        if current:
            db.execute("DELETE FROM work_tags WHERE work_id=? AND tag_id IN (SELECT id FROM tags WHERE category='duration')", (work_id,))
        if target:
            db.execute("INSERT INTO work_tags(work_id,tag_id,source) VALUES(?,?,'duration')", (work_id, target[0]))
        db.execute('INSERT INTO work_tag_state(work_id,revision) VALUES(?,1) ON CONFLICT(work_id) DO UPDATE SET revision=work_tag_state.revision+1', (work_id,))


def refresh_all_duration(store, config, work_ids=None) -> dict:
    """Refresh existing inventory only; called explicitly or after a manual scan/rematch."""
    if work_ids is None:
        with store.connection() as db:
            work_ids = [row[0] for row in db.execute('SELECT id FROM works ORDER BY id')]
    ids = list(dict.fromkeys(work_ids))
    service = DurationService(store, config)
    summary = {'total': len(ids), 'ready': 0, 'unknown': 0, 'partial': 0, 'stale': 0}
    for work_id in ids:
        state = service.refresh(work_id)
        summary[state['duration_status']] += 1
    return summary
