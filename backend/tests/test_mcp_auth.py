import json

from fastapi.testclient import TestClient
import pytest

from backend.config import Config
from backend.main import create_app
from backend.mcp_auth import MCPAuth


RPC = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
ACCEPT = {'Accept': 'application/json, text/event-stream'}
HOST = {'Host': 'localhost:8788', 'Origin': 'http://localhost:8788',
        'X-Workbench-Host-Key': 'test-host-key-' + 'x' * 32}


@pytest.fixture
def context(tmp_path):
    config = Config(data_dir=tmp_path / 'data', roots=())
    app = create_app(config, start_worker=False)
    (config.data_dir / 'host.key').write_text(HOST['X-Workbench-Host-Key'])
    with TestClient(app, base_url='http://localhost:8788') as client:
        yield client, app, config


def reset(client, revision=0, headers=HOST):
    return client.post('/api/mcp-auth/token', headers=headers, json={'expected_revision': revision})


def test_locked_without_token_then_one_time_secret_and_no_public_leaks(context):
    client, app, _ = context
    assert client.get('/api/mcp-auth', headers=HOST).json() == {
        'enabled': False, 'can_manage': True, 'revision': 0, 'updated_at': None}
    denied = client.post('/mcp', headers=ACCEPT, json=RPC)
    assert denied.status_code == 401 and denied.headers['www-authenticate'] == 'Bearer'
    response = reset(client)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    token = response.json()['token']
    assert len(token) == 43 and response.json()['revision'] == 1
    with app.state.store.connection() as db:
        stored = db.execute("SELECT value FROM settings WHERE key='mcp_auth'").fetchone()[0]
    assert token not in stored and len(json.loads(stored)['digest']) == 64
    public = client.get('/api/mcp-auth', headers=HOST)
    assert public.headers['cache-control'] == 'no-store'
    assert token not in public.text and 'digest' not in public.text and 'token' not in public.json()
    assert token not in client.get('/api/settings').text
    authorized = client.post('/mcp', headers={**ACCEPT, 'Authorization': f'Bearer {token}'}, json=RPC)
    assert authorized.status_code == 200
    assert 'workbench_update_work' in {tool['name'] for tool in authorized.json()['result']['tools']}
    assert token not in authorized.text
    # A URL query cannot substitute for the required header (or leak via a generated URL).
    assert client.post('/mcp?token=' + token, headers=ACCEPT, json=RPC).status_code == 401


@pytest.mark.parametrize('method', ['initialize', 'tools/list', 'tools/call'])
@pytest.mark.parametrize('header', [None, 'Basic credentials', 'Bearer bad', 'Bearer ' + 'x' * 43])
def test_authentication_covers_entire_rpc_surface(context, method, header):
    client, _, _ = context
    reset(client)
    headers = dict(ACCEPT)
    if header is not None:
        headers['Authorization'] = header
    payload = {**RPC, 'method': method}
    if method == 'tools/call':
        payload['params'] = {'name': 'workbench_update_work', 'arguments': {
            'script_id': 'S025', 'edit': {'expected_revision': '0' * 64, 'notes': 'Unauthorized change'}}}
    response = client.post('/mcp', headers=headers, json=payload)
    assert response.status_code == 401


def test_rotation_is_atomic_persistent_and_revokes_old_token(context):
    client, app, config = context
    first = reset(client).json()['token']
    second_response = reset(client, 1)
    second = second_response.json()['token']
    assert second != first and second_response.json()['revision'] == 2
    assert reset(client, 1).status_code == 409
    with app.state.store.connection() as db:
        snapshot = '\n'.join(db.iterdump())
    for token, status in [(first, 401), (second, 200)]:
        assert client.post('/mcp', headers={**ACCEPT, 'Authorization': f'Bearer {token}'}, json=RPC).status_code == status
    with app.state.store.connection() as db:
        assert '\n'.join(db.iterdump()) == snapshot
    # Reloading the entire app reads the digest from SQLite, not process memory.
    restarted = create_app(config, start_worker=False)
    with TestClient(restarted, base_url='http://localhost:8788') as other:
        assert other.post('/mcp', headers={**ACCEPT, 'Authorization': f'Bearer {second}'}, json=RPC).status_code == 200
        assert other.post('/mcp', headers={**ACCEPT, 'Authorization': f'Bearer {first}'}, json=RPC).status_code == 401


@pytest.mark.parametrize('headers', [
    {'Host': '192.0.2.6:8787', 'Origin': 'http://192.0.2.6:8787'},
    {**HOST, 'Host': '192.0.2.6:8787', 'Origin': 'http://192.0.2.6:8787'},
    {'Host': 'localhost:8788', 'Origin': 'http://localhost:8788'},
    {**HOST, 'Origin': 'http://attacker.example'},
    {key: value for key, value in HOST.items() if key != 'Origin'},
    {**HOST, 'X-Workbench-Host-Key': 'forged'},
])
def test_management_requires_gateway_host_key_and_exact_origin(context, headers):
    client, app, _ = context
    assert reset(client, headers=headers).status_code == 403
    with app.state.store.connection() as db:
        assert not MCPAuth(app.state.store).state(db)


def test_lan_can_read_with_token_but_cannot_manage_or_read_secret(context):
    client, _, _ = context
    token = reset(client).json()['token']
    state = client.get('/api/mcp-auth', headers={'Host': '192.0.2.6:8787'}).json()
    assert state['enabled'] and not state['can_manage'] and 'token' not in state
    assert client.post('/mcp', headers={**ACCEPT, 'Host': '192.0.2.6:8787',
                                      'Authorization': f'Bearer {token}'}, json=RPC).status_code == 200
    assert reset(client, 1, {**HOST, 'Host': '192.0.2.6:8787',
                           'Origin': 'http://192.0.2.6:8787', 'Authorization': f'Bearer {token}'}).status_code == 403


def test_duplicate_credentials_and_invalid_stored_state_fail_closed(context):
    client, app, _ = context
    token = reset(client).json()['token']
    headers = [*ACCEPT.items(), ('Authorization', f'Bearer {token}'), ('Authorization', f'Bearer {token}')]
    assert client.post('/mcp', headers=headers, json=RPC).status_code == 401
    with app.state.store.connection() as db:
        db.execute("UPDATE settings SET value='invalid-json' WHERE key='mcp_auth'")
    assert client.post('/mcp', headers={**ACCEPT, 'Authorization': f'Bearer {token}'}, json=RPC).status_code == 401


@pytest.mark.parametrize('body', [{'expected_revision': True}, {'expected_revision': -1},
                                  {'expected_revision': '0'}, {'expected_revision': 0, 'token': 'chosen'}, {}])
def test_management_validates_revision_and_never_accepts_user_supplied_secrets(context, body):
    client, _, _ = context
    assert client.post('/api/mcp-auth/token', headers=HOST, json=body).status_code == 422
