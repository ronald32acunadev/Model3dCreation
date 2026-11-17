"""Listening sockets: loopback plus the optional LAN address, on one port."""

import errno
import ipaddress
import json
import socket
from pathlib import Path

from model3d_engine.config import Settings


class BindError(Exception):
    """A listen address could not be bound; the message names the address and the cause."""


def listen_hosts(settings: Settings) -> list[str]:
    hosts = [settings.host]
    if settings.lan_host and settings.lan_host != settings.host:
        hosts.append(settings.lan_host)
    return hosts


def bind_sockets(hosts: list[str], port: int) -> list[socket.socket]:
    """Bind one listening-ready socket per host. Port 0 picks a free port that every host shares."""
    sockets: list[socket.socket] = []
    host = ""
    try:
        for host in hosts:
            address = "127.0.0.1" if host == "localhost" else host
            family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            sockets.append(sock)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((address, port))
            port = sock.getsockname()[1]
    except OSError as error:
        for sock in sockets:
            sock.close()
        if error.errno == errno.EADDRNOTAVAIL:
            raise BindError(f"{host} is not an address of this machine") from error
        if error.errno == errno.EADDRINUSE:
            raise BindError(f"Port {port} is already in use on {host}") from error
        raise
    return sockets


def write_engine_info(home: Path, port: int, hosts: list[str]) -> Path:
    """Record the bound port and hosts in home/engine.json for clients on this machine."""
    home.mkdir(parents=True, exist_ok=True)
    path = home / "engine.json"
    path.write_text(json.dumps({"port": port, "hosts": hosts}))
    return path
