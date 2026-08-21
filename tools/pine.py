#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 mostdak1ng
"""Minimal PINE client used only by the development loop.

The shipped mod is a .pnach text file and needs none of this.  This client
exists so a candidate routine can be installed into a live PCSX2 session and
iterated on without restarting the game.
"""

from __future__ import annotations

import socket
import struct

READ8, READ16, READ32, READ64 = 0, 1, 2, 3
WRITE8, WRITE16, WRITE32, WRITE64 = 4, 5, 6, 7
VERSION, TITLE, GAME_ID, UUID, GAME_VERSION, STATUS = 8, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F

STATUS_RUNNING, STATUS_PAUSED, STATUS_SHUTDOWN = 0, 1, 2


class PineError(RuntimeError):
    pass


class Pine:
    def __init__(self, host: str = "127.0.0.1", port: int = 28011, timeout: float = 3.0):
        self.socket = socket.create_connection((host, port), timeout=timeout)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def __enter__(self) -> "Pine":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.socket.close()

    def _recv_exact(self, size: int) -> bytes:
        chunks, remaining = [], size
        while remaining:
            chunk = self.socket.recv(remaining)
            if not chunk:
                raise PineError("PCSX2 closed the PINE connection")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def request(self, payload: bytes) -> bytes:
        self.socket.sendall(struct.pack("<I", len(payload) + 4) + payload)
        size = struct.unpack("<I", self._recv_exact(4))[0]
        if size < 5:
            raise PineError(f"invalid PINE response length {size}")
        response = self._recv_exact(size - 4)
        if response[0] != 0:
            raise PineError("PCSX2 rejected the PINE request")
        return response[1:]

    def status(self) -> int:
        return struct.unpack("<I", self.request(bytes([STATUS])))[0]

    def text(self, opcode: int) -> str:
        response = self.request(bytes([opcode]))
        size = struct.unpack_from("<I", response, 0)[0]
        return response[4 : 4 + size].rstrip(b"\0").decode("utf-8", "replace")

    def read8(self, address: int) -> int:
        return self.request(struct.pack("<BI", READ8, address))[0]

    def read32(self, address: int) -> int:
        return struct.unpack("<I", self.request(struct.pack("<BI", READ32, address)))[0]

    def read64(self, address: int) -> int:
        return struct.unpack("<Q", self.request(struct.pack("<BI", READ64, address)))[0]

    def write8(self, address: int, value: int) -> None:
        self.request(struct.pack("<BIB", WRITE8, address, value & 0xFF))

    def write32(self, address: int, value: int) -> None:
        self.request(struct.pack("<BII", WRITE32, address, value & 0xFFFFFFFF))

    def write64(self, address: int, value: int) -> None:
        self.request(struct.pack("<BIQ", WRITE64, address, value & 0xFFFFFFFFFFFFFFFF))

    def read_block(self, address: int, size: int) -> bytes:
        """Batch reads into one round trip.  PINE has no block opcode."""
        payload, count = bytearray(), 0
        for offset in range(0, size & ~7, 8):
            payload += struct.pack("<BI", READ64, address + offset)
            count += 1
        for offset in range(size & ~7, size):
            payload += struct.pack("<BI", READ8, address + offset)
            count += 1
        response = self.request(bytes(payload))
        out, cursor = bytearray(), 0
        for index in range(count):
            width = 8 if index < (size // 8) else 1
            out += response[cursor : cursor + width]
            cursor += width
        return bytes(out)

    def write_block(self, address: int, data: bytes) -> None:
        payload = bytearray()
        for offset in range(0, len(data) & ~3, 4):
            word = struct.unpack_from("<I", data, offset)[0]
            payload += struct.pack("<BII", WRITE32, address + offset, word)
        for offset in range(len(data) & ~3, len(data)):
            payload += struct.pack("<BIB", WRITE8, address + offset, data[offset])
        self.request(bytes(payload))
