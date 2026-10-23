"""HTTP client for the Model3D engine on 127.0.0.1:8765, standard library only.

Every call blocks, so the add-on runs them on a worker thread, never on Blender's main thread.
"""

import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class EngineError(Exception):
    """An engine error with its stable code (docs/api.md)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class EngineUnreachable(EngineError):
    """Nothing answers on the engine's port: the desktop app is closed."""

    def __init__(self, message: str):
        super().__init__("engine_unreachable", message)


def _multipart(parts: list[tuple[str, str | None, str | None, bytes]]) -> tuple[bytes, str]:
    """Encode (name, filename, content type, data) parts as multipart/form-data."""
    boundary = uuid.uuid4().hex
    body = bytearray()
    for name, filename, content_type, data in parts:
        body += f"--{boundary}\r\n".encode()
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        body += f"Content-Disposition: {disposition}\r\n".encode()
        if content_type is not None:
            body += f"Content-Type: {content_type}\r\n".encode()
        body += b"\r\n" + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


class EngineClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765", timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: bytes | None = None, content_type: str | None = None,
              timeout: float | None = None) -> bytes:
        request = urllib.request.Request(self.base_url + path, data=body, method=method)
        if content_type is not None:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read())["error"]
                raise EngineError(detail["code"], detail["message"]) from None
            except (ValueError, KeyError, TypeError):
                raise EngineError(f"http_{error.code}", str(error)) from None
        except (urllib.error.URLError, OSError) as error:
            raise EngineUnreachable(str(getattr(error, "reason", error))) from None

    def _json(self, method: str, path: str, **kwargs) -> dict:
        return json.loads(self._call(method, path, **kwargs))

    def health(self) -> dict:
        return self._json("GET", "/v1/health")

    def submit_image(self, image: Path, options: dict) -> dict:
        content_type = IMAGE_TYPES.get(image.suffix.lower(), "application/octet-stream")
        body, multipart = _multipart([
            ("image", image.name, content_type, image.read_bytes()),
            ("options", None, None, json.dumps(options).encode()),
        ])
        return self._json("POST", "/v1/jobs", body=body, content_type=multipart, timeout=60)

    def submit_rig(self, model: Path) -> dict:
        body, multipart = _multipart([("model", model.name, "model/gltf-binary", model.read_bytes())])
        return self._json("POST", "/v1/rig-jobs", body=body, content_type=multipart, timeout=120)

    def job(self, job_id: str) -> dict:
        return self._json("GET", f"/v1/jobs/{job_id}")

    def cancel(self, job_id: str) -> dict:
        return self._json("DELETE", f"/v1/jobs/{job_id}")

    def download(self, job_id: str, target: Path) -> Path:
        """Save the job's result GLB to target, writing a .part file first."""
        data = self._call("GET", f"/v1/jobs/{job_id}/result", timeout=120)
        part = target.with_suffix(".part")
        part.write_bytes(data)
        os.replace(part, target)
        return target
