"""Client for Apple Maps' place-data endpoint.

``POST /data/place`` is the same private endpoint maps.apple.com's own guide
bundle calls to hydrate places beyond the ~20 it server-renders. It takes
``{"places": [{"muid": ..., "resultProviderId": ...}]}`` and returns both map
annotations (title + centre) and full place records.

It is private, so treat it gently: modest batches, a small delay between them,
and an on-disk cache so repeated runs cost nothing.
"""

from __future__ import annotations

import json
import logging
import pathlib
import time
from typing import Iterable, Sequence

import requests

from .model import PlaceRef

log = logging.getLogger(__name__)

PLACE_ENDPOINT = "https://maps.apple.com/data/place"
GUIDES_REFERER = "https://maps.apple.com/guides"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# maps.apple.com chunks these requests itself; 10 is comfortably inside what
# the endpoint accepts and keeps any single failure cheap to retry.
DEFAULT_CHUNK_SIZE = 10


class FetchError(Exception):
    """A place lookup failed."""


def make_session(language: str = "en-US") -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": language,
            "Content-Type": "application/json",
            "Referer": GUIDES_REFERER,
            "Origin": "https://maps.apple.com",
        }
    )
    return session


def _chunk(items: Sequence[PlaceRef], size: int) -> Iterable[Sequence[PlaceRef]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class PlaceClient:
    """Fetches raw place records, with retries and an optional disk cache."""

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        language: str = "en-US",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        cache_dir: pathlib.Path | None = None,
        delay: float = 0.3,
        max_retries: int = 3,
    ) -> None:
        self.session = session or make_session(language)
        self.chunk_size = max(1, chunk_size)
        self.cache_dir = cache_dir
        self.delay = delay
        self.max_retries = max_retries
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, muid: str) -> pathlib.Path | None:
        return self.cache_dir / f"{muid}.json" if self.cache_dir else None

    def _read_cache(self, muid: str) -> dict | None:
        path = self._cache_path(muid)
        if path and path.exists():
            try:
                return json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                log.debug("ignoring unreadable cache entry %s", path)
        return None

    def _write_cache(self, muid: str, record: dict) -> None:
        path = self._cache_path(muid)
        if path:
            try:
                path.write_text(json.dumps(record))
            except OSError:
                log.debug("could not write cache entry %s", path)

    def _post(self, refs: Sequence[PlaceRef]) -> dict:
        body = {
            "places": [
                {"muid": ref.muid, "resultProviderId": ref.result_provider_id}
                for ref in refs
            ]
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            if attempt:
                time.sleep(2**attempt)
            try:
                response = self.session.post(PLACE_ENDPOINT, json=body, timeout=60)
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                log.warning(
                    "place request failed (attempt %d/%d): %s",
                    attempt + 1,
                    self.max_retries,
                    exc,
                )
        raise FetchError(
            f"could not fetch {len(refs)} place(s) after "
            f"{self.max_retries} attempts: {last_error}"
        ) from last_error

    def fetch(self, refs: Sequence[PlaceRef]) -> dict[str, dict]:
        """Fetch records for ``refs``. Returns ``{muid: {annotation, place}}``."""
        records: dict[str, dict] = {}
        pending: list[PlaceRef] = []

        for ref in refs:
            cached = self._read_cache(ref.muid)
            if cached is not None:
                records[ref.muid] = cached
            else:
                pending.append(ref)

        if records:
            log.info("%d place(s) served from cache", len(records))

        for index, batch in enumerate(_chunk(pending, self.chunk_size)):
            if index and self.delay:
                time.sleep(self.delay)
            payload = self._post(batch)
            annotations = {
                a["ref"]: a for a in payload.get("map", {}).get("annotations", [])
            }
            places = {
                p["ref"]: p for p in payload.get("places", []) if p.get("ref")
            }
            for ref in batch:
                annotation = annotations.get(ref.muid)
                place = places.get(ref.muid)
                if annotation is None and place is None:
                    log.warning("no data returned for muid %s", ref.muid)
                    continue
                record = {"annotation": annotation, "place": place}
                records[ref.muid] = record
                self._write_cache(ref.muid, record)
            log.info(
                "fetched %d/%d places", min(len(records), len(refs)), len(refs)
            )

        return records
