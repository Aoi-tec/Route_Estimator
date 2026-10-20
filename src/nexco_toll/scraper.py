"""公式ページが使用している公開JSONPを低頻度で読み取る。公開API保証はない。"""
import time
import httpx
from urllib.parse import urlsplit
from .models import FetchError

HOST='https://search.w-nexco.co.jp'


class Fetcher:
    def __init__(self, client, *, interval=1.0):
        self.client=client
        self.interval=interval
        self.last=0.0

    def get(self, url, *, params=None, max_bytes=2_000_000):
        p=urlsplit(url)
        if p.scheme!='https' or p.netloc!='search.w-nexco.co.jp':
            raise FetchError('NEXCO以外の取得先は許可していません')
        delay=self.interval-(time.monotonic()-self.last)
        if delay>0: time.sleep(delay)
        self.last=time.monotonic()
        try:
            with self.client.stream('GET',url,params=params,timeout=30,follow_redirects=False,
                                    headers={'User-Agent':'RouteEstimator/0.1 (low-volume personal route lookup)'}) as response:
                if response.status_code!=200:
                    raise FetchError(f'NEXCO HTTP {response.status_code}。自動再試行せず保留します')
                chunks=[];size=0
                for chunk in response.iter_bytes():
                    size+=len(chunk)
                    if size>max_bytes: raise FetchError('NEXCO応答が上限を超えました')
                    chunks.append(chunk)
                return b''.join(chunks)
        except httpx.HTTPError:
            raise FetchError('NEXCOへの接続に失敗しました。料金は未取得です') from None
