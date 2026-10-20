"""Geoapifyの距離・時間と返却経路端点を検証する。"""
from decimal import Decimal
import json
import logging
import math
import re
from urllib.parse import urlencode

import httpx

from ..errors import RoutingUnavailableError, SnapDistanceError, NoRouteError, ValidationError
from ..http_transport import HttpTransport, TransportError
from ..models import Coordinates, RouteResult, decimal_value
from .highway_details import extract_highway_details

ENDPOINT = 'https://api.geoapify.com/v1/routing'
ATTRIBUTION = 'Powered by Geoapify (https://www.geoapify.com/) | © OpenStreetMap contributors (https://www.openstreetmap.org/copyright)'


def validate_key(key):
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}',key) or key in ('YOUR_API_KEY','YOUR_GEOAPIFY_API_KEY'):
        raise ValidationError('Geoapify APIキーが未設定または形式不正です。configure_api_keyを実行してください')
    return key


class SecretFilter(logging.Filter):
    def __init__(self,key):
        super().__init__();self.key=key

    def filter(self,record):
        record.msg=record.getMessage().replace(self.key,'[REDACTED]')
        record.args=()
        return True


def endpoint_distance(a,b):
    lat1,lat2=map(math.radians,(a.latitude,b.latitude))
    dlat=lat2-lat1;dlon=math.radians(b.longitude-a.longitude)
    h=math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return Decimal(str(6371008.8*2*math.asin(math.sqrt(min(1,max(0,h))))))


class GeoapifyClient:
    def __init__(self,client:httpx.Client,*,api_key:str,timeout=15,max_snap_distance_m=Decimal(200)):
        self.key=validate_key(api_key)
        self.max_snap_distance_m=decimal_value(max_snap_distance_m,'端点距離上限')
        self.http=HttpTransport(client,timeout=timeout,retries=0,max_bytes=8_000_000)
        self.secret_filter=SecretFilter(self.key)
        self.loggers=[logging.getLogger(name) for name in ('httpx','httpcore','httpcore.connection','httpcore.http11','httpcore.http2','httpcore.proxy','httpcore.socks')]
        for logger in self.loggers:logger.addFilter(self.secret_filter)

    def close(self):
        for logger in self.loggers:logger.removeFilter(self.secret_filter)

    def route(self,origin:Coordinates,destination:Coordinates,*,snap_limits_m=None)->RouteResult:
        params={'waypoints':f'{origin.latitude},{origin.longitude}|{destination.latitude},{destination.longitude}',
                'mode':'drive','type':'balanced','traffic':'free_flow','units':'metric','format':'geojson','details':'instruction_details,route_details','lang':'ja','apiKey':self.key}
        try:
            status,_,body=self.http.get(ENDPOINT+'?'+urlencode(params))
            if status in (401,403):
                raise RoutingUnavailableError('GeoapifyのAPIキーまたは利用権限を確認してください')
            if status==429:
                raise RoutingUnavailableError('Geoapifyの利用回数制限に達しました。時間を置いて確認してください')
            if status!=200:raise RoutingUnavailableError(f'GeoapifyがHTTP {status}を返しました。見積は未確定です')
            data=json.loads(body,parse_float=Decimal)
            if data['type']!='FeatureCollection' or not isinstance(data['features'],list):raise ValueError()
            if not data['features']:raise NoRouteError('Geoapifyで走行可能な経路が見つかりませんでした')
            if len(data['features'])!=1:raise ValueError()
            feature=data['features'][0];p=feature['properties'];geometry=feature['geometry']
            if feature['type']!='Feature' or str(p['distance_units']).lower()!='meters' or str(p.get('time_units','seconds')).lower()!='seconds':raise ValueError()
            if geometry['type']!='MultiLineString':raise ValueError()
            lines=geometry['coordinates']
            if not isinstance(lines,list) or len(lines)!=1 or not isinstance(lines[0],list) or len(lines[0])<2:raise ValueError()
            points=[]
            for pair in lines[0]:
                if not isinstance(pair,list) or len(pair)!=2 or any(isinstance(v,bool) or not isinstance(v,(int,float,Decimal)) for v in pair):raise ValueError()
                points.append(Coordinates(float(pair[1]),float(pair[0])))
            distances=(endpoint_distance(origin,points[0]),endpoint_distance(destination,points[-1]))
            result=RouteResult(p['distance'],p['time'],distances,{
                'provider':'geoapify','mode':'drive','route_type':'balanced','traffic':'free_flow',
                'snap_validation':'input_to_geometry_endpoint','attribution':ATTRIBUTION,
                'highway_details':extract_highway_details(p,[[v.latitude,v.longitude] for v in points])})
        except RoutingUnavailableError:
            raise
        except (TransportError,ValueError,TypeError,KeyError,AttributeError,IndexError,OverflowError,ValidationError):
            # httpxの例外causeにはAPIキー付きURLが入り得る。生の例外/応答は表示しない。
            raise RoutingUnavailableError('Geoapifyへの接続または応答が不正です。見積は未確定です') from None
        limits=snap_limits_m or (self.max_snap_distance_m,self.max_snap_distance_m)
        if any(d>m for d,m in zip(distances,limits)):
            raise SnapDistanceError('入力座標と経路端点の距離が上限を超えたため計算を保留しました')
        return result
