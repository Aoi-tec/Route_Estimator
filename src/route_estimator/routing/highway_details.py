"""経路の高速/有料道路区間を検出する。IC名は別途公式座標データで照合。"""
from decimal import Decimal
from ..models import decimal_value
from ..errors import ValidationError


def _roads(steps):
    result=[]
    for step in steps:
        value=step.get('name','')
        parts=[part.strip() for part in value.split(',') if part.strip()]
        japanese=[part for part in parts if any('\u3040'<=c<='\u9fff' for c in part)]
        for part in japanese or parts:
            if not result or result[-1]!=part: result.append(part)
    return result


class _Invalid(ValueError):
    """検証失敗。理由は利用者向け表示とデバッグ用。"""


def _check(condition, reason):
    if not condition: raise _Invalid(reason)


def extract_highway_details(properties, coordinates):
    """Geoapifyのstepsから高速/有料区間を抽出する。

    Geoapifyは `toll` を有料区間のstepにだけ付け、無料区間では省略することがある。
    省略は「有料でない」と扱う。`road_class` が全stepで欠ける場合だけ判定不能とする。
    """
    try:
        legs=properties.get('legs')
        if not isinstance(legs,list) or len(legs)!=1 or not isinstance(legs[0].get('steps'),list):
            return {'status':'missing','sections':[],'roads':[],'reason':'経路応答にlegs/stepsがありません'}
        steps=legs[0]['steps']
        _check(steps,'stepsが空です')
        elapsed=Decimal(0); previous=0; checked=[]; classified=0
        for n,step in enumerate(steps):
            _check(isinstance(step,dict),f'step{n}の形式が不正です')
            a,b=step.get('from_index'),step.get('to_index')
            _check(type(a) is int and type(b) is int,f'step{n}にfrom_index/to_indexがありません')
            _check(0<=a<=b<len(coordinates),f'step{n}の座標番号が範囲外です（{a}-{b} / 座標{len(coordinates)}点）')
            _check(a==previous,f'step{n}の開始位置が前stepの終点と一致しません（{a}≠{previous}）')
            previous=b
            toll=step.get('toll',False)
            _check(type(toll) is bool,f'step{n}のtollが真偽値ではありません')
            if 'road_class' in step: classified+=1
            duration=decimal_value(step.get('time',0),'区間所要時間')
            checked.append((step,elapsed,toll))
            elapsed+=duration
        _check(previous==len(coordinates)-1,f'最終stepが経路の終点に届いていません（{previous} / {len(coordinates)-1}）')
        _check(classified>0,'道路種別(road_class)が含まれていません（details=route_details未対応の応答）')
        total=decimal_value(properties['time'],'所要時間')
        # 区間別丸め誤差だけ許容し、累積時刻が別経路の値になるのを防ぐ。
        tolerance=max(Decimal(2),Decimal(len(steps)),total*Decimal('0.01'))
        _check(abs(elapsed-total)<=tolerance,f'step所要時間の合計({elapsed}秒)が全体({total}秒)と一致しません')
        groups=[];current=[]
        for i,(step,_,toll) in enumerate(checked):
            active=toll or step.get('road_class')=='motorway'
            if active: current.append(i)
            elif current: groups.append(current);current=[]
        if current: groups.append(current)
        sections=[]
        for indices in groups:
            first,last=indices[0],indices[-1]
            a=steps[first]['from_index'];b=steps[last]['to_index']
            if a==b: continue
            path=coordinates[a:b+1]
            sections.append({'number':len(sections)+1,'entry_point':path[0],'exit_point':path[-1],
                'path':path,'entry_elapsed_s':str(checked[first][1]),'exit_elapsed_s':str(checked[last][1]+decimal_value(steps[last].get('time',0),'時間')),
                'starts_inside':first==0,'ends_inside':last==len(steps)-1 or b==len(coordinates)-1,
                'roads':_roads(steps[first:last+1]),'entry_roads':_roads(steps[first:min(first+10,last+1)]),
                'exit_roads':_roads(steps[max(first,last-10):last+1])})
        return {'status':'available','sections':sections,'roads':_roads(steps),'toll':properties.get('toll')}
    except _Invalid as exc:
        return {'status':'invalid','sections':[],'roads':[],'reason':str(exc)}
    except (ValueError,TypeError,KeyError,IndexError,AttributeError,ValidationError) as exc:
        return {'status':'invalid','sections':[],'roads':[],'reason':f'経路応答の値が不正です（{type(exc).__name__}）'}
