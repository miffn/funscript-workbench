import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.main import create_app
from backend.scanner import Scanner
from backend.store import Store, now


@pytest.fixture
def inventory(tmp_path):
    root = tmp_path / 'inventory'
    root.mkdir()
    for number in range(1, 5):
        (root / f'S{number:03d}').mkdir()
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, r'E:\material', 'Inventory'),),
                    preview_output_root=tmp_path / 'previews')
    app = create_app(config, start_worker=False)
    scanner = Scanner(app.state.store, config)
    scanner.scan()
    with TestClient(app) as client:
        yield config, app, scanner, client


def test_independent_platforms_four_combinations_and_filters(inventory):
    _, _, _, client = inventory
    ids = {work['script_id']: work['id'] for work in client.get('/api/works').json()['items']}
    combinations = [(False, False), (True, False), (False, True), (True, True)]
    for number, (es, patreon) in enumerate(combinations, 1):
        response = client.patch(f'/api/works/{ids[f"S{number:03d}"]}', json={'es_published': es, 'patreon_published': patreon})
        assert response.status_code == 200
        result = response.json()
        assert result['es_published'] is es and result['patreon_published'] is patreon
        assert result['status'] == ('published' if es and patreon else 'pending')
    expected = {'all': 4, 'pending': 3, 'published': 1, 'es_published': 2, 'patreon_published': 2}
    for status, count in expected.items():
        result = client.get('/api/works', params={'status': status}).json()
        assert result['total'] == count
        assert result['stats']['pending'] == 3
        assert result['stats']['published'] == 1
        assert result['stats']['es_published'] == result['stats']['patreon_published'] == 2
    endpoint = f'/api/works/{ids["S004"]}'
    result = client.patch(endpoint, json={'es_published': False}).json()
    assert result['es_published'] is False and result['patreon_published'] is True
    result = client.patch(endpoint, json={'patreon_published': False}).json()
    assert result['es_published'] is False and result['patreon_published'] is False


@pytest.mark.parametrize('field', ['es_published', 'patreon_published'])
@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_platform_flags_require_real_booleans(inventory, field, value):
    _, _, _, client = inventory
    work = client.get('/api/works').json()['items'][0]
    response = client.patch(f'/api/works/{work["id"]}', json={field: value, 'notes': 'must remain atomic'})
    assert response.status_code == 422
    assert client.get(f'/api/works/{work["id"]}').json() == work | {'assets': []}


def test_legacy_status_mapping_and_conflicting_payload_is_atomic(inventory):
    _, app, _, client = inventory
    work = client.get('/api/works').json()['items'][0]
    endpoint = f'/api/works/{work["id"]}'
    for status in ('published', 'pending'):
        result = client.patch(endpoint, json={'status': status}).json()
        assert result['es_published'] is (status == 'published')
        assert result['patreon_published'] is (status == 'published')
    before = client.get(endpoint).json()
    for flag in ('es_published', 'patreon_published'):
        assert client.patch(endpoint, json={'status': 'published', flag: True, 'notes': 'no write'}).status_code == 422
        assert client.get(endpoint).json() == before
    with app.state.store.connection() as db:
        row = db.execute('SELECT * FROM works WHERE id=?', (work['id'],)).fetchone()
        assert row['status'] == 'pending' and row['es_published'] == row['patreon_published'] == 0


def test_legacy_sqlite_migration_links_and_restart_are_one_time(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    db = sqlite3.connect(data / 'workbench.sqlite3')
    db.executescript('''CREATE TABLE works (
        id INTEGER PRIMARY KEY,script_id TEXT NOT NULL UNIQUE,title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',notes TEXT NOT NULL DEFAULT '',
        metadata TEXT NOT NULL DEFAULT '{}',manual_fields TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE work_links(work_id INTEGER PRIMARY KEY,overrides TEXT NOT NULL DEFAULT '{}',revision INTEGER NOT NULL DEFAULT 0);
    ''')
    timestamp = now()
    for number, status, metadata in [(1, 'published', {}), (2, 'pending', {}),
                                     (3, 'pending', {'ES Link': 'https://example.test/post'}),
                                     (4, 'pending', {'ES Link': 'invalid historical note'}),
                                     (5, 'pending', {'ES Link': 'https://example.test/cleared'})]:
        db.execute('INSERT INTO works(id,script_id,title,status,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                   (number, f'S{number:03d}', 'Original title', status, json.dumps(metadata), timestamp, timestamp))
    db.execute('INSERT INTO work_links(work_id,overrides,revision) VALUES(5,?,7)', (json.dumps({'es': ''}),))
    db.commit()
    db.close()
    store = Store(data)
    with store.connection() as db:
        rows = db.execute('SELECT * FROM works ORDER BY id').fetchall()
        assert [(row['es_published'], row['patreon_published']) for row in rows] == [(1, 1), (0, 0), (1, 0), (0, 0), (0, 0)]
        assert all(row['updated_at'] == timestamp and row['title'] == 'Original title' for row in rows)
        assert all(row['es_published_date'] is row['patreon_published_date'] is None for row in rows)
        db.execute("UPDATE works SET es_published=0,patreon_published=1,status='pending' WHERE id=1")
        db.execute('UPDATE works SET es_published=0 WHERE id=3')
    restarted = Store(data)
    with restarted.connection() as db:
        assert tuple(db.execute('SELECT es_published,patreon_published FROM works WHERE id=1').fetchone()) == (0, 1)
        assert db.execute('SELECT es_published FROM works WHERE id=3').fetchone()[0] == 0
        assert db.execute('SELECT revision FROM work_links WHERE work_id=5').fetchone()[0] == 7
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('UPDATE works SET es_published=2 WHERE id=1')


def test_history_import_scanning_and_restart_preserve_manual_platform(inventory):
    config, app, scanner, client = inventory
    works = {row['script_id']: row for row in client.get('/api/works').json()['items']}
    endpoint = f'/api/works/{works["S001"]["id"]}'
    client.patch(endpoint, json={'es_published': False, 'notes': 'kept'})
    imports = config.data_dir / 'import'
    imports.mkdir()
    (imports / 'monthly-release-plan.json').write_text(json.dumps({'rows': [
        {'Script ID': 'S001', 'Status': 'Published'}, {'Script ID': 'S002', 'Status': 'Published'},
        {'Script ID': 'S003', 'ES Link': 'https://example.test/es'},
        {'Script ID': 'S005', 'Status': 'Published'}, {'Script ID': 'S006', 'Status': 'Pending'}]}))
    (config.roots[0].path / 'S005').mkdir()
    (config.roots[0].path / 'S006').mkdir()
    scanner.scan()
    rows = {row['script_id']: row for row in client.get('/api/works').json()['items']}
    assert rows['S001']['es_published'] is False and rows['S001']['patreon_published'] is True
    assert rows['S001']['notes'] == 'kept'
    assert rows['S002']['es_published'] is rows['S002']['patreon_published'] is True
    assert rows['S003']['es_published'] is True and rows['S003']['patreon_published'] is False
    assert rows['S005']['es_published'] is rows['S005']['patreon_published'] is True
    assert rows['S006']['es_published'] is rows['S006']['patreon_published'] is False
    client.patch(endpoint, json={'es_published': True, 'patreon_published': False})
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as restarted:
        row = restarted.get(endpoint).json()
        assert row['es_published'] is True and row['patreon_published'] is False
        assert row['status'] == 'pending' and row['notes'] == 'kept'


def test_es_link_marks_only_es_published_and_clear_does_not_revert(inventory):
    _, _, scanner, client = inventory
    work = client.get('/api/works').json()['items'][0]
    endpoint = f'/api/works/{work["id"]}'
    assert client.patch(endpoint + '/links', json={'links': {'es': 'https://example.test/post'}, 'expected_revision': 0}).status_code == 200
    updated = client.get(endpoint).json()
    assert updated['es_published'] is True and updated['patreon_published'] is False
    assert updated['status'] == 'pending'
    assert client.patch(endpoint + '/links', json={'links': {'es': ''}, 'expected_revision': 1}).status_code == 200
    assert client.get(endpoint).json()['es_published'] is True
    client.patch(endpoint, json={'es_published': False})
    scanner.scan()
    assert client.get(endpoint).json()['es_published'] is False
    assert client.patch(endpoint + '/links', json={'links': {'patreon': 'https://example.test/patreon'}, 'expected_revision': 2}).status_code == 200
    assert client.get(endpoint).json()['patreon_published'] is False


def test_failed_es_link_update_cannot_mark_publication(inventory):
    _, _, _, client = inventory
    work = client.get('/api/works').json()['items'][0]
    endpoint = f'/api/works/{work["id"]}'
    assert client.patch(endpoint + '/links', json={'links': {'es': 'not-a-url'}, 'expected_revision': 0}).status_code == 422
    assert client.patch(endpoint + '/links', json={'links': {'es': 'https://example.test/post'}, 'expected_revision': 99}).status_code == 409
    updated = client.get(endpoint).json()
    assert updated['es_published'] is updated['patreon_published'] is False
    assert updated['links']['es'] == '' and updated['links_revision'] == 0


def test_concurrent_platform_updates_preserve_both_publication_decisions(inventory):
    _, app, _, client = inventory
    work = client.get('/api/works').json()['items'][0]
    endpoint = f'/api/works/{work["id"]}'
    barrier = Barrier(2)

    def publish(field):
        barrier.wait(timeout=5)
        return client.patch(endpoint, json={field: True})

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, ['es_published', 'patreon_published']))
    assert all(response.status_code == 200 for response in results)
    with app.state.store.connection() as db:
        saved = db.execute('SELECT * FROM works WHERE id=?', (work['id'],)).fetchone()
        assert saved['es_published'] == saved['patreon_published'] == 1
        assert saved['status'] == 'published'
        assert {'es_published', 'patreon_published'} <= set(json.loads(saved['manual_fields']))
