from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


class TollError(Exception):
    """料金取得を保留する。0円への代用は禁止。"""

class FetchError(TollError): pass
class ParseError(TollError): pass
class ICNotFoundError(TollError): pass
class NoRouteError(TollError): pass
class AmbiguousICError(TollError):
    def __init__(self, candidates):
        self.candidates = candidates
        super().__init__('IC名が一意ではありません。県・道路・コードを選択してください')


@dataclass(frozen=True)
class IC:
    name: str
    code: str
    prefecture: str
    road: str
    reading: str = ''
    note: str = ''


@dataclass(frozen=True)
class RouteStop:
    name: str
    kind: str
    code: str
    arrival_time: str | None = None


@dataclass(frozen=True)
class RouteSegment:
    road_name: str
    distance_km: Decimal | None
    duration_minutes: int | None
    from_stop_index: int
    to_stop_index: int


@dataclass(frozen=True)
class RouteOption:
    number: int
    etc2_yen: int | None
    distance_km: Decimal | None
    duration_minutes: int | None
    roads: list[str]
    stops: list[RouteStop]
    segments: list[RouteSegment]
    stops_complete: bool


@dataclass(frozen=True)
class TollSearchResult:
    from_ic_name: str
    from_ic_code: str
    to_ic_name: str
    to_ic_code: str
    vehicle_type: str
    routes: list[RouteOption]
    retrieved_at: datetime
    searched_departure_at: str | None
    source_url: str
    master_version: str
    requested_departure_at: str | None = None
    reference_only: bool = True
