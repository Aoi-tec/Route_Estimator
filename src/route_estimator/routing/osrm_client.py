import json
from decimal import Decimal
import re
from urllib.parse import urlsplit

import httpx

from ..errors import NoRouteError, RoutingUnavailableError, SnapDistanceError, ValidationError
from ..http_transport import HttpTransport, TransportError
from ..models import Coordinates, RouteResult, decimal_value


class OsrmClient:
    def __init__(self, client: httpx.Client, *, base_url: str = "http://127.0.0.1:5000", profile: str = "driving", timeout: float = 15, max_snap_distance_m: Decimal = Decimal(200)):
        url = urlsplit(base_url)
        if url.scheme != "http" or url.hostname not in ("127.0.0.1", "::1") or url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
            raise ValidationError("MVPのOSRM接続先はループバックHTTPのみに制限されています")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", profile):
            raise ValidationError("OSRM profileが不正です")
        self.base_url, self.profile = base_url.rstrip("/"), profile
        self.max_snap_distance_m = decimal_value(max_snap_distance_m, "スナップ距離上限")
        self.http = HttpTransport(client, timeout=timeout, max_bytes=1_000_000)

    def route(self, origin: Coordinates, destination: Coordinates) -> RouteResult:
        coords = f"{origin.longitude},{origin.latitude};{destination.longitude},{destination.latitude}"
        url = f"{self.base_url}/route/v1/{self.profile}/{coords}?overview=false&steps=false"
        try:
            status, _, body = self.http.get(url)
            if status not in (200, 400):
                raise TransportError(f"OSRMがHTTP {status}を返しました")
            payload = json.loads(body, parse_float=Decimal)
            if payload.get("code") in ("NoRoute", "NoSegment"):
                raise NoRouteError("走行可能な経路がありません。住所と地図の収録範囲を確認してください")
            if status != 200 or payload.get("code") != "Ok":
                raise TransportError("OSRMが経路取得エラーを返しました")
            routes, points = payload["routes"], payload["waypoints"]
            if not isinstance(routes, list) or not routes or not isinstance(points, list) or len(points) != 2:
                raise ValueError("routes or waypoints missing")
            for point in points:
                location = point["location"]
                if not isinstance(location, list) or len(location) != 2:
                    raise ValueError("invalid snapped coordinates")
                if any(isinstance(v, bool) or not isinstance(v, (int, float, Decimal)) for v in location):
                    raise ValueError("invalid snapped coordinates")
                Coordinates(latitude=float(location[1]), longitude=float(location[0]))
            route = RouteResult(routes[0]["distance"], routes[0]["duration"], tuple(p["distance"] for p in points))
        except (TransportError, ValueError, TypeError, KeyError, AttributeError, ValidationError) as exc:
            raise RoutingUnavailableError("OSRMの接続または応答が不正です。サーバーと地図を確認してください") from exc
        if any(d > self.max_snap_distance_m for d in route.snap_distances_m):
            raise SnapDistanceError("道路へのスナップ距離が上限を超えたため計算を保留しました")
        return route
