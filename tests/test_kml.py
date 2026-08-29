import xml.etree.ElementTree as ET
import zipfile

from mapplizer.kml import MEDIA_LINKS_KEY, _cdata, render_kml, write_kmz
from mapplizer.model import Guide, Photo, Place

NS = {"k": "http://www.opengis.net/kml/2.2"}


def _guide(**overrides) -> Guide:
    place = Place(
        muid="1",
        result_provider_id=9902,
        name="Café Aunja",
        lat=45.4977519,
        lng=-73.5802943,
        category="Persian",
        address_lines=["1448 Sherbrooke", "Montreal QC"],
        phone="+15145551234",
        rating=4.5,
        rating_count=12,
        price_level=2,
        **overrides,
    )
    return Guide(name="Resto", places=[place], source_url="https://maps.apple.com/guides?user=x")


def test_renders_well_formed_kml():
    root = ET.fromstring(render_kml(_guide()))
    assert root.tag == "{http://www.opengis.net/kml/2.2}kml"
    assert len(root.findall(".//k:Placemark", NS)) == 1


def test_coordinates_are_longitude_first():
    root = ET.fromstring(render_kml(_guide()))
    coords = root.find(".//k:coordinates", NS).text
    lng, lat = coords.split(",")
    assert float(lng) == -73.5802943
    assert float(lat) == 45.4977519


def test_extended_data_carries_structured_fields():
    root = ET.fromstring(render_kml(_guide()))
    data = {
        d.get("name"): d.find("k:value", NS).text
        for d in root.findall(".//k:Data", NS)
    }
    assert data["muid"] == "1"
    assert data["category"] == "Persian"
    assert data["rating"] == "4.5"
    assert "address" in data


def test_empty_fields_are_omitted_not_blank():
    guide = _guide()
    guide.places[0].phone = None
    root = ET.fromstring(render_kml(guide))
    names = {d.get("name") for d in root.findall(".//k:Data", NS)}
    assert "phone" not in names
    assert root.find(".//k:Placemark/k:phoneNumber", NS) is None


def test_address_uses_the_native_element():
    """Rego and friends read <address>; it must not be buried in description."""
    root = ET.fromstring(render_kml(_guide()))
    assert (
        root.find(".//k:Placemark/k:address", NS).text
        == "1448 Sherbrooke, Montreal QC"
    )


def test_phone_uses_the_native_element():
    root = ET.fromstring(render_kml(_guide()))
    assert root.find(".//k:Placemark/k:phoneNumber", NS).text == "+15145551234"


def test_description_is_notes_not_a_metadata_dump():
    """Address and phone belong in their own elements, not the notes blob."""
    root = ET.fromstring(render_kml(_guide()))
    description = root.find(".//k:Placemark/k:description", NS).text
    assert "1448 Sherbrooke" not in description
    assert "+15145551234" not in description
    assert "<p>" not in description and "<b>" not in description
    assert "Persian" in description


def test_photos_become_media_links():
    guide = _guide()
    guide.places[0].photos = [
        Photo(url="https://example.test/a.jpg"),
        Photo(url="https://example.test/b.jpg"),
    ]
    root = ET.fromstring(render_kml(guide, {"1": ["a.jpg", "b.jpg"]}))
    data = {d.get("name"): d.find("k:value", NS).text for d in root.findall(".//k:Data", NS)}
    assert data[MEDIA_LINKS_KEY] == "a.jpg b.jpg"


def test_no_media_links_when_there_are_no_photos():
    root = ET.fromstring(render_kml(_guide()))
    names = {d.get("name") for d in root.findall(".//k:Data", NS)}
    assert MEDIA_LINKS_KEY not in names


def test_feature_elements_follow_schema_order():
    """KML 2.2 makes these a sequence; out-of-order output fails validation."""
    root = ET.fromstring(render_kml(_guide()))
    placemark = root.find(".//k:Placemark", NS)
    tags = [child.tag.split("}")[1] for child in placemark]
    expected = ["name", "address", "phoneNumber", "description", "styleUrl",
                "ExtendedData", "Point"]
    assert tags == expected


def test_names_needing_escaping_survive():
    guide = _guide()
    guide.places[0].name = "Ben & Jerry's <Montreal>"
    root = ET.fromstring(render_kml(guide))
    assert root.find(".//k:Placemark/k:name", NS).text == "Ben & Jerry's <Montreal>"


def test_cdata_terminator_in_notes_is_neutralized():
    """Notes go into CDATA verbatim now, so "]]>" must be split, not escaped."""
    guide = _guide()
    guide.places[0].url = "https://example.test/?x=]]>"
    root = ET.fromstring(render_kml(guide))
    description = root.find(".//k:Placemark/k:description", NS).text
    assert "https://example.test/?x=]]>" in description


def test_terminator_in_address_is_escaped():
    guide = _guide()
    guide.places[0].address_lines = ["evil ]]> injection"]
    root = ET.fromstring(render_kml(guide))
    assert root.find(".//k:Placemark/k:address", NS).text == "evil ]]> injection"


def test_guide_with_no_places_still_renders():
    root = ET.fromstring(render_kml(Guide(name="Empty")))
    assert root.findall(".//k:Placemark", NS) == []


def test_cdata_helper_round_trips_terminator():
    """A literal "]]>" must survive by being split across two CDATA sections."""
    wrapped = _cdata("a]]>b")
    assert wrapped.count("<![CDATA[") == 2
    parsed = ET.fromstring(f"<d>{wrapped}</d>")
    assert parsed.text == "a]]>b"


def test_kmz_contains_doc_kml(tmp_path):
    path = write_kmz(_guide(), tmp_path / "out.kmz")
    with zipfile.ZipFile(path) as archive:
        assert "doc.kml" in archive.namelist()
        ET.fromstring(archive.read("doc.kml"))


def test_kmz_without_embedding_keeps_remote_photo_urls(tmp_path):
    guide = _guide()
    guide.places[0].photos = [Photo(url="https://example.test/a.jpg")]
    path = write_kmz(guide, tmp_path / "out.kmz", embed_photos=False)
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == ["doc.kml"]
        assert b"https://example.test/a.jpg" in archive.read("doc.kml")


def test_embedded_photos_can_keep_remote_links(tmp_path):
    """Importers that ignore KMZ-relative paths need the URLs kept."""
    class FakeResponse:
        content = b"jpeg"

        def raise_for_status(self):
            pass

    class FakeSession:
        def get(self, url, timeout=None):
            return FakeResponse()

    guide = _guide()
    guide.places[0].photos = [Photo(url="https://example.test/a.jpg")]
    path = write_kmz(
        guide, tmp_path / "out.kmz", embed_photos=True,
        link_embedded=False, session=FakeSession(),
    )
    with zipfile.ZipFile(path) as archive:
        assert "files/photos/1-0.jpg" in archive.namelist()
        assert b"https://example.test/a.jpg" in archive.read("doc.kml")


def test_kmz_embeds_photos_when_asked(tmp_path):
    class FakeResponse:
        content = b"\xff\xd8\xff-jpeg-bytes"

        def raise_for_status(self):
            pass

    class FakeSession:
        def get(self, url, timeout=None):
            return FakeResponse()

    guide = _guide()
    guide.places[0].photos = [Photo(url="https://example.test/a.jpg")]
    path = write_kmz(guide, tmp_path / "out.kmz", embed_photos=True, session=FakeSession())
    with zipfile.ZipFile(path) as archive:
        assert "files/photos/1-0.jpg" in archive.namelist()
        assert archive.read("files/photos/1-0.jpg") == b"\xff\xd8\xff-jpeg-bytes"


def test_photo_download_failure_falls_back_to_url(tmp_path):
    class FailingSession:
        def get(self, url, timeout=None):
            raise OSError("network down")

    guide = _guide()
    guide.places[0].photos = [Photo(url="https://example.test/a.jpg")]
    path = write_kmz(guide, tmp_path / "out.kmz", embed_photos=True, session=FailingSession())
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == ["doc.kml"]
        assert b"https://example.test/a.jpg" in archive.read("doc.kml")
