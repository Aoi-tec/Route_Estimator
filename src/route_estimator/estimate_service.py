from datetime import datetime, timezone
from decimal import Decimal

from .config import AppConfig
from .errors import GeocodingNotPreciseError, GeocodingUnavailableError, ValidationError, SnapDistanceError
from .geocoding.port import Geocoder
from .models import EstimateResult, GeoResult, Precision, TripRequest, positive_integer
from .pricing import calculate_labor_cost, calculate_fuel_liters, calculate_fuel_cost_optional
from .routing.port import Router

APPROXIMATE_SNAP_LIMIT_M = Decimal(1000)


def estimate_trip(request: TripRequest, *, settings: AppConfig, geocoder: Geocoder, router: Router, highway_service=None) -> EstimateResult:
    people = positive_integer(settings.labor.default_people if request.people is None else request.people, "人数")
    locations = []
    for name, location in (("From", request.origin), ("To", request.destination)):
        if location.coordinates is not None:
            # 登録済みの緯度経度を優先し、住所検索を行わない。
            locations.append(GeoResult(location.coordinates, Precision.MANUAL_VERIFIED, location.address, "registered",
                                       "user", datetime.now(timezone.utc).isoformat(), "config/csv/manual", verified_by_user=True))
            continue
        try:
            geo = geocoder.geocode(location.address)
            accepted = {Precision.EXACT_ADDRESS} | ({Precision.BLOCK_OR_TOWN} if settings.geocoding.allow_approximate else set())
            if geo.precision not in accepted or geo.coordinates is None:
                raise GeocodingNotPreciseError("番地・号まで一致する座標が必要です" if not settings.geocoding.allow_approximate
                                               else "丁目・町字まで一致する座標が必要です")
        except GeocodingNotPreciseError as exc:
            raise GeocodingNotPreciseError(f"{name}: {exc}") from exc
        except GeocodingUnavailableError as exc:
            raise GeocodingUnavailableError(f"{name}: {exc}") from exc
        locations.append(geo)
    origin_geo, destination_geo = locations
    limit = settings.routing.max_snap_distance_m if settings.routing.provider == 'geoapify' else settings.osrm.max_snap_distance_m
    # 代表点は道路から離れていることがあるため、その端点だけ上限を広げる。
    limits = tuple(max(limit, APPROXIMATE_SNAP_LIMIT_M) if g.precision != Precision.EXACT_ADDRESS else limit for g in locations)
    if limits == (limit, limit):
        route = router.route(origin_geo.coordinates, destination_geo.coordinates)
    else:
        route = router.route(origin_geo.coordinates, destination_geo.coordinates, snap_limits_m=limits)
    if any(d > m for d, m in zip(route.snap_distances_m, limits)):
        raise SnapDistanceError("道路へのスナップ距離が上限を超えたため計算を保留しました")
    labor = calculate_labor_cost(route.duration_s, people, settings.labor.hourly_rate_yen_per_person)
    liters = calculate_fuel_liters(route.distance_m, settings.vehicle.fuel_efficiency_km_per_liter, settings.vehicle.count)
    fuel = calculate_fuel_cost_optional(liters, settings.vehicle.fuel_price_yen_per_liter)
    result = EstimateResult(request.origin, request.destination, people, settings.labor.hourly_rate_yen_per_person,
        settings.vehicle.count, settings.vehicle.fuel_efficiency_km_per_liter, route.distance_m, route.duration_s,
        labor, liters, fuel, origin_geo, destination_geo, route.snap_distances_m, route.routing_metadata, departure_at=request.departure_at,
        highway_information=route.routing_metadata.get('highway_details',{'status':'missing','sections':[],'roads':[]}))
    return highway_service.enrich(result) if highway_service is not None else result
