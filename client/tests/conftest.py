import os
import struct
import zlib

import pytest

# Widgets render offscreen in tests; set before any Qt module is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def png_bytes(width: int = 2, height: int = 2) -> bytes:
    """A valid RGB PNG of the given size."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + b"\x80\x40\x20" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


@pytest.fixture
def png(tmp_path):
    path = tmp_path / "inputs" / "penguin.png"
    path.parent.mkdir()
    path.write_bytes(png_bytes())
    return path
