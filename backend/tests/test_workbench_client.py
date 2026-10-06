import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient

from backend.config import Config, Root
from backend.tests.test_es_posts import work
from scripts.workbench_client import ClientError, WorkbenchClient, export_post, normalize_id


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv('WORKBENCH_DATA_DIR', str(tmp_path / 'default-app'))
    from backend.main import create_app
    root = tmp_path / 'materials'
    root.mkdir()
    config = Config(data_dir=tmp_path / 'db', roots=(Root(root, str(root), 'test'),),
                    preview_output_root=tmp_path / 'previews')
    app = create_app(config, start_worker=False)
    api = TestClient(app)
    posts = (api, app.state.store, config, app.state.es_posts)
    monkeypatch.setattr(app.state.es_posts, 'duration', lambda *args: (80, None))
    work_id = work(posts, script_id='S025_001', support='url')
    work(posts, script_id='S025', title='Parent')
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            payload = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            calls.append((self.command, self.path))
            if self.path == '/api/redirect':
                self.send_response(302)
                self.send_header('Location', '/api/health')
                self.end_headers()
                return
            result = api.request(self.command, self.path, content=payload or None,
                                 headers={'Content-Type': 'application/json'})
            self.send_response(result.status_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(result.content)

        do_GET = do_PUT = do_POST = do_PATCH = handle_request

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield WorkbenchClient(f'http://127.0.0.1:{server.server_port}'), posts, work_id, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        api.close()


def test_exact_child_reads_local_data_and_omits_private_notes(bridge):
    client, _, work_id, _ = bridge
    result = client.inspect('s25_1')
    assert result['work']['id'] == work_id
    assert result['work']['script_id'] == 'S025_001'
    assert 'NEVER_PUBLISH_INTERNAL_SECRET' not in json.dumps(result)
    assert result['post']['sources']['author_support']['url'] == 'https://creator.example/support'
    with pytest.raises(ClientError, match='未找到 S999'):
        client.work('S999')


def test_generate_uses_shared_template_and_preserves_separate_cover(bridge, tmp_path):
    client, posts, work_id, _ = bridge
    state = client.post_operation('S025_001', 'generate', {
        'cover_markdown': '![cover](upload://chosen.gif)',
        'preview_markdown': '[video](upload://body.webm)',
        'heatmap_markdown': '![heatmap](upload://heatmap.png)',
    })
    assert state['output']['status'] == 'ready'
    assert 'upload://body.webm' in state['output']['body']
    assert 'upload://heatmap.png' in state['output']['body']
    assert 'upload://chosen.gif' not in state['output']['body']
    assert state['saved_cover_url'] == 'upload://chosen.gif'
    newer = client.post_operation('S025_001', 'save-inputs', {'intro_markdown': 'New intro'})
    assert newer['inputs']['preview_markdown'] == '[video](upload://body.webm)'
    assert newer['inputs']['cover_markdown'] == '![cover](upload://chosen.gif)'
    assert newer['output']['stale'] is True
    path = tmp_path / 'post.md'
    output = export_post(state, path)
    assert output['title'] not in path.read_text()
    assert output['status'] == 'ready'
    with posts[1].connection() as db:
        assert db.execute('SELECT es_published FROM works WHERE id=?', (work_id,)).fetchone()[0] == 0


def test_draft_is_reported_and_never_marked_published(bridge):
    client, posts, work_id, _ = bridge
    state = client.post_operation('S025_001', 'generate')
    assert state['output']['status'] == 'draft'
    assert state['output']['missing']
    with posts[1].connection() as db:
        assert db.execute('SELECT COUNT(*) FROM es_preview_history').fetchone()[0] == 0


def test_record_es_link_preserves_other_links_and_assigns_date(bridge):
    client, _, _, _ = bridge
    before = client.inspect('S025_001')['work']
    result = client.record_es_link('S025_001', 'https://example.org/t/already-published/123')
    assert result['links']['video'] == before['links']['video']
    assert result['links']['patreon'] == before['links']['patreon']
    assert result['es_published_date']
    after = client.inspect('S025_001')['work']
    assert after['es_published'] is True
    assert after['patreon_published'] == before['patreon_published']


def test_conflict_and_redirect_never_retry_mutation(bridge):
    client, _, work_id, calls = bridge
    state = client.request(f'/api/works/{work_id}/es-post')
    client.save_inputs(work_id, state, {'intro_markdown': 'First'})
    with pytest.raises(ClientError, match='已被其他操作更新'):
        client.save_inputs(work_id, state, {'intro_markdown': 'Stale write'})
    assert client.request(f'/api/works/{work_id}/es-post')['inputs']['intro_markdown'] == 'First'
    before = len(calls)
    with pytest.raises(ClientError, match='HTTP 302'):
        client.request('/api/redirect')
    assert len(calls) == before + 1


def test_network_failure_explains_unknown_write_result(monkeypatch):
    client = WorkbenchClient('http://127.0.0.1:8789')
    monkeypatch.setattr(client.opener, 'open', lambda *a, **kw: (_ for _ in ()).throw(URLError('offline')))
    with pytest.raises(ClientError, match='先 inspect 读回'):
        client.request('/api/works/1/es-post/generate', 'POST', {'expected_revision': 0})


@pytest.mark.parametrize('value', ['S025 - title', 'S025_001evil', 'S025/001'])
def test_invalid_identifiers_do_not_silently_match_parent(value):
    with pytest.raises(ClientError):
        normalize_id(value)
