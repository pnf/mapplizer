import pytest

from mapplizer.protobuf import ProtobufError, field_map, read_varint


def test_read_varint_single_byte():
    assert read_varint(b"\x08", 0) == (8, 1)


def test_read_varint_multi_byte():
    # 300 = 0b10_0101100 -> 0xac 0x02
    assert read_varint(b"\xac\x02", 0) == (300, 2)


def test_read_varint_truncated():
    with pytest.raises(ProtobufError, match="truncated"):
        read_varint(b"\x80", 0)


def test_field_map_mixed_types():
    # field 1 varint = 9902, field 2 length-delimited = b"hi"
    buf = b"\x08\xae\x4d" + b"\x12\x02hi"
    assert field_map(buf) == {1: [9902], 2: [b"hi"]}


def test_field_map_repeats_accumulate():
    buf = b"\x08\x01\x08\x02\x08\x03"
    assert field_map(buf) == {1: [1, 2, 3]}


def test_length_overrun_is_rejected():
    with pytest.raises(ProtobufError, match="overruns"):
        field_map(b"\x12\x7fshort")


def test_uint64_muid_survives_decoding():
    # 17539293709750984951 exceeds JS's safe integer range; Python must keep it exact.
    buf = b"\x10\xf7\xc1\xcd\xe7\xba\xd6\x85\xb4\xf3\x01"
    assert field_map(buf)[2][0] == 17539293709750984951
