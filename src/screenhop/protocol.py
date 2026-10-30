"""Huawei Mouse WYN keyboard/mouse crossing protocol.

The packet builders in this module are based on the Windows 1.0.26 binary and
its runtime log.  They deliberately contain no Bluetooth or desktop code so
they can be regression-tested on every build host.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct


MAGIC = 0x9F


class Direction(IntEnum):
    TOP = 1
    BOTTOM = 2
    LEFT = 3
    RIGHT = 4


class Kind(IntEnum):
    REQUEST = 1
    EVENT = 2
    ACK = 3


@dataclass(frozen=True)
class Packet:
    opcode: int
    kind: int
    payload: bytes
    raw: bytes


@dataclass(frozen=True)
class DeviceState:
    host_index: int
    version: str
    crossing_enabled: bool


@dataclass(frozen=True)
class Host:
    index: int
    platform_code: int
    version: str
    flags: int


def frame(opcode: int, kind: int, payload: bytes = b"") -> bytes:
    if len(payload) > 255:
        raise ValueError("payload too large")
    return bytes((MAGIC, opcode & 0xFF, kind & 0xFF, len(payload))) + payload


def parse(data: bytes) -> Packet:
    data = bytes(data)
    if len(data) < 3 or data[0] != MAGIC:
        raise ValueError("invalid ScreenHop packet")
    # Opcode 0x10 is observed as the three-byte query 9f 10 01.
    if len(data) == 3:
        return Packet(data[1], data[2], b"", data)
    # Cross acknowledgements are the exceptional six-byte form
    # 9f 06 03 STATUS SOURCE TARGET and do not carry a length byte.
    if len(data) == 6 and data[1] == 0x06 and data[2] == Kind.ACK:
        return Packet(data[1], data[2], data[3:6], data)
    size = data[3]
    if len(data) < 4 + size:
        raise ValueError("truncated ScreenHop packet")
    return Packet(data[1], data[2], data[4:4 + size], data)


def query_device_info() -> bytes:
    return frame(0x03, Kind.REQUEST, b"\x01")


def set_host_info(version: str = "1.0", slot: int = 0) -> bytes:
    value = version.encode("ascii")
    # Observed Windows request: 9f 04 01 06 01 00 09 31 2e 30
    return frame(0x04, Kind.REQUEST, bytes((1, slot & 0xFF, 9)) + value)


def decode_device_state(packet: Packet | bytes) -> DeviceState:
    p = parse(packet) if isinstance(packet, (bytes, bytearray)) else packet
    if p.opcode != 0x03 or p.kind != Kind.EVENT or len(p.payload) < 7:
        raise ValueError("not a device-info response")
    # Observed: 01 HOST VERSION[3] ENABLED PROTOCOL_REVISION
    version = p.payload[2:5].decode("ascii", errors="replace")
    return DeviceState(p.payload[1], version, p.payload[-2] == 1)


def decode_hosts(packet: Packet | bytes) -> list[Host]:
    p = parse(packet) if isinstance(packet, (bytes, bytearray)) else packet
    if p.opcode != 0x05 or p.kind != Kind.EVENT or not p.payload:
        raise ValueError("not a host-list response")
    count = p.payload[0]
    body = p.payload[1:]
    hosts = []
    # Firmware 1.0 uses fixed six-byte entries: INDEX PLATFORM VERSION[3] FLAGS.
    for offset in range(0, min(len(body), count * 6), 6):
        item = body[offset:offset + 6]
        if len(item) < 6:
            break
        hosts.append(Host(item[0], item[1], item[2:5].decode("ascii", errors="replace"), item[5]))
    return hosts


def enable_cross(enabled: bool) -> bytes:
    return frame(0x01, Kind.REQUEST, bytes((1, 1 if enabled else 0)))


def query_shortcut() -> bytes:
    return bytes((MAGIC, 0x10, Kind.REQUEST))


def set_layout(positions: tuple[int, int, int, int] | list[int]) -> bytes:
    if len(positions) != 4:
        raise ValueError("four hardware layout positions are required")
    payload = b"".join(bytes((index, value & 0xFF)) for index, value in enumerate(positions))
    return frame(0x07, Kind.REQUEST, payload)


def cross_to_host(target_host: int, x: int, y: int) -> bytes:
    """Build CrossToHost: 9f 06 01 0a TARGET 08 X_BE32 Y_BE32.

    TARGET is the mouse's host-slot index, not the screen-edge direction.
    Windows logs confirm a right-edge crossing from slot 2 to slot 1 emits 01.
    """
    target_host = int(target_host)
    if not 0 <= target_host <= 3:
        raise ValueError("target host index must be between 0 and 3")
    x = max(0, min(1000, int(x)))
    y = max(0, min(1000, int(y)))
    return frame(0x06, Kind.REQUEST, bytes((target_host, 8)) + struct.pack(">II", x, y))


def decode_layout(packet: Packet | bytes) -> dict[int, int]:
    p = parse(packet) if isinstance(packet, (bytes, bytearray)) else packet
    if p.opcode != 0x08 or p.kind != Kind.EVENT or len(p.payload) % 2:
        raise ValueError("not a layout response")
    return {p.payload[i]: p.payload[i + 1] for i in range(0, len(p.payload), 2)}


def direction_between(layout: dict[int, int], source_host: int,
                      target_host: int) -> Direction | None:
    """Return the source edge leading to target for Huawei layout cases.

    The firmware uses a different position alphabet for each layout shape:
    horizontal neighbours use 0B/0C, while vertical neighbours use 0B/15.
    The values describe ordered positions, not fixed global directions.
    """
    source = layout.get(int(source_host))
    target = layout.get(int(target_host))
    if source is None or target is None or source == target:
        return None
    pair = {source, target}
    if pair == {0x0B, 0x0C}:
        return Direction.LEFT if target == 0x0B else Direction.RIGHT
    if pair == {0x0B, 0x15}:
        return Direction.TOP if target == 0x0B else Direction.BOTTOM
    return None


def layout_from_directions(current_host: int, peer_directions: dict[int, int],
                           existing: dict[int, int] | None = None) -> list[int]:
    """Encode draggable UI positions into the mouse's four-slot layout."""
    current_host = int(current_host)
    peers = {int(index): int(direction) for index, direction in peer_directions.items()}
    if len(peers) == 1:
        peer, direction = next(iter(peers.items()))
        if {current_host, peer} == {1, 2}:
            if direction in (int(Direction.LEFT), int(Direction.RIGHT)):
                result = [0x0D, 0x0C, 0x0B, 0x0E]
                result[current_host] = 0x0C if direction == int(Direction.LEFT) else 0x0B
                result[peer] = 0x0B if direction == int(Direction.LEFT) else 0x0C
                return result
            if direction in (int(Direction.TOP), int(Direction.BOTTOM)):
                result = [0x16, 0x15, 0x0B, 0x17]
                result[current_host] = 0x15 if direction == int(Direction.TOP) else 0x0B
                result[peer] = 0x0B if direction == int(Direction.TOP) else 0x15
                return result

    code_for_direction = {
        int(Direction.LEFT): 0x0B, int(Direction.RIGHT): 0x0C,
        int(Direction.TOP): 0x0D, int(Direction.BOTTOM): 0x0E,
    }
    result = [int((existing or {}).get(i, (0x0D, 0x0C, 0x0B, 0x0E)[i]))
              for i in range(4)]
    used = set()
    for index, direction in peers.items():
        if 0 <= index < 4 and direction in code_for_direction:
            result[index] = code_for_direction[direction]
            used.add(result[index])
    remaining = [code for code in (0x0B, 0x0C, 0x0D, 0x0E) if code not in used]
    fill_indices = [current_host] + [i for i in range(4)
                                     if i != current_host and i not in peers]
    for index in fill_indices:
        if 0 <= index < 4 and remaining:
            current = result[index]
            result[index] = current if current in remaining else remaining[0]
            remaining.remove(result[index])
    return result


def describe(data: bytes) -> str:
    try:
        p = parse(data)
    except ValueError as exc:
        return f"invalid ({exc}): {bytes(data).hex(' ')}"
    names = {
        0x01: "enable-cross", 0x02: "cross-status", 0x03: "device-info",
        0x04: "host-info", 0x05: "host-list", 0x06: "cross-to-host",
        0x07: "set-layout", 0x08: "layout", 0x10: "shortcut-query",
        0x11: "shortcut-reply", 0x12: "cross-event",
    }
    return f"{names.get(p.opcode, f'op-{p.opcode:02x}')} kind={p.kind} payload={p.payload.hex(' ')}"

