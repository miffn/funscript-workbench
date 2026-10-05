import json
import pytest
from fastapi.testclient import TestClient

from backend.config import Config, Root
from backend.main import create_app
from backend.scanner import Scanner
from backend.store import Store


@pytest.fixture
def production(tmp_path):
    root = tmp_path / 'materials'
    root.mkdir()
    folder = root / 'S901'
    folder.mkdir()
    (folder / 'main.mp4').write_bytes(b'video')
    ready = root / 'S902'
    ready.mkdir()
    (ready / 'main.funscript').write_text('{"actions":[]}')
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, str(root), 'Materials'),), ffprobe='not-installed')
    store = Store(config.data_dir)
    scanner = Scanner(store, config)
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        yield config, store, scanner, client, folder


def items(client, status, **query):
    return client.get('/api/works', params={'status': status, **query}).json()


def test_missing_scripts_require_manual_completion_even_after_rescan_and_restart(production):
    config, store, scanner, client, folder = production
    make = items(client, 'to_make')
    assert make['total'] == make['stats']['to_make'] == 1
    assert [row['script_id'] for row in make['items']] == ['S901']
    assert [row['script_id'] for row in items(client, 'pending')['items']] == ['S902']
    work = make['items'][0]
    endpoint = f"/api/works/{work['id']}/production/confirm"
    assert client.post(endpoint, json={'expected_revision': work['production_revision']}).status_code == 422
    client.patch(f"/api/works/{work['id']}", json={'title': '人工标题', 'notes': '人工备注', 'es_published': True, 'es_published_date': '2026-10-01'})
    (folder / 'main.funscript').write_text('{"actions":[]}')
    scanner.scan(target_id='S901')
    assert items(client, 'to_make')['total'] == 1
    assert items(client, 'pending')['total'] == 1
    with TestClient(create_app(config, start_worker=False)) as restarted:
        waiting = restarted.get(f"/api/works/{work['id']}").json()
        assert waiting['production_required'] and waiting['script_count'] == 1
        confirmed = restarted.post(endpoint, json={'expected_revision': waiting['production_revision']})
        assert confirmed.status_code == 200
        confirmed = confirmed.json()
        assert not confirmed['production_required'] and confirmed['production_confirmed_at']
        assert confirmed['title'] == '人工标题' and confirmed['notes'] == '人工备注'
        assert confirmed['es_published'] and confirmed['es_published_date'] == '2026-10-01'
        assert items(restarted, 'to_make')['total'] == 0
        assert items(restarted, 'pending')['total'] == items(restarted, 'all')['stats']['pending'] == 2
        assert restarted.post(endpoint, json={'expected_revision': waiting['production_revision']}).status_code == 409
    # Removing scripts brings the work back to production. Adding them back
    # never silently reuses the earlier confirmation.
    (folder / 'main.funscript').unlink()
    scanner.scan()
    assert items(client, 'to_make')['total'] == 1
    assert client.get(f"/api/works/{work['id']}").json()['production_confirmed_at'] is None


def test_filters_counts_and_pagination_use_identical_production_rules(production):
    _, store, _, client, _ = production
    make = items(client, 'to_make')
    assert items(client, 'to_make', q='S902')['total'] == 0
    assert items(client, 'to_make', q='S901', page_size=1)['total'] == 1
    assert items(client, 'to_make', page=2, page_size=1)['items'] == []
    tag = client.post('/api/tags', json={'category': 'custom', 'name': '待编写'}).json()
    work = make['items'][0]
    client.put(f"/api/works/{work['id']}/tags", json={'tag_ids': [tag['id']], 'expected_revision': work['tags_revision']})
    assert items(client, 'to_make', tag_id=tag['id'])['total'] == 1
    assert items(client, 'pending', tag_id=tag['id'])['total'] == 0
    # Publication records remain visible even when local scripts are absent.
    client.patch(f"/api/works/{work['id']}", json={'es_published': True, 'patreon_published': True})
    assert items(client, 'published')['total'] == 1
    assert items(client, 'to_make')['total'] == 1


def test_confirmation_blocks_inaccessible_scripts_and_running_tasks(production):
    _, store, scanner, client, folder = production
    (folder / 'main.funscript').write_text('{"actions":[]}')
    scanner.scan()
    work = items(client, 'to_make')['items'][0]
    endpoint = f"/api/works/{work['id']}/production/confirm"
    (folder / 'main.funscript').unlink()
    assert client.post(endpoint, json={'expected_revision': work['production_revision']}).status_code == 422
    (folder / 'main.funscript').write_text('{"actions":[]}')
    with store.connection() as db:
        job_id = db.execute("INSERT INTO jobs(type,status,trigger,created_at,inputs) VALUES('rematch','running','manual','2026-10-05',?)", (json.dumps({'work_id': work['id']}),)).lastrowid
    assert client.post(endpoint, json={'expected_revision': work['production_revision']}).status_code == 409
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job_id,))
    for body in ({'expected_revision': True}, {'expected_revision': 0, 'force': True}):
        assert client.post(endpoint, json=body).status_code == 422
    assert client.post(endpoint, headers={'Origin': 'http://other.example'}, json={'expected_revision': work['production_revision']}).status_code == 403


def test_historical_scripts_do_not_make_a_moved_work_ready(production):
    config, _, scanner, client, folder = production
    (folder / 'main.funscript').write_text('{"actions":[]}')
    scanner.scan()
    work = items(client, 'to_make')['items'][0]
    client.post(f"/api/works/{work['id']}/production/confirm", json={'expected_revision': work['production_revision']})
    archive = config.roots[0].path.parent / 'archive'
    archive.mkdir()
    new_folder = archive / 'S901'
    new_folder.mkdir()
    (new_folder / 'main.mp4').write_bytes(b'video')
    folder.rename(config.roots[0].path / 'old-untracked')
    scanner.scan((*config.roots, Root(archive, str(archive), 'Archive')))
    assert items(client, 'to_make')['total'] == 1
    assert all(row['script_id'] != 'S901' for row in items(client, 'pending')['items'])


def test_existing_scriptless_inventory_is_initialized_only_once(production):
    config, store, _, client, folder = production
    work = items(client, 'to_make')['items'][0]
    with store.connection() as db:
        db.execute("DELETE FROM settings WHERE key='production_confirmation_initialized'")
        db.execute('UPDATE works SET production_required=0 WHERE id=?', (work['id'],))
    Store(config.data_dir)
    assert client.get(f"/api/works/{work['id']}").json()['production_required']
    # Ordinary restarts do not reclassify anything from a historical folder.
    before = client.get(f"/api/works/{work['id']}").json()['production_revision']
    Store(config.data_dir)
    assert client.get(f"/api/works/{work['id']}").json()['production_revision'] == before
