from copy import deepcopy
from datetime import datetime
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace
import json
import pytest
from nexco_toll import IC
from route_estimator.routing.highway_details import extract_highway_details
from route_estimator.routing.ic_catalog import ICCatalog, process_catalog
from route_estimator.highway_service import HighwayService
from route_estimator.highway_cui import show_highways,toll_flow
from route_estimator.scheduling import JST
from route_estimator.models import TripRequest
from route_estimator.estimate_service import estimate_trip
from test_estimate_service import FakeGeocoder,FakeRouter

POINTS=[[34.68,135.5],[34.69,135.5],[34.7,135.5],[34.71,135.5]]


def properties():
    return {'time':600,'legs':[{'steps':[
        {'from_index':0,'to_index':1,'time':100,'toll':False,'road_class':'primary','name':'一般道'},
        {'from_index':1,'to_index':2,'time':300,'toll':True,'road_class':'motorway','name':'E25, 西名阪自動車道'},
        {'from_index':2,'to_index':3,'time':200,'toll':False,'road_class':'primary','name':'一般道'},
    ]}]}


def test_route_segment_boundaries_and_elapsed_times():
    info=extract_highway_details(properties(),POINTS)
    assert info['status']=='available' and len(info['sections'])==1
    s=info['sections'][0]
    assert s['entry_elapsed_s']=='100' and s['entry_point']==POINTS[1] and s['exit_point']==POINTS[2]
    assert s['roads']==['西名阪自動車道'] and not s['starts_inside'] and not s['ends_inside']


def test_missing_and_broken_steps_not_free_route():
    assert extract_highway_details({},POINTS)['status']=='missing'
    p=properties();p['legs'][0]['steps'][1]['from_index']=99
    assert extract_highway_details(p,POINTS)['status']=='invalid'
    p=properties();p['legs'][0]['steps'][0]['time']=-1
    assert extract_highway_details(p,POINTS)['status']=='invalid'


def test_multiple_highway_sections():
    p=properties();p['legs'][0]['steps'].append({'from_index':3,'to_index':4,'time':50,'toll':True,'road_class':'motorway'})
    p['time']=650
    info=extract_highway_details(p,POINTS+[[34.72,135.5]])
    assert len(info['sections'])==2 and info['sections'][1]['entry_elapsed_s']=='600'
    assert info['sections'][1]['ends_inside']


def test_catalog_matches_route_and_road_without_nearest_city_guess():
    catalog=ICCatalog(None,'unused')
    catalog._points=[{'name':'入口','latitude':34.6901,'longitude':135.5,'id':'id1'},
                     {'name':'遠方','latitude':35,'longitude':135.5,'id':'id2'}]
    master=SimpleNamespace(ics=[IC('入口','1000001','大阪府','E25 西名阪自動車道'),IC('遠方','1000002','大阪府','西名阪自動車道')])
    section=extract_highway_details(properties(),POINTS)['sections'][0]
    found=catalog.identify(section,'entry',master)
    assert found['suggested_code']=='1000001'
    assert len(found['candidates'])==1
    master.ics.append(IC('入口','1000003','大阪府','E25 西名阪自動車道'))
    assert catalog.identify(section,'entry',master)['status']=='ambiguous'
    section['starts_inside']=True
    assert catalog.identify(section,'entry',master)['candidates']==[]


def test_wrong_direction_excluded():
    c=ICCatalog(None,'unused');c._points=[{'name':'入口','latitude':34.6901,'longitude':135.5,'id':'id'}]
    master=SimpleNamespace(ics=[IC('入口','1000001','大阪府','西名阪自動車道',note='出口のみ通行可能。')])
    section=extract_highway_details(properties(),POINTS)['sections'][0]
    assert c.identify(section,'entry',master)['status']=='unresolved'


def test_ic_entry_arrival_applied_in_japan(settings):
    base=estimate_trip(TripRequest(settings.company,settings.company,departure_at=datetime(2026,10,21,9,tzinfo=JST)),settings=settings,geocoder=FakeGeocoder(),router=FakeRouter())
    base=replace(base,highway_information=extract_highway_details(properties(),POINTS))
    service=HighwayService.__new__(HighwayService)
    service.tolls=SimpleNamespace(master=SimpleNamespace(version='v155'))
    service.catalog=SimpleNamespace(identify=lambda *args:{'status':'unresolved','candidates':[]})
    result=service.enrich(base)
    assert result.highway_information['sections'][0]['entry_departure_at']=='2026-10-21T09:02:00+09:00'
    assert result.labor_cost_yen==base.labor_cost_yen and result.total_cost_yen==base.total_cost_yen
    assert result.to_dict()['highway_information']['sections']


def test_full_cui_ic_confirmation_and_etc_reference(settings):
    import from_to
    from route_estimator.customer_repository import CustomerRepository
    from route_estimator.models import RouteResult
    from nexco_toll.models import TollSearchResult, RouteOption, RouteStop, FetchError
    candidate_a={'name':'入口','code':'1000001','road':'西名阪自動車道','prefecture':'大阪府','route_distance_m':5.0}
    candidate_b={**candidate_a,'name':'出口','code':'1000002'}
    info=extract_highway_details(properties(),POINTS)
    info['sections'][0].update(entry={'suggested_code':'1000001','candidates':[candidate_a]},
        exit={'suggested_code':'1000002','candidates':[candidate_b]},entry_departure_at='2026-10-21T09:02:00+09:00')
    class Router:
        def route(self,a,b):return RouteResult(D(10000),D(600),(D(0),D(0)),{'provider':'geoapify','attribution':'test','highway_details':info})
    calls=[]
    class Tolls:
        def search(self,a,b,**kwargs):
            calls.append((a,b,kwargs))
            return TollSearchResult('入口',a,'出口',b,kwargs['vehicle_type'],[
                RouteOption(1,960,D('18.7'),20,['西名阪自動車道'],[RouteStop('入口','IC',a),RouteStop('出口','IC',b)],[],True)],
                datetime(2026,10,20,12,tzinfo=JST),'2026-10-21T09:02:00+09:00','https://search.w-nexco.co.jp/','v155')
    service=SimpleNamespace(enrich=lambda result:result,tolls=Tolls())
    inputs=iter(['1','2','住所','','1','1','3'])
    lines=[]
    status=from_to.run_cli(settings=settings,repository=CustomerRepository([]),geocoder=FakeGeocoder(),router=Router(),
        highway_service=service,read=lambda _:next(inputs),write=lines.append)
    text='\n'.join(lines)
    assert status==0 and '入口IC候補：入口' in text and '出口IC候補：出口' in text
    assert 'ETC2.0 960円' in text and '合計未反映' in text
    assert calls[0][0:2]==('1000001','1000002')
    assert calls[0][2]['vehicle_type']=='中型車' and calls[0][2]['departure_at'].hour==9
    assert 'NEXCO検索日時：2026-10-21（水）09:02' in text


def test_missing_class_is_not_claimed_no_highway():
    p=properties()
    for step in p['legs'][0]['steps']:step.pop('road_class');step.pop('toll')
    assert extract_highway_details(p,POINTS)['status']=='invalid'


def test_official_catalog_current_year_filter_and_offline_cache(tmp_path):
    from io import BytesIO
    from zipfile import ZipFile
    import httpx
    features=[]
    for i in range(102):
        features.append({'type':'Feature','properties':{'N06_013':2000,'N06_014':2024 if i==101 else 9999,
            'N06_018':f'地点{i}','N06_019':'1','N06_015':f'ID{i}'},
            'geometry':{'type':'Point','coordinates':[135.5,34.7]}})
    raw=json.dumps({'type':'FeatureCollection','features':features}).encode()
    buffer=BytesIO()
    with ZipFile(buffer,'w') as z:z.writestr('N06-25_GML/UTF-8/N06-25_Joint.geojson',raw)
    calls=[]
    def handler(request):calls.append(request);return httpx.Response(200,content=buffer.getvalue())
    catalog=ICCatalog(httpx.Client(transport=httpx.MockTransport(handler)),tmp_path)
    assert len(catalog.load())==101 and len(calls)==1
    assert len(ICCatalog(None,tmp_path).load())==101
    metadata=json.loads((tmp_path/'ic-2025.json').read_text())
    assert metadata['dataset_date']=='2025-12-31' and metadata['archive_sha256']
    # 無効な再取得では採用済み座標原本を壊さない。
    invalid=ICCatalog(httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b'not a zip'))),tmp_path)
    from nexco_toll import TollError
    with pytest.raises(TollError):invalid.load(force=True)
    assert (tmp_path/'N06-25_Joint.geojson').read_bytes()==raw


def test_json_includes_toll_dataclasses_without_adding_to_total(settings):
    from nexco_toll import TollSearchResult,RouteOption
    result=estimate_trip(TripRequest(settings.company,settings.company),settings=settings,geocoder=FakeGeocoder(),router=FakeRouter())
    toll=TollSearchResult('a','1000001','b','1000002','普通車',[
        RouteOption(1,960,D('18.7'),20,[],[],[],False)],datetime(2026,10,21,9,tzinfo=JST),None,'https://search.w-nexco.co.jp/','v155')
    result=replace(result,toll_results=[toll])
    data=json.loads(json.dumps(result.to_dict()))
    assert data['toll_results'][0]['routes'][0]['etc2_yen']==960
    assert data['toll_results'][0]['reference_only'] and data['total_cost_yen'] is None
