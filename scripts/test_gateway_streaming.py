"""Windows-native gateway streaming tests; no production listeners are touched."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest
from scripts.windows_gateway import make_server

BODY = b'x' * (64 * 1024 * 3 + 13)


class MediaHandler(BaseHTTPRequestHandler):
    def reply(self):
        self.server.request_headers = dict(self.headers)
        ranged = self.headers.get('Range') == 'bytes=100-199'
        payload = BODY[100:200] if ranged else BODY
        self.send_response(206 if ranged else 200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'private, no-store')
        self.send_header('ETag', '"test-media"')
        self.send_header('X-Workbench-Host-Key', 'response-internal')
        if ranged:
            self.send_header('Content-Range', f'bytes 100-199/{len(BODY)}')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload[:64 * 1024])
            self.wfile.flush()
            if self.path == '/delayed':
                self.server.initial_sent.set()
                self.server.finish.wait(5)
            self.wfile.write(payload[64 * 1024:])
    do_GET = do_HEAD = reply
    def log_message(self, *args):
        pass


@pytest.fixture
def pair():
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), MediaHandler)
    upstream.initial_sent = threading.Event()
    upstream.finish = threading.Event()
    gateway = make_server('127.0.0.1', 0, {'localhost:8788'}, upstream.server_port, 'trusted-unit-key')
    for server in (upstream, gateway):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    yield gateway, upstream
    upstream.finish.set()
    for server in (gateway, upstream):
        server.shutdown(); server.server_close()


def connect(gateway, method='GET', path='/media', extra=None):
    connection = http.client.HTTPConnection('127.0.0.1', gateway.server_port, timeout=3)
    connection.request(method, path, headers={'Host': 'localhost:8788', **(extra or {})})
    return connection, connection.getresponse()


def test_head_and_range_headers_body_and_host_key_isolation(pair):
    gateway, upstream = pair
    connection, response = connect(gateway, 'HEAD')
    assert response.status == 200
    assert response.getheader('Content-Length') == str(len(BODY))
    assert response.read() == b''
    connection.close()
    connection, response = connect(gateway, extra={'Range': 'bytes=100-199', 'X-Workbench-Host-Key': 'spoof'})
    assert response.status == 206 and response.read() == BODY[100:200]
    assert response.getheader('Content-Range') == f'bytes 100-199/{len(BODY)}'
    assert response.getheader('Accept-Ranges') == 'bytes'
    assert response.getheader('Cache-Control') == 'private, no-store'
    assert response.getheader('ETag') == '"test-media"'
    assert response.getheader('X-Workbench-Host-Key') is None
    assert upstream.request_headers['X-Workbench-Host-Key'] == 'trusted-unit-key'
    connection.close()


def test_forwards_first_chunk_before_upstream_finishes(pair):
    gateway, upstream = pair
    connection, response = connect(gateway, path='/delayed')
    assert upstream.initial_sent.wait(1)
    assert response.read(64 * 1024) == BODY[:64 * 1024]
    assert not upstream.finish.is_set()
    upstream.finish.set()
    assert response.read() == BODY[64 * 1024:]
    connection.close()