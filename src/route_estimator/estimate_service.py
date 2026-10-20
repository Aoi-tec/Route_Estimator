from .config import AppConfig
from .errors import GeocodingNotPreciseError, GeocodingUnavailableError, ValidationError, SnapDistanceError
from .geocoding.port import Geocoder
from .models import EstimateResult, Precision, TripRequest, positive_integer
from .pricing import calculate_labor_cost, calculate_fuel_liters, calculate_fuel_cost_optional
from .routing.port import Router


def estimate_trip(request: TripRequest, *, settings: AppConfig, geocoder: Geocoder, router: Router, highway_service=None) -> EstimateResult:
    people = positive_integer(settings.labor.default_people if request.people is None else request.people, "人数")
    if settings.geocoding.allow_approximate:
        raise ValidationError("近似住所による見積は承認フロー未実装のため利用できません")
    locations = []
    for name, location in (("From", request.origin), ("To", request.destination)):
        try:
            geo = geocoder.geocode(location.address)
            if geo.precision != Precision.EXACT_ADDRESS or geo.coordinates is None:
                raise GeocodingNotPreciseError("番地・号まで一致する座標が必要です")
        except GeocodingNotPreciseError as exc:
            raise GeocodingNotPreciseError(f"{name}: {exc}") from exc
        except GeocodingUnavailableError as exc:
            raise GeocodingUnavailableError(f"{name}: {exc}") from exc
        locations.append(geo)
    origin_geo, destination_geo = locations
    route = router.route(origin_geo.coordinates, destination_geo.coordinates)
    limit = settings.routing.max_snap_distance_m if settings.routing.provider == 'geoapify' else settings.osrm.max_snap_distance_m
    if any(d > limit for d in route.snap_distances_m):
        raise SnapDistanceError("道路へのスナップ距離が上限を超えたため計算を保留しました")
    labor = calculate_labor_cost(route.duration_s, people, settings.labor.hourly_rate_yen_per_person)
    liters = calculate_fuel_liters(route.distance_m, settings.vehicle.fuel_efficiency_km_per_liter, settings.vehicle.count)
    fuel = calculate_fuel_cost_optional(liters, settings.vehicle.fuel_price_yen_per_liter)
    result = EstimateResult(request.origin, request.destination, people, settings.labor.hourly_rate_yen_per_person,
        settings.vehicle.count, settings.vehicle.fuel_efficiency_km_per_liter, route.distance_m, route.duration_s,
        labor, liters, fuel, origin_geo, destination_geo, route.snap_distances_m, route.routing_metadata, departure_at=request.departure_at,
        highway_information=route.routing_metadata.get('highway_details',{'status':'missing','sections':[],'roads':[]}))
    return highway_service.enrich(result) if highway_service is not None else result
