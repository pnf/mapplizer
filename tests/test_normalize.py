from mapplizer.model import PlaceRef
from mapplizer.normalize import _format_hours, _localized, normalize

REF = PlaceRef(muid="4889791825408711588", result_provider_id=9902)


def test_extracts_core_fields(place_full):
    place = normalize(REF, place_full)
    assert place.name == "Chalet Bar B-Q"
    assert place.lat == 45.4726174
    assert place.lng == -73.6108071
    assert place.category == "BBQ Restaurant"
    assert place.address == "5456 Rue Sherbrooke O, Montréal QC H4A 1V9, Canada"
    assert place.phone == "+15144897235"
    assert place.place_id == "I43DC07C20B185BA4"


def test_extracts_structured_address(place_full):
    place = normalize(REF, place_full)
    assert place.locality == "Montréal"
    assert place.country_code == "CA"
    assert place.postcode == "H4A 1V9"


def test_distinguishes_rating_from_price(place_full):
    place = normalize(REF, place_full)
    assert place.rating == 4.3
    assert place.rating_count == 150
    assert place.price_level == 2
    assert place.price_display == "$$"


def test_photos_skip_amp_templates(place_full):
    place = normalize(REF, place_full)
    assert place.photos
    assert all("{w}" not in p.url for p in place.photos)


def test_falls_back_to_place_info_coordinates(place_no_annotation):
    """With no annotation, coordinates and name come from the place record."""
    place = normalize(REF, place_no_annotation)
    assert place is not None
    assert place.lat == 45.4726174
    assert place.name == "Chalet Bar B-Q"


def test_returns_none_when_uncoordinated():
    assert normalize(REF, {"annotation": None, "place": {}}) is None


def test_empty_components_do_not_crash():
    record = {"annotation": {"title": "X", "center": [1.0, 2.0]}, "place": {}}
    place = normalize(REF, record)
    assert place.name == "X"
    assert place.category is None
    assert place.address == ""


def test_localized_prefers_exact_locale():
    entries = [
        {"locale": "fr", "stringValue": "Boulangerie"},
        {"locale": "en-US", "stringValue": "Bakery"},
    ]
    assert _localized(entries, "en-US") == "Bakery"
    assert _localized(entries, "fr") == "Boulangerie"
    assert _localized([], "en-US") is None


def test_hours_formatting_converts_seconds():
    hours = _format_hours(
        {"weeklyHours": [{"day": ["MONDAY", "FRIDAY"], "timeRange": [{"from": 41400, "to": 50400}]}]}
    )
    assert hours == ["Mon-Fri 11:30-14:00"]


def test_hours_with_no_ranges_reads_closed():
    assert _format_hours({"weeklyHours": [{"day": ["SUNDAY"], "timeRange": []}]}) == [
        "Sun Closed"
    ]
