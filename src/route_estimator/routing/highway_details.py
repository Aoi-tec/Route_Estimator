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


def extract_highway_details(properties, coordinates):
    try:
        legs=properties.get('legs')
        if not isinstance(legs,list) or len(legs)!=1 or not isinstance(legs[0].get('steps'),list):
            return {'status':'missing','sections':[],'roads':[]}
        steps=legs[0]['steps']
        if not steps: raise ValueError()
        elapsed=Decimal(0); previous=0; checked=[]
        for step in steps:
            if not isinstance(step,dict): raise ValueError()
            a,b=step['from_index'],step['to_index']
            if type(a) is not int or type(b) is not int or not 0<=a<=b<len(coordinates) or a!=previous: raise ValueError()
            previous=b
            if a<b and ('road_class' not in step or type(step.get('toll')) is not bool): raise ValueError()
            duration=decimal_value(step['time'],'区間所要時間')
            if 'toll' in step and type(step['toll']) is not bool: raise ValueError()
            checked.append((step,elapsed))
            elapsed+=duration
        if previous!=len(coordinates)-1: raise ValueError()
        total=decimal_value(properties['time'],'所要時間')
        # 区間別丸め誤差だけ許容し、累積時刻が別経路の値になるのを防ぐ。
        if abs(elapsed-total)>max(Decimal(2),Decimal(len(steps))): raise ValueError()
        if properties.get('toll') is True and not any(step.get('toll') is True for step in steps): raise ValueError()
        groups=[];current=[]
        for i,(step,elapsed) in enumerate(checked):
            active=step.get('toll') is True or step.get('road_class')=='motorway'
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
                'path':path,'entry_elapsed_s':str(checked[first][1]),'exit_elapsed_s':str(checked[last][1]+decimal_value(steps[last]['time'],'時間')),
                'starts_inside':first==0,'ends_inside':last==len(steps)-1 or b==len(coordinates)-1,
                'roads':_roads(steps[first:last+1]),'entry_roads':_roads(steps[first:min(first+10,last+1)]),
                'exit_roads':_roads(steps[max(first,last-10):last+1])})
        return {'status':'available','sections':sections,'roads':_roads(steps),'toll':properties.get('toll')}
    except (ValueError,TypeError,KeyError,IndexError,AttributeError,ValidationError):
        return {'status':'invalid','sections':[],'roads':[]}
