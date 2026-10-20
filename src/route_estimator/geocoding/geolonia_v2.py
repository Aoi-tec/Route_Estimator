"""Geolonia日本住所データv2の静的HTTPデータを照合する。"""
import csv
from datetime import datetime, timezone
import io
import re
import unicodedata
from urllib.parse import quote

import httpx

from ..config import GEOLONIA_BASE, GeocodingConfig
from ..errors import GeocodingNotPreciseError, GeocodingUnavailableError, ValidationError
from ..http_transport import HttpTransport, TransportError
from ..models import Coordinates, GeoResult, Precision
from .cache import GeocodeCache


KANJI = dict(zip("〇零一二三四五六七八九", [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]))


def _number(text: str) -> str:
    if text.isascii() and text.isdigit(): return str(int(text))
    if "十" in text:
        left, right = text.split("十", 1)
        return str((KANJI[left] if left else 1) * 10 + (KANJI[right] if right else 0))
    return str(int("".join(str(KANJI[c]) for c in text)))


def normalize_address(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    value = value.translate(str.maketrans({c: "-" for c in "‐‑‒–—−－"}))
    def chome(match):
        try: return _number(match[1]) + "丁目"
        except (ValueError, KeyError): return match[0]
    value = re.sub(r"([0-9〇零一二三四五六七八九十]+)丁目", chome, value)
    return " ".join(value.split())


def _point(value) -> Coordinates | None:
    if not isinstance(value, list) or len(value) != 2: return None
    try: return Coordinates(latitude=value[1], longitude=value[0])
    except ValidationError: return None


def _number_tail(value: str) -> str:
    value = value.strip()
    match = re.fullmatch(r"(\d+(?:(?:-|番地の|番地|番)\d+){0,2})(?:号|番地|番)?(.*)", value)
    if not match: return value
    number = re.sub(r"番地の|番地|番", "-", match[1])
    return number + match[2]


def _suffix(tail: str, expected: str) -> str | None:
    if not tail.startswith(expected): return None
    suffix = tail[len(expected):]
    if not suffix: return ""
    # 数字の継続、枝番、住所構成文字は建物名として読み飛ばさない。
    if suffix[0].isspace():
        stripped = suffix.strip()
        if stripped.startswith(("-", "番", "丁目", "字", "大字", "の")): return None
        if stripped and stripped[0].isdigit() and not re.match(r"\d+(?:号室|階|[Ff](?:\b|$))", stripped): return None
        return stripped
    if suffix[0].isdigit() or suffix[0] in "-番地号丁目字の": return None
    return suffix


class GeoloniaV2:
    def __init__(self, client: httpx.Client, settings: GeocodingConfig, cache: GeocodeCache):
        if settings.base_url != GEOLONIA_BASE:
            raise ValidationError("許可済みGeolonia接続先が必要です")
        self.settings, self.cache = settings, cache
        self.http = HttpTransport(client, timeout=settings.timeout_seconds, retries=settings.retries, max_bytes=settings.max_download_bytes)

    def _api(self, url: str) -> dict:
        cached = self.cache.get_resource(url)
        if cached is not None: return cached
        data = self.http.json(url)
        if not isinstance(data.get("data"), list) or not isinstance(data.get("meta"), dict) or type(data["meta"].get("updated")) is not int:
            raise TransportError("Geoloniaのマスター形式が変更されています")
        if not all(isinstance(item, dict) for item in data["data"]):
            raise TransportError("Geoloniaのマスター行が不正です")
        self.cache.put_resource(url, data)
        return data

    def _range(self, url: str, start: int, length: int) -> bytes | None:
        if type(start) is not int or type(length) is not int or start < 0 or not 0 < length <= self.settings.max_download_bytes:
            raise TransportError("住所データのRangeが不正または大きすぎます")
        end = start + length - 1
        status, headers, body = self.http.get(url, headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"})
        if status == 404: return None
        if headers.get("content-encoding", "identity") not in ("", "identity"):
            raise TransportError("Range応答が圧縮されているためバイト位置を検証できません")
        if status == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", headers.get("content-range", ""))
            if not match or (int(match[1]), int(match[2])) != (start, end) or int(match[3]) <= end or len(body) != length:
                raise TransportError("Content-Rangeまたは応答バイト数が不正です")
            return body
        if status == 200:
            if start + length > len(body):
                raise TransportError("Range非対応サーバーの全体応答が不完全です")
            return body[start:start + length]
        raise TransportError(f"住所データがHTTP {status}を返しました")

    def geocode(self, address: str) -> GeoResult:
        key = normalize_address(address)
        cached = self.cache.get(key)
        if cached is not None:
            if cached.precision == Precision.EXACT_ADDRESS and cached.coordinates and cached.provider == "geolonia_v2":
                return cached
        try:
            result = self._resolve(key)
        except (TransportError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, csv.Error, StopIteration) as exc:
            raise GeocodingUnavailableError("Geoloniaの接続またはデータ形式を確認してください") from exc
        if result.coordinates is not None and result.precision == Precision.EXACT_ADDRESS:
            self.cache.put(key, address, result)
            return result
        if result.coordinates is not None and result.precision == Precision.BLOCK_OR_TOWN and self.settings.allow_approximate:
            # 番地・号が見つからない場合は、見つかった丁目・町字の代表点へフォールバック（キャッシュしない）。
            return result
        if result.precision == Precision.BLOCK_OR_TOWN:
            raise GeocodingNotPreciseError(f"住所精度 {result.precision}：番地・号に一致する座標が取得できません。字・大字等の表記を確認し、住所を修正して再試行できます")
        raise GeocodingNotPreciseError(f"住所精度 {result.precision}：丁目・町字まで一致しません。市区町村以降の表記を確認し、住所を修正して再試行できます")

    def _resolve(self, address: str) -> GeoResult:
        base = self.settings.base_url
        master_url = base + ".json"
        master = self._api(master_url)
        stamp = datetime.now(timezone.utc).isoformat()
        version = str(master["meta"]["updated"])

        def result(precision, matched="", coordinates=None, source=master_url, suffix=""):
            return GeoResult(coordinates, precision, matched, "geolonia_v2", version, stamp, source, unmatched_suffix=suffix)

        for pref in master["data"]:
            if address == pref["pref"]:
                return result(Precision.CITY_OR_PREF, address, _point(pref.get("point")))
        matches = []
        for pref in master["data"]:
            pref_name = pref["pref"]
            explicit_pref = address.startswith(pref_name)
            remaining = address[len(pref_name):].lstrip() if explicit_pref else address
            if not explicit_pref and any(address.startswith(p["pref"]) for p in master["data"]): continue
            for city in pref["cities"]:
                canonical = city.get("county", "") + city["city"] + city.get("ward", "")
                aliases = {canonical, city["city"] + city.get("ward", "")}
                if city.get("ward"): aliases.add(city["ward"])
                for alias in aliases:
                    if remaining.startswith(alias):
                        matches.append((len(alias), pref_name, canonical, city, remaining[len(alias):].lstrip()))
        if not matches: return result(Precision.UNRESOLVED)
        longest = max(m[0] for m in matches)
        matches = [m for m in matches if m[0] == longest]
        unique = {(m[1], m[2]): m for m in matches}
        if len(unique) != 1: return result(Precision.UNRESOLVED)
        _, pref, canonical_city, city, remaining = next(iter(unique.values()))
        city_url = base + "/" + quote(pref, safe="") + "/" + quote(canonical_city, safe="")
        towns = self._api(city_url + ".json")
        version += "/" + str(towns["meta"]["updated"])
        town_matches = []
        for town in towns["data"]:
            name = "".join(town.get(k, "") for k in ("oaza_cho", "chome", "koaza"))
            normalized = normalize_address(name)
            aliases = {normalized}
            # 「西新宿二丁目1-2」および「西新宿2-1-2」の表記を照合。
            if "丁目" in normalized: aliases.add(normalized.replace("丁目", "-"))
            for alias in aliases:
                if alias and remaining.startswith(alias):
                    town_matches.append((len(alias), name, town, remaining[len(alias):].lstrip()))
        if not town_matches: return result(Precision.CITY_OR_PREF, pref + canonical_city, _point(city.get("point")), city_url + ".json")
        longest = max(m[0] for m in town_matches)
        unique = {m[2]["machiaza_id"]: m for m in town_matches if m[0] == longest}
        if len(unique) != 1: return result(Precision.UNRESOLVED)
        _, town_name, town, tail = next(iter(unique.values()))
        matched = pref + canonical_city + town_name
        low = result(Precision.BLOCK_OR_TOWN, matched, _point(town.get("point")), city_url + ".json")
        if not tail: return low
        # 住居表示実施町字では地番を代わりに採用しない。
        kind = "住居表示" if town.get("rsdt") is True else "地番"
        ranges = town.get("csv_ranges", {})
        if kind not in ranges: return low
        span = ranges[kind]
        source = city_url + "-" + quote(kind, safe="") + ".txt"
        body = self._range(source, span["start"], span["length"])
        if body is None: return low
        reader = csv.reader(io.StringIO(body.decode("utf-8")), strict=True)
        heading = next(reader)
        fields = next(reader)
        expected_fields = ["blk_num", "rsdt_num", "rsdt_num2", "lng", "lat"] if kind == "住居表示" else ["prc_num1", "prc_num2", "prc_num3", "lng", "lat"]
        if heading != [kind, town_name] or fields != expected_fields:
            raise TransportError("Rangeの住所名またはCSV列が一致しません")
        hits = []
        normalized_tail = _number_tail(tail)
        for row in reader:
            if len(row) != 5: raise TransportError("住所CSVの列数が不正です")
            number = "-".join(part for part in row[:3] if part)
            if not number: continue
            suffix = _suffix(normalized_tail, number)
            if suffix is not None: hits.append((number, row[3], row[4], suffix))
        if len(hits) != 1: return low  # 番号が見つからない・複数一致は丁目・町字の代表点まで
        number, lng, lat, suffix = hits[0]
        if not lng or not lat: return low
        coordinates = _point([float(lng), float(lat)])
        if coordinates is None: return result(Precision.UNRESOLVED)
        return result(Precision.EXACT_ADDRESS, matched + number, coordinates, source, suffix)
