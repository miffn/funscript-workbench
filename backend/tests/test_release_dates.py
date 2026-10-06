from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from backend import release_dates, work_links
from backend.config import Config, Root
from backend.main import create_app
from backend.scanner import Scanner


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    root = tmp_path / 'inventory'
    (root / 'S069').mkdir(parents=True)
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, r'D:\inventory', 'Inventory'),),
                    preview_output_root=tmp_path / 'previews')
    app = create_app(config, start_worker=False)
    scanner = Scanner(app.state.store, config)
    scanner.scan()
    monkeypatch.setattr(work_links, 'release_today', lambda: '2026-10-01')
    with TestClient(app) as client:
        work = client.get('/api/works').json()['items'][0]
        yield config, app, scanner, client, f'/api/works/{work["id"]}'


def test_first_changed_post_links_get_independent_dates_and_states(inventory):
    _, _, _, client, path = inventory
    for kind, revision in [('patreon', 0), ('es', 1)]:
        result = client.patch(path + '/links', json={
            'links': {kind: f'https://example.test/{kind}'}, 'expected_revision': revision})
        assert result.status_code == 200
        assert result.json()[f'{kind}_published_date'] == '2026-10-01'
        if kind == 'patreon':
            assert result.json()['es_published_date'] is None
            assert client.get(path).json()['patreon_published'] is True
    work = client.get(path).json()
    listed = client.get('/api/works').json()['items'][0]
    assert work['es_published'] is True and work['patreon_published'] is True
    assert work['es_published_date'] == work['patreon_published_date'] == '2026-10-01'
    assert listed['es_published_date'] == listed['patreon_published_date'] == '2026-10-01'


def test_manual_dates_preserved_when_replacing_or_clearing_links(inventory):
    _, _, _, client, path = inventory
    result = client.patch(path + '/links', json={'expected_revision': 0,
        'links': {'es': 'https://example.test/1', 'patreon': 'https://example.test/1'},
        'es_published_date': '2026-09-28', 'patreon_published_date': '2026-09-27'})
    assert result.status_code == 200
    for revision, value in [(1, 'https://example.test/2'), (2, '')]:
        result = client.patch(path + '/links', json={
            'expected_revision': revision, 'links': {'es': value, 'patreon': value}})
        assert result.status_code == 200
        assert result.json()['es_published_date'] == '2026-09-28'
        assert result.json()['patreon_published_date'] == '2026-09-27'
    work = client.get(path).json()
    assert work['es_published'] is True and work['patreon_published'] is True


def test_explicit_date_clear_wins_and_repeat_save_does_not_refill(inventory):
    _, _, _, client, path = inventory
    result = client.patch(path + '/links', json={'expected_revision': 0,
        'links': {'es': 'https://example.test/first', 'patreon': 'https://example.test/first'},
        'es_published_date': '', 'patreon_published_date': None})
    assert result.status_code == 200
    assert result.json()['es_published_date'] is result.json()['patreon_published_date'] is None
    repeated = client.patch(path + '/links', json={'expected_revision': 1,
        'links': {'es': ' https://example.test/first ', 'patreon': 'https://example.test/first'}})
    assert repeated.status_code == 200
    assert repeated.json()['es_published_date'] is repeated.json()['patreon_published_date'] is None
    changed = client.patch(path + '/links', json={'expected_revision': 2,
        'links': {'es': 'https://example.test/changed'}}).json()
    # An already published URL edit preserves a deliberately cleared date too.
    assert changed['es_published_date'] is None
    assert changed['patreon_published_date'] is None


def test_other_links_preserve_unknown_dates_and_marking_published_defaults_today(inventory):
    _, _, _, client, path = inventory
    assert client.patch(path + '/links', json={'expected_revision': 0,
        'links': {'video': 'https://example.test/movie', 'script': 'https://example.test/download'}}).status_code == 200
    assert client.get(path).json()['es_published_date'] is client.get(path).json()['patreon_published_date'] is None
    client.patch(path, json={'es_published': True, 'patreon_published': True})
    work = client.get(path).json()
    from backend.release_dates import release_today
    assert work['es_published_date'] == work['patreon_published_date'] == release_today()


def test_date_only_edits_persist_across_scan_rematch_restart(inventory):
    config, app, scanner, client, path = inventory
    result = client.patch(path + '/links', json={'expected_revision': 0,
        'es_published_date': '2024-02-29', 'patreon_published_date': '2026-09-01'})
    assert result.status_code == 200
    before = result.json()
    scanner.scan()
    work_id = before['work_id']
    job = app.state.worker.enqueue_rematch(work_id)
    app.state.worker.perform(job['id'])
    assert client.get(path + '/links').json() == before
    with TestClient(create_app(config, start_worker=False)) as restarted:
        assert restarted.get(path + '/links').json() == before
        assert restarted.get(path).json()['es_published_date'] == '2024-02-29'
    result = client.patch(path, json={'es_published_date': None, 'patreon_published_date': ''})
    assert result.status_code == 200
    assert result.json()['es_published_date'] is result.json()['patreon_published_date'] is None
    assert result.json()['links_revision'] == 2
    # A links form opened before the independent work edit cannot overwrite its dates.
    assert client.patch(path + '/links', json={'expected_revision': 1,
        'es_published_date': '2026-09-29'}).status_code == 409
    assert client.get(path).json()['es_published_date'] is None


@pytest.mark.parametrize('field', ['es_published_date', 'patreon_published_date'])
@pytest.mark.parametrize('value', ['2026-2-01', '2026-02-30', '2025-02-29', '0000-01-01',
                                  '2026-10-01T12:00:00Z', ' 2026-10-01 ', 20261001, False, [], {}])
@pytest.mark.parametrize('links_route', [False, True])
def test_invalid_date_rejects_entire_write(inventory, field, value, links_route):
    _, _, _, client, path = inventory
    before = client.get(path).json()
    payload = {field: value}
    if links_route:
        payload.update({'expected_revision': 0, 'links': {'es': 'https://example.test/post'}})
    else:
        payload['notes'] = 'must not change'
    response = client.patch(path + ('/links' if links_route else ''), json=payload)
    assert response.status_code == 422
    assert client.get(path).json() == before


def test_default_calendar_day_uses_shanghai_not_server_timezone(monkeypatch):
    class FrozenDateTime:
        @staticmethod
        def now(tz):
            return datetime(2026, 9, 30, 16, 15, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(release_dates, 'datetime', FrozenDateTime)
    assert release_dates.release_today() == '2026-10-01'
