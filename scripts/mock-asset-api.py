#!/usr/bin/env python3
"""
Mock Asset API for OBJ-2 TapirX DICOM Discovery verification.
Accepts POST /api/assets/upsert, writes payloads to output file, returns 200.
Replaces BlueFlow for TapirX integration testing.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


OUTPUT_DIR = os.environ.get("MOCK_ASSET_OUTPUT", "/output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "assets.jsonl")


class MockAssetHandler(BaseHTTPRequestHandler):
    """Handle POST /api/assets/upsert and GET /health."""

    def _send_json(self, status: int, body: dict) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/api/assets/upsert":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                payload = json.loads(body)
                os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
                with open(OUTPUT_FILE, "a") as f:
                    f.write(json.dumps(payload) + "\n")
                self._send_json(200, {"status": "ok"})
            except (json.JSONDecodeError, OSError) as e:
                self._send_json(400, {"error": str(e)})
            return
        self.send_response(404)
        self.end_headers()


def main() -> None:
    server = HTTPServer(("0.0.0.0", 8000), MockAssetHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
