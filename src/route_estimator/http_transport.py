"""同期HTTP。応答を上限付きで読み、リダイレクト先へ追従しない。"""
import json
import time

import httpx


class TransportError(Exception):
    pass


class HttpTransport:
    def __init__(self, client: httpx.Client, *, timeout: float, retries: int = 2, max_bytes: int = 8_000_000):
        self.client = client
        self.timeout = timeout
        self.retries = retries
        self.max_bytes = max_bytes

    def get(self, url: str, *, headers: dict | None = None) -> tuple[int, dict, bytes]:
        for attempt in range(self.retries + 1):
            try:
                with self.client.stream("GET", url, headers=headers, timeout=self.timeout, follow_redirects=False) as response:
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                        time.sleep(0.1 * (2 ** attempt))
                        continue
                    data = bytearray()
                    # iter_bytesの大きさを制限し、巨大な応答を一括メモリ読込しない。
                    for chunk in response.iter_bytes(chunk_size=65536):
                        data.extend(chunk)
                        if len(data) > self.max_bytes:
                            raise TransportError("API応答が最大ダウンロードサイズを超えました")
                    return response.status_code, dict(response.headers), bytes(data)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == self.retries:
                    raise TransportError("APIへの接続または応答がタイムアウトしました") from exc
                time.sleep(0.1 * (2 ** attempt))
            except httpx.HTTPError as exc:
                raise TransportError("APIのHTTP通信に失敗しました") from exc
        raise TransportError("API通信に失敗しました")

    def json(self, url: str) -> dict:
        status, _, body = self.get(url)
        if status != 200:
            raise TransportError(f"APIがHTTP {status}を返しました")
        try:
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError("object required")
            return result
        except (ValueError, UnicodeError) as exc:
            raise TransportError("APIのJSON形式が不正です") from exc
