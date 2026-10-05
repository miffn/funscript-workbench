import json

from fastapi.testclient import TestClient
import pytest

from backend.config import Config
from backend.main import create_app
from backend.store import now


@pytest.fixture
def inventory(tmp_path):
    config = Config(data_dir=tmp_path / 'data', roots=(), preview_output_root=tmp_path / 'previews')
    app = create_app(config, start_worker=False)
    with app.state.store.connection() as db:
        for script_id in ('S025', 'S025_001', 'S069'):
            db.execute('INSERT INTO works(script_id,title,notes,created_at,updated_at) VALUES(?,?,?,?,?)',
                       (script_id, f'Title {script_id}', 'private notes', now(), now()))
    with TestClient(app) as client:
        yield app, client, config


def state(client, script_id='S069'):
    return next(work for work in client.get('/api/release-calendar?month=2026-10').json()['works']
                if work['script_id'] == script_id)


def update(client, work=None, platforms=None, mode='actual', day='2026-10-03'):
    work = work or state(client)
    return client.post('/api/release-calendar', json={'work_id': work['id'],
        'platforms': platforms or ['es'], 'mode': mode, 'date': day, 'expected_revision': work['revision']})


def test_actual_independent_states_and_detail_share_data(inventory):
    _, client, _ = inventory
    result = update(client).json()
    assert result['work']['es_published_date'] == '2026-10-03'
    assert result['work']['es_published'] is True
    assert result['work']['patreon_published'] is False
    detail = client.get(f'/api/works/{result["work"]["id"]}').json()
    assert detail['es_published_date'] == result['work']['es_published_date']
    assert detail['notes'] == 'private notes'
    assert detail['title'] == 'Title S069'
    second = update(client, platforms=['patreon'], day='2026-10-04').json()['work']
    assert second['es_published_date'] == '2026-10-03'
    assert second['patreon_published_date'] == '2026-10-04'
    assert client.get(f'/api/works/{second["id"]}').json()['status'] == 'published'


def test_plans_preserve_actual_and_both_platforms(inventory):
    _, client, _ = inventory
    planned = update(client, platforms=['es', 'patreon'], mode='planned').json()['work']
    assert planned['es_planned_date'] == planned['patreon_planned_date'] == '2026-10-03'
    assert planned['es_published_date'] is planned['patreon_published_date'] is None
    assert not planned['es_published'] and not planned['patreon_published']
    actual = update(client, platforms=['es', 'patreon'], day='2026-10-04').json()['work']
    assert actual['es_planned_date'] == '2026-10-03'
    assert actual['es_published_date'] == actual['patreon_published_date'] == '2026-10-04'
    assert actual['es_published'] and actual['patreon_published']


def test_grid_neighbor_dates_full_ids_and_private_exclusion(inventory, monkeypatch):
    _, client, _ = inventory
    monkeypatch.setattr('backend.release_calendar.release_today', lambda: '2026-10-03')
    update(client, state(client, 'S025_001'), day='2026-09-28')
    update(client, state(client, 'S025'), mode='planned', day='2026-11-01')
    result = client.get('/api/release-calendar?month=2026-10').json()
    assert result['today'] == '2026-10-03'
    assert [work['script_id'] for work in result['works']] == ['S069', 'S025_001', 'S025']
    assert {(event['script_id'], event['date'], event['mode']) for event in result['events']} == {
        ('S025_001', '2026-09-28', 'actual'), ('S025', '2026-11-01', 'planned')}
    assert all('notes' not in work and 'metadata' not in work and 'links' not in work for work in result['works'])
    assert len({event['key'] for event in result['events']}) == len(result['events'])
    assert client.get('/api/release-calendar?month=2026-08').json()['events'] == []


def test_removal_keeps_state_and_links_and_actual_remains(inventory):
    _, client, _ = inventory
    work = state(client)
    path = f'/api/works/{work["id"]}'
    client.patch(path + '/links', json={'expected_revision': 0, 'links': {'es': 'https://example.test/es'}})
    update(client, mode='planned')
    result = update(client, day=None).json()['work']
    assert result['es_published_date'] is None and result['es_published']
    assert result['es_planned_date'] == '2026-10-03'
    assert client.get(path + '/links').json()['links']['es'] == 'https://example.test/es'
    update(client, day='2026-10-04')
    result = update(client, mode='planned', day=None).json()['work']
    assert result['es_planned_date'] is None and result['es_published_date'] == '2026-10-04'


def test_undo_persisted_restores_initial_and_preserves_unrelated_edits(inventory):
    app, client, config = inventory
    work = state(client)
    response = update(client, platforms=['es', 'patreon']).json()
    operation_id = response['operation']['id']
    path = f'/api/works/{work["id"]}'
    assert client.patch(path, json={'title': 'Edited title', 'notes': 'Edited notes'}).status_code == 200
    with TestClient(create_app(config, start_worker=False)) as restarted:
        current = state(restarted)
        assert current['es_published_date'] == '2026-10-03'
        result = restarted.post(f'/api/release-calendar/operations/{operation_id}/undo', json={})
        assert result.status_code == 200
        restored = result.json()['work']
        assert restored['es_published_date'] is restored['patreon_published_date'] is None
        assert not restored['es_published'] and not restored['patreon_published']
        assert restored['title'] == 'Edited title'
        detail = restarted.get(path).json()
        assert detail['notes'] == 'Edited notes'
        assert result.json()['operation']['undone']
        assert restarted.post(f'/api/release-calendar/operations/{operation_id}/undo', json={}).status_code == 409
    with app.state.store.connection() as db:
        manual = set(json.loads(db.execute('SELECT manual_fields FROM works WHERE id=?', (work['id'],)).fetchone()[0]))
        assert 'es_published_date' not in manual and {'title', 'notes'} <= manual


@pytest.mark.parametrize('change', ['date', 'state', 'link', 'plan'])
def test_updates_and_undo_reject_after_external_changes(inventory, change):
    _, client, _ = inventory
    old = state(client)
    result = update(client).json()
    path = f'/api/works/{old["id"]}'
    if change == 'date':
        client.patch(path, json={'es_published_date': '2026-10-05'})
    elif change == 'state':
        client.patch(path, json={'patreon_published': True})
    elif change == 'link':
        revision = client.get(path + '/links').json()['links_revision']
        client.patch(path + '/links', json={'expected_revision': revision, 'links': {'video': 'https://example.test/video'}})
    else:
        update(client, mode='planned', day='2026-10-06')
    before = state(client)
    assert update(client, work=old).status_code == 409
    assert client.post(f'/api/release-calendar/operations/{result["operation"]["id"]}/undo', json={}).status_code == 409
    assert state(client) == before


def test_calendar_invalidates_old_links_editor_and_plan_undo(inventory):
    _, client, _ = inventory
    work = state(client)
    result = update(client, mode='planned').json()
    assert client.post(f'/api/release-calendar/operations/{result["operation"]["id"]}/undo', json={}).json()['work']['es_planned_date'] is None
    update(client)
    response = client.patch(f'/api/works/{work["id"]}/links', json={
        'expected_revision': 0, 'es_published_date': '2026-10-10'})
    assert response.status_code == 409
    assert state(client)['es_published_date'] == '2026-10-03'


def test_link_auto_dates_conflict_with_open_calendar(inventory):
    _, client, _ = inventory
    old = state(client)
    assert client.patch(f'/api/works/{old["id"]}/links', json={
        'expected_revision': 0, 'links': {'es': 'https://example.test/new-post'}}).status_code == 200
    after_link = state(client)
    assert after_link['es_published'] and after_link['es_published_date']
    assert update(client, work=old).status_code == 409
    assert state(client) == after_link


def test_undo_restores_different_initial_platform_states_and_dates(inventory):
    _, client, _ = inventory
    work = state(client)
    client.patch(f'/api/works/{work["id"]}', json={'es_published': True, 'es_published_date': '2026-09-20'})
    before = state(client)
    response = update(client, platforms=['es', 'patreon']).json()
    result = client.post(f'/api/release-calendar/operations/{response["operation"]["id"]}/undo', json={}).json()['work']
    for field in ('es_published', 'patreon_published', 'es_published_date', 'patreon_published_date'):
        assert result[field] == before[field]


def test_actual_dates_do_not_claim_published_when_existing_status_false(inventory):
    _, client, _ = inventory
    work = state(client)
    client.patch(f'/api/works/{work["id"]}', json={'patreon_published_date': '2026-10-03'})
    calendar = client.get('/api/release-calendar?month=2026-10').json()
    assert not calendar['events']
    saved = next(item for item in calendar['works'] if item['id'] == work['id'])
    assert saved['patreon_published_date'] == '2026-10-03' and saved['patreon_published'] is False


def test_two_platform_transaction_rolls_back_as_unit(inventory):
    app, client, _ = inventory
    before = state(client)
    with app.state.store.connection() as db:
        db.execute("CREATE TRIGGER reject_patreon BEFORE UPDATE OF patreon_published_date ON works BEGIN SELECT RAISE(ABORT,'test rollback'); END")
    with pytest.raises(Exception, match='test rollback'):
        update(client, platforms=['es', 'patreon'])
    assert state(client) == before
    with app.state.store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM release_calendar_operations').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM release_calendar_plans').fetchone()[0] == 0


@pytest.mark.parametrize('day', ['2026-02-30', '2026-2-01', '', False, 20261003, '2026-10-03T00:00:00Z'])
def test_invalid_dates_are_rejected_without_mutation(inventory, day):
    _, client, _ = inventory
    before = state(client)
    assert update(client, day=day).status_code == 422
    assert state(client) == before


@pytest.mark.parametrize('change', [{'work_id': True}, {'work_id': '1'}, {'platforms': ['es', 'es']},
                                    {'platforms': []}, {'mode': 'published'}, {'extra': 1}])
def test_invalid_write_models(inventory, change):
    _, client, _ = inventory
    work = state(client)
    payload = {'work_id': work['id'], 'platforms': ['es'], 'mode': 'actual', 'date': '2026-10-03', 'expected_revision': work['revision'], **change}
    assert client.post('/api/release-calendar', json=payload).status_code == 422
    assert state(client) == work


@pytest.mark.parametrize('month', ['2026-2', '2026-13', '0000-01', 'bad'])
def test_invalid_month(inventory, month):
    _, client, _ = inventory
    assert client.get('/api/release-calendar', params={'month': month}).status_code == 422


def test_same_origin_and_unknown_work_or_operation(inventory):
    _, client, _ = inventory
    work = state(client)
    payload = {'work_id': work['id'], 'platforms': ['es'], 'mode': 'actual', 'date': '2026-10-03', 'expected_revision': work['revision']}
    assert client.post('/api/release-calendar', json=payload, headers={'Origin': 'https://evil.test'}).status_code == 403
    assert client.post('/api/release-calendar/operations/1/undo', json={}, headers={'Origin': 'http://evil.test'}).status_code == 403
    assert client.post('/api/release-calendar', json={**payload, 'work_id': 99999}).status_code == 404
    assert client.post('/api/release-calendar/operations/99999/undo', json={}).status_code == 404
    assert client.post('/api/release-calendar/operations/-1/undo', json={}).status_code == 422
    assert client.post('/api/release-calendar', json=payload, headers={'Origin': 'http://testserver'}).status_code == 200
