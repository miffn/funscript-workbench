"""Human-approved recovery of discovery candidates; original materials remain read-only."""
import json
import os
from pathlib import Path

from .config import parse_folder
from .production import require_production_confirmation
from .scan_roots import ScanRoots, ScanRootsError, no_link_components
from .scanner import Scanner, asset_kind, script_axis, item_fingerprint, existing_path_owner
from .store import now
from .tags import sync_axis_tag
from .work_directory import current_directory, directory_status, invalidate_association


class ScanCandidatesError(ValueError):
    def __init__(self, message, status_code=422):
        super().__init__(message)
        self.status_code = status_code


class ScanCandidates:
    def __init__(self, store, config):
        self.store, self.config = store, config

    def eligible_works(self, db):
        roots = ScanRoots(self.store, self.config).roots(db)
        return [dict(work) for work in db.execute('SELECT id,script_id,title,association_revision FROM works WHERE association_missing=1 ORDER BY id')
                if directory_status(db, work['id'], roots) == 'missing']

    def catalog(self):
        with self.store.connection() as db:
            roots = ScanRoots(self.store, self.config).roots(db)
            items = []
            for row in db.execute("SELECT * FROM scan_candidates WHERE status='pending' ORDER BY id"):
                item = {key: row[key] for key in ('id', 'name', 'script_id', 'path', 'windows_path', 'root_path', 'revision', 'video_count', 'script_count')}
                root = next((root for root in roots if str(root.path) == row['root_path']), None)
                item['available'] = bool(row['available']) and root is not None and Path(row['path']).is_dir()
                try:
                    no_link_components(Path(row['path']))
                except ScanRootsError:
                    item['available'] = False
                item['missing_work_ids'] = json.loads(row['missing_work_ids'])
                items.append(item)
            return {'items': items, 'works': self.eligible_works(db), 'total': len(items)}

    def read_item(self, root, path):
        scanner = Scanner(self.store, self.config)
        try:
            no_link_components(root.path)
            no_link_components(path)
            if path.parent.resolve() != root.path.resolve() or scanner.excluded_output(path):
                raise ScanCandidatesError('候选目录不在当前扫描根目录的一级目录中', 403)
            if not path.is_dir():
                raise ScanCandidatesError('候选目录已不存在，请重新扫描', 404)
            parsed = parse_folder(path.name)
            item = {'path': str(path), 'root_path': str(root.path), 'windows_path': root.windows_directory(path),
                    'name': path.name, 'script_id': parsed[0] if parsed else None, 'title': parsed[1] if parsed else path.name,
                    'assets': [], 'errors': []}
            def unreadable(error):
                raise error
            for parent, folders, filenames in os.walk(path, followlinks=False, onerror=unreadable):
                folders[:] = [name for name in folders if not name.startswith('.') and not (Path(parent) / name).is_symlink() and not scanner.excluded_output(Path(parent) / name)]
                for name in sorted(filenames):
                    file = Path(parent) / name
                    if file.is_symlink() or not file.is_file():
                        continue
                    file.resolve().relative_to(path.resolve())
                    stat = file.stat()
                    item['assets'].append({'name': name, 'relative_path': str(file.relative_to(path)), 'kind': asset_kind(file),
                                           'axis': script_axis(file), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
            return item
        except ScanCandidatesError:
            raise
        except (OSError, ValueError, ScanRootsError) as error:
            raise ScanCandidatesError('无法安全读取候选目录，请检查目录后重新扫描', 409) from error

    def resolve(self, candidate_id, action, expected_revision, work_id=None, expected_work_revision=None):
        if action not in {'create', 'associate'} or (action == 'associate' and (work_id is None or expected_work_revision is None)) or (action == 'create' and work_id is not None):
            raise ScanCandidatesError('请选择创建新作品或关联失联作品，并提供当前版本')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE type IN ('scan','rematch','preview') AND status IN ('queued','running') LIMIT 1").fetchone():
                raise ScanCandidatesError('后台任务正在运行，请完成后再处理扫描候选', 409)
            candidate = db.execute('SELECT * FROM scan_candidates WHERE id=?', (candidate_id,)).fetchone()
            if candidate is None:
                raise ScanCandidatesError('扫描候选不存在', 404)
            if candidate['revision'] != expected_revision or candidate['status'] != 'pending':
                raise ScanCandidatesError('扫描候选已变化，请刷新后重试', 409)
            root = next((root for root in ScanRoots(self.store, self.config).roots(db) if str(root.path) == candidate['root_path']), None)
            if root is None or not candidate['available']:
                raise ScanCandidatesError('候选目录当前不可用，请重新扫描', 409)
            item = self.read_item(root, Path(candidate['path']))
            if item_fingerprint(item) != candidate['fingerprint']:
                raise ScanCandidatesError('候选素材已变化，请重新扫描后再处理', 409)
            for row in db.execute('SELECT id FROM works').fetchall():
                directory = current_directory(db, row['id'])
                if directory and existing_path_owner(Path(item['path']), {directory['path']: row['id']}) is not None:
                    raise ScanCandidatesError('候选目录已被其他作品占用，请刷新后重试', 409)
            if item['script_id'] is not None and db.execute('SELECT 1 FROM works WHERE script_id=? AND id!=?', (item['script_id'], work_id or -1)).fetchone():
                raise ScanCandidatesError('候选编号已被其他作品占用，请处理编号冲突', 409)
            if action == 'associate':
                work = db.execute('SELECT * FROM works WHERE id=?', (work_id,)).fetchone()
                if work is None:
                    raise ScanCandidatesError('待恢复作品不存在', 404)
                if work['association_revision'] != expected_work_revision:
                    raise ScanCandidatesError('作品目录关联已变化，请刷新后重试', 409)
                if work_id not in {work['id'] for work in self.eligible_works(db)}:
                    raise ScanCandidatesError('作品不是已确认失联状态，不能替换现有关联', 409)
                invalidate_association(db, work_id)
                db.execute('UPDATE directories SET available=0 WHERE work_id=?', (work_id,))
                db.execute('UPDATE works SET script_id=?,association_missing=0,association_revision=association_revision+1 WHERE id=?', (item['script_id'], work_id))
                if item['script_id']:
                    db.execute('UPDATE history SET applied=1 WHERE script_id=?', (item['script_id'],))
            else:
                work_id = db.execute('INSERT INTO works(script_id,title,created_at,updated_at,association_revision) VALUES(?,?,?,?,1)', (item['script_id'], item['title'], now(), now())).lastrowid
            Scanner(self.store, self.config).bind_item(db, item, work_id, now())
            sync_axis_tag(db, work_id)
            require_production_confirmation(db, work_id)
            db.execute("DELETE FROM issues WHERE work_id=? AND type IN ('directory_missing','identifier_changed','missing_assets','duplicate_identifier')", (work_id,))
        # Refresh derived caches outside the source-association write transaction.
        from .covers import CoverGenerator
        CoverGenerator(self.store, self.config).generate(work_id)
        from .durations import refresh_all_duration
        refresh_all_duration(self.store, self.config, [work_id])
        return {'candidate_id': candidate_id, 'action': action, 'work_id': work_id}
