"""Serialize a :class:`~mapplizer.model.Guide` as KML or KMZ.

The schema we emit is small enough to write directly. Each place becomes a
``<Placemark>`` with a ``<Point>``; the human-readable card goes in
``<description>`` as CDATA'd HTML, and every structured field is repeated in
``<ExtendedData>`` so nothing is lost round-tripping into other tools.
"""

from __future__ import annotations

import logging
import pathlib
import zipfile
from xml.sax.saxutils import escape

from .model import Guide, Place

log = logging.getLogger(__name__)

KML_NS = "http://www.opengis.net/kml/2.2"
PHOTO_DIR = "files/photos"


def _cdata(text: str) -> str:
    # "]]>" would terminate the section early; split it across two sections.
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _tag(name: str, value, indent: int = 0) -> str:
    return f"{' ' * indent}<{name}>{escape(str(value))}</{name}>\n"


def _description_html(place: Place, photo_srcs: list[str]) -> str:
    parts: list[str] = []
    subtitle = " · ".join(
        p for p in (place.category, place.price_display) if p
    )
    if subtitle:
        parts.append(f"<p><b>{escape(subtitle)}</b></p>")
    if place.rating is not None:
        count = f" ({place.rating_count} ratings)" if place.rating_count else ""
        parts.append(f"<p>{place.rating:g}/5{escape(count)}</p>")
    if place.address_lines:
        parts.append("<p>" + "<br/>".join(escape(l) for l in place.address_lines) + "</p>")
    if place.phone:
        parts.append(f'<p><a href="tel:{escape(place.phone)}">{escape(place.phone)}</a></p>')
    if place.url:
        parts.append(f'<p><a href="{escape(place.url)}">{escape(place.url)}</a></p>')
    if place.hours:
        parts.append("<p>" + "<br/>".join(escape(h) for h in place.hours) + "</p>")
    for src in photo_srcs:
        parts.append(f'<p><img src="{escape(src)}" width="308"/></p>')
    parts.append(f'<p><a href="{escape(place.maps_url)}">Open in Apple Maps</a></p>')
    return "\n".join(parts)


def _extended_data(place: Place) -> str:
    fields = {
        "muid": place.muid,
        "place_id": place.place_id,
        "category": place.category,
        "address": place.address or None,
        "locality": place.locality,
        "administrative_area": place.administrative_area,
        "postcode": place.postcode,
        "country": place.country,
        "country_code": place.country_code,
        "phone": place.phone,
        "url": place.url,
        "rating": place.rating,
        "rating_count": place.rating_count,
        "price_level": place.price_level,
        "timezone": place.timezone,
        "hours": "; ".join(place.hours) or None,
        "apple_maps_url": place.maps_url,
    }
    rows = [
        f'        <Data name="{escape(key)}"><value>{escape(str(value))}</value></Data>\n'
        for key, value in fields.items()
        if value not in (None, "")
    ]
    if not rows:
        return ""
    return "      <ExtendedData>\n" + "".join(rows) + "      </ExtendedData>\n"


def _placemark(place: Place, photo_srcs: list[str]) -> str:
    out = "    <Placemark>\n"
    out += _tag("name", place.name, 6)
    out += (
        "      <description>"
        + _cdata(_description_html(place, photo_srcs))
        + "</description>\n"
    )
    out += "      <styleUrl>#mapplizer-place</styleUrl>\n"
    out += _extended_data(place)
    # KML orders coordinates longitude first.
    out += f"      <Point><coordinates>{place.lng:.7f},{place.lat:.7f}</coordinates></Point>\n"
    out += "    </Placemark>\n"
    return out


def render_kml(guide: Guide, photo_srcs: dict[str, list[str]] | None = None) -> str:
    """Render ``guide`` as a KML document string."""
    photo_srcs = photo_srcs or {}
    out = '<?xml version="1.0" encoding="UTF-8"?>\n'
    out += f'<kml xmlns="{KML_NS}">\n'
    out += "  <Document>\n"
    out += _tag("name", guide.name, 4)
    summary = f"{len(guide.places)} places"
    if guide.source_url:
        summary += f" · exported from {guide.source_url}"
    out += "    <description>" + _cdata(escape(summary)) + "</description>\n"
    out += (
        '    <Style id="mapplizer-place">\n'
        "      <IconStyle>\n"
        "        <Icon><href>https://maps.google.com/mapfiles/kml/paddle/red-circle.png</href></Icon>\n"
        "      </IconStyle>\n"
        "    </Style>\n"
    )
    for place in guide.places:
        out += _placemark(place, photo_srcs.get(place.muid, []))
    out += "  </Document>\n</kml>\n"
    return out


def write_kml(guide: Guide, path: pathlib.Path) -> pathlib.Path:
    photo_srcs = {p.muid: [ph.url for ph in p.photos] for p in guide.places}
    path.write_text(render_kml(guide, photo_srcs), encoding="utf-8")
    return path


def write_kmz(
    guide: Guide,
    path: pathlib.Path,
    *,
    embed_photos: bool = False,
    session=None,
) -> pathlib.Path:
    """Write a KMZ. With ``embed_photos``, photos are downloaded into the archive."""
    assets: dict[str, bytes] = {}
    photo_srcs: dict[str, list[str]] = {}

    for place in guide.places:
        srcs = []
        for index, photo in enumerate(place.photos):
            if not embed_photos:
                srcs.append(photo.url)
                continue
            name = f"{PHOTO_DIR}/{place.muid}-{index}.jpg"
            data = _download(photo.url, session)
            if data:
                assets[name] = data
                srcs.append(name)
            else:
                srcs.append(photo.url)
        photo_srcs[place.muid] = srcs

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("doc.kml", render_kml(guide, photo_srcs))
        for name, data in assets.items():
            archive.writestr(name, data)
    return path


def _download(url: str, session) -> bytes | None:
    if session is None:
        return None
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        return response.content
    except Exception as exc:  # noqa: BLE001 - a missing photo must not fail the export
        log.warning("could not download photo %s: %s", url, exc)
        return None
