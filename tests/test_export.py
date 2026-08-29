import pytest

from mapplizer import export
from mapplizer.export import IncompleteExport, build_guide
from mapplizer.model import GuideRef, PlaceRef


@pytest.fixture
def three_place_guide(monkeypatch):
    """Stub the network: a guide of three places, hydrated from a canned record."""
    refs = tuple(PlaceRef(muid=str(i), result_provider_id=9902) for i in (1, 2, 3))
    monkeypatch.setattr(
        export,
        "resolve",
        lambda url, session: (GuideRef(name="Trois", places=refs), "https://canonical"),
    )

    def install(served: list[str]):
        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def fetch(self, requested):
                return {
                    muid: {
                        "annotation": {"title": f"Place {muid}", "center": [1.0, 2.0]},
                        "place": {},
                    }
                    for muid in served
                }

        monkeypatch.setattr(export, "PlaceClient", FakeClient)

    return install


def test_full_guide_resolves(three_place_guide):
    three_place_guide(["1", "2", "3"])
    guide = build_guide("https://maps.apple/ug/x", session=object())
    assert guide.name == "Trois"
    assert [p.name for p in guide.places] == ["Place 1", "Place 2", "Place 3"]
    assert guide.source_url == "https://canonical"


def test_partial_guide_raises_by_default(three_place_guide):
    """The 20-of-N truncation is the failure most likely to pass unnoticed."""
    three_place_guide(["1", "2"])
    with pytest.raises(IncompleteExport, match="2 of 3"):
        build_guide("https://maps.apple/ug/x", session=object())


def test_partial_guide_allowed_explicitly(three_place_guide):
    three_place_guide(["1", "2"])
    guide = build_guide("https://maps.apple/ug/x", session=object(), strict=False)
    assert len(guide.places) == 2


def test_guide_order_follows_the_url_not_the_response(three_place_guide):
    three_place_guide(["3", "1", "2"])
    guide = build_guide("https://maps.apple/ug/x", session=object())
    assert [p.muid for p in guide.places] == ["1", "2", "3"]
