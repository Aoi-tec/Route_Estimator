"""国土数値情報の2025年現況だけを加工し、IC座標の候補を照合する。"""
from datetime import datetime, timezone
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile, BadZipFile
import hashlib
import json
import math
import os
import re
import httpx
from nexco_toll.master import normalize
from nexco_toll.models import TollError

SOURCE='https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N06-2025.html'
ARCHIVE='https://nlftp.mlit.go.jp/ksj/gml/data/N06/N06-25/N06-25_GML.zip'
ATTRIBUTION='国土数値情報「高速道路時系列・2025年現況」（国土交通省）を加工して作成（CC BY 4.0）'


def _name(value):
    value=normalize(value)
    return re.sub(r'(?:SIC|スマート)$','スマート',value,flags=re.I)


def _road(value):
    value=normalize(value)
    value=re.sub(r'E[0-9]+[A-Z]?|[\s　]','',value)
    return value.replace('自動車道','道').replace('高速道路','高速')


def process_catalog(body):
    try:
        data=json.loads(body)
        if data['type']!='FeatureCollection' or not 100<len(data['features'])<20000: raise ValueError()
        points=[]
        for feature in data['features']:
            p=feature['properties'];g=feature['geometry']
            # 最新基準年の現況としてのみ使用。過年度の変遷を混ぜない。
            if not int(p['N06_013'])<=2025<=int(p['N06_014']): continue
            if str(p['N06_019']) not in ('1','2'): continue
            if g['type']!='Point': raise ValueError()
            lon,lat=g['coordinates']
            if not all(type(v) in (int,float) and math.isfinite(v) for v in (lat,lon)) or not 20<=lat<=46 or not 122<=lon<=154: raise ValueError()
            if not isinstance(p['N06_018'],str) or not p['N06_018']: raise ValueError()
            name=p['N06_018']
            if name.endswith('SIC'):name=name[:-3]+'スマート'
            points.append({'name':name,'latitude':lat,'longitude':lon,'id':p['N06_015']})
        if len(points)<100: raise ValueError()
        return points
    except (ValueError,KeyError,TypeError,IndexError): raise TollError('国土数値情報のICデータ形式を確認できません') from None


class ICCatalog:
    def __init__(self, client, directory):
        self.client=client;self.directory=Path(directory);self._points=None

    def load(self, *, force=False):
        if self._points is not None and not force:return self._points
        current=self.directory/'ic-2025.json'
        if current.is_file() and not force:
            try:
                data=json.loads(current.read_text())
                raw=self.directory/'N06-25_Joint.geojson'
                if data['source_url']!=ARCHIVE or data['sha256']!=hashlib.sha256(raw.read_bytes()).hexdigest():raise ValueError()
                self._points=process_catalog(raw.read_bytes())
                return self._points
            except (OSError,ValueError,KeyError,TollError):pass
        try:
            with self.client.stream('GET',ARCHIVE,timeout=40,follow_redirects=False) as response:
                if response.status_code!=200:raise TollError(f'IC座標データ HTTP {response.status_code}')
                chunks=[];size=0
                for chunk in response.iter_bytes():
                    size+=len(chunk)
                    if size>20_000_000:raise TollError('IC座標原本が容量上限を超えました')
                    chunks.append(chunk)
            archive=b''.join(chunks)
            with ZipFile(BytesIO(archive)) as z:
                names=[n for n in z.namelist() if n.endswith('/UTF-8/N06-25_Joint.geojson')]
                if len(names)!=1 or z.getinfo(names[0]).file_size>15_000_000:raise ValueError()
                raw=z.read(names[0])
            points=process_catalog(raw)
            self.directory.mkdir(parents=True,exist_ok=True)
            def save(name,body):
                tmp=self.directory/(name+'.tmp');tmp.write_bytes(body);os.replace(tmp,self.directory/name)
            save('N06-25_GML.zip',archive)
            save('N06-25_Joint.geojson',raw)
            info={'source_url':ARCHIVE,'catalog_url':SOURCE,'dataset_date':'2025-12-31','sha256':hashlib.sha256(raw).hexdigest(),
                  'archive_sha256':hashlib.sha256(archive).hexdigest(),'retrieved_at':datetime.now(timezone.utc).isoformat(),
                  'attribution':ATTRIBUTION,'records':len(points)}
            save('ic-2025.json',json.dumps(info,ensure_ascii=False,indent=2).encode())
            self._points=points
            return points
        except (httpx.HTTPError,ValueError,BadZipFile,KeyError,OSError):
            raise TollError('IC座標データの取得・保存に失敗しました') from None

    def identify(self, section, role, master):
        if section.get('starts_inside' if role=='entry' else 'ends_inside'):
            return {'status':'unresolved','candidates':[],'reason':'経路の始終点が高速・有料道路内のため、区間外の乗降ICは推測しません'}
        points=self.load()
        anchor=section['entry_point' if role=='entry' else 'exit_point']
        path=section['path'] if role=='entry' else list(reversed(section['path']))
        # 接続点から経路に沿って最大1.5km。途中の別ICまで探索しない。
        window=[path[0]];length=0
        for a,b in zip(path,path[1:]):
            length+=_distance(a,b)
            if length>1500:break
            window.append(b)
        roads=section['entry_roads' if role=='entry' else 'exit_roads']
        candidates={}
        for point in points:
            pos=[point['latitude'],point['longitude']]
            boundary_distance=_distance(pos,anchor)
            if boundary_distance>1600:continue
            route_distance=min(_segment_distance(pos,a,b) for a,b in zip(window,window[1:])) if len(window)>1 else _distance(pos,window[0])
            if route_distance>180:continue
            matches=[ic for ic in master.ics if _name(ic.name)==_name(point['name'])]
            for ic in matches:
                # 出入口の制限が明記されている場合は逆向きの候補を除く。
                if role=='entry' and '出口のみ' in ic.note and '入口' not in ic.note:continue
                if role=='exit' and '入口のみ' in ic.note and '出口' not in ic.note:continue
                road_match=any(_road(ic.road) in _road(road) or _road(road) in _road(ic.road) for road in roads if _road(road))
                score=route_distance*3+boundary_distance+(0 if road_match else 1500)
                item={'name':ic.name,'code':ic.code,'road':ic.road,'prefecture':ic.prefecture,'note':ic.note,
                      'route_distance_m':round(route_distance,1),'boundary_distance_m':round(boundary_distance,1),
                      'road_match':road_match,'coordinate_source':SOURCE,'coordinate_id':point['id'],'score':round(score,1)}
                if ic.code not in candidates or score<candidates[ic.code]['score']:candidates[ic.code]=item
        ordered=sorted(candidates.values(),key=lambda c:c['score'])[:5]
        # 自動表示は候補。料金取得前にはCUIで明示確認する。
        unique=bool(ordered) and ordered[0]['road_match'] and (len(ordered)==1 or ordered[1]['score']-ordered[0]['score']>=250)
        return {'status':'candidate' if unique else ('ambiguous' if ordered else 'unresolved'),'candidates':ordered,
                'suggested_code':ordered[0]['code'] if unique else None,'reason':'国土数値情報の座標と経路・道路名を照合（利用者確認前）'}


def _xy(point, origin):
    return ((point[1]-origin[1])*111195*math.cos(math.radians(origin[0])),(point[0]-origin[0])*111195)


def _distance(a,b):
    x,y=_xy(a,b);return math.hypot(x,y)


def _segment_distance(point,a,b):
    px,py=_xy(point,a);bx,by=_xy(b,a)
    if bx*bx+by*by==0:return math.hypot(px,py)
    t=max(0,min(1,(px*bx+py*by)/(bx*bx+by*by)))
    return math.hypot(px-t*bx,py-t*by)
