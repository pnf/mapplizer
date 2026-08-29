"""Turn an Apple Maps share URL into a guide's name and membership.

A short link such as ``https://maps.apple/ug/yDaPfLzD_27SbtQL_DSGUA`` is a
301 to ``https://maps.apple.com/guides?user=<base64 protobuf>``. That protobuf
holds the whole guide::

    field 1 (string)            guide name
    field 2 (repeated message)  one per entry, in one of two shapes

    A point of interest:
      +- field 1 (varint)       resultProviderId
      +- field 2 (varint)       muid

    A dropped pin -- a user-placed marker with no business behind it:
      +- field 3 (string)       reverse-geocoded address
      +- field 4 (message)      +- field 1 (double) latitude
                                +- field 2 (double) longitude

So the guide's membership costs exactly one redirect -- no HTML, no JavaScript,
and dropped pins need no lookup at all.
"""

from __future__ import annotations

import base64
import binascii
import struct
import urllib.parse

from .model import GuideRef, PinRef, PlaceRef
from .protobuf import ProtobufError, field_map

GUIDE_NAME_FIELD = 1
GUIDE_PLACE_FIELD = 2
PLACE_PROVIDER_FIELD = 1
PLACE_MUID_FIELD = 2
PIN_ADDRESS_FIELD = 3
PIN_COORDINATES_FIELD = 4
PIN_LAT_FIELD = 1
PIN_LNG_FIELD = 2


class ResolveError(Exception):
    """The URL could not be resolved to a guide."""


def resolve_share_url(url: str, session) -> str:
    """Follow redirects from a share link to its canonical maps.apple.com URL."""
    response = session.get(url, allow_redirects=True, timeout=30)
    response.raise_for_status()
    return response.url


def _decode_base64(value: str) -> bytes:
    # Apple uses the standard alphabet, percent-encoded in the URL. Two things
    # mangle it in transit: form decoding turns "+" into a space, and some
    # rewriters swap in the URL-safe alphabet. Undo both.
    normalized = value.replace(" ", "+").replace("-", "+").replace("_", "/")
    padded = normalized + "=" * (-len(normalized) % 4)
    try:
        return base64.b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise ResolveError(f"`user` parameter is not valid base64: {exc}") from exc


def parse_guide_url(url: str) -> GuideRef:
    """Extract the guide name and place references from a canonical guide URL."""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)

    for key in ("user", "ref"):
        if key in params:
            payload = params[key][0]
            break
    else:
        raise ResolveError(
            f"no `user` parameter in {url!r}. Only user-created guides "
            "(maps.apple/ug/...) are supported; editorial guides are served "
            "from a different endpoint."
        )

    return parse_guide_ref(payload)


def parse_guide_ref(payload: str) -> GuideRef:
    """Decode a base64 guide ref -- the ``user`` parameter, or a card ``ref``."""
    raw = _decode_base64(payload)
    try:
        fields = field_map(raw)
    except ProtobufError as exc:
        raise ResolveError(f"guide payload is not valid protobuf: {exc}") from exc

    names = fields.get(GUIDE_NAME_FIELD, [])
    name = names[0].decode("utf-8", "replace") if names else "Apple Maps Guide"

    entries = []
    for raw_entry in fields.get(GUIDE_PLACE_FIELD, []):
        if not isinstance(raw_entry, bytes):
            continue
        try:
            sub = field_map(raw_entry)
        except ProtobufError:
            continue

        muid = sub.get(PLACE_MUID_FIELD, [None])[0]
        if muid is not None:
            provider = sub.get(PLACE_PROVIDER_FIELD, [None])[0]
            entries.append(
                # muids are uint64 and exceed JS's safe integer range; Apple's
                # own payloads carry them as strings, so we do the same.
                PlaceRef(muid=str(muid), result_provider_id=int(provider or 0))
            )
            continue

        pin = _parse_pin(sub)
        if pin is not None:
            entries.append(pin)

    if not entries:
        raise ResolveError("guide payload decoded but contained no places")

    return GuideRef(name=name, entries=tuple(entries))


def _read_double(value) -> float | None:
    """Protobuf fixed64 fields arrive as ints; reinterpret the bits as a double."""
    if not isinstance(value, int):
        return None
    try:
        return struct.unpack("<d", value.to_bytes(8, "little"))[0]
    except (OverflowError, struct.error):
        return None


def _parse_pin(sub: dict[int, list]) -> PinRef | None:
    coordinates = sub.get(PIN_COORDINATES_FIELD, [None])[0]
    if not isinstance(coordinates, bytes):
        return None
    try:
        point = field_map(coordinates)
    except ProtobufError:
        return None

    lat = _read_double(point.get(PIN_LAT_FIELD, [None])[0])
    lng = _read_double(point.get(PIN_LNG_FIELD, [None])[0])
    if lat is None or lng is None:
        return None

    address = sub.get(PIN_ADDRESS_FIELD, [b""])[0]
    address = address.decode("utf-8", "replace") if isinstance(address, bytes) else ""
    return PinRef(address=address, lat=lat, lng=lng)


def resolve(url: str, session) -> tuple[GuideRef, str]:
    """Resolve any Apple Maps guide URL. Returns ``(guide_ref, canonical_url)``."""
    canonical = url
    if "user=" not in url and "ref=" not in url:
        canonical = resolve_share_url(url, session)
    return parse_guide_url(canonical), canonical
