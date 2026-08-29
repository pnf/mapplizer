"""Flatten Apple's place records into :class:`~mapplizer.model.Place`.

A place record carries a ``component`` array of ``{type, value}`` pairs, most of
them null for any given place, with the interesting data nested a few levels
down. Everything Apple-schema-shaped lives here so the rest of the package
doesn't have to care when Apple moves things around.
"""

from __future__ import annotations

import logging

from .model import Photo, Place, PlaceRef

log = logging.getLogger(__name__)

DAY_NAMES = {
    "MONDAY": "Mon",
    "TUESDAY": "Tue",
    "WEDNESDAY": "Wed",
    "THURSDAY": "Thu",
    "FRIDAY": "Fri",
    "SATURDAY": "Sat",
    "SUNDAY": "Sun",
}


def _components(record: dict) -> dict[str, list]:
    """Index a place record's components by type, dropping the null ones."""
    out: dict[str, list] = {}
    for component in record.get("component") or []:
        kind, value = component.get("type"), component.get("value")
        if kind and value:
            out.setdefault(kind, value)
    return out


def _first(values: list | None, key: str) -> dict | None:
    """Pull ``key`` out of the first entry of a component's value list."""
    for entry in values or []:
        if isinstance(entry, dict) and isinstance(entry.get(key), dict):
            return entry[key]
    return None


def _localized(entries: list | None, language: str) -> str | None:
    """Pick a string from Apple's ``[{locale, stringValue}]`` lists."""
    if not entries:
        return None
    prefix = language.split("-")[0].lower()
    best = None
    for entry in entries:
        value = entry.get("stringValue")
        if not value:
            continue
        locale = (entry.get("locale") or "").lower()
        if locale == language.lower():
            return value
        if best is None or locale.startswith(prefix):
            best = best or value
    return best


def _seconds_to_clock(seconds: int) -> str:
    hours, minutes = divmod(int(seconds) // 60, 60)
    return f"{hours:02d}:{minutes:02d}"


def _format_hours(business_hours: dict | None) -> list[str]:
    if not business_hours:
        return []
    lines = []
    for block in business_hours.get("weeklyHours") or []:
        days = [DAY_NAMES.get(d, d.title()) for d in block.get("day") or []]
        ranges = [
            f"{_seconds_to_clock(r['from'])}-{_seconds_to_clock(r['to'])}"
            for r in block.get("timeRange") or []
            if r.get("from") is not None and r.get("to") is not None
        ]
        if not days:
            continue
        label = days[0] if len(days) == 1 else f"{days[0]}-{days[-1]}"
        lines.append(f"{label} {', '.join(ranges) if ranges else 'Closed'}")
    return lines


def _photos(categorized: dict | None, limit: int = 3) -> list[Photo]:
    photos: list[Photo] = []
    for item in (categorized or {}).get("photo") or []:
        versions = (item.get("photo") or {}).get("photoVersion") or []
        # Skip AMP templates ({w}x{h} placeholders) -- we want a fetchable URL.
        concrete = [
            v for v in versions if v.get("url") and "{w}" not in v.get("url", "")
        ]
        if not concrete:
            continue
        best = max(concrete, key=lambda v: (v.get("width") or 0) * (v.get("height") or 0))
        attribution = (item.get("attribution") or {}).get("displayName")
        photos.append(
            Photo(
                url=best["url"],
                width=best.get("width"),
                height=best.get("height"),
                author=item.get("author"),
                attribution=attribution,
            )
        )
        if len(photos) >= limit:
            break
    return photos


def normalize(ref: PlaceRef, record: dict, language: str = "en-US") -> Place | None:
    """Build a :class:`Place` from one ``{annotation, place}`` record."""
    annotation = record.get("annotation") or {}
    raw = record.get("place") or {}
    components = _components(raw)

    entity = _first(components.get("COMPONENT_TYPE_ENTITY"), "entity") or {}
    snippet = _first(components.get("COMPONENT_TYPE_RESULT_SNIPPET"), "resultSnippet") or {}
    address_obj = _first(components.get("COMPONENT_TYPE_ADDRESS_OBJECT"), "addressObject") or {}
    place_info = _first(components.get("COMPONENT_TYPE_PLACE_INFO"), "placeInfo") or {}

    name = (
        annotation.get("title")
        or _localized(entity.get("name"), language)
        or snippet.get("name")
    )

    centre = annotation.get("center")
    if isinstance(centre, list) and len(centre) == 2:
        lat, lng = float(centre[0]), float(centre[1])
    else:
        point = place_info.get("center") or place_info.get("enhancedCenter") or {}
        if point.get("lat") is None or point.get("lng") is None:
            sharded = (raw.get("mapsId") or {}).get("shardedId") or {}
            point = sharded.get("center") or {}
        if point.get("lat") is None or point.get("lng") is None:
            log.warning("no coordinates for muid %s (%s); skipping", ref.muid, name)
            return None
        lat, lng = float(point["lat"]), float(point["lng"])

    if not name:
        log.warning("no name for muid %s; using coordinates", ref.muid)
        name = f"{lat:.5f}, {lng:.5f}"

    structured = (address_obj.get("address") or {}).get("structuredAddress") or {}
    address_lines = list(address_obj.get("formattedAddressLines") or [])

    place = Place(
        muid=ref.muid,
        result_provider_id=ref.result_provider_id,
        name=name,
        lat=lat,
        lng=lng,
        place_id=((raw.get("mapsId") or {}).get("shardedId") or {}).get("placeId"),
        address_lines=address_lines,
        locality=structured.get("locality"),
        administrative_area=structured.get("administrativeArea"),
        postcode=structured.get("postCode"),
        country=structured.get("country"),
        country_code=structured.get("countryCode"),
        phone=entity.get("telephone"),
        url=entity.get("url"),
        timezone=(place_info.get("timezone") or {}).get("identifier"),
        hours=_format_hours(
            _first(components.get("COMPONENT_TYPE_BUSINESS_HOURS"), "businessHours")
        ),
        photos=_photos(
            _first(components.get("COMPONENT_TYPE_CATEGORIZED_PHOTOS"), "categorizedPhotos")
        ),
    )

    place.category = snippet.get("category") or _best_category(entity, language)

    for entry in components.get("COMPONENT_TYPE_RATING") or []:
        rating = entry.get("rating") or {}
        if rating.get("ratingType") == "USER_RATING":
            place.rating = rating.get("score")
            place.rating_count = rating.get("numRatingsUsedForScore")
        elif rating.get("ratingType") == "PRICE_RANGE":
            place.price_level = rating.get("score")
            place.price_symbol = rating.get("currencySymbol") or "$"

    return place


def _best_category(entity: dict, language: str) -> str | None:
    """Pick the most specific named category Apple offers."""
    categories = entity.get("localizedCategory") or []
    if not categories:
        return None
    deepest = max(categories, key=lambda c: c.get("level") or 0)
    return _localized(deepest.get("localizedName"), language)
