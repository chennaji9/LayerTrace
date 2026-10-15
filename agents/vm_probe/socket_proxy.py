"""A GET-only Unix socket gateway. No Docker write endpoints are exposed."""

from __future__ import annotations

import http.client
import os
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str):
        super().__init__("localhost", timeout=20)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        allowed = path in ("/containers/json", "/events") or re.fullmatch(
            r"/containers/[0-9a-f]{64}/(json|stats)", path
        )
        if not allowed:
            self.send_error(403, "Read-only endpoint allowlist")
            return
        connection = UnixHTTPConnection(os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock"))
        try:
            connection.request("GET", self.path, headers={"Host": "localhost"})
            response = connection.getresponse()
            data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                self.send_error(502, "Docker response exceeds bounded limit")
                return
            self.send_response(response.status)
            self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (OSError, http.client.HTTPException):
            self.send_error(502, "Docker socket unavailable")
        finally:
            connection.close()

    def do_POST(self):
        self.send_error(403, "Docker writes are prohibited")

    do_DELETE = do_POST
    do_PUT = do_POST
    do_PATCH = do_POST

    def log_message(self, *_args):
        pass


def main():
    ThreadingHTTPServer(
        (
            os.environ.get("DOCKER_PROXY_BIND_HOST", "0.0.0.0"),
            int(os.environ.get("DOCKER_PROXY_PORT", "2375")),
        ),
        Handler,
    ).serve_forever()


if __name__ == "__main__":
    main()
