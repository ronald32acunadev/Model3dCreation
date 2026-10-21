import pytest
from starlette.websockets import WebSocketDisconnect

from model3d_contracts.events import parse_event

from .fakes import FakeRunner
from .test_api import make_client, post_job, wait_state


def read_until_close(ws) -> tuple[list, int]:
    events = []
    while True:
        try:
            events.append(parse_event(ws.receive_text()))
        except WebSocketDisconnect as closed:
            return events, closed.code


def test_ws_streams_snapshot_progress_completed(tmp_path):
    with make_client(tmp_path, shape=FakeRunner("shape.glb", delay=0.3)) as client:
        job_id = post_job(client).json()["job_id"]
        with client.websocket_connect(f"/v1/jobs/{job_id}/events") as ws:
            events, code = read_until_close(ws)
    types = [e.type for e in events]
    assert types[0] == "snapshot"
    assert types[-1] == "completed"
    assert "progress" in types
    assert {e.job_id for e in events} == {job_id}
    assert code == 1000


def test_ws_after_completion_gets_snapshot_and_final(tmp_path):
    with make_client(tmp_path) as client:
        job_id = post_job(client, options={"texture": False}).json()["job_id"]
        wait_state(client, job_id, "succeeded")
        with client.websocket_connect(f"/v1/jobs/{job_id}/events") as ws:
            events, code = read_until_close(ws)
    assert [e.type for e in events] == ["snapshot", "completed"]
    assert events[0].state == "succeeded"
    assert code == 1000


def test_ws_unknown_job_closes_4404(tmp_path):
    with make_client(tmp_path) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/v1/jobs/nope/events") as ws:
                ws.receive_text()
    assert closed.value.code == 4404
