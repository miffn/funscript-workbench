from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from pathlib import Path, PureWindowsPath

from .config import Config, Root
from .store import Store


class ScanRootsError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def windows_path(path: Path) -> str:
    mounted = re.fullmatch(r'/mnt/([a-z])/(.+)', str(path), re.I)
    if mounted:
        return mounted[1].upper() + ':\\' + mounted[2].replace('/', '\\')
    try:
        result = subprocess.run(['wslpath', '-w', str(path)], capture_output=True, text=True,
                                check=True, timeout=5).stdout.strip()
        if PureWindowsPath(result).is_absolute():
            return result
    except (OSError, subprocess.SubprocessError):
        pass
    return str(path)


def no_link_components(path: Path):
    candidate = Path(path.anchor)
    for part in path.parts[1:]:
        candidate /= part
        if candidate.is_symlink():
            raise ScanRootsError('扫描目录不能包含符号链接')


class ScanRoots:
    """Persistent root catalog. Configuration is used only for first-run migration."""

    KEY = 'root_catalog'

    def __init__(self, store: Store, config: Config):
        self.store = store
        self.config = config

    def state(self, db) -> dict:
        row = db.execute('SELECT value FROM settings WHERE key=?', (self.KEY,)).fetchone()
        if row:
            state = json.loads(row[0])
            if any('identification' not in root for root in state['roots']):
                state['roots'] = [{**root, 'identification': root.get('identification', 'numbered')} for root in state['roots']]
                db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(state, ensure_ascii=False), self.KEY))
        else:
            old = db.execute("SELECT value FROM settings WHERE key='scan_roots'").fetchone()
            selection = json.loads(old[0]) if old else None
            roots = {str(root.path): root for root in self.config.roots}
            for directory in db.execute('SELECT root_path,path,windows_path FROM directories ORDER BY id'):
                path = directory['root_path']
                if path in roots:
                    continue
                try:
                    relative = Path(directory['path']).relative_to(Path(path))
                    mapped = PureWindowsPath(directory['windows_path'])
                    for _ in relative.parts:
                        mapped = mapped.parent
                    roots[path] = Root(Path(path), str(mapped), Path(path).name)
                except ValueError:
                    continue
            if not self.config.roots and selection:
                for path in selection['enabled_paths']:
                    if path not in roots:
                        roots[path] = Root(Path(path), windows_path(Path(path)), Path(path).name)
            state = {'roots': [
                {'path': str(root.path), 'windows_path': root.windows_path, 'label': root.label,
                 'enabled': str(root.path) in selection['enabled_paths'] if selection else True,
                 'identification': root.identification}
                for root in roots.values()], 'revision': selection['revision'] if selection else 0}
            db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',
                       (self.KEY, json.dumps(state, ensure_ascii=False)))
            state = json.loads(db.execute('SELECT value FROM settings WHERE key=?', (self.KEY,)).fetchone()[0])
        return {**state, 'enabled_paths': [root['path'] for root in state['roots'] if root['enabled']]}

    def roots(self, db=None) -> tuple[Root, ...]:
        if db is None:
            with self.store.connection() as connection:
                return self.roots(connection)
        return tuple(Root(Path(root['path']), root['windows_path'], root['label'], root['identification'])
                     for root in self.state(db)['roots'])

    def normalize(self, definition: dict, existing: dict[str, dict]) -> dict:
        value = definition['path'].strip()
        if not value or len(value) > 2000 or any(unicodedata.category(char) in {'Cc', 'Cf'} for char in value):
            raise ScanRootsError('扫描目录路径无效')
        drive = re.match(r'^([A-Za-z]):[\\/]', value)
        if drive:
            windows = PureWindowsPath(value)
            if any(part in {'.', '..'} for part in re.split(r'[\\/]', value[3:])):
                raise ScanRootsError('扫描目录不能包含 . 或 .. 路径跳转')
            if any(any(char in part for char in '<>:"|?*') or part.endswith((' ', '.'))
                   for part in windows.parts[1:]):
                raise ScanRootsError('Windows 扫描目录路径无效')
            path = Path('/mnt') / drive[1].lower() / Path(*windows.parts[1:])
        else:
            if not value.startswith('/') or '\\' in value:
                raise ScanRootsError('请输入 Windows 盘符绝对路径或 WSL 绝对路径')
            if any(part in {'.', '..'} for part in value.split('/')):
                raise ScanRootsError('扫描目录不能包含 . 或 .. 路径跳转')
            path = Path(value)
        if path == Path('/') or path == Path('/mnt') or re.fullmatch(r'/mnt/[a-z]', str(path), re.I):
            raise ScanRootsError('请选择具体素材目录，不能扫描整个文件系统或磁盘')
        no_link_components(path)
        if path.exists() and not path.is_dir():
            raise ScanRootsError('扫描路径必须是目录')
        if path.resolve().is_relative_to(self.config.preview_output_root.resolve()):
            raise ScanRootsError('预览输出目录不能作为库存扫描目录')
        previous = existing.get(str(path))
        identification = definition.get('identification', previous.get('identification', 'numbered') if previous else 'folder')
        if identification not in {'numbered', 'folder'}:
            raise ScanRootsError('识别方式应为 numbered 或 folder')
        label = definition.get('label', '').strip() or (previous['label'] if previous else path.name)
        if len(label) > 120 or any(unicodedata.category(char) in {'Cc', 'Cf'} for char in label):
            raise ScanRootsError('目录名称应为 1 到 120 个普通字符')
        return {'path': str(path), 'windows_path': previous['windows_path'] if previous else windows_path(path),
                'label': label, 'enabled': definition['enabled'], 'identification': identification}

    def update(self, enabled_paths: list[str], expected_revision: int) -> dict:
        if not enabled_paths:
            raise ScanRootsError("请至少选择一个扫描目录")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.state(db)
            if any(path not in {root['path'] for root in current['roots']} for path in enabled_paths):
                raise ScanRootsError('扫描目录必须是已配置的库存根目录')
            roots = [{**root, 'enabled': root['path'] in enabled_paths} for root in current['roots']]
            return self._save(db, current, roots, expected_revision)

    def replace(self, definitions: list[dict], expected_revision: int) -> dict:
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self.state(db)
            if current['revision'] != expected_revision:
                raise ScanRootsError('扫描目录设置已被其他页面修改，请刷新后重试', 409)
            existing = {root['path']: root for root in current['roots']}
            roots = [self.normalize(definition, existing) for definition in definitions]
            for index, root in enumerate(roots):
                for other in roots[:index]:
                    first, second = Path(root['path']), Path(other['path'])
                    first_windows, second_windows = PureWindowsPath(root['windows_path']), PureWindowsPath(other['windows_path'])
                    windows_overlap = first_windows.is_relative_to(second_windows) or second_windows.is_relative_to(first_windows)
                    if windows_overlap or first.is_relative_to(second) or second.is_relative_to(first):
                        raise ScanRootsError('扫描目录不能重复或存在父子目录重叠')
            return self._save(db, current, roots, expected_revision)

    def _save(self, db, current: dict, roots: list[dict], expected_revision: int) -> dict:
        if current['revision'] != expected_revision:
            raise ScanRootsError('扫描目录设置已被其他页面修改，请刷新后重试', 409)
        structure = lambda items: {(root['path'], root['windows_path'], root['label'], root.get('identification', 'numbered')) for root in items}
        if structure(current['roots']) != structure(roots):
            if db.execute("SELECT 1 FROM jobs WHERE type IN ('scan','rematch','preview') AND status IN ('queued','running') LIMIT 1").fetchone():
                raise ScanRootsError('正在扫描、匹配或生成预览，请等待任务完成后增删目录', 409)
        removed = {root['path'] for root in current['roots']} - {root['path'] for root in roots}
        for path in removed:
            db.execute('UPDATE directories SET available=0 WHERE root_path=?', (path,))
        state = {'roots': roots, 'revision': current['revision'] + 1}
        db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                   (self.KEY, json.dumps(state, ensure_ascii=False)))
        return {**state, 'enabled_paths': [root['path'] for root in roots if root['enabled']]}
