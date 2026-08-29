"""Normalized data model.

Everything downstream of :mod:`mapplizer.normalize` speaks in these types, so
Apple's component schema is confined to that one module.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlaceRef:
    """A place's identity, as carried in a share URL."""

    muid: str
    result_provider_id: int


@dataclass(frozen=True)
class PinRef:
    """A dropped pin: a user-placed marker with no business behind it.

    Unlike a POI these are self-describing -- the share URL carries the
    coordinates and reverse-geocoded address directly, so no lookup is needed.
    """

    address: str
    lat: float
    lng: float


GuideEntry = PlaceRef | PinRef


@dataclass(frozen=True)
class GuideRef:
    """A guide's name and membership, decoded from a share URL."""

    name: str
    entries: tuple[GuideEntry, ...]

    @property
    def places(self) -> tuple[PlaceRef, ...]:
        """Just the entries needing a place lookup."""
        return tuple(e for e in self.entries if isinstance(e, PlaceRef))

    @property
    def pins(self) -> tuple[PinRef, ...]:
        return tuple(e for e in self.entries if isinstance(e, PinRef))


@dataclass
class Photo:
    url: str
    width: int | None = None
    height: int | None = None
    author: str | None = None
    attribution: str | None = None


@dataclass
class Place:
    """One resolved place, flattened out of Apple's component array."""

    muid: str
    result_provider_id: int
    name: str
    lat: float
    lng: float

    place_id: str | None = None
    category: str | None = None
    address_lines: list[str] = field(default_factory=list)
    # Structured address, named to line up with CNPostalAddress: street,
    # subLocality, city, subAdministrativeArea, state, postalCode, country,
    # ISOCountryCode. Apple supplies every one of these.
    street: str | None = None
    sub_locality: str | None = None
    locality: str | None = None
    sub_administrative_area: str | None = None
    administrative_area: str | None = None
    postcode: str | None = None
    country: str | None = None
    country_code: str | None = None
    phone: str | None = None
    url: str | None = None
    rating: float | None = None
    rating_count: int | None = None
    price_level: int | None = None
    price_symbol: str | None = None
    timezone: str | None = None
    hours: list[str] = field(default_factory=list)
    photos: list[Photo] = field(default_factory=list)

    @property
    def address(self) -> str:
        return ", ".join(self.address_lines)

    @property
    def postal_address(self) -> dict[str, str]:
        """The structured address, keyed as CNPostalAddress names its fields."""
        fields = {
            "street": self.street,
            "subLocality": self.sub_locality,
            "city": self.locality,
            "subAdministrativeArea": self.sub_administrative_area,
            "state": self.administrative_area,
            "postalCode": self.postcode,
            "country": self.country,
            "ISOCountryCode": self.country_code,
        }
        return {k: v for k, v in fields.items() if v}

    @property
    def maps_url(self) -> str:
        """A canonical Apple Maps link back to this place."""
        if self.place_id:
            return (
                f"https://maps.apple.com/place?place-id={self.place_id}"
                f"&address={self.lat},{self.lng}"
            )
        query = urllib.parse.urlencode(
            {"ll": f"{self.lat},{self.lng}", "q": self.name}
        )
        return f"https://maps.apple.com/?{query}"

    @property
    def price_display(self) -> str | None:
        if self.price_level is None:
            return None
        return (self.price_symbol or "$") * self.price_level


@dataclass
class Guide:
    """A fully resolved guide, ready to serialize."""

    name: str
    places: list[Place] = field(default_factory=list)
    source_url: str | None = None
