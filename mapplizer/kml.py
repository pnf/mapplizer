"""Serialize a :class:`~mapplizer.model.Guide` as KML or KMZ.

Importers such as Rego map a KML placemark onto their own place model, so
metadata has to land in the fields they look for rather than being flattened
into a block of description HTML:

===================  ==========================================================
name                 ``<name>``
address              ``<address>`` -- a native KML 2.2 feature element
phone                ``<phoneNumber>`` -- likewise native
notes                ``<description>``, as plain text
photos               ``<Data name="gx_media_links">``, the Google My Maps
                     convention: URLs (or KMZ-relative paths) separated by
                     spaces
everything else      typed ``<Data>`` entries in ``<ExtendedData>``
===================  ==========================================================

KML 2.2 makes AbstractFeatureType a *sequence*, so these elements have a
required order: name, address, phoneNumber, Snippet, description, styleUrl,
Region, ExtendedData, and only then the geometry. Emitting them out of order
produces a file that parses but fails schema validation, and that some
importers reject.
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

#: Google My Maps carries photo references in this ExtendedData key, as a
#: space-separated list. Importers built for My Maps exports look for it.
MEDIA_LINKS_KEY = "gx_media_links"


def _cdata(text: str) -> str:
    # "]]>" would terminate the section early; split it across two sections.
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _tag(name: str, value, indent: int) -> str:
    return f"{' ' * indent}<{name}>{escape(str(value))}</{name}>\n"


def _notes(place: Place) -> str:
    """The human-readable leftovers, as plain text.

    Address and phone are deliberately absent -- they have their own elements.
    What remains is what an importer has no field for and a person would want
    to read as a note.
    """
    lines: list[str] = []
    headline = " · ".join(p for p in (place.category, place.price_display) if p)
    if headline:
        lines.append(headline)
    if place.rating is not None:
        count = f" ({place.rating_count} ratings)" if place.rating_count else ""
        lines.append(f"Rating: {place.rating:g}/5{count}")
    if place.hours:
        lines.append("Hours: " + "; ".join(place.hours))
    if place.url:
        lines.append(place.url)
    lines.append(place.maps_url)
    return "\n".join(lines)


def _extended_data(place: Place, photo_srcs: list[str], indent: int) -> str:
    fields: dict[str, object] = {
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
    if photo_srcs:
        # Space-separated, as My Maps writes it. URLs are percent-encoded and
        # KMZ paths carry no spaces, so the separator stays unambiguous.
        fields[MEDIA_LINKS_KEY] = " ".join(photo_srcs)

    pad = " " * indent
    rows = [
        f'{pad}  <Data name="{escape(key)}"><value>{escape(str(value))}</value></Data>\n'
        for key, value in fields.items()
        if value not in (None, "")
    ]
    if not rows:
        return ""
    return f"{pad}<ExtendedData>\n" + "".join(rows) + f"{pad}</ExtendedData>\n"


def _placemark(place: Place, photo_srcs: list[str]) -> str:
    out = "    <Placemark>\n"
    # Order below is fixed by the KML 2.2 schema; do not rearrange.
    out += _tag("name", place.name, 6)
    if place.address:
        out += _tag("address", place.address, 6)
    if place.phone:
        out += _tag("phoneNumber", place.phone, 6)
    notes = _notes(place)
    if notes:
        out += "      <description>" + _cdata(notes) + "</description>\n"
    out += "      <styleUrl>#mapplizer-place</styleUrl>\n"
    out += _extended_data(place, photo_srcs, 6)
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
        summary += f"\n{guide.source_url}"
    out += "    <description>" + _cdata(summary) + "</description>\n"
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
    link_embedded: bool = True,
    session=None,
) -> pathlib.Path:
    """Write a KMZ.

    With ``embed_photos`` the photos are downloaded into the archive. They are
    then referenced by their archive-relative path, unless ``link_embedded`` is
    false, in which case the archive still carries them but the media links
    keep pointing at the original URLs -- which importers that ignore
    KMZ-relative paths need.
    """
    assets: dict[str, bytes] = {}
    photo_srcs: dict[str, list[str]] = {}

    for place in guide.places:
        srcs = []
        for index, photo in enumerate(place.photos):
            if not embed_photos:
                srcs.append(photo.url)
                continue
            name = f"{PHOTO_DIR}/{_safe_asset_name(place.muid)}-{index}.jpg"
            data = _download(photo.url, session)
            if data:
                assets[name] = data
                srcs.append(name if link_embedded else photo.url)
            else:
                srcs.append(photo.url)
        photo_srcs[place.muid] = srcs

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("doc.kml", render_kml(guide, photo_srcs))
        for name, data in assets.items():
            archive.writestr(name, data)
    return path


def _safe_asset_name(muid: str) -> str:
    """Pin refs contain dots, commas and minus signs; keep archive paths tame."""
    return "".join(c if c.isalnum() else "_" for c in muid)


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
