from pathlib import Path
from dataclasses import replace
from datetime import datetime
import json
import httpx
import pytest
from nexco_toll import TollService, IC, ParseError, FetchError, AmbiguousICError
from nexco_toll.master import ICMaster, _array
from nexco_toll.parser import parse_response
from nexco_toll.scraper import Fetcher
from nexco_toll.updater import MasterUpdater
from route_estimator.scheduling import JST

FIXTURE=Path(__file__).parent/'fixtures/nexco_20261021.jsonp'
A=IC('大分宮河内','1302420','大分県','E10 東九州自動車道')
B=IC('太宰府','1030071','福岡県','E3 九州自動車道')
DATE=datetime(2026,10,21,9,tzinfo=JST)


def raw_data():
    text=FIXTURE.read_text()
    return json.loads(text[text.index('(')+1:text.rindex(')')])


def wrap(data): return 'routecallback('+json.dumps(data,ensure_ascii=False)+');'


def master_text():
    rows=[[f'地点{i}',f'ちてん{i}',f'{1000000+i}','27','0','','0'] for i in range(120)]
    rows.extend([[ic.name,ic.name,ic.code,'27','0','','0'] for ic in (A,B)])
    return "var hiver='v155';var rdM=[['1','道路',[1]]];var preM=[['27','大阪府']];var icM="+repr(rows)+';'


def test_real_response_exact_routes_and_details():
    routes,searched=parse_response(FIXTURE.read_bytes(),A,B,requested_departure=DATE)
    assert searched==DATE
    assert [r.etc2_yen for r in routes]==[4020,4020,5880]
    assert [s.name for s in routes[0].stops]==['大分宮河内','日出ＪＣＴ','鳥栖ＪＣＴ','太宰府']
    assert all(r.stops_complete for r in routes)
    assert all('ＳＡ' not in stop.name and 'ＰＡ' not in stop.name for r in routes for stop in r.stops)
    assert routes[0].duration_minutes==112
    assert str(routes[0].distance_km)=='156.7'
    assert routes[0].segments[0].from_stop_index==0 and routes[0].segments[-1].to_stop_index==3


@pytest.mark.parametrize('count',[1,2])
def test_returns_only_existing_routes(count):
    d=raw_data();d['Total']=d['Total'][:count];d['Detail']=d['Detail'][:count]
    routes,_=parse_response(wrap(d),A,B)
    assert len(routes)==count


def test_missing_etc2_never_uses_normal_or_etc():
    d=raw_data()
    for t in (d['Total'][0],d['Detail'][0]['Total']):t.pop('TotalChargeETC2');t.pop('DispTotalChargeETC2')
    routes,_=parse_response(wrap(d),A,B)
    assert routes[0].etc2_yen is None
    for t in (d['Total'][0],d['Detail'][0]['Total']):t['TotalChargeETC2']='0';t['DispTotalChargeETC2']='0'
    assert parse_response(wrap(d),A,B)[0][0].etc2_yen==0


@pytest.mark.parametrize('change',[
 lambda d:d['Detail'].reverse(),
 lambda d:d['Total'][0].update(DispTotalChargeETC2='99'),
 lambda d:d['Total'][0].update(TotalChargeETC2=True),
 lambda d:d['fromIc'].update(Code='9999999'),
 lambda d:d['Total'][0].update(TotalTime='bad'),
])
def test_corrupt_response_fails_closed(change):
    d=raw_data();change(d)
    with pytest.raises(ParseError):parse_response(wrap(d),A,B,requested_departure=DATE)


def test_wrong_date_rejected():
    with pytest.raises(ParseError):parse_response(FIXTURE.read_bytes(),A,B,requested_departure=DATE.replace(hour=10))


def test_incomplete_detail_retains_summary_without_inventing_stops():
    d=raw_data();last=list(d['Detail'][0]['Detail'])[-1];del d['Detail'][0]['Detail'][last]
    routes,_=parse_response(wrap(d),A,B)
    assert not routes[0].stops_complete and routes[0].etc2_yen==4020
    assert routes[0].stops[-1].name=='鳥栖ＪＣＴ'
    assert all(s.to_stop_index<len(routes[0].stops) for s in routes[0].segments)


def test_no_js_execution_and_master_mapping():
    master=ICMaster(master_text())
    assert master.resolve(A.name).code==A.code
    assert master.resolve(A.code).road=='道路'
    assert master.candidates('ちてん')
    with pytest.raises(ParseError):_array('var icM=[__import__("os").system("bad")];','icM')
    with pytest.raises(ParseError):parse_response('routecallback({}); evil();',A,B)
    master.ics.append(replace(A,code='9999999'))
    with pytest.raises(AmbiguousICError):master.resolve(A.name)


def test_request_exact_datetime_and_no_customer_data():
    def handler(request):
        assert request.url.host=='search.w-nexco.co.jp'
        p=dict(request.url.params)
        assert p['val_date']=='20261021' and p['val_time_hour']=='9' and p['val_time_min']=='0'
        assert p['val_cartype']=='2' and p['val_from']==A.code
        assert not {'address','people','customer','apiKey'} & p.keys()
        return httpx.Response(200,content=FIXTURE.read_bytes())
    service=TollService(httpx.Client(transport=httpx.MockTransport(handler)),master=ICMaster(master_text()))
    result=service.search(A.code,B.code,departure_at=DATE)
    assert result.reference_only and result.searched_departure_at==DATE.isoformat()


@pytest.mark.parametrize('status',[302,403,429,500])
def test_http_failure_no_retry(status):
    calls=[]
    def handler(request):calls.append(request);return httpx.Response(status,headers={'location':'https://evil.example'})
    f=Fetcher(httpx.Client(transport=httpx.MockTransport(handler)),interval=0)
    with pytest.raises(FetchError):f.get('https://search.w-nexco.co.jp/')
    assert len(calls)==1


def test_bad_master_update_preserves_current(tmp_path):
    (tmp_path/'current.js').write_text(master_text())
    def handler(request):
        if request.url.path=='/':return httpx.Response(200,text='<script src="/js_navi/route_master_v999.js"></script>')
        return httpx.Response(200,text='bad new file')
    f=Fetcher(httpx.Client(transport=httpx.MockTransport(handler)),interval=0)
    with pytest.raises(ParseError):MasterUpdater(f,tmp_path).load(force=True)
    assert (tmp_path/'current.js').read_text()==master_text()
