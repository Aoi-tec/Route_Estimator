"""IC自動候補と料金検索を接続。Geoapifyの全行程へ参考料金を勝手に加算しない。"""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import httpx
from nexco_toll import TollService, TollError
from .routing.ic_catalog import ICCatalog


class HighwayService:
    def __init__(self, directory, *, client=None):
        self.client=client or httpx.Client(trust_env=False,follow_redirects=False)
        self.owned=client is None
        self.tolls=TollService(self.client,data_directory=directory/'nexco')
        self.catalog=ICCatalog(self.client,directory/'ic-catalog')

    def close(self):
        if self.owned:self.client.close()

    def enrich(self, result):
        info=deepcopy(result.highway_information)
        if info.get('status')!='available' or not info.get('sections'):return result
        try:
            master=self.tolls.master
            for section in info['sections']:
                for role in ('entry','exit'):
                    section[role]=self.catalog.identify(section,role,master)
                if result.departure_at is not None:
                    value=result.departure_at+timedelta(seconds=float(Decimal(section['entry_elapsed_s'])))
                    # 公式検索が分単位のため、最も近い分へ丸める。
                    value=(value+timedelta(seconds=30)).replace(second=0,microsecond=0)
                    section['entry_departure_at']=value.isoformat()
            info['ic_source']='MLIT N06-25 + NEXCO IC master '+master.version
        except (TollError,OSError,UnicodeError):
            info['ic_error']='IC原本または座標データを取得できません。IC・通行料金は未確定です'
        return replace(result,highway_information=info)
