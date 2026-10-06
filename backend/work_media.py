"""Host-only source playback and non-destructive cover editing."""
from __future__ import annotations

import hashlib
import json
import math
import mimetypes
from pathlib import Path
import re
import subprocess
import time
import uuid
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from PIL import Image

from .covers import CoverGenerator
from .previews import PreviewError, PreviewService
from .scan_roots import no_link_components, ScanRootsError
from .store import now
from .work_directory import current_directory

TOKEN = re.compile(r'[0-9a-f]{32}')


class FrameInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    video_asset_id: int = Field(gt=0)
    time_seconds: float = Field(ge=0, allow_inf_nan=False)


class Crop(BaseModel):
    model_config = ConfigDict(extra='forbid')
    x: float = Field(ge=0, lt=1, allow_inf_nan=False)
    y: float = Field(ge=0, lt=1, allow_inf_nan=False)
    width: float = Field(gt=0, le=1, allow_inf_nan=False)
    height: float = Field(gt=0, le=1, allow_inf_nan=False)


class SaveInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    frame_id: str
    crop: Crop
    expected_revision: int = Field(ge=0)


class RestoreInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)


class WorkMedia:
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.previews = PreviewService(store, config)
        self.covers = CoverGenerator(store, config)
        self.frames = config.data_dir / 'cover-frames'
        self.frames.mkdir(exist_ok=True)

    def source(self, work_id: int, asset_id: int):
        with self.store.connection() as db:
            directory = current_directory(db, work_id)
            if directory is None:
                raise HTTPException(404, '作品没有唯一可用的关联目录')
            row = db.execute("SELECT a.*,d.path,d.root_path,d.available FROM assets a JOIN directories d ON d.id=a.directory_id WHERE a.id=? AND d.id=? AND a.kind='video'",
                             (asset_id, directory['id'])).fetchone()
        if row is None:
            raise HTTPException(404, '所选素材不是当前作品关联的视频')
        try:
            return self.previews.valid_source(dict(row))
        except PreviewError as error:
            raise HTTPException(error.status_code, str(error)) from error

    def state(self, work_id):
        with self.store.connection() as db:
            if not db.execute('SELECT 1 FROM works WHERE id=?', (work_id,)).fetchone():
                raise HTTPException(404, '作品不存在')
            row = db.execute('SELECT * FROM covers WHERE work_id=?', (work_id,)).fetchone()
        if row is None:
            return {'work_id': work_id, 'revision': 0, 'mode': 'automatic', 'cover_url': None,
                    'video_asset_id': None, 'time_seconds': None, 'crop': None}
        row = dict(row)
        return {'work_id': work_id, 'revision': row['revision'], 'mode': row['mode'],
                'cover_url': f"/api/covers/{work_id}?v={row['fingerprint'][:20]}" if row['path'] and Path(row['path']).is_file() else None,
                'video_asset_id': row['video_asset_id'], 'time_seconds': row['time_seconds'],
                'crop': json.loads(row['crop']) if row['crop'] else None}

    def cache_file(self, token, suffix):
        if not TOKEN.fullmatch(token):
            raise HTTPException(404, '截帧缓存不存在')
        path = self.frames / (token + suffix)
        try:
            no_link_components(path)
            path.resolve().relative_to(self.frames.resolve())
        except (OSError, ValueError, ScanRootsError) as error:
            raise HTTPException(403, '截帧缓存路径无效') from error
        return path

    @staticmethod
    def snapshot(path):
        stat = path.stat()
        return {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}

    def frame(self, work_id, options):
        source = self.source(work_id, options.video_asset_id)
        before = self.snapshot(source)
        token = uuid.uuid4().hex
        image = self.cache_file(token, '.jpg')
        metadata = self.cache_file(token, '.json')
        try:
            subprocess.run([self.config.ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                            '-ss', str(options.time_seconds), '-i', str(source), '-frames:v', '1',
                            '-q:v', '2', '-threads', '1', str(image)],
                           capture_output=True, timeout=45, check=True)
            if self.snapshot(self.source(work_id, options.video_asset_id)) != before:
                raise HTTPException(409, '视频素材已变化，请重新截帧')
            with Image.open(image) as opened:
                opened.verify()
            with Image.open(image) as opened:
                width, height = opened.size
            value = {'work_id': work_id, 'video_asset_id': options.video_asset_id, 'time_seconds': options.time_seconds,
                     'width': width, 'height': height, 'created_at': time.time(), 'source': before}
            metadata.write_text(json.dumps(value), encoding='utf-8')
            return {'frame_id': token, 'frame_url': f'/api/works/{work_id}/cover/frames/{token}',
                    **{key: value[key] for key in ('width', 'height', 'video_asset_id', 'time_seconds')}}
        except HTTPException:
            image.unlink(missing_ok=True); metadata.unlink(missing_ok=True)
            raise
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            image.unlink(missing_ok=True); metadata.unlink(missing_ok=True)
            raise HTTPException(422, '未能提取有效视频帧，请检查时间和源视频') from error

    def frame_data(self, work_id, token):
        image, metadata = self.cache_file(token, '.jpg'), self.cache_file(token, '.json')
        try:
            value = json.loads(metadata.read_text(encoding='utf-8'))
            if value['work_id'] != work_id or time.time() - value['created_at'] > 3600 or not image.is_file():
                raise ValueError()
            return image, value
        except (OSError, ValueError, KeyError) as error:
            raise HTTPException(404, '截帧缓存不存在或已过期，请重新截帧') from error

    @staticmethod
    def check_revision(db, work_id, expected):
        if not db.execute('SELECT 1 FROM works WHERE id=?', (work_id,)).fetchone():
            raise HTTPException(404, '作品不存在')
        row = db.execute('SELECT * FROM covers WHERE work_id=?', (work_id,)).fetchone()
        if (row['revision'] if row else 0) != expected:
            raise HTTPException(409, '封面已被其他操作更新，请重新读取')
        return dict(row) if row else None

    def save(self, work_id, options):
        image, frame = self.frame_data(work_id, options.frame_id)
        if self.snapshot(self.source(work_id, frame['video_asset_id'])) != frame['source']:
            raise HTTPException(409, '视频素材已变化，请重新截帧')
        crop = options.crop.model_dump()
        if crop['x'] + crop['width'] > 1.0000001 or crop['y'] + crop['height'] > 1.0000001:
            raise HTTPException(422, '裁切区域超出画面')
        ratio = crop['width'] * frame['width'] / (crop['height'] * frame['height'])
        if not math.isclose(ratio, 16 / 9, rel_tol=.01):
            raise HTTPException(422, '裁切区域必须为 16:9')
        with self.store.connection() as db:
            self.check_revision(db, work_id, options.expected_revision)
        token = uuid.uuid4().hex
        final = self.covers.cache_dir / f'{work_id}-manual-{token}.jpg'
        temporary = self.covers.cache_dir / f'.{work_id}-manual-{token}.jpg'
        committed = False
        try:
            no_link_components(temporary)
            no_link_components(final)
            with Image.open(image) as opened:
                w, h = opened.size
                box = (round(crop['x'] * w), round(crop['y'] * h),
                       round((crop['x'] + crop['width']) * w), round((crop['y'] + crop['height']) * h))
                if box[2] <= box[0] or box[3] <= box[1]:
                    raise HTTPException(422, '裁切区域过小')
                opened.convert('RGB').crop(box).resize((1280, 720), Image.Resampling.LANCZOS).save(temporary, 'JPEG', quality=92)
            temporary.replace(final)
            fingerprint = hashlib.sha256((token + json.dumps(crop, sort_keys=True)).encode()).hexdigest()
            with self.store.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                old = self.check_revision(db, work_id, options.expected_revision)
                automatic = old if old and old['mode'] == 'automatic' else None
                db.execute("""INSERT INTO covers(work_id,fingerprint,path,error,source_path,updated_at,mode,revision,video_asset_id,time_seconds,crop,automatic_path,automatic_fingerprint,automatic_source_path)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                              ON CONFLICT(work_id) DO UPDATE SET fingerprint=excluded.fingerprint,path=excluded.path,error=NULL,source_path=excluded.source_path,updated_at=excluded.updated_at,mode='manual',revision=excluded.revision,video_asset_id=excluded.video_asset_id,time_seconds=excluded.time_seconds,crop=excluded.crop,automatic_path=excluded.automatic_path,automatic_fingerprint=excluded.automatic_fingerprint,automatic_source_path=excluded.automatic_source_path""",
                           (work_id, fingerprint, str(final), None, frame['source']['path'], now(), 'manual', options.expected_revision + 1,
                            frame['video_asset_id'], frame['time_seconds'], json.dumps(crop),
                            automatic['path'] if automatic else old['automatic_path'] if old else None,
                            automatic['fingerprint'] if automatic else old['automatic_fingerprint'] if old else None,
                            automatic['source_path'] if automatic else old['automatic_source_path'] if old else None))
                db.execute("DELETE FROM issues WHERE work_id=? AND type='cover_failed'", (work_id,))
            committed = True
            return self.state(work_id)
        except (OSError, ValueError, ScanRootsError) as error:
            if not committed:
                final.unlink(missing_ok=True)
            raise HTTPException(422, '封面未能写入，请重试；原封面仍保留') from error
        except Exception:
            if not committed:
                final.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def restore(self, work_id, expected):
        with self.store.connection() as db:
            old = self.check_revision(db, work_id, expected)
        if old and old['mode'] == 'automatic':
            return self.state(work_id)
        path = Path(old['automatic_path']) if old and old['automatic_path'] else None
        if path is not None:
            try:
                no_link_components(path)
                path.resolve().relative_to(self.covers.cache_dir.resolve())
                if not path.is_file():
                    path = None
            except (OSError, ValueError, ScanRootsError):
                path = None
        prepared = None
        if path is None:
            source = self.covers.source(work_id)
            if source is None:
                raise HTTPException(422, '没有可用视频，无法恢复自动封面')
            prepared = self.covers.render_source(work_id, source)
            if prepared['error']:
                raise HTTPException(422, prepared['error'])
            path = Path(prepared['path'])
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.check_revision(db, work_id, expected)
            fingerprint = prepared['fingerprint'] if prepared else old['automatic_fingerprint']
            source = prepared['source_path'] if prepared else old['automatic_source_path']
            db.execute("""INSERT INTO covers(work_id,fingerprint,path,error,source_path,updated_at,mode,revision)
                          VALUES(?,?,?,?,?,?,'automatic',?) ON CONFLICT(work_id) DO UPDATE SET fingerprint=excluded.fingerprint,path=excluded.path,error=NULL,source_path=excluded.source_path,updated_at=excluded.updated_at,mode='automatic',revision=excluded.revision,video_asset_id=NULL,time_seconds=NULL,crop=NULL""",
                       (work_id, fingerprint, str(path), None, source, now(), expected + 1))
        return self.state(work_id)


def register_media_routes(app, store, config, host_capability, require_host_origin):
    service = WorkMedia(store, config)

    def allow_read(request):
        if not host_capability(request):
            raise HTTPException(403, '仅素材所在主机可播放视频或编辑封面')
        if request.headers.get('sec-fetch-site') not in {None, 'same-origin', 'none'}:
            raise HTTPException(403, '媒体请求仅允许本机同源页面')
        referer = request.headers.get('referer')
        if referer is not None and (urlparse(referer).scheme != 'http' or urlparse(referer).netloc.lower() != request.headers.get('host', '').lower()):
            raise HTTPException(403, '媒体请求仅允许本机同源页面')
        origin = request.headers.get('origin')
        if origin is not None and origin != 'http://' + request.headers.get('host', '').lower():
            raise HTTPException(403, '媒体请求仅允许本机同源页面')

    @app.api_route('/api/works/{work_id}/assets/{asset_id}/media', methods=['GET', 'HEAD'])
    def media(work_id: int, asset_id: int, request: Request):
        allow_read(request)
        source = service.source(work_id, asset_id)
        return FileResponse(source, media_type=mimetypes.guess_type(source.name)[0] or 'application/octet-stream',
                            filename=source.name, content_disposition_type='inline', headers={'Cache-Control': 'private, no-store'})

    @app.get('/api/works/{work_id}/cover')
    def state(work_id: int, request: Request):
        allow_read(request)
        return service.state(work_id)

    @app.post('/api/works/{work_id}/cover/frames')
    def frame(work_id: int, options: FrameInput, request: Request):
        require_host_origin(request)
        return service.frame(work_id, options)

    @app.api_route('/api/works/{work_id}/cover/frames/{token}', methods=['GET', 'HEAD'])
    def image(work_id: int, token: str, request: Request):
        allow_read(request)
        path, _metadata = service.frame_data(work_id, token)
        return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'private, no-store'})

    @app.post('/api/works/{work_id}/cover')
    def save(work_id: int, options: SaveInput, request: Request):
        require_host_origin(request)
        return service.save(work_id, options)

    @app.post('/api/works/{work_id}/cover/restore')
    def restore(work_id: int, options: RestoreInput, request: Request):
        require_host_origin(request)
        return service.restore(work_id, options.expected_revision)

    return service