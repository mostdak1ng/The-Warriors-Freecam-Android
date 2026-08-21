#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 mostdak1ng
"""Read bytes at an EE virtual address straight from the ELF inside the ISO.

Static data such as vtables is authoritative here, so this recovers original
values without needing a save state or a running game.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

SECTOR_SIZE = 2048


def directory_records(handle, extent: int, size: int):
    handle.seek(extent * SECTOR_SIZE)
    data = handle.read(size)
    offset = 0
    while offset < len(data):
        length = data[offset]
        if not length:
            offset = (offset + SECTOR_SIZE) & ~(SECTOR_SIZE - 1)
            continue
        record = data[offset : offset + length]
        name_length = record[32]
        name = record[33 : 33 + name_length].decode("ascii", "replace").split(";", 1)[0]
        yield {
            "extent": struct.unpack_from("<I", record, 2)[0],
            "size": struct.unpack_from("<I", record, 10)[0],
            "name": name,
        }
        offset += length


def load_elf(iso: Path, name: str = "SLUS_212.15") -> bytes:
    with iso.open("rb") as handle:
        handle.seek(16 * SECTOR_SIZE)
        pvd = handle.read(SECTOR_SIZE)
        if pvd[1:6] != b"CD001":
            raise ValueError("not an ISO-9660 primary volume descriptor")
        root = pvd[156 : 156 + pvd[156]]
        extent = struct.unpack_from("<I", root, 2)[0]
        size = struct.unpack_from("<I", root, 10)[0]
        for record in directory_records(handle, extent, size):
            if record["name"].upper() == name.upper():
                handle.seek(record["extent"] * SECTOR_SIZE)
                return handle.read(record["size"])
    raise FileNotFoundError(f"{name} not found in {iso}")


def read_vaddr(elf: bytes, vaddr: int, length: int) -> bytes:
    """Map an EE virtual address through the ELF program headers."""
    if elf[:4] != b"\x7fELF":
        raise ValueError("not an ELF")
    phoff = struct.unpack_from("<I", elf, 0x1C)[0]
    phentsize = struct.unpack_from("<H", elf, 0x2A)[0]
    phnum = struct.unpack_from("<H", elf, 0x2C)[0]
    for index in range(phnum):
        base = phoff + index * phentsize
        p_type, p_offset, p_vaddr, _, p_filesz = struct.unpack_from("<5I", elf, base)
        if p_type != 1:
            continue
        if p_vaddr <= vaddr < p_vaddr + p_filesz:
            start = p_offset + (vaddr - p_vaddr)
            return elf[start : start + length]
    raise ValueError(f"0x{vaddr:08X} is not inside a loadable segment")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("address", type=lambda v: int(v, 0))
    parser.add_argument("--length", type=lambda v: int(v, 0), default=0x100)
    parser.add_argument("--iso", type=Path, default=Path("C:/DevMST/TheWarriors/Warriors, The (USA).iso"))
    parser.add_argument("--words", action="store_true")
    args = parser.parse_args()

    data = read_vaddr(load_elf(args.iso), args.address, args.length)
    if args.words:
        for index in range(0, len(data) - 3, 4):
            word = struct.unpack_from("<I", data, index)[0]
            print(f"  {args.address + index:08X}  {word:08X}")
    else:
        print(data.hex())


if __name__ == "__main__":
    main()
