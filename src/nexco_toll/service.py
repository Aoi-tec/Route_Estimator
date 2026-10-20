from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import httpx
from .models import TollSearchResult, TollError
from .master import ICMaster
from .updater import MasterUpdater
from .scraper import Fetcher, HOST
from .parser import parse_response

VEHICLES={'軽・自動二輪':'1','普通車':'2','中型車':'3','大型車':'4','特大車':'5'}


class TollService:
    def __init__(self, client=None, *, data_directory=None, master=None):
        self.client=client or httpx.Client(trust_env=False,follow_redirects=False)
        self.owned=client is None
        self.fetcher=Fetcher(self.client)
        self.updater=MasterUpdater(self.fetcher,data_directory or Path.cwd()/'state/nexco')
        self._master=master

    @property
    def master(self):
        if self._master is None: self._master=self.updater.load()
        return self._master

    def close(self):
        if self.owned: self.client.close()

    def search(self, from_ic, to_ic, *, vehicle_type='普通車', departure_at=None):
        if vehicle_type not in VEHICLES: raise TollError('車種区分が不正です')
        if departure_at is not None and (not isinstance(departure_at,datetime) or departure_at.tzinfo is None or departure_at.utcoffset() is None):
            raise TollError('出発日時はタイムゾーン付きで指定してください')
        master=self.master
        a,b=master.resolve(from_ic),master.resolve(to_ic)
        if a.code==b.code: raise TollError('入口と出口が同じICです')
        params={'val_from':a.code,'val_to':b.code,'val_cartype':VEHICLES[vehicle_type],
                'val_pass1':'','val_pass2':'','val_pass3':'','val_detour1':'','val_detour2':'','val_roadtype':'',
                'val_maxresult':'3','val_priority':'2','val_latilongi':'2','val_roaddisp':'2','callback':'routecallback'}
        if departure_at is not None:
            from datetime import timedelta
            local=departure_at.astimezone(timezone(timedelta(hours=9)))
            params.update(val_date=local.strftime('%Y%m%d'),val_time_hour=str(local.hour),val_time_min=str(local.minute),val_timekind='1')
        else:
            # サイト既定日時は検索ページが返す実際の値を使用する。
            import re
            html=self.fetcher.get(HOST+'/route.php',params={'fcd':a.code,'tcd':b.code,'cartyp':VEHICLES[vehicle_type]}).decode('utf-8')
            for target,name in [('val_date','date'),('val_time_hour','time_hour'),('val_time_min','time_min'),('val_timekind','timekind')]:
                match=re.search(r'var\s+prm_'+name+r'\s*=\s*"([0-9]+)"',html)
                if not match: raise TollError('サイト既定日時が取得できません')
                params[target]=match[1]
        url=HOST+'/route_ajax/mk_route.php'
        body=self.fetcher.get(url,params=params)
        routes,searched=parse_response(body,a,b,requested_departure=departure_at,known_codes={ic.code for ic in master.ics})
        return TollSearchResult(a.name,a.code,b.name,b.code,vehicle_type,routes,datetime.now(timezone.utc),searched.isoformat(),
            url+'?'+urlencode(params),master.version,departure_at.isoformat() if departure_at else None)
