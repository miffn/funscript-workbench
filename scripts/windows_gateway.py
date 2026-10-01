"""Fixed upstream gateway. Only the Windows loopback listener grants host capability."""
from __future__ import annotations

import argparse
import base64
import http.client
import logging
import ntpath
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "x-workbench-host-key",
               "x-workbench-open-folder", "x-workbench-folder-root"}
MAX_BODY = 1024 * 1024


def decode_folder_path(encoded):
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("Missing folder authorization")
    path = base64.b64decode(encoded + "=" * (-len(encoded) % 4),
                            altchars=b"-_", validate=True).decode("utf-8")
    path = path.replace("/", "\\")
    if any(ord(char) < 32 for char in path) or path.startswith(("\\\\?\\", "\\\\.\\")):
        raise ValueError("Invalid folder path")
    drive, tail = ntpath.splitdrive(path)
    drive_absolute = bool(re.fullmatch(r"[A-Za-z]:", drive)) and tail.startswith("\\")
    share_parts = drive[2:].split("\\") if drive.startswith("\\\\") else []
    unc_absolute = (len(share_parts) == 2 and all(part and part not in {".", ".."} for part in share_parts)
                    and (not tail or tail.startswith("\\")))
    if not (drive_absolute or unc_absolute) or any(char in path for char in '*?"<>|'):
        raise ValueError("Invalid folder path")
    return ntpath.normpath(path)


def inside_folder_root(path, root):
    normalized, normalized_root = ntpath.normcase(path), ntpath.normcase(root)
    try:
        return ntpath.commonpath([normalized_root, normalized]) == normalized_root
    except ValueError:
        return False


def launch_folder(encoded_path, encoded_root=None):
    # Authorization comes only from the fixed local backend, never client headers.
    path, root = decode_folder_path(encoded_path), decode_folder_path(encoded_root)
    if not inside_folder_root(path, root):
        raise ValueError("Folder is outside the authorized root")
    folder, root_folder = Path(path), Path(root)
    if not folder.is_dir() or not root_folder.is_dir():
        raise ValueError("Folder is no longer available")
    resolved, resolved_root = str(folder.resolve(strict=True)), str(root_folder.resolve(strict=True))
    if not inside_folder_root(ntpath.normpath(resolved), ntpath.normpath(resolved_root)):
        raise ValueError("Folder resolves outside the authorized root")
    subprocess.Popen([r"C:\Windows\explorer.exe", path], shell=False)


def request_headers(headers, key: str | None):
    extra_hop = {part.strip().lower() for part in headers.get("Connection", "").split(",")}
    result = {name: value for name, value in headers.items()
              if name.lower() not in HOP_HEADERS | extra_hop}
    if key:
        result["X-Workbench-Host-Key"] = key
    return result


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def fail(self, code, message):
        body = message.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def forward(self):
        host = self.headers.get("Host", "").lower()
        if host not in self.server.allowed_hosts:
            return self.fail(403, "不支持此访问地址")
        if not self.path.startswith("/") or self.path.startswith("//"):
            return self.fail(400, "请求路径无效")
        if self.command not in {"GET", "HEAD", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"}:
            return self.fail(405, "不支持此请求方法")
        if self.command in {"POST", "PATCH", "PUT", "DELETE"}:
            if self.headers.get("Origin", "") != f"http://{host}":
                return self.fail(403, "请从工作台页面发起操作")
        if self.headers.get("Transfer-Encoding"):
            return self.fail(400, "不支持分块请求体")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.fail(400, "请求长度无效")
        if not 0 <= length <= MAX_BODY:
            return self.fail(413, "请求体过大")
        self.connection.settimeout(60)
        body = self.rfile.read(length) if length else None
        upstream = http.client.HTTPConnection("127.0.0.1", self.server.upstream_port, timeout=60)
        try:
            upstream.request(self.command, self.path, body=body,
                             headers=request_headers(self.headers, self.server.host_key))
            response = upstream.getresponse()
            payload = response.read()
            folder = response.getheader("X-Workbench-Open-Folder")
            folder_root = response.getheader("X-Workbench-Folder-Root")
            if folder and self.server.host_key and self.command == "POST" and response.status == 200 and re.fullmatch(r"/api/works/\d+/(?:preview/)?open-folder", self.path):
                try:
                    launch_folder(folder, folder_root)
                except (OSError, ValueError):
                    return self.fail(502, "打开文件夹失败，请检查目录是否仍存在")
            self.send_response(response.status, response.reason)
            for name, value in response.getheaders():
                if name.lower() not in HOP_HEADERS | {"content-length", "server", "date"}:
                    self.send_header(name, value)
            size = response.getheader("Content-Length", "0") if self.command == "HEAD" else str(len(payload))
            self.send_header("Content-Length", size)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except (OSError, http.client.HTTPException):
            self.fail(502, "WSL 服务暂时不可用，请检查服务运行状态")
        finally:
            upstream.close()

    do_GET = do_HEAD = do_POST = do_PATCH = do_PUT = do_DELETE = do_OPTIONS = forward

    def log_message(self, fmt, *args):
        logging.info("%s %s", self.server.server_address, fmt % args)


def make_server(bind, port, allowed_hosts, upstream_port, host_key=None):
    server = ThreadingHTTPServer((bind, port), GatewayHandler)
    server.daemon_threads = True
    server.allowed_hosts = {value.lower() for value in allowed_hosts}
    server.upstream_port = upstream_port
    server.host_key = host_key
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lan-host", required=True)
    parser.add_argument("--host-key-file", type=Path, required=True)
    parser.add_argument("--local-port", type=int, default=8788)
    parser.add_argument("--lan-port", type=int, default=8787)
    parser.add_argument("--upstream-port", type=int, default=8789)
    args = parser.parse_args()
    key = args.host_key_file.read_text(encoding="utf-8").strip()
    if len(key) < 32:
        raise ValueError("Host key is missing or invalid")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    local = make_server("127.0.0.1", args.local_port,
                        {f"localhost:{args.local_port}", f"127.0.0.1:{args.local_port}"},
                        args.upstream_port, key)
    lan = make_server(args.lan_host, args.lan_port, {f"{args.lan_host}:{args.lan_port}"},
                      args.upstream_port)
    thread = threading.Thread(target=local.serve_forever, daemon=True)
    thread.start()
    try:
        lan.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        local.shutdown()
        local.server_close()
        lan.server_close()


if __name__ == "__main__":
    main()
