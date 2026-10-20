from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
from urllib.parse import urlsplit
import re
import math

from .errors import ConfigError, ValidationError
from .models import Location, decimal_value, positive_integer

GEOLONIA_BASE = "https://japanese-addresses-v2.geoloniamaps.com/api/ja"


@dataclass(frozen=True)
class LaborConfig:
    hourly_rate_yen_per_person: Decimal
    default_people: int


@dataclass(frozen=True)
class VehicleConfig:
    count: int
    fuel_efficiency_km_per_liter: Decimal
    fuel_price_yen_per_liter: Decimal | None


@dataclass(frozen=True)
class OsrmConfig:
    base_url: str
    profile: str
    timeout_seconds: float
    max_snap_distance_m: Decimal


@dataclass(frozen=True)
class GeocodingConfig:
    provider: str
    base_url: str
    timeout_seconds: float
    allow_approximate: bool
    cache_database: Path
    cache_ttl_days: int = 30
    max_download_bytes: int = 8_000_000
    retries: int = 2


@dataclass(frozen=True)
class AppConfig:
    company: Location
    labor: LaborConfig
    vehicle: VehicleConfig
    osrm: OsrmConfig
    geocoding: GeocodingConfig
    routing: 'RoutingConfig'
    root: Path | None = None


@dataclass(frozen=True)
class RoutingConfig:
    provider: str
    timeout_seconds: float
    max_snap_distance_m: Decimal
    api_key_env: str
    api_key_file: Path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"設定キー {key} が重複しています")
        result[key] = value
    return result


def _reject_constant(value):
    raise ConfigError("NaN/Infinityは設定に使用できません")


def _timeout(value, name):
    result = float(decimal_value(value, name, positive=True))
    if not math.isfinite(result):
        raise ConfigError(f"{name}が大きすぎます")
    return result


def load_config(path: str | Path) -> AppConfig:
    path = Path(path).resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"), parse_float=Decimal,
                          object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        company, labor, vehicle, geo = (data[k] for k in ("company", "labor", "vehicle", "geocoding"))
        osrm=data.get('osrm',{'base_url':'http://127.0.0.1:5000','profile':'driving','timeout_seconds':15,'max_snap_distance_m':200})
        if geo["provider"] != "geolonia_v2" or geo["base_url"] != GEOLONIA_BASE:
            raise ConfigError("Geoloniaの接続先は許可済み公式HTTPS URLに限ります")
        if geo["allow_approximate"] is not False:
            raise ConfigError("近似住所の承認フローがないためallow_approximateはfalseにしてください")
        parsed = urlsplit(osrm["base_url"])
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "::1") or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ConfigError("OSRM接続先はループバックのHTTP URLが必要です")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ConfigError("OSRMポートが不正です")
        if not isinstance(osrm["profile"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", osrm["profile"]):
            raise ConfigError("OSRM profileが不正です")
        db = geo["cache_database"]
        if not isinstance(db, str) or not db.strip():
            raise ConfigError("キャッシュDBのパスが必要です")
        cache_path = Path(db)
        if not cache_path.is_absolute():
            cache_path = path.parent / cache_path
        retries = geo.get("retries", 2)
        if type(retries) is not int or not 0 <= retries <= 3:
            raise ConfigError("retriesは0〜3の整数が必要です")
        max_bytes = positive_integer(geo.get("max_download_bytes", 8_000_000), "最大取得バイト数")
        if not 1024 <= max_bytes <= 50_000_000:
            raise ConfigError("最大取得サイズは1024〜50000000バイトにしてください")
        price = vehicle.get("fuel_price_yen_per_liter")
        routing = data.get('routing', {'provider':'osrm'})
        if routing['provider'] not in ('osrm','geoapify'):
            raise ConfigError('routing.providerはosrmまたはgeoapifyが必要です')
        if set(routing)-{'provider','timeout_seconds','max_snap_distance_m','api_key_env','api_key_file'}:
            raise ConfigError('routingに未知の設定があります。APIキー自体はJSONへ記載しないでください')
        env_name=routing.get('api_key_env','GEOAPIFY_API_KEY')
        if not isinstance(env_name,str) or not re.fullmatch('[A-Z_][A-Z0-9_]*',env_name):raise ConfigError('APIキー環境変数名が不正です')
        key_file=routing.get('api_key_file','.env')
        if not isinstance(key_file,str) or not key_file.strip():raise ConfigError('APIキーファイル名が不正です')
        key_path=Path(key_file)
        if not key_path.is_absolute():key_path=path.parent/key_path
        return AppConfig(
            Location(company["name"], company["address"]),
            LaborConfig(decimal_value(labor["hourly_rate_yen_per_person"], "人件費単価"), positive_integer(labor["default_people"], "標準人数")),
            VehicleConfig(positive_integer(vehicle["count"], "車両台数"), decimal_value(vehicle["fuel_efficiency_km_per_liter"], "燃費", positive=True), None if price is None else decimal_value(price, "燃料単価")),
            OsrmConfig(osrm["base_url"], osrm["profile"], _timeout(osrm["timeout_seconds"], "OSRM timeout"), decimal_value(osrm["max_snap_distance_m"], "スナップ距離上限")),
            GeocodingConfig(geo["provider"], geo["base_url"], _timeout(geo["timeout_seconds"], "Geolonia timeout"), False, cache_path,
                positive_integer(geo.get("cache_ttl_days", 30), "キャッシュ有効日数"), max_bytes, retries),
            RoutingConfig(routing['provider'],_timeout(routing.get('timeout_seconds',15),'経路 timeout'),
                decimal_value(routing.get('max_snap_distance_m',200),'端点距離上限'),env_name,key_path),
            path.parent,
        )
    except ConfigError:
        raise
    except (OSError, ValueError, UnicodeError, KeyError, TypeError, AttributeError, ValidationError) as exc:
        raise ConfigError("設定ファイルが不正です。必須キー・数値範囲・UTF-8 JSONを確認してください") from exc
