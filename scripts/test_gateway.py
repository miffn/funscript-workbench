import http.client
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from scripts.windows_gateway import make_server
from scripts import windows_gateway


class EchoHandler(BaseHTTPRequestHandler):
    def reply(self):
        length = int(self.headers.get("Content-Length", "0"))
        result = json.dumps({"host": self.headers.get("Host"),
                             "key": self.headers.get("X-Workbench-Host-Key"),
                             "origin": self.headers.get("Origin"),
                             "body": self.rfile.read(length).decode()}).encode()
        self.send_response(200)
        if self.path.endswith("/open-folder"):
            self.send_header("X-Workbench-Open-Folder", base64.urlsafe_b64encode(b"D:\\Media\\2026\\S029").decode())
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
    remote = make_server("127.0.0.1", 0, {"192.0.2.6:8787"}, upstream.server_port)
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


def test_loopback_grants_key_and_lan_strips_spoofed_key(gateways):
    local, remote = gateways
    status, body = request(local, headers={"Host": "localhost:8788", "X-Workbench-Host-Key": "spoof"})
    assert status == 200 and json.loads(body)["key"] == "trusted-secret"
    status, body = request(remote, headers={"Host": "192.0.2.6:8787", "X-Workbench-Host-Key": "spoof"})
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
    headers = {"Host": "192.0.2.6:8787", "Connection": "X-Workbench-Host-Key", "X-Workbench-Host-Key": "spoof"}
    assert json.loads(request(remote, headers=headers)[1])["key"] is None


def test_only_local_open_route_launches_folder(gateways, monkeypatch):
    local, remote = gateways
    launched = []
    monkeypatch.setattr(windows_gateway, "launch_folder", launched.append)
    request(local, "POST", {"Host": "localhost:8788", "Origin": "http://localhost:8788"}, "{}", "/api/works/1/open-folder")
    assert len(launched) == 1
    request(remote, "POST", {"Host": "192.0.2.6:8787", "Origin": "http://192.0.2.6:8787"}, "{}", "/api/works/1/open-folder")
    request(local, "GET", {"Host": "localhost:8788"}, path="/api/works/1/open-folder")
    assert len(launched) == 1


def test_preview_folder_route_preserves_host_only_opening(gateways, monkeypatch):
    local, remote = gateways
    launched = []
    monkeypatch.setattr(windows_gateway, "launch_folder", launched.append)
    local_headers = {"Host": "localhost:8788", "Origin": "http://localhost:8788"}
    path = "/api/works/54/preview/open-folder"
    assert request(local, "POST", local_headers, "{}", path)[0] == 200
    assert len(launched) == 1
    remote_headers = {"Host": "192.0.2.6:8787", "Origin": "http://192.0.2.6:8787",
                      "X-Workbench-Host-Key": "spoof"}
    request(remote, "POST", remote_headers, "{}", path)
    request(local, "GET", {"Host": "localhost:8788"}, path=path)
    request(local, "POST", local_headers, "{}", "/api/works/54/other/open-folder")
    assert len(launched) == 1


@pytest.mark.parametrize("path", [r"D:\private\folder", r"C:\Windows", r"D:\Media\2026\..\other", r"\\remote\share"])
def test_launcher_rejects_paths_outside_inventory(path):
    encoded = base64.urlsafe_b64encode(path.encode()).decode()
    with pytest.raises(ValueError):
        windows_gateway.launch_folder(encoded)
