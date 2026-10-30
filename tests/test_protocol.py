import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from screenhop.protocol import (cross_to_host, decode_device_state,
                                decode_hosts, decode_layout, direction_between,
                                enable_cross, layout_from_directions, parse, set_host_info,
                                set_layout)


def test_enable():
    assert enable_cross(True).hex() == "9f0101020101"
    assert enable_cross(False).hex() == "9f0101020100"


def test_cross_exact():
    assert cross_to_host(2, 5, 122).hex() == "9f06010a0208000000050000007a"


def test_layout_exact():
    assert set_layout((13, 12, 11, 14)).hex() == "9f070108000d010c020b030e"


def test_parse():
    p = parse(bytes.fromhex("9f080208000d010c020b030e"))
    assert (p.opcode, p.kind, p.payload[-1]) == (8, 2, 14)
    ack = parse(bytes.fromhex("9f0603010102"))
    assert (ack.opcode, ack.kind, ack.payload) == (6, 3, b"\x01\x01\x02")


def test_registration_and_state():
    assert set_host_info().hex() == "9f040106010009312e30"
    state = decode_device_state(bytes.fromhex("9f0302070102312e300101"))
    assert (state.host_index, state.crossing_enabled) == (2, True)
    hosts = decode_hosts(bytes.fromhex("9f050207010209312e3000"))
    assert len(hosts) == 1 and hosts[0].index == 2 and hosts[0].platform_code == 9
    layout = decode_layout(bytes.fromhex("9f080208000d010c020b030e"))
    assert layout == {0: 13, 1: 12, 2: 11, 3: 14}


def test_layout_direction_horizontal_and_vertical():
    assert int(direction_between({1: 0x0C, 2: 0x0B}, 1, 2)) == 3
    assert int(direction_between({1: 0x0B, 2: 0x0C}, 1, 2)) == 4
    assert int(direction_between({1: 0x15, 2: 0x0B}, 1, 2)) == 1
    assert int(direction_between({1: 0x0B, 2: 0x15}, 1, 2)) == 2


def test_dragged_layout_encoding():
    assert layout_from_directions(1, {2: 3}) == [0x0D, 0x0C, 0x0B, 0x0E]
    assert layout_from_directions(1, {2: 4}) == [0x0D, 0x0B, 0x0C, 0x0E]
    assert layout_from_directions(1, {2: 1}) == [0x16, 0x15, 0x0B, 0x17]
    assert layout_from_directions(1, {2: 2}) == [0x16, 0x0B, 0x15, 0x17]

