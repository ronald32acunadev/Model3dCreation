import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from model3d_engine.api.app import LoopbackGuard

from .test_api import assert_error, image_bytes, make_client

LAN_URL = "http://192.168.90.10:8765"
LAN_WS_URL = "ws://192.168.90.10:8765"
UNAUTHORIZED = "Send Authorization: Bearer <key> to use the engine from the network"


def lan_client(tmp_path, api_keys=("k1",)):
    return make_client(
        tmp_path,
        lan_host="192.168.90.10",
        api_keys=api_keys,
        base_url=LAN_URL,
        wait_ready=False,
    )


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_lan_request_without_key_is_unauthorized(tmp_path):
    with lan_client(tmp_path) as client:
        response = client.get("/v1/health")
        assert response.status_code == 401
        assert response.json() == {"error": {"code": "unauthorized", "message": UNAUTHORIZED}}


def test_lan_request_with_wrong_key_is_unauthorized(tmp_path):
    with lan_client(tmp_path) as client:
        assert_error(client.get("/v1/health", headers=bearer("nope")), 401, "unauthorized")


def test_lan_request_with_valid_key_passes(tmp_path):
    with lan_client(tmp_path, api_keys=("k1", "k2")) as client:
        assert client.get("/v1/health", headers=bearer("k2")).status_code == 200


def test_lan_request_without_bearer_prefix_is_unauthorized(tmp_path):
    with lan_client(tmp_path) as client:
        assert_error(client.get("/v1/health", headers={"Authorization": "k1"}), 401, "unauthorized")


def test_loopback_request_needs_no_key(tmp_path):
    with make_client(tmp_path, api_keys=("k1",), base_url="http://127.0.0.1:8765") as client:
        assert client.get("/v1/health").status_code == 200


def test_ipv6_loopback_connection_needs_no_key():
    # TestClient cannot express an IPv6 base URL, so the guard is driven with the ASGI scope directly.
    reached = []

    async def app(scope, receive, send):
        reached.append(scope)

    async def send(message):
        raise AssertionError(f"rejected: {message}")

    guard = LoopbackGuard(app, allowed_origins=(), api_keys=("k1",))
    scope = {"type": "http", "method": "GET", "headers": [(b"host", b"[::1]:8765")], "server": ("::1", 8765)}
    asyncio.run(guard(scope, None, send))
    assert len(reached) == 1


def test_lan_websocket_without_key_is_rejected(tmp_path):
    with lan_client(tmp_path) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"{LAN_WS_URL}/v1/jobs/x/events") as ws:
                ws.receive_text()
    assert closed.value.code == 4401


def test_lan_post_with_valid_key_and_foreign_origin_is_forbidden(tmp_path):
    with lan_client(tmp_path) as client:
        files = {"image": ("in.png", image_bytes(), "image/png")}
        headers = {**bearer("k1"), "Origin": "http://evil.example"}
        assert_error(client.post("/v1/jobs", files=files, headers=headers), 403, "forbidden_origin")


def test_foreign_host_with_valid_key_is_forbidden(tmp_path):
    with lan_client(tmp_path) as client:
        headers = {**bearer("k1"), "Host": "evil.example:8765"}
        assert_error(client.get("/v1/health", headers=headers), 403, "forbidden_origin")
