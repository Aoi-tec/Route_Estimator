from dataclasses import replace
from decimal import Decimal as D
import json
import pytest

from route_estimator.errors import GeocodingNotPreciseError, SnapDistanceError
from route_estimator.estimate_service import estimate_trip
from route_estimator.models import Coordinates, GeoResult, Precision, TripRequest, RouteResult


class FakeGeocoder:
    def __init__(self, precision=Precision.EXACT_ADDRESS):
        self.calls = []
        self.precision = precision

    def geocode(self, address):
        self.calls.append(address)
        return GeoResult(Coordinates(34.7, 135.5), self.precision, address, "fake", "test", "2026-10-19T00:00:00+00:00", "fixture://test")


class FakeRouter:
    def __init__(self): self.calls = []
    def route(self, origin, destination, **kwargs):
        self.calls.append((origin, destination))
        return RouteResult(D(120000), D(7200), (D(1), D(2)))


def test_service_reuse_defaults_and_json(settings):
    geo, router = FakeGeocoder(), FakeRouter()
    req = TripRequest(settings.company, settings.company)
    result = estimate_trip(req, settings=settings, geocoder=geo, router=router)
    assert result.people == 2
    assert result.labor_cost_yen == D(12000)
    assert result.fuel_liters == 12
    assert result.fuel_cost_yen_or_none is None
    assert result.total_cost_yen is None
    assert result.geocode_quality == {"origin": "EXACT_ADDRESS", "destination": "EXACT_ADDRESS"}
    assert result.provider_metadata["origin"]["provider"] == "fake"
    assert geo.calls == [settings.company.address, settings.company.address]
    json.dumps(result.to_dict(), ensure_ascii=False)


def test_no_route_on_approximate_result(settings):
    router = FakeRouter()
    with pytest.raises(GeocodingNotPreciseError):
        estimate_trip(TripRequest(settings.company, settings.company), settings=settings, geocoder=FakeGeocoder(Precision.BLOCK_OR_TOWN), router=router)
    assert not router.calls


def test_injected_router_cannot_bypass_snap_check(settings):
    class FarRouter:
        def route(self, a, b): return RouteResult(D(1), D(1), (D(201), D(1)))
    with pytest.raises(SnapDistanceError):
        estimate_trip(TripRequest(settings.company, settings.company), settings=settings, geocoder=FakeGeocoder(), router=FarRouter())


def test_people_override_and_fuel(settings):
    settings = replace(settings, vehicle=replace(settings.vehicle, fuel_price_yen_per_liter=D(170)))
    result = estimate_trip(TripRequest(settings.company, settings.company, 3), settings=settings, geocoder=FakeGeocoder(), router=FakeRouter())
    assert result.labor_cost_yen == 18000
    assert result.fuel_cost_yen_or_none == 2040


def test_town_point_fallback_widens_snap_limit_only_for_that_end(fallback_settings):
    class Router:
        def __init__(self): self.kwargs = None
        def route(self, origin, destination, **kwargs):
            self.kwargs = kwargs
            return RouteResult(D(120000), D(7200), (D(800), D(1)))
    class Mixed(FakeGeocoder):
        def geocode(self, address):
            r = super().geocode(address)
            return replace(r, precision=Precision.BLOCK_OR_TOWN) if len(self.calls) == 1 else r
    router = Router()
    req = TripRequest(fallback_settings.company, fallback_settings.company)
    result = estimate_trip(req, settings=fallback_settings, geocoder=Mixed(), router=router)
    assert router.kwargs == {"snap_limits_m": (D(1000), D(200))}
    assert result.geocode_quality == {"origin": "BLOCK_OR_TOWN", "destination": "EXACT_ADDRESS"}


def test_town_point_snap_still_limited(fallback_settings):
    class Router:
        def route(self, origin, destination, **kwargs): return RouteResult(D(1), D(1), (D(1001), D(1)))
    with pytest.raises(SnapDistanceError):
        estimate_trip(TripRequest(fallback_settings.company, fallback_settings.company), settings=fallback_settings,
                      geocoder=FakeGeocoder(Precision.BLOCK_OR_TOWN), router=Router())
