"""The end-to-end pipeline: share URL in, :class:`Guide` out."""

from __future__ import annotations

import logging
import pathlib

from .fetch import PlaceClient, make_session
from .model import Guide, Place, PinRef, PlaceRef
from .normalize import normalize
from .resolve import resolve

log = logging.getLogger(__name__)


class IncompleteExport(Exception):
    """Fewer places resolved than the guide declares."""


def _place_from_pin(pin: PinRef) -> Place:
    """A dropped pin is self-describing -- no lookup needed."""
    lines = [part.strip() for part in pin.address.split(",") if part.strip()]
    return Place(
        # Mirrors the synthetic ref maps.apple.com uses for pins.
        muid=f"ll.{pin.lat},{pin.lng}",
        result_provider_id=0,
        # Apple labels a pin with its street line, not the whole address.
        name=lines[0] if lines else f"{pin.lat:.5f}, {pin.lng:.5f}",
        lat=pin.lat,
        lng=pin.lng,
        address_lines=lines,
    )


def build_guide(
    url: str,
    *,
    language: str = "en-US",
    cache_dir: pathlib.Path | None = None,
    chunk_size: int | None = None,
    session=None,
    strict: bool = True,
) -> Guide:
    """Resolve ``url`` into a fully hydrated :class:`Guide`."""
    session = session or make_session(language)

    guide_ref, canonical = resolve(url, session)
    log.info(
        "guide %r declares %d entries (%d places, %d dropped pins)",
        guide_ref.name,
        len(guide_ref.entries),
        len(guide_ref.places),
        len(guide_ref.pins),
    )

    client = PlaceClient(
        session,
        language=language,
        cache_dir=cache_dir,
        **({"chunk_size": chunk_size} if chunk_size else {}),
    )
    result = client.fetch(guide_ref.places)

    places: list[Place] = []
    failed: list[str] = []
    for entry in guide_ref.entries:
        if isinstance(entry, PinRef):
            places.append(_place_from_pin(entry))
            continue

        record = result.records.get(entry.muid)
        if record is None:
            if entry.muid not in result.unresolved:
                log.warning("no record for muid %s", entry.muid)
                failed.append(entry.muid)
            continue
        place = normalize(entry, record, language)
        if place is None:
            failed.append(entry.muid)
        else:
            places.append(place)

    _check_complete(guide_ref, places, result.unresolved, failed, strict)
    return Guide(name=guide_ref.name, places=places, source_url=canonical)


def _check_complete(guide_ref, places, unresolved, failed, strict) -> None:
    """Refuse to write a silently short export.

    The guide page server-renders only the first ~20 places, so a partial
    result is the failure most likely to slip through unnoticed. Dead
    references are called out separately: they are unresolvable at source
    rather than something a retry would fix.
    """
    missing = len(guide_ref.entries) - len(places)
    if not missing:
        return

    reasons = []
    if unresolved:
        reasons.append(
            f"{len(unresolved)} no longer listed by Apple "
            f"({', '.join(sorted(unresolved))})"
        )
    if failed:
        reasons.append(f"{len(failed)} failed to resolve ({', '.join(sorted(failed))})")

    message = (
        f"exported {len(places)} of {len(guide_ref.entries)} entries; "
        f"{missing} missing"
    )
    if reasons:
        message += " -- " + "; ".join(reasons)

    if strict and failed:
        raise IncompleteExport(message)
    if strict and unresolved:
        raise IncompleteExport(message)
    log.warning("%s", message)
