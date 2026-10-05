"""Persistent, inventory-scoped video/axis selections; source files remain read-only."""
from __future__ import annotations

import json
from pathlib import Path

from preview_generator.scripts import AXES, ScriptError, discover_scripts, load_script

from .previews import PreviewError, no_symlinks
from .work_directory import current_directory


class PreviewMatching:
    def __init__(self, previews):
        self.previews = previews
        self.store = previews.store

    def assets(self, db, work_id):
        directory = current_directory(db, work_id)
        return [dict(row) for row in db.execute("SELECT a.*,d.path,d.root_path,d.available FROM assets a JOIN directories d ON d.id=a.directory_id WHERE d.id=? AND a.kind IN ('video','script') ORDER BY a.name COLLATE NOCASE,a.id", (directory['id'],))] if directory is not None else []

    def active(self, db, work_id):
        return db.execute("SELECT id FROM jobs WHERE type IN ('preview','rematch') AND status IN ('queued','running') AND json_extract(inputs,'$.work_id')=? ORDER BY id LIMIT 1", (work_id,)).fetchone()

    def state(self, work_id, video_asset_id=None):
        work = self.previews.work(work_id)
        with self.store.connection() as db:
            assets = self.assets(db, work_id)
            active = db.execute("SELECT id FROM jobs WHERE type='rematch' AND json_extract(inputs,'$.work_id')=? ORDER BY id DESC LIMIT 1", (work_id,)).fetchone()
            old_selection = db.execute('SELECT * FROM preview_bindings WHERE work_id=? AND video_asset_id=? ORDER BY revision DESC LIMIT 1', (work_id, video_asset_id)).fetchone() if video_asset_id is not None else None
            directory_count = db.execute('SELECT count(*) FROM directories WHERE work_id=? AND available=1', (work_id,)).fetchone()[0]
        valid = []
        for asset in assets:
            try:
                self.previews.valid_source(asset)
                valid.append(asset)
            except PreviewError:
                pass
        videos = [asset for asset in valid if asset['kind'] == 'video']
        scripts = [asset for asset in valid if asset['kind'] == 'script']
        selected = next((asset for asset in videos if asset['id'] == video_asset_id), None)
        if video_asset_id is None and len(videos) == 1:
            selected = videos[0]
        issues, mapping, paths = [], {}, {}
        revision, mode = 0, 'auto'
        video_path = None
        if directory_count > 1:
            issues.append('同一完整编号对应多个目录，请处理编号冲突后重新匹配文件')
        if selected:
            video_path = self.previews.valid_source(selected)
            with self.store.connection() as db:
                saved = db.execute("SELECT * FROM preview_bindings WHERE work_id=? AND video_path=?", (work_id, str(video_path))).fetchone()
            if not saved and old_selection:
                saved = old_selection
                issues.append('手动选择的源视频路径已变化，请检查并重新保存对应关系')
            by_path = {str(self.previews.valid_source(asset)): asset for asset in scripts}
            if saved:
                revision, mode = saved['revision'], 'manual'
                paths = json.loads(saved['scripts'])
                for axis, path in paths.items():
                    asset = by_path.get(path)
                    if asset:
                        mapping[axis] = asset['id']
                    else:
                        issues.append(f"{axis} 手动关联的脚本已消失或不可访问：{Path(path).name}，请重新指定")
            else:
                try:
                    expected = {f"{video_path.stem}{'' if axis == 'stroke' else '.' + axis}.funscript".casefold() for axis in AXES}
                    for candidate in video_path.parent.iterdir():
                        if candidate.name.casefold() in expected:
                            no_symlinks(candidate)
                    found = discover_scripts(video_path)
                    for axis, path in found.items():
                        asset = by_path.get(str(path))
                        if asset:
                            mapping[axis] = asset['id']
                            paths[axis] = str(path)
                        else:
                            issues.append(f"{axis} 脚本尚未关联到库存，请重新匹配文件")
                except (ScriptError, OSError, ValueError) as error:
                    issues.append(f"自动匹配未完成：{error}，可手动指定脚本")
            if not paths and not issues:
                issues.append('未匹配到轴脚本，请手动指定后保存')
            for axis, asset_id in mapping.items():
                try:
                    path = self.previews.valid_source(next(asset for asset in scripts if asset['id'] == asset_id))
                    load_script(path)
                except (ScriptError, OSError, ValueError) as error:
                    issues.append(f'{axis} 脚本无法使用：{error}')
        elif video_asset_id is not None:
            if old_selection:
                revision, mode = old_selection['revision'], 'manual'
            issues.append('所选视频已消失或不可访问，请重新匹配文件并选择源视频')
        elif not videos:
            issues.append('没有可读取的视频，请检查目录并重新匹配文件')
        else:
            issues.append('作品关联多个视频，请先选择源视频')
        key = self.previews.preview_key(work)
        manifest = self.previews._manifest(self.previews.output_directory(key) / 'manifest.json', key)
        changed = bool(work['preview_stale'])
        if manifest and manifest.get('status') == 'completed' and selected:
            old_video, old_scripts = manifest.get('video', {}), manifest.get('scripts', {})
            if old_video and isinstance(old_scripts, dict):
                changed = changed or old_video.get('path') != str(video_path) or {axis: info.get('path') for axis, info in old_scripts.items()} != paths
                changed = changed or old_video.get('size', video_path.stat().st_size) != video_path.stat().st_size
                # Filesystem stamps catch edits without repeatedly hashing a large video.
                stamps = manifest.get('source_signatures')
                if stamps:
                    try:
                        changed = changed or stamps != self.signatures(str(video_path), paths)
                    except OSError:
                        changed = True
                elif not changed:
                    # Legacy manifests lack filesystem stamps; scripts are small
                    # and their checksums are cached against size/mtime/inode.
                    changed = any(info.get('sha256') and self.previews._checksum(Path(paths[axis])) != info['sha256']
                                  for axis, info in old_scripts.items() if axis in paths)
        public = lambda asset: {key: asset.get(key) for key in ('id','name','relative_path','kind','axis','size','directory_id')}
        return {'work_id': work_id, 'video_asset_id': selected['id'] if selected else None,
                'mode': mode, 'revision': revision, 'script_asset_ids': mapping, 'issues': issues,
                'videos': [public(asset) for asset in videos], 'scripts': [public(asset) for asset in scripts],
                'job': self.store.job(active[0]) if active else None, 'source_changed': changed}

    @staticmethod
    def signatures(video_path, scripts):
        return {path: {'size': Path(path).stat().st_size, 'mtime_ns': Path(path).stat().st_mtime_ns}
                for path in [video_path, *scripts.values()]}

    def save(self, work_id, video_asset_id, script_asset_ids, expected_revision):
        self.previews.work(work_id)
        if set(script_asset_ids) - set(AXES):
            raise PreviewError('轴类型无效，只支持 Stroke / Surge / Sway / Twist / Roll / Pitch')
        if len(set(script_asset_ids.values())) != len(script_asset_ids):
            raise PreviewError('同一个脚本不能重复分配给多个轴')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.active(db, work_id):
                raise PreviewError('作品正在生成预览或重新匹配文件，请等待完成', 409)
            assets = self.assets(db, work_id)
            video = next((asset for asset in assets if asset['id'] == video_asset_id and asset['kind'] == 'video'), None)
            if not video:
                raise PreviewError('所选素材不是该作品的视频', 404)
            video_path = str(self.previews.valid_source(video))
            saved = db.execute('SELECT revision FROM preview_bindings WHERE work_id=? AND video_path=?', (work_id, video_path)).fetchone()
            if not saved:
                saved = db.execute('SELECT revision FROM preview_bindings WHERE work_id=? AND video_asset_id=? ORDER BY revision DESC LIMIT 1', (work_id, video_asset_id)).fetchone()
            if expected_revision != (saved[0] if saved else 0):
                raise PreviewError('文件匹配已被其他页面修改，请刷新后重新确认', 409)
            paths = {}
            for axis, asset_id in script_asset_ids.items():
                asset = next((item for item in assets if item['id'] == asset_id and item['kind'] == 'script'), None)
                if not asset:
                    raise PreviewError('所选脚本不属于当前作品或文件类型错误', 422)
                path = self.previews.valid_source(asset)
                try:
                    load_script(path)
                except (ScriptError, OSError, ValueError) as error:
                    raise PreviewError(f'{axis} 脚本无法使用：{error}') from error
                paths[axis] = str(path)
            db.execute("INSERT INTO preview_bindings(work_id,video_path,video_asset_id,scripts,revision) VALUES(?,?,?,?,?) ON CONFLICT(work_id,video_path) DO UPDATE SET scripts=excluded.scripts,revision=excluded.revision,video_asset_id=excluded.video_asset_id", (work_id, video_path, video_asset_id, json.dumps(paths, ensure_ascii=False), expected_revision + 1))
        return self.state(work_id, video_asset_id)
