import json
import httpx
import pytest
from route_estimator.models import Coordinates
from route_estimator.errors import RoutingUnavailableError, SnapDistanceError, NoRouteError
from route_estimator.routing.geoapify_client import GeoapifyClient

A, B = Coordinates(34.7,135.5), Coordinates(34.8,135.6)
KEY = 'test_key_never_expose_123456789'

def payload():
    return {'type':'FeatureCollection','features':[{'type':'Feature',
        'properties':{'distance':120000.25,'time':7200.5,'distance_units':'meters','time_units':'seconds'},
        'geometry':{'type':'MultiLineString','coordinates':[[[135.5,34.7],[135.6,34.8]]]}}]}

def client(handler):
    return GeoapifyClient(httpx.Client(transport=httpx.MockTransport(handler)), api_key=KEY)

def test_request_contract_and_metadata():
    def handler(r):
        assert r.url.host=='api.geoapify.com' and r.url.scheme=='https'
        assert dict(r.url.params)=={'waypoints':'34.7,135.5|34.8,135.6','mode':'drive',
            'type':'balanced','traffic':'free_flow','units':'metric','format':'geojson','details':'instruction_details,route_details','lang':'ja','apiKey':KEY}
        return httpx.Response(200,json=payload())
    c=client(handler)
    try:
        result=c.route(A,B)
        assert str(result.distance_m)=='120000.25' and str(result.duration_s)=='7200.5'
        assert result.snap_distances_m==(0,0)
        assert result.routing_metadata['provider']=='geoapify'
        assert KEY not in str(result.routing_metadata)
    finally:c.close()

@pytest.mark.parametrize('status',[401,403,429,500,302])
def test_http_failure_has_no_retry_or_secret(status):
    calls=[]
    def handler(r):
        calls.append(r);return httpx.Response(status,text=KEY,headers={'location':'https://evil.example'})
    c=client(handler)
    try:
        with pytest.raises(RoutingUnavailableError) as e:c.route(A,B)
        assert KEY not in str(e.value) and len(calls)==1
    finally:c.close()

def test_transport_traceback_and_logging_redact_key(caplog):
    import traceback,logging
    def handler(r):
        logging.getLogger('httpx').warning('URL %s',str(r.url))
        raise httpx.ReadTimeout(str(r.url),request=r)
    c=client(handler)
    try:
        with pytest.raises(RoutingUnavailableError) as e:c.route(A,B)
        assert KEY not in ''.join(traceback.format_exception(e.value))
        assert KEY not in caplog.text
    finally:c.close()

@pytest.mark.parametrize('mutation',[
    lambda d:d['features'][0]['properties'].update(distance=-1),
    lambda d:d['features'][0]['properties'].update(time=True),
    lambda d:d['features'][0]['properties'].update(distance_units='miles'),
    lambda d:d['features'][0].pop('geometry'),
    lambda d:d['features'][0]['geometry'].update(coordinates=[]),
    lambda d:d['features'][0]['geometry'].update(coordinates=[[[135.5,34.7],[999,34.8]]]),
])
def test_invalid_response_never_estimates(mutation):
    d=payload();mutation(d);c=client(lambda _:httpx.Response(200,json=d))
    try:
        with pytest.raises(RoutingUnavailableError):c.route(A,B)
    finally:c.close()

def test_no_route_and_endpoint_distance():
    c=client(lambda _:httpx.Response(200,json={'type':'FeatureCollection','features':[]}))
    try:
        with pytest.raises(NoRouteError):c.route(A,B)
    finally:c.close()
    d=payload();d['features'][0]['geometry']['coordinates'][0][-1]=[135.7,34.8]
    c=client(lambda _:httpx.Response(200,json=d))
    try:
        with pytest.raises(SnapDistanceError):c.route(A,B)
    finally:c.close()
