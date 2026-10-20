from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from datetime import datetime
from enum import StrEnum
import math

from .errors import ValidationError


def decimal_value(value: object, name: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValidationError(f"{name}は数値で指定してください")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{name}が不正です") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValidationError(f"{name}は{'正の' if positive else '0以上の'}有限数が必要です")
    return result


def positive_integer(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValidationError(f"{name}は1以上の整数が必要です")
    return value


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float

    def __post_init__(self):
        for value, limit, name in ((self.latitude, 90, "緯度"), (self.longitude, 180, "経度")):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > limit:
                raise ValidationError(f"{name}が範囲外または不正です")


@dataclass(frozen=True)
class Customer:
    code: str
    name: str
    address: str


@dataclass(frozen=True)
class Location:
    label: str
    address: str
    customer_code: str | None = None
    original_address: str | None = None

    def __post_init__(self):
        if not isinstance(self.label, str) or not self.label.strip() or not isinstance(self.address, str) or not self.address.strip():
            raise ValidationError("場所の名称と住所は空欄にできません")


@dataclass(frozen=True)
class TripRequest:
    origin: Location
    destination: Location
    people: int | None = None
    departure_at: datetime | None = None

    def __post_init__(self):
        if not isinstance(self.origin, Location) or not isinstance(self.destination, Location):
            raise ValidationError("From/ToにはLocationが必要です")
        if self.departure_at is not None and (not isinstance(self.departure_at, datetime) or self.departure_at.tzinfo is None or self.departure_at.utcoffset() is None):
            raise ValidationError("出発日時はタイムゾーン付きのdatetimeで指定してください")
        if self.people is not None:
            positive_integer(self.people, "人数")


class Precision(StrEnum):
    EXACT_ADDRESS = "EXACT_ADDRESS"
    BLOCK_OR_TOWN = "BLOCK_OR_TOWN"
    CITY_OR_PREF = "CITY_OR_PREF"
    UNRESOLVED = "UNRESOLVED"
    MANUAL_VERIFIED = "MANUAL_VERIFIED"


@dataclass(frozen=True)
class GeoResult:
    coordinates: Coordinates | None
    precision: Precision
    matched_address: str
    provider: str
    dataset_version: str
    fetched_at: str
    source_url: str
    verified_by_user: bool = False
    unmatched_suffix: str = ""


@dataclass(frozen=True)
class RouteResult:
    distance_m: Decimal
    duration_s: Decimal
    snap_distances_m: tuple[Decimal, Decimal]
    routing_metadata: dict = field(default_factory=lambda: {'provider':'osrm','mode':'driving','attribution':'OSRM / © OpenStreetMap contributors'})

    def __post_init__(self):
        object.__setattr__(self, "distance_m", decimal_value(self.distance_m, "距離"))
        object.__setattr__(self, "duration_s", decimal_value(self.duration_s, "時間"))
        if len(self.snap_distances_m) != 2:
            raise ValidationError("From/To両方のスナップ距離が必要です")
        object.__setattr__(self, "snap_distances_m", tuple(decimal_value(v, "スナップ距離") for v in self.snap_distances_m))


@dataclass(frozen=True)
class EstimateResult:
    origin: Location
    destination: Location
    people: int
    hourly_rate: Decimal
    vehicle_count: int
    efficiency: Decimal
    distance_m: Decimal
    duration_s: Decimal
    labor_cost_yen: Decimal
    fuel_liters: Decimal
    fuel_cost_yen_or_none: Decimal | None
    origin_geocode: GeoResult
    destination_geocode: GeoResult
    snap_distances_m: tuple[Decimal, Decimal]
    routing_metadata: dict = field(default_factory=lambda: {'provider':'osrm','mode':'driving','attribution':'OSRM / © OpenStreetMap contributors'})

    departure_at: datetime | None = None
    highway_information: dict = field(default_factory=dict)
    toll_results: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Decimalは精度を保つ文字列へ変換し、JSON化可能な値だけ返す。"""
        from dataclasses import fields, is_dataclass

        def convert(value):
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, Decimal):
                return str(value)
            if is_dataclass(value):
                return {f.name: convert(getattr(value, f.name)) for f in fields(value)}
            if isinstance(value, (tuple, list)):
                return [convert(v) for v in value]
            return value

        result = convert(self)
        result["total_cost_yen"] = convert(self.total_cost_yen)
        result["geocode_quality"] = self.geocode_quality
        result["provider_metadata"] = self.provider_metadata
        return result

    @property
    def total_cost_yen(self) -> Decimal | None:
        if self.fuel_cost_yen_or_none is None:
            return None
        return self.labor_cost_yen + self.fuel_cost_yen_or_none

    @property
    def geocode_quality(self) -> dict[str, str]:
        return {"origin": str(self.origin_geocode.precision), "destination": str(self.destination_geocode.precision)}

    @property
    def provider_metadata(self) -> dict[str, dict]:
        return {
            name: {"provider": geo.provider, "dataset_version": geo.dataset_version,
                   "fetched_at": geo.fetched_at, "source_url": geo.source_url,
                   "unmatched_suffix": geo.unmatched_suffix, "verified_by_user": geo.verified_by_user}
            for name, geo in (("origin", self.origin_geocode), ("destination", self.destination_geocode))
        }
