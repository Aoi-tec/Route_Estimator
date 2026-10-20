import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from .master import normalize
from .models import ParseError, NoRouteError, RouteOption, RouteStop, RouteSegment


def _integer(value):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+',str(value)): raise ValueError()
    return int(value)


def _decimal(value):
    if isinstance(value,bool): raise ValueError()
    result=Decimal(str(value))
    if not result.is_finite() or result<0: raise ValueError()
    return result


def _price(total):
    # 全体のETC2.0だけ。通常ETC・現金・区間合計からの代用をしない。
    raw=total.get('TotalChargeETC2')
    if raw is None or raw in ('','-','対象外'): return None
    price=_integer(raw)
    display=total.get('DispTotalChargeETC2')
    if display is not None and str(display).replace(',','')!=str(price): raise ValueError()
    return price


def _departure(total):
    day=datetime.strptime(total['DepDate'],'%Y%m%d').replace(tzinfo=timezone(timedelta(hours=9)))
    minutes=_integer(total['DepTime'])
    if minutes>=1440: raise ValueError()
    return day+timedelta(minutes=minutes)


def parse_response(body, origin, destination, *, requested_departure=None, known_codes=()):
    try:
        text=body.decode('utf-8') if isinstance(body,bytes) else body
        match=re.fullmatch(r'\s*routecallback\(\s*(\{.*\})\s*\);?\s*',text,re.S)
        if not match: raise ValueError()
        data=json.loads(match[1],parse_float=Decimal)
        if data.get('ErrorMessage'):
            # 既知の「経路なし」だけ区別し、未知エラーは構造エラーとして停止。
            message=data['ErrorMessage']
            if isinstance(message,str) and ('経路がありません' in message or 'ルートがありません' in message):
                raise NoRouteError('NEXCOに該当経路がありません')
            raise ParseError('NEXCOが検索エラーを返しました。条件を確認してください')
        if data['fromIc']['Code']!=origin.code or data['toIc']['Code']!=destination.code: raise ValueError()
        totals,details=data['Total'],data['Detail']
        if not isinstance(totals,list) or not 1<=len(totals)<=3 or not isinstance(details,list) or len(totals)!=len(details): raise ValueError()
        routes=[];departures=[]
        for index,(total,detail) in enumerate(zip(totals,details),1):
            # 公式JSも同じ配列位置をルート番号に使う。NodePoint等を照合して混線を検出。
            dt=detail['Total']
            for key in ('NodePoint','DepDate','DepTime','TotalDist','TotalTime'):
                if total[key]!=dt[key]: raise ValueError()
            if _price(total)!=_price(dt): raise ValueError()
            departure=_departure(total);departures.append(departure)
            if requested_departure is not None and departure!=requested_departure.astimezone(departure.tzinfo).replace(second=0,microsecond=0): raise ValueError()
            roads=total['RoadList']
            if not isinstance(roads,list) or any(not isinstance(v,str) for v in roads): raise ValueError()
            blocks=detail.get('Detail')
            stops=[];segments=[];complete=isinstance(blocks,dict) and bool(blocks)
            if complete:
                blocks=list(blocks.values())
                for position,block in enumerate(blocks):
                    ic=block.get('LineIc',{})
                    name,code=ic.get('Ic'),ic.get('Code')
                    if not isinstance(name,str) or not isinstance(code,str) or not re.fullmatch(r'[0-9]{7}',code):
                        complete=False;break
                    normalized=normalize(name)
                    kind='JCT' if 'JCT' in normalized else ('IC' if code in known_codes or code in (origin.code,destination.code) else 'UNKNOWN')
                    stops.append(RouteStop(name,kind,code,ic.get('colonTime')))
                    road=block.get('LineRoad')
                    if road and position<len(blocks)-1:
                        segments.append(RouteSegment(str(road['Road']),_decimal(road['Dist']) if road.get('Dist') is not None else None,
                            _integer(road['intTime']) if road.get('intTime') is not None else None,position,position+1))
                complete=complete and len(stops)==len(blocks) and bool(stops) and stops[0].code==origin.code and stops[-1].code==destination.code
                # 途中までしか読めない場合、存在しない地点への区間リンクを残さない。
                segments=[segment for segment in segments if segment.to_stop_index<len(stops)]
            routes.append(RouteOption(index,None if origin.code in ('9125010','9125020') else _price(total),_decimal(total['DispTotalDist']),_integer(total['TotalTime']),roads,stops,segments,bool(complete)))
        if len(set(departures))!=1: raise ValueError()
        return routes,departures[0]
    except (ValueError,KeyError,TypeError,AttributeError,InvalidOperation,OverflowError,RecursionError):
        raise ParseError('NEXCO応答の経路・料金・日時の対応を確認できません。料金を保留します') from None
