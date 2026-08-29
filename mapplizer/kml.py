"""Serialize a :class:`~mapplizer.model.Guide` as KML or KMZ.

Two audiences want incompatible things from a placemark, so there are two
profiles.

``earth`` is the standards-shaped output: metadata in the native KML fields,
an HTML card in ``<description>``. Google Earth and GIS tools read it.

``rego`` targets what the Rego iOS app actually does, established by importing
a probe file (see :mod:`mapplizer.probe`) rather than from documentation:

* ``<address>``, ``<phoneNumber>`` and ``<ExtendedData>`` are **all ignored**.
  Rego keeps only coordinates and derives its own address, so anything the
  reader should see has to be in ``<description>``.
* ``<description>`` is shown as **plain text**. HTML is not rendered -- it is
  displayed raw, tags and all.
* The sole photo channel is an ``<img>`` tag in ``<description>`` whose ``src``
  is a **KMZ-relative path**. Remote URLs are not fetched, and the Google My
  Maps ``gx_media_links`` convention is ignored entirely.

Both profiles still emit the native elements and ``ExtendedData``: they cost
nothing, they are correct KML, and other tools do read them.

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
#: space-separated list. Rego ignores it, but Google Earth and Locus Map read it.
MEDIA_LINKS_KEY = "gx_media_links"

PROFILE_REGO = "rego"
PROFILE_EARTH = "earth"
PROFILES = (PROFILE_REGO, PROFILE_EARTH)


def _cdata(text: str) -> str:
    # "]]>" would terminate the section early; split it across two sections.
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _tag(name: str, value, indent: int) -> str:
    return f"{' ' * indent}<{name}>{escape(str(value))}</{name}>\n"


def _notes_text(place: Place) -> str:
    """Everything a reader should see, as plain text.

    Address and phone are repeated here even though they have their own
    elements: Rego discards those elements, so this is the only copy that
    reaches the reader.
    """
    lines: list[str] = []
    headline = " · ".join(p for p in (place.category, place.price_display) if p)
    if headline:
        lines.append(headline)
    if place.address:
        lines.append(place.address)
    if place.phone:
        lines.append(place.phone)
    if place.rating is not None:
        count = f" ({place.rating_count} ratings)" if place.rating_count else ""
        lines.append(f"Rating: {place.rating:g}/5{count}")
    if place.hours:
        lines.append("Hours: " + "; ".join(place.hours))
    if place.url:
        lines.append(place.url)
    lines.append(place.maps_url)
    return "\n".join(lines)


def _notes_html(place: Place) -> str:
    """The same content as an HTML card, for readers that render it."""
    parts: list[str] = []
    headline = " · ".join(p for p in (place.category, place.price_display) if p)
    if headline:
        parts.append(f"<p><b>{escape(headline)}</b></p>")
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
    parts.append(f'<p><a href="{escape(place.maps_url)}">Open in Apple Maps</a></p>')
    return "\n".join(parts)


def _description(place: Place, photo_srcs: list[str], profile: str) -> str:
    """Build the description body for ``profile``.

    Photos ride along as ``<img>`` tags in both profiles: it is the only
    channel Rego reads, and Google Earth renders them too.
    """
    if profile == PROFILE_REGO:
        body = _notes_text(place)
        # Rego does not fetch remote images, so a URL here would render as a
        # stray tag rather than a photo. Only archive-relative paths survive;
        # a photo that failed to embed is simply left out.
        srcs = [s for s in photo_srcs if not s.startswith(("http://", "https://"))]
    else:
        body = _notes_html(place)
        srcs = photo_srcs
    images = "\n".join(f'<img src="{escape(src)}"/>' for src in srcs)
    return f"{body}\n{images}" if images else body


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


def _placemark(place: Place, photo_srcs: list[str], profile: str) -> str:
    out = "    <Placemark>\n"
    # Order below is fixed by the KML 2.2 schema; do not rearrange.
    out += _tag("name", place.name, 6)
    if place.address:
        out += _tag("address", place.address, 6)
    if place.phone:
        out += _tag("phoneNumber", place.phone, 6)
    description = _description(place, photo_srcs, profile)
    if description:
        out += "      <description>" + _cdata(description) + "</description>\n"
    out += "      <styleUrl>#mapplizer-place</styleUrl>\n"
    out += _extended_data(place, photo_srcs, 6)
    # KML orders coordinates longitude first.
    out += f"      <Point><coordinates>{place.lng:.7f},{place.lat:.7f}</coordinates></Point>\n"
    out += "    </Placemark>\n"
    return out


def render_kml(
    guide: Guide,
    photo_srcs: dict[str, list[str]] | None = None,
    profile: str = PROFILE_REGO,
) -> str:
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
        out += _placemark(place, photo_srcs.get(place.muid, []), profile)
    out += "  </Document>\n</kml>\n"
    return out


def write_kml(
    guide: Guide, path: pathlib.Path, profile: str = PROFILE_REGO
) -> pathlib.Path:
    photo_srcs = {p.muid: [ph.url for ph in p.photos] for p in guide.places}
    path.write_text(render_kml(guide, photo_srcs, profile), encoding="utf-8")
    return path


def write_kmz(
    guide: Guide,
    path: pathlib.Path,
    *,
    embed_photos: bool = False,
    link_embedded: bool = True,
    profile: str = PROFILE_REGO,
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
        archive.writestr("doc.kml", render_kml(guide, photo_srcs, profile))
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
