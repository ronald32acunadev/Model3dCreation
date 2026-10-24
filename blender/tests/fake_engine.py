"""A minimal engine stand-in on the standard library's http.server, run in a thread."""

import json
import threading
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def job(job_id="J1", state="queued", stage=None, progress=0.0, error=None) -> dict:
    base = f"/v1/jobs/{job_id}"
    return {
        "job_id": job_id, "state": state, "stage": stage, "progress": progress, "queue_position": None,
        "seed": 42, "created_at": "2026-10-22T10:00:00Z", "finished_at": None, "error": error,
        "links": {"self": base, "events": f"{base}/events", "result": f"{base}/result"},
    }


@dataclass
class Controller:
    health: dict = field(default_factory=lambda: {"ready": True, "rig": True, "rig_reason": None})
    submit: tuple[int, dict] = field(default_factory=lambda: (202, job()))
    jobs: list[dict] = field(default_factory=list)  # answers to GET /v1/jobs/{id}, in order (the last repeats)
    result: bytes = b"glTF result"
    received: list[dict] = field(default_factory=list)


def parse_multipart(content_type: str, body: bytes) -> dict[str, dict]:
    message = BytesParser(policy=HTTP).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    parts = {}
    for part in message.iter_parts():
        parts[part.get_param("name", header="content-disposition")] = {
            "filename": part.get_filename(),
            "content_type": part.get_content_type(),
            "data": part.get_payload(decode=True),
        }
    return parts


class FakeEngine:
    def __init__(self):
        self.controller = c = Controller()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status: int, body: bytes, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/v1/health":
                    self._send(200, json.dumps(c.health).encode())
                elif self.path.endswith("/result"):
                    self._send(200, c.result, "model/gltf-binary")
                else:
                    answer = c.jobs.pop(0) if len(c.jobs) > 1 else c.jobs[0]
                    self._send(200, json.dumps(answer).encode())

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                c.received.append({"path": self.path, "parts": parse_multipart(self.headers["Content-Type"], body)})
                status, answer = c.submit
                self._send(status, json.dumps(answer).encode())

            def do_DELETE(self):
                c.received.append({"path": self.path, "method": "DELETE"})
                self._send(202, json.dumps(job(state="shaping")).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
