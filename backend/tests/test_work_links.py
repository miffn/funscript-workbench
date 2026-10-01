from concurrent.futures import ThreadPoolExecutor
import json
import threading

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.main import create_app
from backend.scanner import Scanner
from backend.work_links import WorkLinks, WorkLinksError, validate_link


@pytest.fixture
def inventory(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    for name in ('S064', 'S064_001'):
        folder = root / name
        folder.mkdir()
        (folder / 'main.mp4').write_bytes(b'video')
        (folder / 'main.funscript').write_text('{"actions":[]}')
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, 'D:\\inventory', 'workspace'),),
                    ffmpeg='not-available', ffprobe='not-available', preview_output_root=tmp_path / 'previews')
    app = create_app(config, start_worker=False)
    scanner = Scanner(app.state.store, config)
    scanner.scan()
    with app.state.store.connection() as db:
        ids = {row['script_id']: row['id'] for row in db.execute('SELECT id,script_id FROM works')}
        metadata = {'Patreon post ink': 'https://example.test/patreon', 'Video URL': 'https://example.test/video',
                    'ES Link': 'https://example.test/es', 'Script Link': 'https://example.test/script', 'unrelated': 'retained'}
        db.execute('UPDATE works SET metadata=?,title=?,notes=?,status=?,es_published=1,patreon_published=1 WHERE id=?',
                   (json.dumps(metadata), 'Manual title', 'Private notes', 'published', ids['S064']))
    return config, app, scanner, ids


def test_history_initial_values_exposed_in_detail_and_list(inventory):
    _, app, _, ids = inventory
    work_id = ids['S064']
    with TestClient(app) as client:
        state = client.get(f'/api/works/{work_id}/links').json()
        assert state == {'work_id': work_id, 'links_revision': 0, 'links': {
            kind: f'https://example.test/{kind}' for kind in ('patreon', 'video', 'script', 'es')}}
        detail = client.get(f'/api/works/{work_id}').json()
        listed = next(row for row in client.get('/api/works').json()['items'] if row['id'] == work_id)
        for result in (detail, listed):
            assert result['links'] == state['links']
            assert result['links_revision'] == 0


def test_patch_preserves_other_fields_and_clear_overrides_history(inventory):
    _, app, _, ids = inventory
    work_id = ids['S064']
    with TestClient(app) as client:
        before = client.get(f'/api/works/{work_id}').json()
        response = client.patch(f'/api/works/{work_id}/links', json={
            'links': {'video': '  https://another.test/movie?a=1#part  ', 'es': ''}, 'expected_revision': 0})
        assert response.status_code == 200
        assert response.json()['links'] == {'patreon': 'https://example.test/patreon', 'video': 'https://another.test/movie?a=1#part',
                                             'es': '', 'script': 'https://example.test/script'}
        assert response.json()['links_revision'] == 1
        after = client.get(f'/api/works/{work_id}').json()
        for field in ('title', 'status', 'notes', 'metadata', 'tags', 'tags_revision', 'directories', 'assets'):
            assert after[field] == before[field]
        sibling = client.get(f"/api/works/{ids['S064_001']}/links").json()
        assert all(value == '' for value in sibling['links'].values())
        assert sibling['links_revision'] == 0
        response = client.patch(f'/api/works/{work_id}/links', json={'links': {'patreon': ''}, 'expected_revision': 1})
        assert response.status_code == 200
        assert response.json()['links']['es'] == ''
        assert response.json()['links']['video'] == 'https://another.test/movie?a=1#part'
        assert response.json()['links_revision'] == 2


def test_saved_links_survive_full_scan_rematch_and_restart(inventory):
    config, app, scanner, ids = inventory
    work_id = ids['S064']
    with TestClient(app) as client:
        saved = client.patch(f'/api/works/{work_id}/links', json={
            'links': {'patreon': 'https://new.test/post', 'script': ''}, 'expected_revision': 0}).json()
        scanner.scan()
        job = app.state.worker.enqueue_rematch(work_id)
        app.state.worker.perform(job['id'])
        assert app.state.store.job(job['id'])['status'] == 'completed'
        assert client.get(f'/api/works/{work_id}/links').json() == saved
    with TestClient(create_app(config, start_worker=False)) as restarted:
        assert restarted.get(f'/api/works/{work_id}/links').json() == saved
        detail = restarted.get(f'/api/works/{work_id}').json()
        assert (detail['title'], detail['status'], detail['notes']) == ('Manual title', 'published', 'Private notes')


def test_concurrent_revision_only_one_writer_succeeds(inventory):
    _, app, _, ids = inventory
    links = WorkLinks(app.state.store)
    barrier = threading.Barrier(2)

    def update(kind):
        barrier.wait(timeout=5)
        try:
            return links.update(ids['S064'], {kind: f'https://new.test/{kind}'}, 0)
        except WorkLinksError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ('es', 'video')))
    assert results.count(409) == 1
    winning = next(result for result in results if isinstance(result, dict))
    assert winning['links_revision'] == 1
    assert sum(winning['links'][kind] == f'https://new.test/{kind}' for kind in ('es', 'video')) == 1


@pytest.mark.parametrize('value', [
    'javascript:alert(1)', 'file:///tmp/file', 'https://', 'https://user@host.test/a',
    'https://user:pass@host.test/a', 'https://host.test:65536/', 'https://host.test:0/', 'https://host.test:/',
    'https://host.test:invalid/', 'https://bad host.test/', 'https://-bad.test/', 'https://bad..test/',
    'https://host.test/a\nb', 'https://host.test/\x00b', 'https://host.test/\x7f', 'https://host.test\\@other.test/',
    'https://host.test/\x80', 'https://host.test/\u200b',
])
def test_invalid_urls_are_rejected_without_mutation(inventory, value):
    _, app, _, ids = inventory
    with TestClient(app) as client:
        path = f"/api/works/{ids['S064']}/links"
        before = client.get(path).json()
        response = client.patch(path, json={'links': {'script': value}, 'expected_revision': 0})
        assert response.status_code == 422
        assert client.get(path).json() == before


@pytest.mark.parametrize('value', ['', 'https://www.patreon.com/posts/example-123', 'http://localhost:1234/file',
                                   'http://192.0.2.5:3000/video', 'https://[::1]:443/path', 'https://例子.测试/path',
                                   'https://example.test/a%20b?token=abc#fragment'])
def test_valid_url_domains_are_unrestricted(value):
    assert validate_link(value) == value


@pytest.mark.parametrize('payload', [
    {'links': {'unknown': 'https://example.test'}, 'expected_revision': 0},
    {'links': {'video': None}, 'expected_revision': 0},
    {'links': {'video': 123}, 'expected_revision': 0},
    {'links': {'video': 'x' * 4001}, 'expected_revision': 0},
    {'links': {}, 'expected_revision': 0},
    {'links': {'video': ''}, 'expected_revision': True},
    {'links': {'video': ''}, 'expected_revision': -1},
    {'links': {'video': ''}, 'expected_revision': 0, 'metadata': {}},
])
def test_request_schema_strict(inventory, payload):
    _, app, _, ids = inventory
    with TestClient(app) as client:
        response = client.patch(f"/api/works/{ids['S064']}/links", json=payload)
        assert response.status_code == 422


def test_unknown_work_and_stale_revision(inventory):
    _, app, _, ids = inventory
    with TestClient(app) as client:
        assert client.get('/api/works/999999/links').status_code == 404
        assert client.patch('/api/works/999999/links', json={'links': {'es': ''}, 'expected_revision': 0}).status_code == 404
        path = f"/api/works/{ids['S064']}/links"
        assert client.patch(path, json={'links': {'es': ''}, 'expected_revision': 0}).status_code == 200
        assert client.patch(path, json={'links': {'es': 'https://other.test'}, 'expected_revision': 0}).status_code == 409
        assert client.get(path).json()['links']['es'] == ''


def test_unsafe_historical_cells_are_not_exposed_as_links(inventory):
    _, app, _, ids = inventory
    with app.state.store.connection() as db:
        db.execute('UPDATE works SET metadata=? WHERE id=?', (json.dumps({'patreon_url': 'not available', 'es_url': 'javascript:alert(1)'}), ids['S064']))
    with TestClient(app) as client:
        assert set(client.get(f"/api/works/{ids['S064']}/links").json()['links'].values()) == {''}


def test_canonical_history_links_take_precedence(inventory):
    _, app, _, ids = inventory
    with app.state.store.connection() as db:
        metadata = {f'{kind}_url': f'https://canonical.test/{kind}' for kind in ('patreon', 'video', 'script', 'es')}
        metadata.update({'Patreon Post URL': 'https://legacy.test', 'ES Post URL': 'https://legacy.test',
                         'Video Link': 'https://legacy.test', 'Script Link': 'https://legacy.test'})
        db.execute('UPDATE works SET metadata=? WHERE id=?', (json.dumps(metadata), ids['S064']))
    with TestClient(app) as client:
        links = client.get(f"/api/works/{ids['S064']}/links").json()['links']
        assert links == {kind: f'https://canonical.test/{kind}' for kind in ('patreon', 'video', 'script', 'es')}


def test_bad_field_rejects_entire_patch(inventory):
    _, app, _, ids = inventory
    with TestClient(app) as client:
        path = f"/api/works/{ids['S064']}/links"
        before = client.get(path).json()
        assert client.patch(path, json={'links': {'es': 'https://valid.test/post', 'video': 'bad'}, 'expected_revision': 0}).status_code == 422
        assert client.get(path).json() == before
