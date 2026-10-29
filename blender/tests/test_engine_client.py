import json
import socket

import pytest

from fake_engine import FakeEngine, job
from model3d.engine_client import EngineClient, EngineError, EngineUnreachable

PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture
def engine():
    with FakeEngine() as fake:
        yield fake


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_health_and_unreachable(engine):
    assert EngineClient(engine.url).health()["ready"] is True
    with pytest.raises(EngineUnreachable) as raised:
        EngineClient(f"http://127.0.0.1:{closed_port()}", timeout=1).health()
    assert raised.value.code == "engine_unreachable"


def test_submit_image_multipart_reaches_server(engine, tmp_path):
    image = tmp_path / "cat.png"
    image.write_bytes(PNG)
    answer = EngineClient(engine.url).submit_image(image, {"texture": True, "quality": "high", "rig": True})
    assert answer["job_id"] == "J1"
    received = engine.controller.received[0]
    assert received["path"] == "/v1/jobs"
    assert received["parts"]["image"]["filename"] == "cat.png"
    assert received["parts"]["image"]["content_type"] == "image/png"
    assert received["parts"]["image"]["data"] == PNG
    assert json.loads(received["parts"]["options"]["data"]) == {"texture": True, "quality": "high", "rig": True}


def test_submit_rig_sends_model_part(engine, tmp_path):
    model = tmp_path / "model.glb"
    model.write_bytes(b"glTF bytes")
    EngineClient(engine.url).submit_rig(model)
    received = engine.controller.received[0]
    assert received["path"] == "/v1/rig-jobs"
    assert received["parts"]["model"]["content_type"] == "model/gltf-binary"
    assert received["parts"]["model"]["data"] == b"glTF bytes"


def test_api_error_becomes_engine_error(engine, tmp_path):
    engine.controller.submit = (400, {"error": {"code": "rig_unavailable", "message": "UniRig is missing"}})
    image = tmp_path / "cat.png"
    image.write_bytes(PNG)
    with pytest.raises(EngineError) as raised:
        EngineClient(engine.url).submit_image(image, {})
    assert (raised.value.code, raised.value.message) == ("rig_unavailable", "UniRig is missing")
    assert not isinstance(raised.value, EngineUnreachable)


def test_job_and_cancel(engine):
    engine.controller.jobs = [job(state="painting", stage="paint", progress=0.5)]
    client = EngineClient(engine.url)
    assert client.job("J1")["stage"] == "paint"
    client.cancel("J1")
    assert engine.controller.received[-1] == {"path": "/v1/jobs/J1", "method": "DELETE"}


def test_download_writes_atomically(engine, tmp_path):
    target = tmp_path / "entry" / "model.glb"
    target.parent.mkdir()
    assert EngineClient(engine.url).download("J1", target) == target
    assert target.read_bytes() == b"glTF result"
    assert not target.with_suffix(".part").exists()
