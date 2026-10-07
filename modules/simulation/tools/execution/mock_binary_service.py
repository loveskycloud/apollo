#!/usr/bin/env python3
"""Minimal binary ID service for local integration; replace its API later."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import threading


def make_server(identity, archive, *, host='127.0.0.1', port=8088, sha256=None):
    archive = Path(archive).resolve(strict=True)
    if not archive.is_file():
        raise ValueError('Binary archive must be a file')
    if sha256 is None:
        digest = hashlib.sha256()
        with archive.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        sha256 = digest.hexdigest()
    counts = {}
    lock = threading.Lock()
    metadata_route = '/binaries/' + identity
    archive_route = '/archives/' + identity + '/binary.tar.gz'

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                counts[self.path] = counts.get(self.path, 0) + 1
            if self.path == metadata_route:
                self.json_response(dict(id=identity, archive_url=archive_route,
                                        sha256=sha256, size_bytes=archive.stat().st_size))
            elif self.path == '/stats':
                with lock:
                    value = dict(counts)
                self.json_response(value)
            elif self.path == archive_route:
                self.send_response(200)
                self.send_header('Content-Type', 'application/gzip')
                self.send_header('Content-Length', str(archive.stat().st_size))
                self.end_headers()
                with archive.open('rb') as source:
                    shutil.copyfileobj(source, self.wfile, length=1024 * 1024)
            else:
                self.send_error(404, 'Unknown binary ID or route')

        def json_response(self, value):
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary-id', default='123143')
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8088)
    args = parser.parse_args()
    if not args.binary_id.isascii() or not args.binary_id.isdigit() or int(args.binary_id) <= 0:
        parser.error('Binary ID must be positive decimal digits')
    server = make_server(args.binary_id, args.archive, host=args.host, port=args.port)
    print(f'Binary service: http://{args.host}:{server.server_port}/binaries/{args.binary_id}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
