import json
import os
from pathlib import Path
import pytest
import httpx
from route_estimator import bootstrap
from route_estimator.config import load_config
from route_estimator.errors import ConfigError,ValidationError
from route_estimator.routing.geoapify_client import GeoapifyClient
from route_estimator.routing.osrm_client import OsrmClient
from configure_api_key import save_key
from route_estimator.credentials import read_key_file,resolve_api_key

EXAMPLE=Path(__file__).parents[1]/'config.example.json'
KEY='local_dummy_key_1234567890123456'

def config(tmp_path):
    p=tmp_path/'config.json';p.write_text(EXAMPLE.read_text());return load_config(p)

def test_missing_key_prevents_any_network(tmp_path,monkeypatch):
    monkeypatch.delenv('GEOAPIFY_API_KEY',raising=False)
    s=config(tmp_path)
    with pytest.raises(ConfigError,match='キー'):
        with bootstrap.open_adapters(s):pytest.fail('must not yield adapters')

def test_key_file_and_environment_priority(tmp_path,monkeypatch):
    s=config(tmp_path);save_key(s.routing.api_key_file,KEY)
    if os.name!='nt':assert s.routing.api_key_file.stat().st_mode & 0o777==0o600
    monkeypatch.delenv('GEOAPIFY_API_KEY',raising=False)
    with bootstrap.open_adapters(s) as (_,router):assert isinstance(router,GeoapifyClient) and router.key==KEY
    monkeypatch.setenv('GEOAPIFY_API_KEY','')
    with pytest.raises(ValidationError):
        with bootstrap.open_adapters(s):pass
    assert s.routing.api_key_file.name=='.env'
    assert read_key_file(s.routing.api_key_file,'GEOAPIFY_API_KEY')==KEY

def test_legacy_config_without_provider_keeps_osrm(tmp_path):
    d=json.loads(EXAMPLE.read_text());d.pop('routing')
    p=tmp_path/'config.json';p.write_text(json.dumps(d));s=load_config(p)
    assert s.routing.provider=='osrm'
    with bootstrap.open_adapters(s) as (_,router):assert isinstance(router,OsrmClient)

def test_key_never_belongs_in_json(tmp_path):
    d=json.loads(EXAMPLE.read_text());d['routing']['api_key']=KEY
    p=tmp_path/'config.json';p.write_text(json.dumps(d))
    with pytest.raises(ConfigError) as e:load_config(p)
    assert KEY not in str(e.value)

def test_invalid_key_never_overwrites_existing_key(tmp_path):
    p=tmp_path/'key.txt';save_key(p,KEY)
    with pytest.raises(ValidationError):save_key(p,'bad\nkey')
    assert read_key_file(p,'GEOAPIFY_API_KEY')==KEY

def test_dotenv_keeps_other_lines_and_replaces_key(tmp_path):
    p=tmp_path/'.env';p.write_text('# comment\nOTHER=1\nGEOAPIFY_API_KEY=old_dummy_key_00000000000\n')
    save_key(p,KEY)
    text=p.read_text()
    assert '# comment' in text and 'OTHER=1' in text and 'old_dummy' not in text
    assert read_key_file(p,'GEOAPIFY_API_KEY')==KEY

def test_legacy_state_key_file_still_readable(tmp_path,monkeypatch):
    monkeypatch.delenv('GEOAPIFY_API_KEY',raising=False)
    legacy=tmp_path/'state/geoapify_api_key.txt';legacy.parent.mkdir();legacy.write_text(KEY+'\n')
    assert resolve_api_key('GEOAPIFY_API_KEY',tmp_path/'.env',tmp_path)==KEY
    (tmp_path/'.env').write_text('GEOAPIFY_API_KEY="'+KEY[::-1]+'"\n')
    assert resolve_api_key('GEOAPIFY_API_KEY',tmp_path/'.env',tmp_path)==KEY[::-1]

def test_api_result_flows_through_public_service_and_cui(settings):
    from route_estimator.models import Coordinates, GeoResult, Precision, TripRequest
    from route_estimator.estimate_service import estimate_trip
    from route_estimator.routing.geoapify_client import GeoapifyClient
    from from_to import _result
    coords=[Coordinates(34.7,135.5),Coordinates(34.8,135.6)]
    class Geocoder:
        def geocode(self,address):
            c=coords.pop(0)
            return GeoResult(c,Precision.EXACT_ADDRESS,address,'fake','test','test','fixture://')
    d={'type':'FeatureCollection','features':[{'type':'Feature',
        'properties':{'distance':120000,'time':7200,'distance_units':'meters'},
        'geometry':{'type':'MultiLineString','coordinates':[[[135.5,34.7],[135.6,34.8]]]}}]}
    router=GeoapifyClient(httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=d))),api_key=KEY)
    try:result=estimate_trip(TripRequest(settings.company,settings.company),settings=settings,geocoder=Geocoder(),router=router)
    finally:router.close()
    assert result.labor_cost_yen==12000 and result.fuel_liters==12
    assert result.to_dict()['routing_metadata']['provider']=='geoapify'
    output=[];_result(result,output.append)
    assert any('Powered by Geoapify' in line for line in output)
    assert not any('※ OSRM' in line for line in output)
