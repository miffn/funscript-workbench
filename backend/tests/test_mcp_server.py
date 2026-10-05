"""Exercise real MCP JSON-RPC transport against disposable workbench data."""
import json

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.main import create_app
from backend.store import now
from backend.mcp_auth import MCPAuth

HEADERS = {'Accept': 'application/json, text/event-stream'}


def rpc(client, method, params=None):
    response = client.post('/mcp', headers=HEADERS,
                           json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})
    assert response.status_code == 200, response.text
    return response.json()


def call(client, name, arguments=None):
    return rpc(client, 'tools/call', {'name': name, 'arguments': arguments or {}})['result']


def data(result):
    assert not result.get('isError'), result
    return result['structuredContent']


@pytest.fixture
def mcp_client(tmp_path):
    root = tmp_path / 'materials'
    root.mkdir()
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<html>Workbench UI</html>')
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, str(root), 'test'),),
                    preview_output_root=tmp_path / 'previews', frontend_dist=dist)
    app = create_app(config, start_worker=False)
    with app.state.store.connection() as db:
        for identifier in ('S025', 'S025_001', 'S064'):
            directory = root / identifier
            directory.mkdir()
            work_id = db.execute('INSERT INTO works(script_id,title,notes,created_at,updated_at) VALUES(?,?,?,?,?)',
                                 (identifier, 'Title ' + identifier, 'Saved note', now(), now())).lastrowid
            directory_id = db.execute('INSERT INTO directories(work_id,path,windows_path,root_path,name) VALUES(?,?,?,?,?)',
                                      (work_id, str(directory), str(directory), str(root), identifier)).lastrowid
            if identifier != 'S064':
                path = directory / 'test.funscript'
                path.write_text('{"actions":[]}')
                db.execute('INSERT INTO assets(directory_id,name,relative_path,kind,size,mtime_ns) VALUES(?,?,?,?,?,?)',
                           (directory_id, path.name, path.name, 'script', path.stat().st_size, path.stat().st_mtime_ns))
        db.execute("UPDATE works SET production_required=1 WHERE script_id='S064'")
        tag_id = db.execute("INSERT INTO tags(category,name,name_key,support_status,support_url) VALUES('author','Creator','creator','url','https://creator.example')").lastrowid
        parent_id = db.execute("SELECT id FROM works WHERE script_id='S025'").fetchone()[0]
        db.execute('INSERT INTO work_tags(work_id,tag_id) VALUES(?,?)', (parent_id, tag_id))
        db.execute("UPDATE works SET es_published=1, es_published_date='2026-10-03' WHERE id=?", (parent_id,))
    with TestClient(app, base_url='http://127.0.0.1:8789') as client:
        token = MCPAuth(app.state.store).reset(0)['token']
        client.headers['Authorization'] = f'Bearer {token}'
        yield client, app


def test_initialize_catalog_and_frontend_remain_accessible(mcp_client):
    client, _ = mcp_client
    initialized = rpc(client, 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                            'clientInfo': {'name': 'test', 'version': '1'}})['result']
    assert initialized['serverInfo']['name'] == 'funscript-workbench'
    tools = rpc(client, 'tools/list')['result']['tools']
    assert len(tools) == 9
    assert all(tool['annotations']['readOnlyHint'] and not tool['annotations']['destructiveHint'] for tool in tools)
    assert client.get('/').status_code == 200
    assert client.get('/api/health').json()['status'] == 'ok'
    assert client.get('/mcp').status_code == 405  # Stateless JSON transport has no endless GET stream.


def test_read_tools_use_ui_rules_exact_child_and_do_not_write(mcp_client):
    client, app = mcp_client
    with app.state.store.connection() as db:
        before = '\n'.join(db.iterdump())
    overview = data(call(client, 'workbench_get_overview'))
    assert overview['stats']['total'] == 3 and overview['stats']['to_make'] == 1
    assert data(call(client, 'workbench_list_works', {'status': 'to_make'}))['items'][0]['script_id'] == 'S064'
    assert {item['script_id'] for item in data(call(client, 'workbench_list_works', {'status': 'pending'}))['items']} == {'S025', 'S025_001'}
    child = data(call(client, 'workbench_get_work', {'script_id': 's25_1'}))
    assert child['script_id'] == 'S025_001' and child['assets'][0]['name'] == 'test.funscript'
    assert 'metadata' not in child
    tag_catalog = data(call(client, 'workbench_list_tags', {'category': 'author'}))
    assert 'import_report' not in tag_catalog
    authors = tag_catalog['items']
    assert len(authors) == 1 and authors[0]['support_url'] == 'https://creator.example'
    tagged = data(call(client, 'workbench_list_works', {'tag_id': authors[0]['id']}))
    assert [item['script_id'] for item in tagged['items']] == ['S025']
    calendar = data(call(client, 'workbench_get_release_calendar', {'month': '2026-10'}))
    assert calendar['events'][0]['platform'] == 'es' and calendar['events'][0]['mode'] == 'actual'
    assert data(call(client, 'workbench_get_preview', {'script_id': 'S025_001'}))['preview']['files'] == []
    assert data(call(client, 'workbench_get_jobs'))['items'] == []
    settings = data(call(client, 'workbench_get_settings'))
    assert settings['settings']['scan_mode'] == 'manual' and 'avatar' not in settings['profile']
    assert 'host.key' not in json.dumps(settings)
    post = data(call(client, 'workbench_get_post_materials', {'script_id': 'S025_001'}))
    assert post['work']['script_id'] == 'S025_001' and 'template' in post and post['post']['output'] is None
    with app.state.store.connection() as db:
        assert '\n'.join(db.iterdump()) == before


@pytest.mark.parametrize('name,args', [
    ('workbench_get_work', {'script_id': 'S025_999'}),
    ('workbench_get_work', {'script_id': '../S025'}),
    ('workbench_get_release_calendar', {'month': '2026-13'}),
    ('workbench_list_works', {'page_size': 101}),
    ('workbench_list_works', {'status': 'invalid'}),
    ('workbench_get_jobs', {'job_id': 999}),
    ('workbench_generate_preview', {'script_id': 'S025'}),
])
def test_invalid_queries_and_write_tool_names_fail(mcp_client, name, args):
    client, app = mcp_client
    with app.state.store.connection() as db:
        before = '\n'.join(db.iterdump())
    result = call(client, name, args)
    assert result.get('isError'), result
    with app.state.store.connection() as db:
        assert '\n'.join(db.iterdump()) == before


@pytest.mark.parametrize('host,origin,status', [
    ('attacker.example', None, 403), ('localhost:9999', None, 403),
    ('8.8.8.8:8787', None, 403), ('192.168.1.100:8787', None, 200),
    ('localhost:8788', 'http://attacker.example', 403),
    ('localhost:8788', 'http://localhost:8788', 200),
])
def test_dynamic_lan_host_and_origin_boundary(mcp_client, host, origin, status):
    client, _ = mcp_client
    headers = {**HEADERS, 'Host': host}
    if origin is not None:
        headers['Origin'] = origin
    response = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    assert response.status_code == status
