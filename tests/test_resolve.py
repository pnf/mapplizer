import pytest

from mapplizer.resolve import ResolveError, parse_guide_ref, parse_guide_url


def test_parses_name_and_all_places(guide_ref_b64):
    guide = parse_guide_ref(guide_ref_b64)
    assert guide.name == "Resto"
    assert len(guide.places) == 57


def test_muids_are_strings(guide_ref_b64):
    guide = parse_guide_ref(guide_ref_b64)
    assert guide.places[0].muid == "3262736025014158887"
    assert all(isinstance(p.muid, str) for p in guide.places)
    assert all(p.result_provider_id == 9902 for p in guide.places)


def test_place_order_is_preserved(guide_ref_b64):
    guide = parse_guide_ref(guide_ref_b64)
    assert len(set(p.muid for p in guide.places)) == 57


def test_url_and_hand_pasted_url_agree(guide_ref_b64):
    """A pasted link has '+' decoded to a space; both must decode identically."""
    import urllib.parse

    encoded = "https://maps.apple.com/guides?user=" + urllib.parse.quote(
        guide_ref_b64, safe=""
    )
    pasted = "https://maps.apple.com/guides?user=" + guide_ref_b64
    assert parse_guide_url(encoded) == parse_guide_url(pasted)


def test_url_without_user_param_explains_itself():
    with pytest.raises(ResolveError, match="no `user` parameter"):
        parse_guide_url("https://maps.apple.com/guides")


def test_bad_base64_is_reported():
    with pytest.raises(ResolveError, match="base64"):
        parse_guide_ref("!!!not base64!!!")


def test_valid_base64_that_is_not_a_guide():
    with pytest.raises(ResolveError):
        parse_guide_ref("aGVsbG8gd29ybGQ=")  # "hello world"
