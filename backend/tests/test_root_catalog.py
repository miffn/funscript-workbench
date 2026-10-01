from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path, PureWindowsPath

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.covers import CoverGenerator
from backend.jobs import JobWorker
from backend.main import create_app
from backend.previews import PreviewError, PreviewService
from backend.scan_roots import ScanRoots, ScanRootsError
from backend.scanner import Scanner
from backend.store import Store, now


@pytest.fixture
def catalog(tmp_path):
    material = tmp_path / 'inventory'
    material.mkdir()
    config = Config(data_dir=tmp_path / 'data', roots=(Root(material, 'D:\\inventory', '库存'),),
                    preview_output_root=tmp_path / 'generated', open_mode='gateway')
    store = Store(config.data_dir)
    client = TestClient(create_app(config, start_worker=False))
    with client:
        yield config, store, client


def definitions(client):
    return [{key: root[key] for key in ('path', 'label', 'enabled')}
            for root in client.get('/api/settings').json()['roots']]


def save(client, roots, revision=None):
    if revision is None:
        revision = client.get('/api/settings').json()['scan_roots_revision']
    return client.put('/api/settings/scan-roots', json={'roots': roots, 'expected_revision': revision})


def source(root, script_id='S080'):
    folder = root / script_id
    folder.mkdir(parents=True)
    (folder / 'video.mp4').write_bytes(b'fixture-video')
    (folder / 'video.funscript').write_text(json.dumps({'actions': [{'at': 0, 'pos': 0}, {'at': 1000, 'pos': 100}]}))
    return folder


def rows(store, name):
    with store.connection() as db:
        return [dict(row) for row in db.execute(f'SELECT * FROM {name} ORDER BY rowid')]


def test_new_install_has_no_personal_roots_and_project_relative_output(monkeypatch, tmp_path):
    monkeypatch.delenv('WORKBENCH_ROOTS_JSON', raising=False)
    monkeypatch.delenv('WORKBENCH_PREVIEW_OUTPUT_ROOT', raising=False)
    monkeypatch.setenv('WORKBENCH_DATA_DIR', str(tmp_path / 'new-data'))
    config = Config.from_environment()
    assert config.roots == ()
    assert config.preview_output_root == config.data_dir / 'previews'
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.get('/api/settings').json()['roots'] == []
        assert client.post('/api/scans').status_code == 422


def test_add_windows_and_linux_paths_persist_without_environment_readding(catalog, tmp_path):
    config, store, client = catalog
    original = definitions(client)
    response = save(client, [*original, {'path': 'e:\\素材\\新库存\\', 'label': '外置盘', 'enabled': True}])
    assert response.status_code == 200
    root = response.json()['roots'][-1]
    assert root == {'path': '/mnt/e/素材/新库存', 'windows_path': 'E:\\素材\\新库存',
                    'label': '外置盘', 'enabled': True, 'available': False}
    fresh = replace(config, roots=(Root(tmp_path / 'other', 'Z:\\other', '不要补回'),))
    with TestClient(create_app(fresh, start_worker=False)) as restarted:
        assert restarted.get('/api/settings').json()['roots'] == response.json()['roots']
        assert save(restarted, []).status_code == 200
    with TestClient(create_app(config, start_worker=False)) as restarted:
        assert restarted.get('/api/settings').json()['roots'] == []
    assert rows(store, 'jobs') == []


def test_first_upgrade_preserves_discovered_unselected_roots_and_revision(tmp_path):
    roots = tuple(Root(tmp_path / name, f'D:\\{name}', name) for name in ('one', 'two'))
    for root in roots:
        source(root.path, 'S080' if root.label == 'one' else 'S081')
    config = Config(data_dir=tmp_path / 'data', roots=roots, preview_output_root=tmp_path / 'generated')
    store = Store(config.data_dir)
    Scanner(store, config).scan()
    with store.connection() as db:
        db.execute("DELETE FROM settings WHERE key='root_catalog'")
        db.execute("INSERT INTO settings(key,value) VALUES('scan_roots',?)",
                   (json.dumps({'enabled_paths': [str(roots[1].path)], 'revision': 7}),))
    with TestClient(create_app(replace(config, roots=()), start_worker=False)) as client:
        settings = client.get('/api/settings').json()
        assert settings['scan_roots_revision'] == 7
        actual = {root['path']: root for root in settings['roots']}
        assert actual[str(roots[0].path)]['enabled'] is False
        assert actual[str(roots[1].path)]['enabled'] is True
        assert actual[str(roots[0].path)]['windows_path'] == 'D:\\one'
        assert actual[str(roots[1].path)]['windows_path'] == 'D:\\two'


def test_remove_preserves_inventory_manual_fields_links_tags_and_matching_then_readd(catalog):
    config, store, client = catalog
    source(config.roots[0].path)
    Scanner(store, config).scan()
    work = client.get('/api/works').json()['items'][0]
    work_id = work['id']
    assert client.patch(f'/api/works/{work_id}', json={'title': '人工标题', 'notes': '保留备注', 'status': 'published'}).status_code == 200
    tag = client.post('/api/tags', json={'category': 'custom', 'name': '保留'}).json()
    assert client.put(f'/api/works/{work_id}/tags', json={'tag_ids': [tag['id']], 'expected_revision': work['tags_revision']}).status_code == 200
    assert client.patch(f'/api/works/{work_id}/links', json={'links': {'video': 'https://example.com/video'}, 'expected_revision': 0}).status_code == 200
    service = PreviewService(store, config)
    inputs = service.select_inputs(work_id)
    before = {table: rows(store, table) for table in ('works', 'assets', 'work_tags', 'work_tag_state', 'work_links', 'preview_bindings')}
    assert save(client, []).status_code == 200
    detail = client.get(f'/api/works/{work_id}').json()
    assert detail['directories'][0]['available'] is False
    assert detail['video_count'] == 1
    assert detail['title'] == '人工标题' and detail['status'] == 'published'
    assert detail['links']['video'] == 'https://example.com/video'
    for table, expected in before.items():
        assert rows(store, table) == expected
    assert CoverGenerator(store, config).source(work_id) is None
    with pytest.raises(PreviewError):
        service.select_inputs(work_id)
    with pytest.raises(PreviewError):
        JobWorker(store, config).enqueue_preview(inputs)
    with TestClient(create_app(config, start_worker=False)) as restarted:
        assert restarted.get('/api/settings').json()['roots'] == []
        assert save(restarted, [{'path': str(config.roots[0].path), 'label': '库存', 'enabled': True}]).status_code == 200
    Scanner(store, replace(config, roots=())).scan()
    assert client.get(f'/api/works/{work_id}').json()['directories'][0]['available'] is True
    assert service.select_inputs(work_id)['work_id'] == work_id
    assert len(rows(store, 'works')) == 1
    assert rows(store, 'work_links') == before['work_links']


def test_new_root_scanning_cover_preview_rematch_and_open_share_catalog(catalog, tmp_path, monkeypatch):
    config, store, client = catalog
    root = tmp_path / 'additional'
    video = source(root, 'S081') / 'video.mp4'
    response = save(client, [*definitions(client), {'path': str(root), 'enabled': True}])
    assert response.status_code == 200
    mapped_root = response.json()['roots'][-1]['windows_path']
    worker = JobWorker(store, config)
    monkeypatch.setattr(worker.covers, 'generate', lambda *args, **kwargs: 'skipped')
    job = worker.enqueue()
    worker.perform(job['id'])
    assert store.job(job['id'])['status'] == 'completed'
    work = client.get('/api/works').json()['items'][0]
    assert work['script_id'] == 'S081'
    assert worker.covers.source(work['id']) == video
    inputs = worker.previews.select_inputs(work['id'])
    assert inputs['video_path'] == str(video)
    rematch = worker.enqueue_rematch(work['id'])
    worker.perform(rematch['id'])
    assert store.job(rematch['id'])['status'] == 'completed'
    (config.data_dir / 'host.key').write_text('test-host-key')
    headers = {'Host': 'localhost:8788', 'Origin': 'http://localhost:8788', 'X-Workbench-Host-Key': 'test-host-key'}
    opened = client.post(f"/api/works/{work['id']}/open-folder", headers=headers)
    assert opened.status_code == 200
    assert base64.urlsafe_b64decode(opened.headers['X-Workbench-Folder-Root']).decode() == mapped_root
    assert PureWindowsPath(base64.urlsafe_b64decode(opened.headers['X-Workbench-Open-Folder']).decode()) == PureWindowsPath(mapped_root) / 'S081'
    assert client.post(f"/api/works/{work['id']}/open-folder").status_code == 403


@pytest.mark.parametrize('kind', ['scan', 'rematch', 'preview'])
@pytest.mark.parametrize('status', ['queued', 'running'])
def test_structure_changes_wait_for_active_jobs_but_enabled_snapshot_can_change(catalog, kind, status):
    config, store, client = catalog
    original = definitions(client)
    with store.connection() as db:
        db.execute('INSERT INTO jobs(type,status,trigger,created_at) VALUES(?,?,?,?)', (kind, status, 'manual', now()))
    before = rows(store, 'settings')
    assert save(client, []).status_code == 409
    assert rows(store, 'settings') == before
    assert save(client, [{**original[0], 'label': '重命名'}]).status_code == 409
    assert save(client, [{**original[0], 'enabled': False}]).status_code == 200
    assert client.post('/api/scans').status_code == 422


@pytest.mark.parametrize('path', ['relative/path', 'D:relative', '/', '/mnt', 'D:\\', '/mnt/d',
                                  'D:\\assets\\..\\other', '/tmp/../secret', '/tmp/./assets',
                                  'D:\\assets\\bad:name', 'D:\\assets\\bad.'])
def test_invalid_paths_leave_catalog_unchanged(catalog, path):
    _, store, client = catalog
    before = rows(store, 'settings')
    response = save(client, [{'path': path, 'enabled': True}])
    assert response.status_code == 422
    assert rows(store, 'settings') == before


@pytest.mark.parametrize('payload', [
    {'roots': [], 'enabled_paths': []}, {'roots': None}, {},
    {'roots': [{'path': '/tmp/other', 'enabled': 1}]},
    {'roots': [{'path': '/tmp/other', 'enabled': True, 'windows_path': 'D:\\spoof'}]},
])
def test_invalid_schema(catalog, payload):
    _, store, client = catalog
    before = rows(store, 'settings')
    assert client.put('/api/settings/scan-roots', json={**payload, 'expected_revision': 0}).status_code == 422
    assert rows(store, 'settings') == before


def test_symlink_file_generated_output_and_overlapping_roots_rejected(catalog, tmp_path):
    config, store, client = catalog
    linked = tmp_path / 'linked'
    linked.symlink_to(config.roots[0].path, target_is_directory=True)
    file = tmp_path / 'file'
    file.write_text('fixture')
    for path in (linked, linked / 'child', file, config.preview_output_root, config.preview_output_root / 'S081'):
        assert save(client, [{'path': str(path), 'enabled': True}]).status_code == 422
    for paths in (['D:\\assets', '/mnt/d/assets/'], ['D:\\assets', 'D:\\assets\\sub'],
                  ['D:\\Assets', 'd:\\assets\\sub']):
        assert save(client, [{'path': path, 'enabled': True} for path in paths]).status_code == 422
    assert client.get('/api/settings').json()['scan_roots_revision'] == 0


def test_concurrent_catalog_edits_have_one_winner(catalog, tmp_path):
    config, store, _ = catalog
    service = ScanRoots(store, config)
    def change(index):
        try:
            return service.replace([{'path': str(tmp_path / f'catalog-{index}'), 'enabled': True}], 0)['revision']
        except ScanRootsError as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change, (1, 2))) == [1, 409]
    with store.connection() as db:
        assert service.state(db)['revision'] == 1
