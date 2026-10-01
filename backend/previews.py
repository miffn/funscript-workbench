"""Validated inventory inputs and fixed preview media, independent of HTTP handlers."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
import re
import threading
from urllib.parse import quote

from preview_generator import Config as GeneratorConfig, generate, GenerationCancelled
from preview_generator.scripts import AXES, ScriptError, discover_scripts, load_script

from .config import Config, normalize_id
from .store import Store
from .heatmaps import generate_heatmap


CLIP_MEDIA_FILES = {
    **{f"预览视频{index}.webm": ("video", index) for index in range(1, 5)},
    **{f"预览gif{index}.gif": ("gif", index) for index in range(1, 5)},
}
MEDIA_FILES = {**CLIP_MEDIA_FILES, '热力图.png': ('heatmap', 0)}


class PreviewError(ValueError):
    def __init__(self, message: str, status_code=422):
        super().__init__(message)
        self.status_code = status_code


def no_symlinks(path: Path):
    """Check lexical components too: resolving a link first would conceal it."""
    path = path.absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise PreviewError("不允许使用符号链接路径", 403)


class PreviewService:
    def __init__(self, store: Store, config: Config):
        self.store = store
        self.config = config
        self._hashes = OrderedDict()
        self._hash_lock = threading.Lock()
        self._publish_lock = threading.RLock()
        from .preview_matching import PreviewMatching
        self.matching = PreviewMatching(self)

    @staticmethod
    def write_json(path: Path, value: dict):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix='.workbench-json-', suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(value, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def work(self, work_id: int):
        with self.store.connection() as db:
            row = db.execute("SELECT id,script_id FROM works WHERE id=?", (work_id,)).fetchone()
        if row is None:
            raise PreviewError("库存编号不存在", 404)
        return dict(row)

    def output_directory(self, script_id: str) -> Path:
        if normalize_id(script_id) != script_id:
            raise PreviewError("作品完整编号无效", 403)
        root = self.config.preview_output_root.absolute()
        output = root / script_id
        no_symlinks(root)
        no_symlinks(output)
        if not output.resolve().is_relative_to(root.resolve()):
            raise PreviewError("预览目录不在授权输出根目录中", 403)
        return output

    def windows_path(self, output: Path) -> str:
        for root in self.config.roots:
            try:
                return root.windows_directory(output)
            except ValueError:
                continue
        mounted = re.fullmatch(r"/mnt/([a-z])/(.+)", str(output), re.I)
        return mounted[1].upper() + ":\\" + mounted[2].replace("/", "\\") if mounted else ""

    def valid_source(self, asset: dict) -> Path:
        if not asset["available"]:
            raise PreviewError("视频所在目录当前不可访问", 404)
        root = next((root for root in self.config.roots if str(root.path) == asset["root_path"]), None)
        if not root:
            raise PreviewError("视频不在配置的库存根目录中", 403)
        directory = Path(asset["path"])
        candidate = directory / asset["relative_path"]
        try:
            no_symlinks(root.path)
            no_symlinks(directory)
            no_symlinks(candidate)
            directory.resolve().relative_to(root.path.resolve())
            candidate.resolve().relative_to(directory.resolve())
            candidate.resolve().relative_to(root.path.resolve())
            if candidate.resolve().is_relative_to(self.config.preview_output_root.resolve()):
                raise PreviewError("生成目录内的文件不能作为原始视频", 403)
        except (OSError, ValueError) as error:
            if isinstance(error, PreviewError):
                raise
            raise PreviewError("视频路径超出库存目录", 403) from error
        if not directory.is_dir() or not candidate.is_file():
            raise PreviewError("原视频不存在或当前不可访问，请刷新库存", 404)
        return candidate

    def select_inputs(self, work_id: int, video_asset_id: int | None = None) -> dict:
        work = self.work(work_id)
        output = self.output_directory(work["script_id"])
        with self.store.connection() as db:
            assets = [dict(row) for row in db.execute("SELECT a.*,d.path,d.root_path,d.available FROM assets a JOIN directories d ON d.id=a.directory_id WHERE d.work_id=? AND a.kind='video' ORDER BY a.id", (work_id,))]
        if video_asset_id is not None:
            selected = next((asset for asset in assets if asset["id"] == video_asset_id), None)
            if selected is None:
                raise PreviewError("所选素材不是该作品关联的视频", 404)
            video = self.valid_source(selected)
        else:
            available = []
            for asset in assets:
                try:
                    available.append((asset, self.valid_source(asset)))
                except PreviewError:
                    continue
            if not available:
                raise PreviewError("没有可用原视频，请检查目录并刷新库存")
            if len(available) > 1:
                raise PreviewError("作品关联多个视频，请先选择具体视频", 409)
            selected, video = available[0]
        match = self.matching.state(work_id, selected['id'])
        if match['mode'] == 'auto':
            expected = {f"{video.stem}{'' if axis == 'stroke' else '.' + axis}.funscript".casefold() for axis in AXES}
            for candidate in video.parent.iterdir():
                if candidate.name.casefold() in expected:
                    no_symlinks(candidate)
        if match['issues']:
            raise PreviewError('；'.join(match['issues']))
        with self.store.connection() as db:
            rows = self.matching.assets(db, work_id)
        scripts = {axis: self.valid_source(next(asset for asset in rows if asset['id'] == asset_id))
                   for axis, asset_id in match['script_asset_ids'].items()}
        return {"work_id": work_id, "script_id": work["script_id"], "video_asset_id": selected["id"],
                "video_path": str(video), "scripts": {axis: str(path) for axis, path in scripts.items()},
                "matching_revision": match['revision'],
                "source_signatures": self.matching.signatures(str(video), {axis: str(path) for axis, path in scripts.items()}),
                "output_dir": str(output)}

    def assert_video_asset(self, work_id: int, video_asset_id: int):
        with self.store.connection() as db:
            row = db.execute("SELECT a.*,d.path,d.root_path,d.available FROM assets a JOIN directories d ON d.id=a.directory_id WHERE d.work_id=? AND a.kind='video' AND a.id=?", (work_id, video_asset_id)).fetchone()
        if row is None:
            raise PreviewError("所选素材不是该作品关联的视频", 404)
        self.valid_source(dict(row))

    def _manifest(self, path: Path, script_id: str) -> dict | None:
        try:
            no_symlinks(path)
            if path.stat().st_size > 2 * 1024 * 1024:
                return None
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("work_id") == script_id and isinstance(value.get("outputs"), list):
                return value
        except (OSError, ValueError, UnicodeError):
            pass
        return None

    def _checksum(self, path: Path) -> str:
        stat = path.stat()
        signature = lambda value: (value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_ino, value.st_dev)
        key = (str(path), *signature(stat))
        with self._hash_lock:
            if key in self._hashes:
                return self._hashes[key]
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if signature(path.stat()) != signature(stat):
            raise PreviewError("预览文件正在更新，请稍后重试", 409)
        checksum = digest.hexdigest()
        with self._hash_lock:
            self._hashes[key] = checksum
            while len(self._hashes) > 128:
                self._hashes.popitem(last=False)
        return checksum

    def trusted_files(self, work: dict, manifests: list[dict]) -> list[dict]:
        output = self.output_directory(work["script_id"])
        files = {}
        for manifest in manifests:
            for entry in manifest.get("outputs", []):
                if not isinstance(entry, dict):
                    continue
                filename = entry.get("filename")
                if filename not in MEDIA_FILES or filename in files:
                    continue
                kind, index = MEDIA_FILES[filename]
                if entry.get("kind") != kind or entry.get("clip_index") != index:
                    continue
                width, height = entry.get("width"), entry.get("height")
                if any(isinstance(dimension, bool) or not isinstance(dimension, int) or not 1 <= dimension <= 8192 for dimension in (width, height)):
                    continue
                checksum = entry.get("sha256")
                if not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
                    continue
                path = output / filename
                try:
                    no_symlinks(path)
                    if not path.is_file() or path.stat().st_size != entry.get("size") or self._checksum(path) != checksum:
                        continue
                except (OSError, PreviewError):
                    continue
                files[filename] = {"filename": filename, "kind": kind, "clip_index": index, "width": width,
                                   "height": height, "url": f"/api/works/{work['id']}/preview/files/{quote(filename)}?v={checksum[:20]}",
                                   "size": path.stat().st_size}
        return sorted(files.values(), key=lambda item: (item['kind'] == 'heatmap', item["clip_index"], item["kind"] != "video"))

    def state_path(self, work_id: int) -> Path:
        return self.config.data_dir / "preview-state" / f"{work_id}.json"

    def state(self, work_id: int) -> dict:
        with self._publish_lock:
            return self._state(work_id)

    def _state(self, work_id: int) -> dict:
        work = self.work(work_id)
        output = self.output_directory(work["script_id"])
        current = self._manifest(output / "manifest.json", work["script_id"])
        previous = self._manifest(self.state_path(work_id), work["script_id"])
        with self.store.connection() as db:
            row = db.execute("SELECT id FROM jobs WHERE type='preview' AND json_extract(inputs,'$.work_id')=? ORDER BY id DESC LIMIT 1", (work_id,)).fetchone()
        job = self.store.job(row[0]) if row else None
        error = job.get("error") if job and job["status"] == "failed" else None
        if not error and not (job and job["status"] in {"queued", "running"}) and current and current.get("status") in {"failed", "cancelled"}:
            error = current.get("error") or "上次预览生成未完成"
        return {"job": job, "files": self.trusted_files(work, [manifest for manifest in (current, previous) if manifest]),
                "output_dir": str(output), "windows_path": self.windows_path(output), "error": error}

    def media_path(self, work_id: int, filename: str) -> Path:
        if filename not in MEDIA_FILES:
            raise PreviewError("预览文件不存在", 404)
        state = self.state(work_id)
        if not any(file["filename"] == filename for file in state["files"]):
            raise PreviewError("预览文件不存在或校验未通过", 404)
        path = Path(state["output_dir"]) / filename
        no_symlinks(path)
        return path

    def save_previous(self, work_id: int, script_id: str):
        current = self._manifest(self.output_directory(script_id) / "manifest.json", script_id)
        if current and current.get("status") == "completed":
            destination = self.state_path(work_id)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".tmp")
            temporary.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
            temporary.replace(destination)

    def recover_stale_lock(self, output: Path, expected_pid: int | None):
        if expected_pid is None:
            return  # Do not claim a standalone CLI's abandoned lock as our own.
        lock = output / ".preview-generator.lock"
        try:
            no_symlinks(lock)
            stat = lock.stat()
            if stat.st_size > 4096:
                return
            value = json.loads(lock.read_text(encoding="utf-8"))
            pid = value.get("pid") if isinstance(value, dict) else None
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid != expected_pid:
                return
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                current = lock.stat()
                if (current.st_size, current.st_mtime_ns, current.st_ino) == (stat.st_size, stat.st_mtime_ns, stat.st_ino):
                    stage_name = value.get('workbench_stage')
                    if stage_name:
                        if not isinstance(stage_name, str) or Path(stage_name).name != stage_name or not stage_name.startswith('.workbench-stage-'):
                            return
                        stage = output / stage_name
                        no_symlinks(stage)
                        if stage.exists():
                            journal = stage / 'publication.json'
                            no_symlinks(journal)
                            if journal.is_file():
                                publication = json.loads(journal.read_text())
                                names, previous = publication.get('filenames', []), publication.get('previous', [])
                                allowed = {*MEDIA_FILES, 'manifest.json'}
                                if not isinstance(names, list) or not isinstance(previous, list) or any(not isinstance(name, str) or name not in allowed for name in [*names, *previous]):
                                    return
                                if publication.get('status') == 'publishing':
                                    # A service exit during the short publication phase restores
                                    # the previous complete set before replaying the queued job.
                                    for filename in names:
                                        old, target = stage / 'prior' / filename, output / filename
                                        no_symlinks(old)
                                        no_symlinks(target)
                                        if filename in previous:
                                            if not old.is_file():
                                                raise PreviewError('预览恢复副本缺失，请检查生成目录', 409)
                                            shutil.copy2(old, target)
                                        else:
                                            target.unlink(missing_ok=True)
                            shutil.rmtree(stage)
                    lock.unlink()
            except PermissionError:
                pass  # A process exists; never remove its lock.
        except (OSError, ValueError):
            pass

    def generate(self, inputs: dict, on_progress, cancel_event):
        # Recheck the persisted selection after queueing/restart, never stored paths alone.
        checked = self.select_inputs(inputs["work_id"], inputs["video_asset_id"])
        if any(checked[key] != inputs.get(key, checked[key]) for key in
               ("script_id", "video_path", "scripts", "matching_revision", "source_signatures")):
            raise PreviewError("素材或脚本匹配在任务排队期间变化，请刷新后重新生成", 409)
        output = Path(checked['output_dir'])
        output.mkdir(parents=True, exist_ok=True)
        self.recover_stale_lock(output, inputs.get('_recovery_pid'))
        lock_path = output / '.preview-generator.lock'
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            raise PreviewError('该作品有其他预览生成程序正在运行，请等待完成', 409) from error
        with os.fdopen(lock_fd, 'w') as handle:
            json.dump({'pid': os.getpid()}, handle)
        stage = None
        try:
            self.save_previous(inputs['work_id'], inputs['script_id'])
            stage = Path(tempfile.mkdtemp(prefix='.workbench-stage-', dir=output))
            self.write_json(lock_path, {'pid': os.getpid(), 'workbench_stage': stage.name})
            # Copy prior validated files into staging so normal generation can reuse them.
            current = self._manifest(output / 'manifest.json', checked['script_id'])
            if current and current.get('status') == 'completed' and not inputs.get('force', False):
                for filename in [*MEDIA_FILES, 'manifest.json']:
                    path = output / filename
                    no_symlinks(path)
                    if path.is_file():
                        shutil.copy2(path, stage / filename)
            generator_config = GeneratorConfig(work_id=checked['script_id'], video=checked['video_path'],
                scripts=checked['scripts'], output_dir=str(stage), renderer=self.config.preview_renderer,
                model='builtin', show_axis_hud=True, ffmpeg=self.config.ffmpeg, ffprobe=self.config.ffprobe,
                force=bool(inputs.get('force', False)), cache_dir=self.config.data_dir / 'preview-generator')
            def preview_progress(event):
                if on_progress:
                    on_progress({**event, 'progress': event.get('progress', 0) * .9,
                                 'stage': 'previews_ready' if event.get('stage') == 'completed' else event.get('stage')})
            manifest = generate(generator_config, on_progress=preview_progress, cancel_event=cancel_event)
            if manifest.get('status') != 'completed' or manifest.get('work_id') != checked['script_id']:
                raise PreviewError('生成程序未返回完整的完成结果')
            clip_records = manifest.get('outputs', [])
            if len(clip_records) != len(CLIP_MEDIA_FILES) or {entry.get('filename') for entry in clip_records} != set(CLIP_MEDIA_FILES):
                raise PreviewError('生成结果文件列表无效')
            if on_progress:
                on_progress({'stage': 'heatmap', 'progress': .92})
            no_symlinks(self.config.heatmap_tool)
            if not self.config.heatmap_tool.is_file():
                raise PreviewError('指定的热力图工具不可用，请检查热力图工具路径')
            heatmap_fingerprint = hashlib.sha256(json.dumps({
                'scripts': manifest.get('scripts'), 'duration_seconds': manifest.get('video', {}).get('duration_seconds'),
                'tool_sha256': self._checksum(self.config.heatmap_tool),
                'adapter_sha256': self._checksum(Path(__file__).with_name('heatmaps.py')),
            }, sort_keys=True).encode()).hexdigest()
            old_heatmap = next((entry for entry in (current or {}).get('outputs', []) if entry.get('filename') == '热力图.png'), None)
            heatmap_path = stage / '热力图.png'
            if (not inputs.get('force', False) and current and current.get('heatmap_fingerprint') == heatmap_fingerprint
                    and old_heatmap and heatmap_path.is_file() and self._checksum(heatmap_path) == old_heatmap.get('sha256')):
                heatmap = {**old_heatmap, 'path': str(heatmap_path), 'reused': True}
            else:
                heatmap = generate_heatmap(checked['script_id'], checked['scripts'], stage,
                    duration_seconds=manifest.get('video', {}).get('duration_seconds'),
                    cancel_event=cancel_event, tool_path=self.config.heatmap_tool)
                heatmap['reused'] = False
            manifest['outputs'].append(heatmap)
            manifest['heatmap_fingerprint'] = heatmap_fingerprint
            if cancel_event and cancel_event.is_set():
                raise GenerationCancelled('Generation cancelled before publication')
            if self.matching.signatures(checked['video_path'], checked['scripts']) != checked['source_signatures']:
                raise PreviewError('生成期间素材发生变化，请重新匹配后再生成，保留旧预览', 409)
            records = manifest.get('outputs', [])
            expected = {entry.get('filename') for entry in records}
            if not records or len(expected) != len(records) or expected != set(MEDIA_FILES):
                raise PreviewError('生成结果文件列表无效')
            for entry in records:
                path = stage / entry['filename']
                no_symlinks(path)
                kind, index = MEDIA_FILES[entry['filename']]
                dimensions_valid = ((entry.get('width'), entry.get('height')) == ((1920, 1080) if kind == 'video' else (192, 108))
                                    if kind != 'heatmap' else entry.get('width') == 2048 and isinstance(entry.get('height'), int)
                                    and not isinstance(entry.get('height'), bool) and 1 <= entry['height'] <= 4096)
                if (entry.get('kind'), entry.get('clip_index')) != (kind, index) or not dimensions_valid:
                    raise PreviewError('生成结果的类型、片段或尺寸校验失败，保留旧预览')
                if not path.is_file() or path.stat().st_size != entry.get('size') or self._checksum(path) != entry.get('sha256'):
                    raise PreviewError('生成结果校验失败，保留旧预览')
                entry['path'] = str(output / entry['filename'])
            manifest['output_dir'] = str(output)
            manifest['source_signatures'] = checked['source_signatures']
            (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
            # Backup/rollback includes the manifest. Old results stay usable until all clips finish.
            with self._publish_lock:
                prior = stage / 'prior'
                prior.mkdir()
                names = [*expected, 'manifest.json']
                previous_names = []
                for filename in names:
                    target = output / filename
                    no_symlinks(target)
                    if target.is_file():
                        shutil.copy2(target, prior / filename)
                        previous_names.append(filename)
                publication = {'status': 'publishing', 'filenames': names, 'previous': previous_names}
                journal = stage / 'publication.json'
                self.write_json(journal, publication)
                published = []
                try:
                    for filename in names:
                        target = output / filename
                        no_symlinks(target)
                        (stage / filename).replace(target)
                        published.append(filename)
                    self.save_previous(inputs['work_id'], inputs['script_id'])
                    publication['status'] = 'published'
                    self.write_json(journal, publication)
                except BaseException:
                    for filename in reversed(published):
                        old = prior / filename
                        if old.exists():
                            old.replace(output / filename)
                        else:
                            (output / filename).unlink(missing_ok=True)
                    raise
            if on_progress:
                on_progress({'stage': 'completed', 'progress': 1.0})
            return manifest
        finally:
            if stage:
                # Only our freshly created staging directory under the authorized output root.
                if stage.parent == output and stage.name.startswith('.workbench-stage-'):
                    shutil.rmtree(stage)
            lock_path.unlink(missing_ok=True)
