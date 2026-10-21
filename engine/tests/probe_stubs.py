"""Probe targets run in spawned processes by the GPU probe tests."""

import time


def raising_probe(conn) -> None:
    raise RuntimeError("driver exploded")


def hanging_probe(conn) -> None:
    time.sleep(3600)


def reporting_probe(conn) -> None:
    conn.send(("error", "CUDA driver version is insufficient"))
