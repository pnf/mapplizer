"""The end-to-end pipeline: share URL in, :class:`Guide` out."""

from __future__ import annotations

import logging
import pathlib

from .fetch import PlaceClient, make_session
from .model import Guide
from .normalize import normalize
from .resolve import resolve

log = logging.getLogger(__name__)


class IncompleteExport(Exception):
    """Fewer places resolved than the guide declares."""


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
    log.info("guide %r declares %d places", guide_ref.name, len(guide_ref.places))

    client = PlaceClient(
        session,
        language=language,
        cache_dir=cache_dir,
        **({"chunk_size": chunk_size} if chunk_size else {}),
    )
    records = client.fetch(guide_ref.places)

    places = []
    for ref in guide_ref.places:
        record = records.get(ref.muid)
        if record is None:
            log.warning("no record for muid %s", ref.muid)
            continue
        place = normalize(ref, record, language)
        if place is not None:
            places.append(place)

    # The server-rendered page only ever contains the first ~20 places, so a
    # partial result is the failure mode most likely to slip through unnoticed.
    # Say so loudly rather than writing a KML that merely looks complete.
    missing = len(guide_ref.places) - len(places)
    if missing:
        message = (
            f"resolved {len(places)} of {len(guide_ref.places)} places "
            f"({missing} missing)"
        )
        if strict:
            raise IncompleteExport(message)
        log.warning("%s", message)

    return Guide(name=guide_ref.name, places=places, source_url=canonical)
