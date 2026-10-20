from decimal import Decimal as D
import httpx
import pytest

from route_estimator.errors import RoutingUnavailableError, NoRouteError, SnapDistanceError, ValidationError
from route_estimator.models import Coordinates
from route_estimator.routing.osrm_client import OsrmClient

A, B = Coordinates(34.7, 135.5), Coordinates(34.8, 135.6)


def ok():
    return {"code": "Ok", "routes": [{"distance": 120000.25, "duration": 7200.5}], "waypoints": [
        {"distance": 10, "location": [135.5, 34.7]}, {"distance": 20, "location": [135.6, 34.8]}]}


def router(handler):
    return OsrmClient(httpx.Client(transport=httpx.MockTransport(handler)))


def test_units_coordinate_order_and_snap():
    def handler(request):
        assert request.url.path == "/route/v1/driving/135.5,34.7;135.6,34.8"
        assert request.url.params["steps"] == "false"
        return httpx.Response(200, json=ok())
    result = router(handler).route(A, B)
    assert result.distance_m == D("120000.25")
    assert result.duration_s == D("7200.5")


@pytest.mark.parametrize("status,body,error", [
    (400, {"code": "NoRoute"}, NoRouteError),
    (200, {"code": "NoSegment"}, NoRouteError),
    (200, {"code": "Ok", "routes": []}, RoutingUnavailableError),
    (200, [], RoutingUnavailableError),
    (404, {}, RoutingUnavailableError),
    (200, "broken JSON", RoutingUnavailableError),
])
def test_errors(status, body, error):
    with pytest.raises(error):
        router(lambda _: httpx.Response(status, json=body)).route(A, B)


@pytest.mark.parametrize("field,value", [("distance", -1), ("duration", float("inf")), ("distance", True)])
def test_invalid_measurements(field, value):
    data = ok()
    data["routes"][0][field] = value
    import json
    with pytest.raises(RoutingUnavailableError):
        router(lambda _: httpx.Response(200, content=json.dumps(data))).route(A, B)


def test_snap_missing_and_too_far():
    data = ok()
    data["waypoints"][1]["distance"] = 201
    with pytest.raises(SnapDistanceError):
        router(lambda _: httpx.Response(200, json=data)).route(A, B)
    del data["waypoints"][1]["distance"]
    with pytest.raises(RoutingUnavailableError):
        router(lambda _: httpx.Response(200, json=data)).route(A, B)


@pytest.mark.parametrize("exception", [httpx.ConnectError, httpx.ReadTimeout])
def test_transport_failure(exception):
    calls = []
    def handler(request):
        calls.append(request)
        raise exception("offline", request=request)
    with pytest.raises(RoutingUnavailableError):
        router(handler).route(A, B)
    assert len(calls) == 3


def test_reject_external_host():
    with pytest.raises(ValidationError):
        OsrmClient(httpx.Client(), base_url="http://example.com")
