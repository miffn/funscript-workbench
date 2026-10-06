import http.client
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace

import pytest

from scripts.windows_gateway import make_server
from scripts import windows_gateway


class EchoHandler(BaseHTTPRequestHandler):
    def reply(self):
        length = int(self.headers.get("Content-Length", "0"))
        result = json.dumps({"host": self.headers.get("Host"),
                             "key": self.headers.get("X-Workbench-Host-Key"),
                             "folder": self.headers.get("X-Workbench-Open-Folder"),
                             "folder_root": self.headers.get("X-Workbench-Folder-Root"),
                             "origin": self.headers.get("Origin"),
                             "authorization": self.headers.get("Authorization"),
                             "body": self.rfile.read(length).decode()}).encode()
        self.send_response(200)
        if self.path.endswith("/open-folder"):
            self.send_header("X-Workbench-Open-Folder", encode(r"E:\custom-library\S029"))
            if not getattr(self.server, "omit_folder_root", False):
                self.send_header("X-Workbench-Folder-Root", encode(r"E:\custom-library"))
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)
    do_GET = do_PATCH = do_POST = reply
    def log_message(self, *args):
        pass


@pytest.fixture
def gateways():
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
    local = make_server("127.0.0.1", 0, {"localhost:8788"}, upstream.server_port, "trusted-secret")
    remote = make_server("127.0.0.1", 0, {"192.0.2.10:8787"}, upstream.server_port)
    local.upstream_test_server = upstream
    servers = (upstream, local, remote)
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    yield local, remote
    for server in reversed(servers):
        server.shutdown()
        server.server_close()


def request(server, method="GET", headers=None, body=None, path="/api/capabilities"):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    result = response.status, response.read()
    conn.close()
    return result


def encode(path):
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode()


def test_loopback_grants_key_and_lan_strips_spoofed_key(gateways):
    local, remote = gateways
    status, body = request(local, headers={"Host": "localhost:8788", "X-Workbench-Host-Key": "spoof"})
    assert status == 200 and json.loads(body)["key"] == "trusted-secret"
    status, body = request(remote, headers={"Host": "192.0.2.10:8787", "X-Workbench-Host-Key": "spoof"})
    assert status == 200 and json.loads(body)["key"] is None


def test_rejects_rebinding_and_cross_origin_write(gateways):
    local, remote = gateways
    assert request(local, headers={"Host": "attacker.example"})[0] == 403
    assert request(remote, headers={"Host": "localhost:8788"})[0] == 403
    headers = {"Host": "localhost:8788", "Origin": "http://attacker.example"}
    assert request(local, "PATCH", headers, "{}")[0] == 403
    assert request(local, "POST", {"Host": "localhost:8788"}, "{}")[0] == 403


def test_same_origin_write_preserves_body_and_host(gateways):
    local, _ = gateways
    headers = {"Host": "localhost:8788", "Origin": "http://localhost:8788", "Content-Type": "application/json"}
    status, body = request(local, "PATCH", headers, '{"status":"pending"}')
    assert status == 200
    echoed = json.loads(body)
    assert echoed["body"] == '{"status":"pending"}'
    assert echoed["host"] == "localhost:8788"


def test_connection_header_cannot_smuggle_host_key(gateways):
    local, remote = gateways
    headers = {"Host": "192.0.2.10:8787", "Connection": "X-Workbench-Host-Key", "X-Workbench-Host-Key": "spoof"}
    assert json.loads(request(remote, headers=headers)[1])["key"] is None


def test_mcp_bearer_header_reaches_backend_without_granting_lan_host_capability(gateways):
    local, remote = gateways
    for server, host in [(local, 'localhost:8788'), (remote, '192.0.2.10:8787')]:
        status, body = request(server, 'POST', {'Host': host, 'Authorization': 'Bearer test-token',
                                              'X-Workbench-Host-Key': 'forged'}, '{}', '/mcp')
        assert status == 200
        echoed = json.loads(body)
        assert echoed['authorization'] == 'Bearer test-token'
        assert echoed['key'] == ('trusted-secret' if server is local else None)


def test_gateway_rejects_duplicate_mcp_credentials_before_header_forwarding(gateways):
    for server, host in [(gateways[0], 'localhost:8788'), (gateways[1], '192.0.2.10:8787')]:
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
        conn.putrequest('POST', '/mcp', skip_host=True)
        conn.putheader('Host', host)
        conn.putheader('Authorization', 'Bearer first')
        conn.putheader('Authorization', 'Bearer second')
        conn.putheader('Content-Length', '0')
        conn.endheaders()
        response = conn.getresponse()
        assert response.status == 401
        response.read()
        conn.close()


def test_only_local_open_route_launches_folder(gateways, monkeypatch):
    local, remote = gateways
    launched = []
    monkeypatch.setattr(windows_gateway, "launch_folder", lambda path, root: launched.append((path, root)))
    request(local, "POST", {"Host": "localhost:8788", "Origin": "http://localhost:8788"}, "{}", "/api/works/1/open-folder")
    assert len(launched) == 1
    assert launched[0] == (encode(r"E:\custom-library\S029"), encode(r"E:\custom-library"))
    request(remote, "POST", {"Host": "192.0.2.10:8787", "Origin": "http://192.0.2.10:8787"}, "{}", "/api/works/1/open-folder")
    request(local, "GET", {"Host": "localhost:8788"}, path="/api/works/1/open-folder")
    assert len(launched) == 1


def test_preview_folder_route_preserves_host_only_opening(gateways, monkeypatch):
    local, remote = gateways
    launched = []
    monkeypatch.setattr(windows_gateway, "launch_folder", lambda path, root: launched.append((path, root)))
    local_headers = {"Host": "localhost:8788", "Origin": "http://localhost:8788"}
    path = "/api/works/54/preview/open-folder"
    assert request(local, "POST", local_headers, "{}", path)[0] == 200
    assert len(launched) == 1
    remote_headers = {"Host": "192.0.2.10:8787", "Origin": "http://192.0.2.10:8787",
                      "X-Workbench-Host-Key": "spoof"}
    request(remote, "POST", remote_headers, "{}", path)
    request(local, "GET", {"Host": "localhost:8788"}, path=path)
    request(local, "POST", local_headers, "{}", "/api/works/54/other/open-folder")
    assert len(launched) == 1


@pytest.mark.parametrize("path", [r"E:\private\folder", r"C:\Windows", r"E:\custom-library\..\other",
                                 r"\\remote\share", r"E:\custom-library-other\S029"])
def test_launcher_rejects_paths_outside_authorized_root(path):
    with pytest.raises(ValueError):
        windows_gateway.launch_folder(encode(path), encode(r"E:\custom-library"))


@pytest.mark.parametrize("path", [r"E:custom-library", r"\custom-library", "/custom-library",
                                 r"\\?\E:\custom-library", r"\\.\E:\custom-library",
                                 r"\\remote", "E:\\custom-library\x00", r"E:\custom*library"])
def test_launcher_rejects_non_absolute_and_device_paths(path):
    with pytest.raises(ValueError):
        windows_gateway.launch_folder(encode(path), encode(path))


def fake_directories(monkeypatch, resolved=None, unavailable=()):
    class Directory:
        def __init__(self, path):
            self.path = path

        def is_dir(self):
            return self.path not in unavailable

        def resolve(self, strict=False):
            assert strict
            return (resolved or {}).get(self.path, self.path)

    monkeypatch.setattr(windows_gateway, "Path", Directory)


@pytest.mark.parametrize("root, path", [(r"E:\custom-library", r"E:\custom-library\S029"),
                                       (r"E:\custom-library", r"e:\CUSTOM-LIBRARY\S029"),
                                       (r"E:\custom-library", r"E:\custom-library"),
                                       (r"\\host\share\videos", r"\\host\share\videos\S029")])
def test_launcher_accepts_backend_authorized_custom_root(monkeypatch, root, path):
    fake_directories(monkeypatch)
    launched = []
    monkeypatch.setattr(windows_gateway, "open_folder_window", launched.append)
    windows_gateway.launch_folder(encode(path), encode(root))
    assert launched == [path]


def test_launcher_rejects_junction_escape(monkeypatch):
    path, root = r"E:\custom-library\S029", r"E:\custom-library"
    fake_directories(monkeypatch, resolved={path: r"E:\private\S029"})
    with pytest.raises(ValueError, match="resolves outside"):
        windows_gateway.launch_folder(encode(path), encode(root))


def test_launcher_rejects_missing_directory(monkeypatch):
    path, root = r"E:\custom-library\S029", r"E:\custom-library"
    fake_directories(monkeypatch, unavailable=(path,))
    with pytest.raises(ValueError, match="no longer available"):
        windows_gateway.launch_folder(encode(path), encode(root))


@pytest.mark.parametrize("root", [None, "", "!!!"])
def test_launcher_requires_encoded_authorized_root(root):
    with pytest.raises(ValueError):
        windows_gateway.launch_folder(encode(r"E:\custom-library\S029"), root)


def test_gateway_strips_folder_authorization_in_both_directions(gateways):
    local, remote = gateways
    for server, host in ((local, "localhost:8788"), (remote, "192.0.2.10:8787")):
        headers = {"Host": host, "X-Workbench-Folder-Root": encode("C:\\"),
                   "X-Workbench-Open-Folder": encode(r"C:\Windows")}
        echoed = json.loads(request(server, headers=headers)[1])
        assert echoed["folder"] is None and echoed["folder_root"] is None
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        conn.request("GET", "/api/works/1/open-folder", headers=headers)
        response = conn.getresponse()
        assert response.status == 200
        assert response.getheader("X-Workbench-Folder-Root") is None
        assert response.getheader("X-Workbench-Open-Folder") is None
        response.read()
        conn.close()


def test_missing_upstream_root_returns_open_failure(gateways):
    local, _ = gateways
    local.upstream_test_server.omit_folder_root = True
    headers = {"Host": "localhost:8788", "Origin": "http://localhost:8788"}
    status, body = request(local, "POST", headers, "{}", "/api/works/1/open-folder")
    assert status == 502 and "打开文件夹失败" in body.decode("utf-8")


def test_folder_activation_keeps_path_as_data_and_runs_hidden(monkeypatch):
    launched, activated = [], []
    path = r"E:\中文素材\S029 $(not-a-command) '"
    monkeypatch.setattr(windows_gateway.subprocess, 'Popen', lambda args, **kwargs: launched.append((args, kwargs)))
    def run(args, **kwargs):
        activated.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"hwnd": 123, "visible": true, "foreground": true}')
    monkeypatch.setattr(windows_gateway.subprocess, 'run', run)
    windows_gateway.open_folder_window(path)
    assert launched[0][0][-1] == path and launched[0][1]['shell'] is False
    args, options = activated[0]
    assert args[args.index('-WindowStyle') + 1] == 'Hidden'
    script = base64.b64decode(args[-1]).decode('utf-16-le')
    assert path not in script and 'SetForegroundWindow' in script and 'ShowWindowAsync' in script
    assert base64.b64decode(options['env']['WORKBENCH_OPEN_FOLDER_B64']).decode('utf-8') == path
    assert options['shell'] is False and options['timeout'] == 12


@pytest.mark.parametrize('code,output', [(1, ''), (0, 'not json'), (0, '{"visible":false}')])
def test_folder_activation_failure_is_reported(monkeypatch, code, output):
    monkeypatch.setattr(windows_gateway.subprocess, 'Popen', lambda *args, **kwargs: None)
    monkeypatch.setattr(windows_gateway.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=code, stdout=output))
    with pytest.raises(OSError):
        windows_gateway.open_folder_window(r'E:\素材\S029')


def test_folder_activation_timeout_returns_gateway_failure(gateways, monkeypatch):
    def timeout(*args):
        raise windows_gateway.subprocess.TimeoutExpired('activation', 12)
    monkeypatch.setattr(windows_gateway, 'launch_folder', timeout)
    local, _ = gateways
    assert request(local, 'POST', {'Host': 'localhost:8788', 'Origin': 'http://localhost:8788'}, '{}', '/api/works/1/open-folder')[0] == 502


def test_mcp_native_read_rpc_without_origin_is_allowed_only_on_exact_endpoint(gateways):
    local, remote = gateways
    for server, host in ((local, 'localhost:8788'), (remote, '192.0.2.10:8787')):
        headers = {'Host': host, 'Content-Type': 'application/json'}
        assert request(server, 'POST', headers, '{}', '/mcp')[0] == 200
        for path in ('/api/scans', '/mcp/other', '/mcp/', '/mcp?write=1'):
            assert request(server, 'POST', headers, '{}', path)[0] == 403
        assert request(server, 'PATCH', headers, '{}', '/mcp')[0] == 403
        headers['Origin'] = 'http://attacker.example'
        assert request(server, 'POST', headers, '{}', '/mcp')[0] == 403
