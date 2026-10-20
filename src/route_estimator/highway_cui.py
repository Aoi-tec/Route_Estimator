"""ICの自動候補、名称選択、NEXCO公式候補を表示する。"""
from datetime import datetime
import html
import re
from nexco_toll import TollError
from .scheduling import departure_label
from .routing.ic_catalog import ATTRIBUTION, SOURCE


def _clean(value):
    text=html.unescape(re.sub(r'<[^>]*>',' ',str(value)))
    return ''.join(char for char in text if char>=' ' and char!='\x7f')


def _label(candidate):
    return f"{_clean(candidate['name'])} [{candidate['code']}] / {_clean(candidate['road'])} / {_clean(candidate['prefecture'])}"


def _suggestion(identification):
    code=identification.get('suggested_code')
    return next((c for c in identification.get('candidates',[]) if c['code']==code),None)


def show_highways(result, write):
    write('\n'+'─'*52+'\n  利用する高速・有料道路 / IC候補\n'+'─'*52)
    info=result.highway_information
    if info.get('status')!='available':
        write('経路の道路詳細がないため、利用ICを判定できません（通行料金は未取得）。')
        if info.get('reason'):write(f"  理由：{info['reason']}")
        return
    if not info.get('sections'):
        write('取得した道路属性では、高速・有料道路の利用区間はありません。')
        return
    for section in info['sections']:
        write(f"[区間{section['number']}]")
        for role,label in (('entry','入口'),('exit','出口')):
            identification=section.get(role,{})
            candidate=_suggestion(identification)
            if candidate:
                write(f'{label}IC候補：'+_label(candidate))
                write(f"  経路からの距離：約{candidate['route_distance_m']:.0f}m / 利用者確認前")
                if candidate.get('note'): write('  出入口の注意：'+_clean(candidate['note']))
            elif identification.get('candidates'):
                write(f'{label}IC候補：複数あります。料金検索前に選択してください。')
                for c in identification['candidates']:write('  ・'+_label(c))
            else:
                write(f'{label}IC：未確定（名称検索で補えます）')
        if section.get('entry_departure_at'):
            write('入口到達予定：'+departure_label(datetime.fromisoformat(section['entry_departure_at'])))
        write('経由道路：'+' → '.join(_clean(v) for v in section['roads']))
        write('')
    if info.get('ic_error'):write(info['ic_error'])
    write('※ ICは座標と道路名からの自動候補です。出入口の方向・制限を確認してください。')
    write('IC座標出典：'+ATTRIBUTION+' / '+SOURCE)


def _pick_ic(master, identification, title, read, write):
    candidates=identification.get('candidates',[])
    while True:
        write(f'[{title}ICの選択]')
        for index,ic in enumerate(candidates,1):
            write(f'{index}. '+_label(ic))
            if ic.get('note'):write('   '+_clean(ic['note']))
        value=read('番号 / IC名・読み・7桁コードで検索 / 0: 保留 > ').strip()
        if value=='0':return None
        if value.isdecimal() and 1<=int(value)<=len(candidates):return candidates[int(value)-1]
        found=master.candidates(value)
        candidates=[{'name':ic.name,'code':ic.code,'road':ic.road,'prefecture':ic.prefecture,'note':ic.note} for ic in found]
        if not candidates:write('該当するICがありません。')
        elif len(candidates)>30:
            write(f'{len(candidates)}件あります。名称を絞ってください。');candidates=[]


def _vehicle(read,write):
    vehicles={'1':'普通車','2':'軽・自動二輪','3':'中型車','4':'大型車','5':'特大車'}
    write('[NEXCO車種区分] 1. 普通車 / 2. 軽・自動二輪 / 3. 中型車 / 4. 大型車 / 5. 特大車')
    while True:
        value=read('車種（Enterで普通車） > ').strip() or '1'
        if value in vehicles:return vehicles[value]
        write('1〜5を選択してください。')


def _pick_route(found, read, write):
    if len(found.routes)<=1:return found.routes[0] if found.routes else None
    numbers={str(route.number):route for route in found.routes}
    while True:
        value=read(f'採用するルート番号（Enterで{found.routes[0].number}） > ').strip() or str(found.routes[0].number)
        if value in numbers:return numbers[value]
        write('表示されたルート番号を入力してください。')


def toll_flow(result, service, read, write):
    """区間ごとにICを確認してETC2.0参考額を検索する。

    戻り値: 区間ごとの採用結果 [{'section','from','to','route','yen'}]。保留・失敗はyen=None。
    """
    info=result.highway_information
    selections=[]
    if info.get('status')!='available' or not info.get('sections'):return selections
    write('\n【高速料金（ETC2.0参考額）の確認】通常ETC・現金への代用はしません。')
    vehicle=None
    for section in info['sections']:
        held={'section':section['number'],'from':None,'to':None,'route':None,'yen':None}
        a=_suggestion(section.get('entry',{}));b=_suggestion(section.get('exit',{}))
        manually_selected=False
        while True:
            write(f"\n区間{section['number']}："+(_label(a) if a else '入口未確定')+' → '+(_label(b) if b else '出口未確定'))
            if manually_selected:
                write('※ 修正ICへの経路は未再計算。検索時刻は元経路の接続点到達予定を使う参考検索です。')
            write('1. このICで参考料金を検索 / 2. ICを選択・修正 / 0. この区間を保留')
            choice=read('選択 > ').strip()
            if choice=='0':selections.append(held);break
            if choice not in ('1','2'):continue
            try:
                if choice=='2' or a is None:
                    manually_selected=True
                    a=_pick_ic(service.tolls.master,section.get('entry',{}),'入口',read,write)
                    if a is None:selections.append(held);break
                if choice=='2' or b is None:
                    manually_selected=True
                    b=_pick_ic(service.tolls.master,section.get('exit',{}),'出口',read,write)
                    if b is None:selections.append(held);break
                if choice=='2':continue
                if not section.get('entry_departure_at'):
                    write('入口到達時刻が未確定のため料金検索を保留しました。');selections.append(held);break
                vehicle=vehicle or _vehicle(read,write)
                departure=datetime.fromisoformat(section['entry_departure_at'])
                write('NEXCO検索中…')
                found=service.tolls.search(a['code'],b['code'],vehicle_type=vehicle,departure_at=departure)
                write(f'\n[区間{section["number"]} / ETC2.0参考額]')
                write(f'入口：{_clean(found.from_ic_name)} → 出口：{_clean(found.to_ic_name)} / {found.vehicle_type} / 1台あたり')
                write('NEXCO検索日時：'+departure_label(datetime.fromisoformat(found.searched_departure_at)))
                for route in found.routes:
                    price='取得不可・対象外' if route.etc2_yen is None else f'{route.etc2_yen:,}円'
                    write(f'\n  ルート{route.number}：ETC2.0 {price} / {route.distance_km} km / {route.duration_minutes}分')
                    write('  通過地点：'+' → '.join(_clean(stop.name) for stop in route.stops))
                    if not route.stops_complete:write('  [要確認] 経路詳細を最後まで照合できませんでした。')
                    write('  道路：'+' → '.join(_clean(road) for road in route.roads))
                    for segment in route.segments:
                        write(f'    {_clean(route.stops[segment.from_stop_index].name)} → {_clean(route.stops[segment.to_stop_index].name)}：{_clean(segment.road_name)} / {segment.distance_km} km / {segment.duration_minutes}分')
                write('\n※ Geoapifyとの経路一致・採用は未確定。NEXCOはIC間、Geoapifyは住所間の距離・時間です。')
                write('※ 表示地点は公式詳細の掲載地点です。物理的に通過する全ICの列挙ではありません。')
                write('取得日時：'+found.retrieved_at.astimezone(departure.tzinfo).isoformat())
                write('料金出典：'+found.source_url)
                result.toll_results.append(found)
                chosen=_pick_route(found,read,write)
                selections.append({'section':section['number'],'from':_clean(found.from_ic_name),'to':_clean(found.to_ic_name),
                                   'route':chosen.number if chosen else None,'yen':chosen.etc2_yen if chosen else None,
                                   'manual_ic':manually_selected})
                break
            except (TollError,OSError,UnicodeError) as exc:
                if isinstance(exc,TollError):write(_clean(exc))
                write('ICまたはETC2.0料金を取得できませんでした。この区間の料金は保留です（0円ではありません）。')
                selections.append(held)
                break
    return selections
