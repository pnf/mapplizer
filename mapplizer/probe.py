"""Generate a diagnostic KMZ that reveals what an importer actually reads.

Rego is a closed iOS app with no published KML mapping, so which conventions it
honours can only be settled by importing a file and looking. This builds one
archive whose placemarks each exercise a *single* convention in isolation, with
the answer written into the placemark's own name. Import it, look at the
resulting places, and the list tells you which conventions survived.
"""

from __future__ import annotations

import logging
import pathlib
import zipfile
from xml.sax.saxutils import escape

from .kml import KML_NS, MEDIA_LINKS_KEY, PHOTO_DIR, _cdata

log = logging.getLogger(__name__)

# Somewhere identifiable and empty of real data: mid-Atlantic, so probe pins
# never get confused with a real import.
ORIGIN_LAT, ORIGIN_LNG = 45.0, -30.0

# A stable, public Apple Maps photo, used for the remote-photo probes.
SAMPLE_PHOTO = (
    "https://is1-ssl.mzstatic.com/image/thumb/WOOlOdUeS3o69KLIm-6jsw/320x320bb.jpeg"
)

EMBEDDED_NAME = f"{PHOTO_DIR}/probe-embedded.jpg"


def _placemark(
    index: int,
    label: str,
    *,
    address: str | None = None,
    phone: str | None = None,
    description: str | None = None,
    data: dict[str, str] | None = None,
) -> str:
    lat = ORIGIN_LAT + index * 0.01
    out = "    <Placemark>\n"
    out += f"      <name>{escape(f'{index:02d} {label}')}</name>\n"
    if address:
        out += f"      <address>{escape(address)}</address>\n"
    if phone:
        out += f"      <phoneNumber>{escape(phone)}</phoneNumber>\n"
    if description:
        out += "      <description>" + _cdata(description) + "</description>\n"
    if data:
        out += "      <ExtendedData>\n"
        for key, value in data.items():
            out += (
                f'        <Data name="{escape(key)}">'
                f"<value>{escape(value)}</value></Data>\n"
            )
        out += "      </ExtendedData>\n"
    out += f"      <Point><coordinates>{ORIGIN_LNG:.7f},{lat:.7f}</coordinates></Point>\n"
    out += "    </Placemark>\n"
    return out


ADDRESS = "143 Mont-Royal Est, Montreal QC H2T 1N9, Canada"
PHONE = "+14383834700"


def render_probe_kml() -> str:
    """Build the probe document. Each placemark tests exactly one convention."""
    parts = [
        _placemark(
            1,
            "address element",
            address=ADDRESS,
            description="Does this place show an address?",
        ),
        _placemark(
            2,
            "ExtendedData address",
            data={"address": ADDRESS},
            description="Does this place show an address?",
        ),
        _placemark(
            3,
            "phoneNumber element",
            phone=PHONE,
            description="Does this place show a phone number?",
        ),
        _placemark(
            4,
            "photo gx_media_links remote",
            description="Does this place have a photo?",
            data={MEDIA_LINKS_KEY: SAMPLE_PHOTO},
        ),
        _placemark(
            5,
            "photo gx_media_links embedded",
            description="Does this place have a photo?",
            data={MEDIA_LINKS_KEY: EMBEDDED_NAME},
        ),
        _placemark(
            6,
            "photo two gx_media_links",
            description="Does this place have TWO photos?",
            data={MEDIA_LINKS_KEY: f"{SAMPLE_PHOTO} {SAMPLE_PHOTO}"},
        ),
        _placemark(
            7,
            "photo description img tag",
            description=f'<img src="{SAMPLE_PHOTO}"/>',
        ),
        _placemark(
            8,
            "photo description img embedded",
            description=f'<img src="{EMBEDDED_NAME}"/>',
        ),
        _placemark(
            9,
            "description plain text",
            description="Plain text notes.\nSecond line.",
        ),
        _placemark(
            10,
            "description html",
            description="<p><b>Bold</b> and <i>italic</i> notes.</p>",
        ),
    ]

    out = '<?xml version="1.0" encoding="UTF-8"?>\n'
    out += f'<kml xmlns="{KML_NS}">\n  <Document>\n'
    out += "    <name>mapplizer import probe</name>\n"
    out += (
        "    <description>"
        + _cdata(
            "Each pin tests one KML convention. After importing, note which "
            "pins show an address, a phone number, a photo, or notes -- the "
            "pin name says which convention produced it."
        )
        + "</description>\n"
    )
    out += "".join(parts)
    out += "  </Document>\n</kml>\n"
    return out


def write_probe(path: pathlib.Path, session=None) -> pathlib.Path:
    """Write the probe archive, embedding a real image for the KMZ-path probes."""
    kml_text = render_probe_kml()
    photo: bytes | None = None
    if session is not None:
        try:
            response = session.get(SAMPLE_PHOTO, timeout=30)
            response.raise_for_status()
            photo = response.content
        except Exception as exc:  # noqa: BLE001
            log.warning("could not fetch the sample photo: %s", exc)

    if path.suffix.lower() == ".kml":
        path.write_text(kml_text, encoding="utf-8")
        return path

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("doc.kml", kml_text)
        if photo:
            archive.writestr(EMBEDDED_NAME, photo)
        else:
            log.warning("probe written without an embedded photo; probes 5 and 8 "
                        "cannot succeed")
    return path
