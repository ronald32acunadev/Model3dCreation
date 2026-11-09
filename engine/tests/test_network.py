import json
import socket

import pytest

from model3d_engine.config import Settings
from model3d_engine.net import BindError, bind_sockets, listen_hosts, write_engine_info


def test_bind_two_hosts_shares_one_port():
    socks = bind_sockets(["127.0.0.1", "127.0.0.2"], 0)
    try:
        ports = {sock.getsockname()[1] for sock in socks}
        assert len(socks) == 2
        assert len(ports) == 1
        port = ports.pop()
        assert port != 0
        for sock in socks:
            sock.listen()
        for host in ("127.0.0.1", "127.0.0.2"):
            socket.create_connection((host, port), timeout=2).close()
    finally:
        for sock in socks:
            sock.close()


def test_missing_address_fails_and_closes_bound_sockets():
    with pytest.raises(BindError) as raised:
        bind_sockets(["127.0.0.1", "192.0.2.1"], 0)
    assert str(raised.value) == "192.0.2.1 is not an address of this machine"


def test_busy_port_fails_naming_it():
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]
        with pytest.raises(BindError) as raised:
            bind_sockets(["127.0.0.1"], port)
        assert str(raised.value) == f"Port {port} is already in use on 127.0.0.1"
    finally:
        holder.close()


def test_listen_hosts(tmp_path):
    assert listen_hosts(Settings(home=tmp_path, hunyuan_dir=tmp_path)) == ["127.0.0.1"]
    lan = Settings(home=tmp_path, hunyuan_dir=tmp_path, lan_host="192.168.90.10")
    assert listen_hosts(lan) == ["127.0.0.1", "192.168.90.10"]


def test_write_engine_info(tmp_path):
    path = write_engine_info(tmp_path / "home", 9001, ["127.0.0.1", "192.168.90.10"])
    assert path == tmp_path / "home" / "engine.json"
    assert path.read_text() == json.dumps({"port": 9001, "hosts": ["127.0.0.1", "192.168.90.10"]})
