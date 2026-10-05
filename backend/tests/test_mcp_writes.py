"""Real authenticated MCP maintenance against disposable shared API/SQLite state."""
from concurrent.futures import ThreadPoolExecutor
import asyncio
import base64
from io import BytesIO
import json

from PIL import Image
import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.config import Config
from backend.mcp_auth import MCPAuth
from backend.store import now

from backend.tests.test_mcp_server import mcp_client, call, data, rpc, HEADERS


def read(client, script_id='S025_001'):
    return data(call(client, 'workbench_get_work', {'script_id': script_id}))


def text(result):
    return '\n'.join(content.get('text', '') for content in result.get('content', []))


def snapshot(app):
    with app.state.store.connection() as db:
        return '\n'.join(db.iterdump())


def test_work_updates_shared_ui_exact_child_and_revision_is_not_persisted(mcp_client):
    client, app = mcp_client
    parent = read(client, 'S025')
    child = read(client)
    assert len(child['data_revision']) == 64
    updated = data(call(client, 'workbench_update_work', {'script_id': 's25_1', 'edit': {
        'expected_revision': child['data_revision'], 'title': 'Changed child', 'notes': 'User note',
        'es_published': True, 'es_published_date': '2026-10-06', 'patreon_published': False,
    }}))
    assert updated['script_id'] == 'S025_001' and updated['title'] == 'Changed child'
    assert updated['es_published'] and not updated['patreon_published']
    assert updated['es_published_date'] == '2026-10-06'
    assert updated['data_revision'] != child['data_revision']
    assert client.get(f'/api/works/{child["id"]}').json()['notes'] == 'User note'
    assert read(client, 'S025')['data_revision'] == parent['data_revision']
    with app.state.store.connection() as db:
        row = dict(db.execute('SELECT * FROM works WHERE id=?', (child['id'],)).fetchone())
        assert 'expected_revision' not in row and 'expected_revision' not in json.loads(row['manual_fields'])
    # Existing web clients can continue maintaining without the new optimistic token.
    assert client.patch(f'/api/works/{child["id"]}', json={'notes': 'Legacy web edit'}).status_code == 200
    before = snapshot(app)
    stale = call(client, 'workbench_update_work', {'script_id': 'S025_001', 'edit': {
        'expected_revision': updated['data_revision'], 'notes': 'Do not overwrite',
    }})
    assert stale['isError'] and 'HTTP 409' in text(stale) and 'Re-read' in text(stale)
    assert snapshot(app) == before


def test_work_revision_parallel_writers_have_one_winner(mcp_client):
    client, app = mcp_client
    current = read(client)
    def update(title):
        return call(client, 'workbench_update_work', {'script_id': current['script_id'],
            'edit': {'expected_revision': current['data_revision'], 'title': title}})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ['First writer', 'Second writer']))
    assert sum(not result.get('isError') for result in results) == 1
    errors = [result for result in results if result.get('isError')]
    assert len(errors) == 1 and 'HTTP 409' in text(errors[0])
    assert read(client)['title'] in {'First writer', 'Second writer'}


def test_nonexistent_exact_child_reports_http404_without_writing(mcp_client):
    client, app = mcp_client
    before = snapshot(app)
    result = call(client, 'workbench_update_work', {'script_id': 'S025_999', 'edit': {
        'expected_revision': 'a' * 64, 'notes': 'Must not fall back to parent',
    }})
    assert result['isError'] and 'HTTP 404' in text(result) and 'S025_999' in text(result)
    assert snapshot(app) == before


@pytest.mark.parametrize('edit', [
    {'title': 'Missing revision'},
    {'expected_revision': None, 'title': 'Invalid revision'},
    {'expected_revision': 'a' * 64},
    {'expected_revision': 'a' * 64, 'production_required': False},
    {'expected_revision': 'a' * 64, 'status': 'published'},
    {'expected_revision': 'a' * 64, 'es_published': 'true'},
    {'expected_revision': 'a' * 64, 'es_published_date': '2026-02-31'},
    {'expected_revision': 'a' * 64, 'title': '   '},
])
def test_invalid_work_changes_leave_shared_data_unchanged(mcp_client, edit):
    client, app = mcp_client
    before = snapshot(app)
    result = call(client, 'workbench_update_work', {'script_id': 'S025_001', 'edit': edit})
    assert result.get('isError') and snapshot(app) == before


def test_creating_and_updating_shared_tags_then_full_replacement(mcp_client):
    client, app = mcp_client
    first = data(call(client, 'workbench_create_tag', {'edit': {'category': 'author', 'name': 'New author',
        'support_status': 'url', 'support_url': 'https://example.com/creator'}}))
    second = data(call(client, 'workbench_create_tag', {'edit': {'category': 'custom', 'name': 'Collection'}}))
    child = read(client)
    bound = data(call(client, 'workbench_set_work_tags', {'script_id': child['script_id'], 'edit': {
        'expected_revision': child['tags_revision'], 'tag_ids': [first['id'], second['id']],
    }}))
    assert len(bound['tags']) == 2 and bound['tags_revision'] == 1
    updated = data(call(client, 'workbench_update_tag', {'tag_id': first['id'], 'edit': {
        'expected_revision': first['revision'], 'support_url': 'https://example.com/new',
    }}))
    assert updated['revision'] == first['revision'] + 1
    assert next(tag for tag in read(client)['tags'] if tag['id'] == first['id'])['support_url'] == 'https://example.com/new'
    before = snapshot(app)
    stale = call(client, 'workbench_set_work_tags', {'script_id': child['script_id'], 'edit': {'expected_revision': 0, 'tag_ids': []}})
    assert stale['isError'] and 'HTTP 409' in text(stale) and snapshot(app) == before
    duplicate = call(client, 'workbench_create_tag', {'edit': {'category': 'author', 'name': 'new AUTHOR'}})
    assert duplicate['isError'] and 'HTTP 409' in text(duplicate) and snapshot(app) == before
    invalid = call(client, 'workbench_update_tag', {'tag_id': first['id'], 'edit': {
        'expected_revision': updated['revision'], 'support_url': 'https://user:password@example.com',
    }})
    assert invalid['isError'] and 'HTTP 422' in text(invalid) and snapshot(app) == before
    clear = data(call(client, 'workbench_set_work_tags', {'script_id': child['script_id'], 'edit': {
        'expected_revision': bound['tags_revision'], 'tag_ids': [],
    }}))
    assert clear['tags'] == [] and clear['tags_revision'] == 2


def test_links_dates_calendar_and_guarded_undo_use_one_state(mcp_client):
    client, app = mcp_client
    child = read(client)
    links = data(call(client, 'workbench_update_work_links', {'script_id': child['script_id'], 'edit': {
        'expected_revision': child['links_revision'], 'links': {'video': 'https://example.com/video', 'es': 'https://example.com/es'},
        'es_published_date': '2026-10-06',
    }}))
    assert links['es_published_date'] == '2026-10-06'
    assert read(client)['es_published']
    calendar = data(call(client, 'workbench_get_release_calendar', {'month': '2026-10'}))
    state = next(work for work in calendar['works'] if work['script_id'] == child['script_id'])
    planned = data(call(client, 'workbench_update_release_calendar', {'script_id': child['script_id'], 'edit': {
        'platforms': ['es', 'patreon'], 'mode': 'planned', 'date': '2026-10-10', 'expected_revision': state['revision'],
    }}))
    assert planned['work']['es_planned_date'] == planned['work']['patreon_planned_date'] == '2026-10-10'
    assert planned['work']['es_published_date'] == '2026-10-06' and not planned['work']['patreon_published']
    undone = data(call(client, 'workbench_undo_calendar_operation', {'operation_id': planned['operation']['id']}))
    assert undone['work']['es_planned_date'] is None and undone['operation']['undone']
    before = snapshot(app)
    repeated = call(client, 'workbench_undo_calendar_operation', {'operation_id': planned['operation']['id']})
    assert repeated['isError'] and 'HTTP 409' in text(repeated) and snapshot(app) == before
    actual = data(call(client, 'workbench_update_release_calendar', {'script_id': child['script_id'], 'edit': {
        'platforms': ['patreon'], 'mode': 'actual', 'date': '2026-10-07', 'expected_revision': undone['work']['revision'],
    }}))
    assert read(client)['patreon_published'] and read(client)['patreon_published_date'] == '2026-10-07'
    changed = read(client)
    data(call(client, 'workbench_update_work', {'script_id': child['script_id'], 'edit': {
        'expected_revision': changed['data_revision'], 'patreon_published_date': '2026-10-08',
    }}))
    before = snapshot(app)
    unsafe_undo = call(client, 'workbench_undo_calendar_operation', {'operation_id': actual['operation']['id']})
    assert unsafe_undo['isError'] and 'HTTP 409' in text(unsafe_undo) and snapshot(app) == before


def test_settings_profile_preserves_avatar_and_language_is_versioned(mcp_client):
    client, app = mcp_client
    image = BytesIO()
    Image.new('RGB', (1, 1)).save(image, format='PNG')
    avatar = 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode()
    assert client.put('/api/profile', json={'name': 'Original', 'bio': 'Existing bio', 'avatar': avatar, 'expected_revision': 0}).status_code == 200
    settings = data(call(client, 'workbench_get_settings'))
    assert 'avatar' not in settings['profile'] and settings['language']['revision'] == 0
    updated = data(call(client, 'workbench_update_profile', {'edit': {'name': 'Maintained', 'expected_revision': settings['profile']['revision']}}))
    assert updated['name'] == 'Maintained' and updated['bio'] == 'Existing bio'
    assert 'avatar' not in updated and avatar not in json.dumps(updated)
    assert client.get('/api/profile').json()['avatar'] == avatar
    before = snapshot(app)
    stale = call(client, 'workbench_update_profile', {'edit': {'bio': 'Old tab overwrite', 'expected_revision': 1}})
    assert stale['isError'] and 'HTTP 409' in text(stale) and avatar not in text(stale)
    assert snapshot(app) == before
    rejected = call(client, 'workbench_update_profile', {'edit': {'bio': 'Changed', 'avatar': None, 'expected_revision': updated['revision']}})
    assert rejected['isError'] and snapshot(app) == before
    language = data(call(client, 'workbench_update_language', {'edit': {'language': 'en', 'expected_revision': 0}}))
    assert language == {'language': 'en', 'revision': 1}
    before = snapshot(app)
    stale = call(client, 'workbench_update_language', {'edit': {'language': 'zh-CN', 'expected_revision': 0}})
    assert stale['isError'] and 'HTTP 409' in text(stale) and snapshot(app) == before


def test_profile_api_validation_error_never_echoes_preserved_avatar(mcp_client):
    client, app = mcp_client
    private_avatar = 'data:image/png;base64,private-avatar-bytes'
    with app.state.store.connection() as db:
        db.execute("INSERT INTO settings(key,value) VALUES('workspace_profile',?)", (json.dumps({
            'name': 'Original', 'bio': 'Existing bio', 'avatar': private_avatar, 'revision': 1,
        }),))
    before = snapshot(app)
    invalid = call(client, 'workbench_update_profile', {'edit': {'name': 'Updated', 'expected_revision': 1}})
    assert invalid['isError'] and 'HTTP 422' in text(invalid)
    assert private_avatar not in text(invalid) and snapshot(app) == before


@pytest.mark.parametrize('authorization', [None, 'Bearer ' + 'x' * 43])
def test_write_rpc_requires_valid_bearer_and_cannot_expand_scope(mcp_client, authorization):
    client, app = mcp_client
    before = snapshot(app)
    headers = dict(HEADERS)
    if authorization is not None:
        headers['Authorization'] = authorization
    previous = client.headers.pop('Authorization')
    try:
        response = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'workbench_create_tag', 'arguments': {'edit': {'category': 'custom', 'name': 'Unauthorized'}}}})
        assert response.status_code == 401 and snapshot(app) == before
    finally:
        client.headers['Authorization'] = previous


@pytest.mark.parametrize('name', ['workbench_open_folder', 'workbench_reset_token', 'workbench_scan',
                                 'workbench_generate_preview', 'workbench_confirm_production', 'workbench_http_request'])
def test_no_jobs_privileged_or_arbitrary_request_tools(mcp_client, name):
    client, app = mcp_client
    before = snapshot(app)
    result = call(client, name)
    assert result['isError'] and snapshot(app) == before


def test_maintenance_tools_describe_required_versions_and_no_production_field(mcp_client):
    client, _ = mcp_client
    tools = {tool['name']: tool for tool in rpc(client, 'tools/list')['result']['tools']}
    update = tools['workbench_update_work']
    assert not update['annotations']['readOnlyHint']
    def edit_schema(tool):
        root = tool['inputSchema']
        value = root['properties']['edit']
        return root['$defs'][value['$ref'].split('/')[-1]] if '$ref' in value else value
    schema = edit_schema(update)
    assert 'expected_revision' in schema['required']
    assert 'production_required' not in schema['properties']
    assert 'status' not in schema['properties']
    calendar = edit_schema(tools['workbench_update_release_calendar'])
    assert 'work_id' not in calendar['properties'] and 'expected_revision' in calendar['required']
    profile = edit_schema(tools['workbench_update_profile'])
    assert 'avatar' not in profile['properties']


def test_explicit_null_actual_date_clears_only_selected_field(mcp_client):
    client, app = mcp_client
    child = read(client)
    seeded = client.patch(f'/api/works/{child["id"]}', json={'es_published_date': '2026-10-06', 'patreon_published_date': '2026-10-07'}).json()
    notes = data(call(client, 'workbench_update_work', {'script_id': child['script_id'], 'edit': {
        'expected_revision': seeded['data_revision'], 'notes': 'No date changes',
    }}))
    assert notes['es_published_date'] == '2026-10-06' and notes['patreon_published_date'] == '2026-10-07'
    cleared = data(call(client, 'workbench_update_work', {'script_id': child['script_id'], 'edit': {
        'expected_revision': notes['data_revision'], 'es_published_date': None,
    }}))
    assert cleared['es_published_date'] is None and cleared['patreon_published_date'] == '2026-10-07'
    assert cleared['data_revision'] != notes['data_revision']


def test_mcp_maintenance_persists_after_recreating_app_with_same_sqlite(mcp_client):
    client, app = mcp_client
    child = read(client)
    data(call(client, 'workbench_update_work', {'script_id': child['script_id'], 'edit': {
        'expected_revision': child['data_revision'], 'notes': 'Persisted across restart',
    }}))
    tag = data(call(client, 'workbench_create_tag', {'edit': {'category': 'custom', 'name': 'Persistent tag'}}))
    data(call(client, 'workbench_set_work_tags', {'script_id': child['script_id'], 'edit': {
        'expected_revision': 0, 'tag_ids': [tag['id']],
    }}))
    data(call(client, 'workbench_update_work_links', {'script_id': child['script_id'], 'edit': {
        'expected_revision': 0, 'links': {'video': 'https://example.com/persisted'},
    }}))
    data(call(client, 'workbench_update_profile', {'edit': {'bio': 'Persisted profile', 'expected_revision': 0}}))
    data(call(client, 'workbench_update_language', {'edit': {'language': 'en', 'expected_revision': 0}}))
    restarted = create_app(app.state.config, start_worker=False)
    with TestClient(restarted, base_url='http://127.0.0.1:8789') as other:
        other.headers['Authorization'] = client.headers['Authorization']
        restored = read(other)
        assert restored['notes'] == 'Persisted across restart'
        assert restored['tags'][0]['id'] == tag['id']
        assert restored['links']['video'] == 'https://example.com/persisted'
        settings = data(call(other, 'workbench_get_settings'))
        assert settings['profile']['bio'] == 'Persisted profile'
        assert settings['language'] == {'language': 'en', 'revision': 1}


def test_real_sdk_nested_edit_schema_read_write_readback(tmp_path):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    app = create_app(Config(data_dir=tmp_path / 'data', roots=(), preview_output_root=tmp_path / 'previews'), start_worker=False)
    with app.state.store.connection() as db:
        for script_id in ('S025', 'S025_001'):
            db.execute('INSERT INTO works(script_id,title,notes,created_at,updated_at) VALUES(?,?,?,?,?)',
                       (script_id, script_id, 'Original note', now(), now()))
    token = MCPAuth(app.state.store).reset(0)['token']

    async def exercise():
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url='http://127.0.0.1:8789',
                                          headers={'Authorization': f'Bearer {token}'}) as client:
                async with streamable_http_client('http://127.0.0.1:8789/mcp', http_client=client) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert len(tools.tools) == 18
                        child = await session.call_tool('workbench_get_work', {'script_id': 'S025_001'})
                        assert not child.is_error
                        maintained = await session.call_tool('workbench_update_work', {'script_id': 'S025_001', 'edit': {
                            'expected_revision': child.structured_content['data_revision'], 'notes': 'SDK persisted note',
                        }})
                        assert not maintained.is_error
                        restored = await session.call_tool('workbench_get_work', {'script_id': 'S025_001'})
                        assert restored.structured_content['notes'] == 'SDK persisted note'
                        parent = await session.call_tool('workbench_get_work', {'script_id': 'S025'})
                        assert parent.structured_content['notes'] == 'Original note'
    asyncio.run(exercise())
