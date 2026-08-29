"""Minimal protobuf wire-format reader.

Apple packs a shared guide's entire contents into the ``user`` query parameter
of a maps.apple.com/guides URL. The message is tiny and its shape is stable, so
a few dozen lines of varint decoding beats taking on a protobuf dependency and
a generated schema we'd have to keep in sync with Apple.
"""

from __future__ import annotations

from typing import Iterator, NamedTuple

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LENGTH = 2
WIRE_FIXED32 = 5


class Field(NamedTuple):
    number: int
    wire_type: int
    value: int | bytes


class ProtobufError(ValueError):
    """Raised when a buffer is not decodable as protobuf wire format."""


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Read a base-128 varint at ``pos``. Returns ``(value, new_pos)``."""
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ProtobufError("truncated varint")
        if shift > 63:
            raise ProtobufError("varint too long")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def iter_fields(buf: bytes) -> Iterator[Field]:
    """Yield top-level fields of a protobuf message.

    Values are returned raw: varints as ``int``, length-delimited fields as
    ``bytes`` (recurse with ``iter_fields`` to decode nested messages).
    """
    pos = 0
    while pos < len(buf):
        key, pos = read_varint(buf, pos)
        number, wire_type = key >> 3, key & 0x07
        if wire_type == WIRE_VARINT:
            value, pos = read_varint(buf, pos)
        elif wire_type == WIRE_LENGTH:
            length, pos = read_varint(buf, pos)
            end = pos + length
            if end > len(buf):
                raise ProtobufError("length-delimited field overruns buffer")
            value, pos = buf[pos:end], end
        elif wire_type == WIRE_FIXED64:
            value, pos = int.from_bytes(buf[pos : pos + 8], "little"), pos + 8
        elif wire_type == WIRE_FIXED32:
            value, pos = int.from_bytes(buf[pos : pos + 4], "little"), pos + 4
        else:
            raise ProtobufError(f"unsupported wire type {wire_type}")
        yield Field(number, wire_type, value)


def field_map(buf: bytes) -> dict[int, list[int | bytes]]:
    """Decode a message into ``{field_number: [values]}``."""
    out: dict[int, list[int | bytes]] = {}
    for field in iter_fields(buf):
        out.setdefault(field.number, []).append(field.value)
    return out
