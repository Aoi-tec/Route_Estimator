from typing import Protocol
from ..models import GeoResult


class Geocoder(Protocol):
    def geocode(self, address: str) -> GeoResult: ...
