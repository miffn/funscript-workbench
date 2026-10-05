"""Stable identities cover unnumbered inventory without broadening MCP permissions."""
import json

import pytest

from backend.tests.test_mcp_server import mcp_client, call, data, rpc


def unnumbered(app):
    with app.state.store.connection() as db:
        work_id = db.execute("SELECT id FROM works WHERE script_id='S025_001'").fetchone()[0]
        db.execute('UPDATE works SET script_id=NULL WHERE id=?', (work_id,))
    return work_id


def snapshot(app):
    with app.state.store.connection() as db:
        return '\n'.join(db.iterdump())


def test_read_unnumbered_work_and_preview_by_internal_id_is_read_only(mcp_client):
    client, app = mcp_client
    work_id = unnumbered(app)
    before = snapshot(app)
    item = data(call(client, 'workbench_get_work', {'work_id': work_id}))
    assert item['id'] == work_id and item['script_id'] is None
    preview = data(call(client, 'workbench_get_preview', {'work_id': work_id}))
    assert preview['work_id'] == work_id and preview['script_id'] is None
    assert preview['preview']['preview_key'] == f'work-{work_id}'
    assert preview['preview']['files'] == []
    assert data(call(client, 'workbench_get_overview'))['stats']['total'] == 3
    assert any(item['script_id'] is None for item in data(call(client, 'workbench_list_works'))['items'])
    post = call(client, 'workbench_get_post_materials', {'work_id': work_id})
    assert post['isError'] and 'require a full script ID' in json.dumps(post)
    assert snapshot(app) == before


@pytest.mark.parametrize('tool', ['workbench_get_work', 'workbench_get_preview', 'workbench_get_post_materials'])
@pytest.mark.parametrize('identity', [{}, {'work_id': 1, 'script_id': 'S025'}, {'work_id': True},
                                     {'work_id': '1'}, {'work_id': 0}, {'work_id': -1}, {'work_id': 99999}])
def test_work_resolver_rejects_ambiguous_invalid_or_missing_identity_without_writes(mcp_client, tool, identity):
    client, app = mcp_client
    before = snapshot(app)
    assert call(client, tool, identity).get('isError')
    assert snapshot(app) == before


def test_content_and_calendar_edits_can_resolve_an_unnumbered_work(mcp_client):
    client, app = mcp_client
    work_id = unnumbered(app)
    item = data(call(client, 'workbench_get_work', {'work_id': work_id}))
    changed = data(call(client, 'workbench_update_work', {'work_id': work_id, 'edit': {
        'expected_revision': item['data_revision'], 'notes': 'User maintained note'}}))
    assert changed['id'] == work_id and changed['notes'] == 'User maintained note'
    tag = data(call(client, 'workbench_list_tags'))['items'][0]
    tagged = data(call(client, 'workbench_set_work_tags', {'work_id': work_id, 'edit': {
        'expected_revision': changed['tags_revision'], 'tag_ids': [tag['id']]}}))
    assert tagged['tags'][0]['id'] == tag['id']
    linked = data(call(client, 'workbench_update_work_links', {'work_id': work_id, 'edit': {
        'expected_revision': changed['links_revision'], 'links': {'video': 'https://example.com/video'}}}))
    assert linked['links']['video'] == 'https://example.com/video'
    calendar = data(call(client, 'workbench_get_release_calendar', {'month': '2026-10'}))
    state = next(work for work in calendar['works'] if work['id'] == work_id)
    planned = data(call(client, 'workbench_update_release_calendar', {'work_id': work_id, 'edit': {
        'platforms': ['es'], 'mode': 'planned', 'date': '2026-10-12', 'expected_revision': state['revision']}}))
    assert planned['work']['id'] == work_id and planned['work']['script_id'] is None
    assert planned['work']['es_planned_date'] == '2026-10-12'
    data(call(client, 'workbench_update_release_calendar', {'work_id': work_id, 'edit': {
        'platforms': ['es'], 'mode': 'actual', 'date': '2026-10-03', 'expected_revision': planned['work']['revision']}}))
    same_day = data(call(client, 'workbench_get_release_calendar', {'month': '2026-10'}))
    assert any(event['script_id'] is None and event['date'] == '2026-10-03' for event in same_day['events'])
    assert client.get(f'/api/works/{work_id}').json()['notes'] == 'User maintained note'


def test_write_tools_reject_both_identity_fields_and_cannot_override_calendar_work(mcp_client):
    client, app = mcp_client
    item = data(call(client, 'workbench_get_work', {'script_id': 'S025_001'}))
    calendar = data(call(client, 'workbench_get_release_calendar', {'month': '2026-10'}))
    state = next(work for work in calendar['works'] if work['id'] == item['id'])
    edits = {
        'workbench_update_work': {'expected_revision': item['data_revision'], 'notes': 'Should not persist'},
        'workbench_set_work_tags': {'expected_revision': item['tags_revision'], 'tag_ids': []},
        'workbench_update_work_links': {'expected_revision': item['links_revision'], 'links': {'video': 'https://example.com/new'}},
        'workbench_update_release_calendar': {'platforms': ['es'], 'mode': 'planned', 'date': '2026-10-12', 'expected_revision': state['revision']},
    }
    before = snapshot(app)
    for tool, edit in edits.items():
        assert call(client, tool, {'work_id': item['id'], 'script_id': item['script_id'], 'edit': edit})['isError']
        assert call(client, tool, {'edit': edit})['isError']
    invalid_calendar = {**edits['workbench_update_release_calendar'], 'work_id': item['id'] + 1}
    assert call(client, 'workbench_update_release_calendar', {'work_id': item['id'], 'edit': invalid_calendar})['isError']
    assert snapshot(app) == before
    tools = rpc(client, 'tools/list')['result']['tools']
    assert len(tools) == 18
    assert not any(any(word in tool['name'] for word in ('generate', 'scan', 'rematch', 'production', 'open_folder', 'token')) for tool in tools)
