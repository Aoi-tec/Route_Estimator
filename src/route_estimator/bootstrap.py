"""外部依存の生成。対話処理や見積計算を置かない。"""
from contextlib import contextmanager
import httpx
from .credentials import resolve_api_key
from .errors import ConfigError
from .routing.geoapify_client import GeoapifyClient
from .geocoding.cache import GeocodeCache
from .geocoding.geolonia_v2 import GeoloniaV2
from .routing.osrm_client import OsrmClient


@contextmanager
def open_adapters(settings):
    # trust_env=False: ローカルOSRMや住所が環境変数のプロキシへ流れないようにする。
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        cache = GeocodeCache(settings.geocoding.cache_database, ttl_days=settings.geocoding.cache_ttl_days)
        geocoder = GeoloniaV2(client, settings.geocoding, cache)
        routing=settings.routing
        if routing.provider=='geoapify':
            key=resolve_api_key(routing.api_key_env,routing.api_key_file,getattr(settings,'root',None))
            if key is None:raise ConfigError('Geoapify APIキーをconfigure_api_keyで登録してください（保存先: .env）')
            router=GeoapifyClient(client,api_key=key,timeout=routing.timeout_seconds,max_snap_distance_m=routing.max_snap_distance_m)
        else:
            osrm = settings.osrm
            router = OsrmClient(client, base_url=osrm.base_url, profile=osrm.profile,
                                timeout=osrm.timeout_seconds, max_snap_distance_m=osrm.max_snap_distance_m)
        try:yield geocoder, router
        finally:
            if isinstance(router,GeoapifyClient):router.close()
