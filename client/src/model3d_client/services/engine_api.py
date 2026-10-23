"""Non-blocking client for the engine's REST and WebSocket API, on the Qt event loop."""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from model3d_contracts.events import parse_event
from model3d_contracts.health import Health
from model3d_contracts.jobs import ErrorBody, Job, JobOptions
from pydantic import ValidationError
from PySide6.QtCore import QByteArray, QObject, QUrl, Signal
from PySide6.QtNetwork import QHttpMultiPart, QHttpPart, QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

logger = logging.getLogger(__name__)
HEALTH_TIMEOUT_MS = 3000
REQUEST_TIMEOUT_MS = 30000
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


@dataclass
class ApiError:
    status: int | None
    code: str
    message: str


class EventStream(QObject):
    """A job's event socket: emits parsed events, then `closed` once, whatever ended it."""

    event = Signal(object)
    closed = Signal()

    def __init__(self, url: QUrl, parent: QObject | None = None):
        super().__init__(parent)
        self._done = False
        self._socket = QWebSocket(parent=self)
        self._socket.textMessageReceived.connect(self._on_message)
        self._socket.disconnected.connect(self._finish)
        self._socket.errorOccurred.connect(self._on_error)
        self._socket.open(url)

    def close(self) -> None:
        self._socket.close()

    def _on_message(self, text: str) -> None:
        try:
            self.event.emit(parse_event(text))
        except ValidationError as error:
            logger.warning("Ignoring unreadable engine event: %s", error)

    def _on_error(self, _error) -> None:
        logger.info("Event socket error: %s", self._socket.errorString())
        self._finish()

    def _finish(self) -> None:
        if not self._done:
            self._done = True
            self.closed.emit()


class EngineApi(QObject):
    """Every call returns at once; its callback runs on the UI thread when the answer arrives."""

    def __init__(self, base_url: str = "http://127.0.0.1:8765", parent: QObject | None = None):
        super().__init__(parent)
        self.base_url = base_url.rstrip("/")
        self._manager = QNetworkAccessManager(self)
        self._replies: set[QNetworkReply] = set()

    @property
    def pending(self) -> int:
        """Requests still in flight; an EngineApi must outlive them."""
        return len(self._replies)

    def health(self, on_done: Callable[[Health | None], None]) -> None:
        def done(reply: QNetworkReply) -> None:
            if self._status(reply) != 200:
                on_done(None)
                return
            try:
                on_done(Health.model_validate_json(bytes(reply.readAll().data())))
            except ValidationError as error:
                logger.warning("Unreadable health answer: %s", error)
                on_done(None)

        self._send(self._manager.get(self._request("/v1/health", HEALTH_TIMEOUT_MS)), done)

    def submit(self, image: Path, options: JobOptions, on_done: Callable[[Job | ApiError], None]) -> None:
        multi = QHttpMultiPart(QHttpMultiPart.ContentType.FormDataType)
        image_part = QHttpPart()
        image_part.setHeader(
            QNetworkRequest.KnownHeaders.ContentDispositionHeader,
            f'form-data; name="image"; filename="{image.name}"',
        )
        image_part.setHeader(
            QNetworkRequest.KnownHeaders.ContentTypeHeader,
            IMAGE_TYPES.get(image.suffix.lower(), "application/octet-stream"),
        )
        image_part.setBody(QByteArray(image.read_bytes()))
        options_part = QHttpPart()
        options_part.setHeader(QNetworkRequest.KnownHeaders.ContentDispositionHeader, 'form-data; name="options"')
        options_part.setBody(QByteArray(options.model_dump_json().encode()))
        multi.append(image_part)
        multi.append(options_part)
        reply = self._manager.post(self._request("/v1/jobs"), multi)
        multi.setParent(reply)
        self._send(reply, self._job_handler(202, on_done))

    def submit_rig(self, model: Path, on_done: Callable[[Job | ApiError], None]) -> None:
        """Ask for a skeleton on an existing model (a GLB file)."""
        multi = QHttpMultiPart(QHttpMultiPart.ContentType.FormDataType)
        model_part = QHttpPart()
        model_part.setHeader(
            QNetworkRequest.KnownHeaders.ContentDispositionHeader,
            f'form-data; name="model"; filename="{model.name}"',
        )
        model_part.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "model/gltf-binary")
        model_part.setBody(QByteArray(model.read_bytes()))
        multi.append(model_part)
        # A dense model can take a moment to upload, even over loopback.
        reply = self._manager.post(self._request("/v1/rig-jobs", 120000), multi)
        multi.setParent(reply)
        self._send(reply, self._job_handler(202, on_done))

    def cancel(self, job_id: str, on_done: Callable[[Job | ApiError], None]) -> None:
        reply = self._manager.deleteResource(self._request(f"/v1/jobs/{job_id}"))
        self._send(reply, self._job_handler(202, on_done))

    def job(self, job_id: str, on_done: Callable[[Job | ApiError], None]) -> None:
        self._send(self._manager.get(self._request(f"/v1/jobs/{job_id}")), self._job_handler(200, on_done))

    def download_result(self, job_id: str, target: Path, on_done: Callable[[Path | ApiError], None]) -> None:
        def done(reply: QNetworkReply) -> None:
            if self._status(reply) != 200:
                on_done(self._error(reply))
                return
            part = target.with_suffix(".part")
            try:
                part.write_bytes(bytes(reply.readAll().data()))
                os.replace(part, target)
            except OSError as error:
                on_done(ApiError(status=None, code="write_failed", message=str(error)))
                return
            on_done(target)

        # The paint stage can make a large GLB; it still comes from loopback.
        self._send(self._manager.get(self._request(f"/v1/jobs/{job_id}/result", 120000)), done)

    def open_events(self, job_id: str) -> EventStream:
        url = QUrl(f"{self.base_url}/v1/jobs/{job_id}/events")
        url.setScheme("wss" if url.scheme() == "https" else "ws")
        return EventStream(url, self)

    def _request(self, path: str, timeout_ms: int = REQUEST_TIMEOUT_MS) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(self.base_url + path))
        request.setTransferTimeout(timeout_ms)
        return request

    def _send(self, reply: QNetworkReply, handler: Callable[[QNetworkReply], None]) -> None:
        # Keep the reply referenced until it finishes, or Python may collect it mid-flight.
        self._replies.add(reply)

        def finished() -> None:
            self._replies.discard(reply)
            try:
                handler(reply)
            finally:
                reply.deleteLater()

        reply.finished.connect(finished)

    def _job_handler(self, success: int, on_done: Callable[[Job | ApiError], None]) -> Callable[[QNetworkReply], None]:
        def done(reply: QNetworkReply) -> None:
            if self._status(reply) == success:
                on_done(Job.model_validate_json(bytes(reply.readAll().data())))
            else:
                on_done(self._error(reply))

        return done

    @staticmethod
    def _status(reply: QNetworkReply) -> int | None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        return int(status) if status is not None else None

    def _error(self, reply: QNetworkReply) -> ApiError:
        status = self._status(reply)
        if status is None:
            return ApiError(status=None, code="engine_unreachable", message=reply.errorString())
        body = bytes(reply.readAll().data())
        try:
            error = ErrorBody.model_validate_json(body).error
            return ApiError(status=status, code=error.code, message=error.message)
        except ValidationError:
            return ApiError(status=status, code="internal_error", message=body.decode(errors="replace")[:500])
