"""End-to-end folder identity through the shared API and persistent worker."""
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.main import create_app
from backend.scanner import Scanner
from backend.store import Store
from backend.tests.test_previews import fake_generate
import backend.previews as preview_module


@pytest.fixture
def folder_workbench(tmp_path, fake_heatmap_tool, monkeypatch):
    root = tmp_path / 'materials'
    root.mkdir()
    folder = root / '作品 A'
    folder.mkdir()
    (folder / 'main.mp4').write_bytes(b'original-video')
    (folder / 'main.funscript').write_text('{"actions":[{"at":0,"pos":0},{"at":10000,"pos":100}]}')
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, str(root), 'Materials', identification='folder'),),
                    preview_output_root=root / 'previews', heatmap_tool=fake_heatmap_tool,
                    ffmpeg='missing-ffmpeg-for-covers', ffprobe='missing-ffprobe-for-covers')
    store = Store(config.data_dir)
    scanner = Scanner(store, config)
    scanner.scan()
    monkeypatch.setattr(preview_module, 'generate', fake_generate)
    app = create_app(config, start_worker=False)
    with TestClient(app) as client:
        yield config, store, scanner, client, app.state.worker, folder


def details(client, work_id):
    result = client.get(f'/api/works/{work_id}')
    assert result.status_code == 200, result.text
    return result.json()


def test_folder_move_restores_the_original_record_with_manual_data_and_preview(folder_workbench):
    config, store, scanner, client, worker, folder = folder_workbench
    work = client.get('/api/works').json()['items'][0]
    work_id = work['id']
    assert work['script_id'] is None and work['title'] == folder.name
    title = '人工保留标题'
    assert client.patch(f'/api/works/{work_id}', json={'title': title, 'notes': '保留备注'}).status_code == 200
    tag = client.post('/api/tags', json={'category': 'author', 'name': 'Author A'}).json()
    assert client.put(f'/api/works/{work_id}/tags', json={'tag_ids': [tag['id'], *(item['id'] for item in work['tags'])], 'expected_revision': work['tags_revision']}).status_code == 200
    links = client.get(f'/api/works/{work_id}/links').json()
    links_body = {'links': {'es': 'https://example.com/post/1', 'patreon': 'https://example.com/patreon/1'},
                  'expected_revision': links['links_revision'], 'es_published_date': '2026-10-02', 'patreon_published_date': '2026-10-03'}
    assert client.patch(f'/api/works/{work_id}/links', json=links_body).status_code == 200
    assert client.patch(f'/api/works/{work_id}', json={'patreon_published': True}).status_code == 200
    generated = client.post(f'/api/works/{work_id}/preview')
    assert generated.status_code == 202, generated.text
    worker.perform_preview(generated.json()['id'])
    finished = store.job(generated.json()['id'])
    assert finished['status'] == 'completed', finished['error']
    preview = client.get(f'/api/works/{work_id}/preview').json()
    assert Path(preview['output_dir']).name == f'work-{work_id}' and len(preview['files']) == 9
    source_hash = hashlib.sha256((folder / 'main.mp4').read_bytes()).hexdigest()
    waiting = details(client, work_id)
    reset = client.post(f'/api/works/{work_id}/production/reset', json={'expected_revision': waiting['production_revision']})
    assert reset.status_code == 200, reset.text
    reset_work = reset.json()
    assert reset_work['production_required'] and reset_work['es_published'] and reset_work['patreon_published']
    assert client.get('/api/works', params={'status': 'pending'}).json()['total'] == 0
    assert client.patch(f'/api/works/{work_id}', json={'es_published': False}).status_code == 200
    links = client.get(f'/api/works/{work_id}/links').json()
    assert client.patch(f'/api/works/{work_id}/links', json={'links': links['links'], 'expected_revision': links['links_revision']}).status_code == 200
    assert not details(client, work_id)['es_published']
    moved = folder.with_name('重新命名作品')
    folder.rename(moved)
    (moved / 'main.pitch.funscript').write_text((moved / 'main.funscript').read_text())
    assert details(client, work_id)['association_status'] == 'missing'
    scanner.scan()
    assert client.get('/api/works').json()['total'] == 1
    candidates = client.get('/api/scan-candidates').json()
    assert candidates['total'] == 1
    candidate = candidates['items'][0]
    lost = next(row for row in candidates['works'] if row['id'] == work_id)
    result = client.post(f"/api/scan-candidates/{candidate['id']}/resolve", json={
        'action': 'associate', 'work_id': work_id, 'expected_revision': candidate['revision'],
        'expected_work_revision': lost['association_revision']})
    assert result.status_code == 200, result.text
    restored = result.json()['work']
    assert restored['id'] == work_id and restored['title'] == title and restored['notes'] == '保留备注'
    assert restored['axis_type'] == '多轴' and restored['script_count'] == 2
    assert not restored['es_published'] and restored['patreon_published']
    assert restored['es_published_date'] == '2026-10-02' and restored['patreon_published_date'] == '2026-10-03'
    assert restored['links']['es'] == links_body['links']['es']
    assert any(item['id'] == tag['id'] for item in restored['tags'])
    assert restored['production_required'] and restored['preview_stale']
    assert len(restored['directories']) == 1 and restored['directories'][0]['path'] == str(moved)
    assert hashlib.sha256((moved / 'main.mp4').read_bytes()).hexdigest() == source_hash
    old_preview = client.get(f'/api/works/{work_id}/preview').json()
    assert len(old_preview['files']) == 9 and old_preview['output_dir'] == preview['output_dir']
    with TestClient(create_app(config, start_worker=False)) as restarted:
        persisted = details(restarted, work_id)
        assert persisted['production_required'] and not persisted['es_published'] and persisted['patreon_published']
        confirm = restarted.post(f'/api/works/{work_id}/production/confirm', json={'expected_revision': persisted['production_revision']})
        assert confirm.status_code == 200, confirm.text
        assert restarted.get('/api/works', params={'status': 'pending'}).json()['total'] == 1
        regenerated = restarted.post(f'/api/works/{work_id}/preview', json={'force': True})
        assert regenerated.status_code == 202, regenerated.text
        restarted.app.state.worker.perform_preview(regenerated.json()['id'])
        assert store.job(regenerated.json()['id'])['status'] == 'completed'
        assert not details(restarted, work_id)['preview_stale']


def test_unnumbered_rematch_only_refreshes_the_selected_work(folder_workbench):
    config, store, scanner, client, worker, folder = folder_workbench
    selected = client.get('/api/works').json()['items'][0]
    fresh = config.roots[0].path / 'unrelated-new-work'
    fresh.mkdir()
    (fresh / 'new.funscript').write_text('{"actions":[]}')
    (folder / 'main.roll.funscript').write_text((folder / 'main.funscript').read_text())
    queued = client.post(f"/api/works/{selected['id']}/rematch")
    assert queued.status_code == 202, queued.text
    worker.perform_rematch(queued.json()['id'])
    finished = store.job(queued.json()['id'])
    assert finished['status'] == 'completed', finished['error']
    rows = client.get('/api/works').json()
    assert rows['total'] == 1
    assert details(client, selected['id'])['script_count'] == 2
    assert client.get('/api/scan-candidates').json()['total'] == 0


def test_publication_defaults_and_withdrawal_do_not_rewrite_history(folder_workbench, monkeypatch):
    _, _, _, client, _, _ = folder_workbench
    monkeypatch.setattr('backend.main.release_today', lambda: '2026-10-06')
    work = client.get('/api/works').json()['items'][0]
    endpoint = f"/api/works/{work['id']}"
    published = client.patch(endpoint, json={'es_published': True}).json()
    assert published['es_published_date'] == '2026-10-06'
    assert client.patch(endpoint, json={'es_published_date': None}).status_code == 200
    # Repeating an already-true flag must not infer a previously unknown actual date.
    unchanged = client.patch(endpoint, json={'es_published': True, 'notes': 'Keep actual date unknown'}).json()
    assert unchanged['es_published_date'] is None
    assert client.patch(endpoint, json={'es_published_date': '2026-10-02'}).status_code == 200
    calendar = client.get('/api/release-calendar', params={'month': '2026-10'}).json()
    item = next(row for row in calendar['works'] if row['id'] == work['id'])
    plan = client.post('/api/release-calendar', json={'work_id': work['id'], 'platforms': ['es'],
        'mode': 'planned', 'date': '2026-10-10', 'expected_revision': item['revision']})
    assert plan.status_code == 200, plan.text
    withdrawn = client.patch(endpoint, json={'es_published': False}).json()
    assert withdrawn['es_published_date'] == '2026-10-02'
    events = client.get('/api/release-calendar', params={'month': '2026-10'}).json()['events']
    assert not any(row['work_id'] == work['id'] and row['platform'] == 'es' and row['mode'] == 'actual' for row in events)
    assert any(row['work_id'] == work['id'] and row['mode'] == 'planned' and row['date'] == '2026-10-10' for row in events)
    restored = client.patch(endpoint, json={'es_published': True}).json()
    assert restored['es_published_date'] == '2026-10-02'


def test_legacy_numbered_import_does_not_match_or_change_unnumbered_works(folder_workbench):
    from backend.import_tags import plan_import
    _, store, _, _, _, _ = folder_workbench
    with store.connection() as db:
        before = '\n'.join(db.iterdump())
        result = plan_import(db, [{'Script ID': 'S999', '作者': 'Imported author'}], [])
        assert all(row['script_id'] is not None for row in result['plans'])
        assert '\n'.join(db.iterdump()) == before


def test_hidden_subfolders_are_excluded_from_scan_and_recovery_fingerprints(folder_workbench):
    _, _, scanner, client, _, folder = folder_workbench
    hidden = folder / '.cache'
    hidden.mkdir()
    (hidden / 'hidden.pitch.funscript').write_text('{"actions":[]}')
    scanner.scan()
    work = client.get('/api/works').json()['items'][0]
    assert details(client, work['id'])['script_count'] == 1
    moved = folder.with_name('Moved with hidden cache')
    folder.rename(moved)
    scanner.scan()
    candidate = client.get('/api/scan-candidates').json()['items'][0]
    assert candidate['script_count'] == 1
    (moved / '.cache' / 'hidden.pitch.funscript').write_text('{"actions":[{"at":1,"pos":20}]}')
    lost = details(client, work['id'])
    restored = client.post(f"/api/scan-candidates/{candidate['id']}/resolve", json={
        'action': 'associate', 'work_id': work['id'], 'expected_revision': candidate['revision'],
        'expected_work_revision': lost['association_revision']})
    assert restored.status_code == 200, restored.text
    assert restored.json()['work']['script_count'] == 1
    assert all('.cache' not in row['relative_path'] for row in restored.json()['work']['assets'])
