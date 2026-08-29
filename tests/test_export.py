import pytest

from mapplizer import export
from mapplizer.export import IncompleteExport, build_guide
from mapplizer.fetch import FetchResult
from mapplizer.model import GuideRef, PinRef, PlaceRef

PIN = PinRef(address="1353 Rue Rachel E, Montréal QC, Canada", lat=45.5279507, lng=-73.5720299)


@pytest.fixture
def stub_guide(monkeypatch):
    """Stub the network. Returns an installer taking the guide's entries."""

    def install(entries, served=None, unresolved=()):
        monkeypatch.setattr(
            export,
            "resolve",
            lambda url, session: (
                GuideRef(name="Trois", entries=tuple(entries)),
                "https://canonical",
            ),
        )
        muids = [e.muid for e in entries if isinstance(e, PlaceRef)]
        serve = muids if served is None else served

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def fetch(self, requested):
                return FetchResult(
                    records={
                        muid: {
                            "annotation": {"title": f"Place {muid}", "center": [1.0, 2.0]},
                            "place": {},
                        }
                        for muid in serve
                    },
                    unresolved=set(unresolved),
                )

        monkeypatch.setattr(export, "PlaceClient", FakeClient)

    return install


def _refs(*ids):
    return [PlaceRef(muid=str(i), result_provider_id=9902) for i in ids]


def test_full_guide_resolves(stub_guide):
    stub_guide(_refs(1, 2, 3))
    guide = build_guide("https://maps.apple/ug/x", session=object())
    assert guide.name == "Trois"
    assert [p.name for p in guide.places] == ["Place 1", "Place 2", "Place 3"]
    assert guide.source_url == "https://canonical"


def test_partial_guide_raises_by_default(stub_guide):
    """The 20-of-N truncation is the failure most likely to pass unnoticed."""
    stub_guide(_refs(1, 2, 3), served=["1", "2"])
    with pytest.raises(IncompleteExport, match="2 of 3"):
        build_guide("https://maps.apple/ug/x", session=object())


def test_partial_guide_allowed_explicitly(stub_guide):
    stub_guide(_refs(1, 2, 3), served=["1", "2"])
    guide = build_guide("https://maps.apple/ug/x", session=object(), strict=False)
    assert len(guide.places) == 2


def test_guide_order_follows_the_url_not_the_response(stub_guide):
    stub_guide(_refs(1, 2, 3), served=["3", "1", "2"])
    guide = build_guide("https://maps.apple/ug/x", session=object())
    assert [p.muid for p in guide.places] == ["1", "2", "3"]


def test_dropped_pin_needs_no_lookup(stub_guide):
    stub_guide([PIN])
    guide = build_guide("https://maps.apple/ug/x", session=object())
    assert len(guide.places) == 1
    place = guide.places[0]
    assert place.lat == 45.5279507
    assert place.lng == -73.5720299
    assert place.name == "1353 Rue Rachel E"
    assert place.address == "1353 Rue Rachel E, Montréal QC, Canada"


def test_pins_keep_their_position_among_places(stub_guide):
    stub_guide([_refs(1)[0], PIN, _refs(2)[0]])
    guide = build_guide("https://maps.apple/ug/x", session=object())
    assert [p.name for p in guide.places] == [
        "Place 1",
        "1353 Rue Rachel E",
        "Place 2",
    ]


def test_dead_reference_is_named_in_the_error(stub_guide):
    """A muid Apple no longer lists is reported distinctly from a fetch failure."""
    stub_guide(_refs(1, 2), served=["1"], unresolved=["2"])
    with pytest.raises(IncompleteExport, match="no longer listed by Apple"):
        build_guide("https://maps.apple/ug/x", session=object())


def test_dead_reference_can_be_exported_anyway(stub_guide):
    stub_guide(_refs(1, 2), served=["1"], unresolved=["2"])
    guide = build_guide("https://maps.apple/ug/x", session=object(), strict=False)
    assert [p.muid for p in guide.places] == ["1"]
