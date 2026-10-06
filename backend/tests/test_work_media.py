from dataclasses import replace
from io import BytesIO
import hashlib
from pathlib import Path
import subprocess

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.config import Config, Root
from backend.covers import CoverGenerator
from backend.scanner import Scanner
from backend.store import Store
from backend.work_media import register_media_routes

HOST = {'Host': 'localhost:8788', 'X-Workbench-Host-Key': 'unit-host-key',
        'Origin': 'http://localhost:8788', 'Sec-Fetch-Site': 'same-origin'}


@pytest.fixture
def media(tmp_path):
    root = tmp_path / 'library'
    folder = root / 'S070 sample'
    folder.mkdir(parents=True)
    video = folder / 'main.mp4'
    # A real two-colour source verifies frame decoding and crop pixels.
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=320x180:r=10:d=1',
                    '-vf', 'drawbox=x=160:y=0:w=160:h=180:color=blue:t=fill', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-threads', '1', str(video)], check=True)
    (folder / 'main.funscript').write_text('{"actions":[]}')
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, 'D:\\library', 'library'),),
                    preview_output_root=tmp_path / 'previews')
    store = Store(config.data_dir)
    Scanner(store, config).scan()
    with store.connection() as db:
        work_id = db.execute('SELECT id FROM works').fetchone()[0]
        asset_id = db.execute("SELECT id FROM assets WHERE kind='video'").fetchone()[0]
    app = FastAPI()
    def host(request):
        return request.headers.get('host') == 'localhost:8788' and request.headers.get('x-workbench-host-key') == 'unit-host-key'
    def origin(request):
        if not host(request) or request.headers.get('origin') != 'http://localhost:8788':
            raise HTTPException(403)
    service = register_media_routes(app, store, config, host, origin)
    with TestClient(app) as client:
        yield client, service, store, config, work_id, asset_id, video


def frame(media):
    client, _service, _store, _config, work_id, asset_id, _video = media
    response = client.post(f'/api/works/{work_id}/cover/frames', headers=HOST,
                           json={'video_asset_id': asset_id, 'time_seconds': .2})
    assert response.status_code == 200, response.text
    return response.json()


def save(media, revision=0, crop=None):
    client, _service, _store, _config, work_id, _asset_id, _video = media
    token = frame(media)['frame_id']
    return client.post(f'/api/works/{work_id}/cover', headers=HOST,
                       json={'frame_id': token, 'expected_revision': revision,
                             'crop': crop or {'x': .5, 'y': .25, 'width': .5, 'height': .5}})


def test_local_video_ranges_head_and_cross_site_rejection(media):
    client, _service, _store, _config, work_id, asset_id, video = media
    url = f'/api/works/{work_id}/assets/{asset_id}/media'
    headers = {key: value for key, value in HOST.items() if key != 'Origin'}
    result = client.get(url, headers={**headers, 'Range': 'bytes=3-11'})
    assert result.status_code == 206
    assert result.content == video.read_bytes()[3:12]
    assert result.headers['content-range'].startswith('bytes 3-11/')
    assert result.headers['content-disposition'].startswith('inline')
    head = client.head(url, headers=headers)
    assert head.status_code == 200 and not head.content
    assert int(head.headers['content-length']) == video.stat().st_size
    assert client.get(url, headers={'Host': 'localhost:8788'}).status_code == 403
    assert client.get(url, headers={**HOST, 'Host': '192.0.2.10:8787'}).status_code == 403
    assert client.get(url, headers={**HOST, 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert client.get(url, headers={**HOST, 'Origin': 'http://evil.example'}).status_code == 403
    assert client.get(f'/api/works/{work_id}/assets/{asset_id+100}/media', headers=HOST).status_code == 404


def test_real_frame_crop_revision_and_readonly_source(media):
    client, service, store, _config, work_id, asset_id, video = media
    original = hashlib.sha256(video.read_bytes()).digest()
    extracted = frame(media)
    assert (extracted['width'], extracted['height']) == (320, 180)
    with Image.open(BytesIO(client.get(extracted['frame_url'], headers=HOST).content)) as image:
        assert image.size == (320, 180)
    assert client.get(extracted['frame_url'], headers={'Host': 'localhost:8788'}).status_code == 403
    result = save(media)
    assert result.status_code == 200, result.text
    assert result.json()['mode'] == 'manual' and result.json()['revision'] == 1
    with store.connection() as db:
        row = dict(db.execute('SELECT * FROM covers WHERE work_id=?', (work_id,)).fetchone())
    with Image.open(row['path']) as image:
        assert image.size == (1280, 720)
        red, green, blue = image.getpixel((640, 360))
        assert blue > 180 and red < 30 and green < 30
    assert row['video_asset_id'] == asset_id and row['time_seconds'] == .2
    assert hashlib.sha256(video.read_bytes()).digest() == original
    conflict = save(media, revision=0)
    assert conflict.status_code == 409
    assert service.state(work_id)['cover_url'] == result.json()['cover_url']


def test_invalid_crop_frame_binding_and_decode_failures_preserve_cover(media):
    client, service, store, config, work_id, asset_id, video = media
    assert save(media).status_code == 200
    before = service.state(work_id)
    bad = save(media, revision=1, crop={'x': .9, 'y': 0, 'width': .5, 'height': .5})
    assert bad.status_code == 422 and service.state(work_id) == before
    bad = save(media, revision=1, crop={'x': 0, 'y': 0, 'width': .5, 'height': 1})
    assert bad.status_code == 422 and service.state(work_id) == before
    assert client.post(f'/api/works/{work_id}/cover', headers=HOST,
                       json={'frame_id': '../outside', 'expected_revision': 1, 'crop': {'x': 0, 'y': 0, 'width': 1, 'height': 1}}).status_code == 404
    token = frame(media)['frame_id']
    assert client.get(f'/api/works/{work_id+1}/cover/frames/{token}', headers=HOST).status_code == 404
    service.config = replace(config, ffmpeg='missing-ffmpeg')
    assert client.post(f'/api/works/{work_id}/cover/frames', headers=HOST,
                       json={'video_asset_id': asset_id, 'time_seconds': 0}).status_code == 422
    assert service.state(work_id) == before
    video.write_bytes(b'changed')
    assert client.post(f'/api/works/{work_id}/cover', headers=HOST,
                       json={'frame_id': token, 'expected_revision': 1, 'crop': {'x': 0, 'y': 0, 'width': 1, 'height': 1}}).status_code == 409


def test_manual_cover_survives_force_scan_and_source_disappearance_and_restores_automatic(media):
    _client, service, store, config, work_id, _asset_id, video = media
    generator = CoverGenerator(store, config)
    assert generator.generate(work_id) == 'generated'
    automatic = service.state(work_id)
    result = save(media, revision=automatic['revision'])
    assert result.status_code == 200
    manual = service.state(work_id)
    assert generator.generate(work_id, force=True) == 'cached'
    video.unlink()
    assert generator.generate(work_id, force=True) == 'cached'
    assert service.state(work_id) == manual
    restored = service.restore(work_id, manual['revision'])
    assert restored['mode'] == 'automatic'
    assert restored['cover_url'] == automatic['cover_url']
    assert restored['revision'] == manual['revision'] + 1


def test_restore_failure_and_revision_conflict_preserve_manual(media):
    _client, service, _store, config, work_id, _asset_id, video = media
    assert save(media).status_code == 200
    before = service.state(work_id)
    with pytest.raises(HTTPException) as conflict:
        service.restore(work_id, 0)
    assert conflict.value.status_code == 409
    service.covers.config = replace(config, ffmpeg='missing-ffmpeg')
    with pytest.raises(HTTPException) as failure:
        service.restore(work_id, 1)
    assert failure.value.status_code == 422
    assert service.state(work_id) == before


def test_symlink_unregistered_and_foreign_sources_are_rejected(media, tmp_path):
    client, _service, store, _config, work_id, asset_id, video = media
    url = f'/api/works/{work_id}/assets/{asset_id}/media'
    external = tmp_path / 'external.mp4'
    external.write_bytes(video.read_bytes())
    video.unlink(); video.symlink_to(external)
    assert client.get(url, headers=HOST).status_code == 403
    with store.connection() as db:
        db.execute('UPDATE assets SET relative_path=? WHERE id=?', ('../../external.mp4', asset_id))
    assert client.get(url, headers=HOST).status_code == 403
    with store.connection() as db:
        db.execute('UPDATE directories SET root_path=? WHERE work_id=?', (str(tmp_path / 'unregistered'), work_id))
    assert client.get(url, headers=HOST).status_code in {403, 404}

def test_cover_writes_are_host_only_and_simultaneous_saves_use_revision_cas(media):
    from concurrent.futures import ThreadPoolExecutor
    client, service, _store, _config, work_id, asset_id, _video = media
    token = frame(media)['frame_id']
    body = {'frame_id': token, 'expected_revision': 0, 'crop': {'x': 0, 'y': 0, 'width': 1, 'height': 1}}
    for headers in ({'Host': '192.0.2.10:8787'}, {**HOST, 'Origin': 'http://evil.example'}, {'Host': 'localhost:8788'}):
        assert client.post(f'/api/works/{work_id}/cover', headers=headers, json=body).status_code == 403
        assert client.post(f'/api/works/{work_id}/cover/restore', headers=headers, json={'expected_revision': 0}).status_code == 403
        assert client.post(f'/api/works/{work_id}/cover/frames', headers=headers, json={'video_asset_id': asset_id, 'time_seconds': 0}).status_code == 403
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: client.post(f'/api/works/{work_id}/cover', headers=HOST, json=body).status_code, range(2)))
    assert sorted(outcomes) == [200, 409]
    assert service.state(work_id)['revision'] == 1


def test_concurrent_automatic_generation_does_not_replace_a_new_manual_selection(media, monkeypatch):
    _client, service, store, config, work_id, _asset_id, _video = media
    generator = CoverGenerator(store, config)
    original = generator.render_source
    def render_then_manual(work_id, video):
        candidate = original(work_id, video)
        assert save(media).status_code == 200
        return candidate
    monkeypatch.setattr(generator, 'render_source', render_then_manual)
    assert generator.generate(work_id, force=True) == 'cached'
    assert service.state(work_id)['mode'] == 'manual'
    with store.connection() as db:
        assert db.execute('SELECT revision FROM covers WHERE work_id=?', (work_id,)).fetchone()[0] == 1