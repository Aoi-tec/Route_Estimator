from dataclasses import replace
from decimal import Decimal
import json
import sqlite3
import httpx
import pytest

from route_estimator.errors import GeocodingNotPreciseError, GeocodingUnavailableError
from route_estimator.geocoding.cache import GeocodeCache
from route_estimator.geocoding.geolonia_v2 import GeoloniaV2

ADDRESS = "大阪府大阪市北区中之島1丁目1-27"


def fixture_api(*, rows=None, range_status=206, content_range=None, rsdt=True, duplicate=False):
    kind = "住居表示" if rsdt else "地番"
    fields = "blk_num,rsdt_num,rsdt_num2,lng,lat" if rsdt else "prc_num1,prc_num2,prc_num3,lng,lat"
    body = (kind + ",中之島一丁目\n" + fields + "\n" + (rows if rows is not None else "1,27,,135.502,34.694\n")).encode()
    master = {"meta": {"updated": 123}, "data": [{"pref": "大阪府", "point": [135, 34], "cities": [
        {"city": "大阪市", "ward": "北区", "point": [135.5, 34.7]}]}]}
    town = {"machiaza_id": "001", "oaza_cho": "中之島", "chome": "一丁目", "chome_n": 1,
            "point": [135.5, 34.69], "csv_ranges": {kind: {"start": 100, "length": len(body)}}}
    if rsdt: town["rsdt"] = True
    towns = {"meta": {"updated": 124}, "data": [town]}
    if duplicate: towns["data"].append(dict(town, machiaza_id="002"))
    calls = []
    def handler(request):
        calls.append(request)
        path = request.url.path
        if path.endswith("/ja.json"): return httpx.Response(200, json=master)
        if path.endswith(".json"): return httpx.Response(200, json=towns)
        assert request.headers["range"] == f"bytes=100-{99 + len(body)}"
        assert request.headers["accept-encoding"] == "identity"
        content = b" " * 100 + body if range_status == 200 else body
        return httpx.Response(range_status, content=content,
            headers={"Content-Range": content_range or f"bytes 100-{99 + len(body)}/{100 + len(body)}"})
    return handler, calls


def geocoder(tmp_path, settings, **kwargs):
    handler, calls = fixture_api(**kwargs)
    cache = GeocodeCache(tmp_path / "cache.db")
    geo = GeoloniaV2(httpx.Client(transport=httpx.MockTransport(handler)), settings.geocoding, cache)
    return geo, calls, cache


@pytest.mark.parametrize("address", [ADDRESS, "大阪府大阪市北区中之島一丁目1番27号", "大阪府北区中之島1-1-27", "大阪市北区中之島１丁目１−２７"])
def test_exact_coordinates_and_ward_forms(tmp_path, settings, address):
    geo, calls, _ = geocoder(tmp_path, settings)
    result = geo.geocode(address)
    assert result.coordinates.latitude == 34.694
    assert result.coordinates.longitude == 135.502
    assert result.precision == "EXACT_ADDRESS"
    assert result.dataset_version == "123/124"
    assert len(calls) == 3


@pytest.mark.parametrize("suffix", [" 公開ビル101号室", "公開ビル101号室", " 101号室"])
def test_building_is_not_verified(tmp_path, settings, suffix):
    geo, _, _ = geocoder(tmp_path, settings)
    result = geo.geocode(ADDRESS + suffix)
    assert result.precision == "EXACT_ADDRESS"
    assert result.unmatched_suffix
    assert result.unmatched_suffix == suffix.strip()


@pytest.mark.parametrize("address", ["大阪府大阪市北区中之島", "大阪府大阪市北区中之島1丁目", ADDRESS + "0", ADDRESS + "-1", ADDRESS + "の1", ADDRESS + " 1", ADDRESS + " 1-2", "大阪府大阪市北区不明1-2", "不明"])
def test_no_fabricated_exact(tmp_path, settings, address):
    geo, _, cache = geocoder(tmp_path, settings)
    with pytest.raises(GeocodingNotPreciseError): geo.geocode(address)
    with sqlite3.connect(cache.path) as db:
        assert db.execute("SELECT COUNT(*) FROM geocodes").fetchone()[0] == 0


def test_hit_expiry_and_address_edit(tmp_path, settings):
    geo, calls, cache = geocoder(tmp_path, settings)
    geo.geocode(ADDRESS)
    geo.geocode(ADDRESS)
    assert len(calls) == 3
    with sqlite3.connect(cache.path) as db:
        db.execute("UPDATE geocodes SET updated_at='2000-01-01T00:00:00+00:00'")
    geo.geocode(ADDRESS)
    assert len(calls) == 4  # 町字・自治体JSONは独立にキャッシュ
    with pytest.raises(GeocodingNotPreciseError): geo.geocode(ADDRESS.replace("中之島", "字中之島"))


@pytest.mark.parametrize("kwargs", [{"rows": "1,27,,,\n"}, {"rows": "1,27,,135.502,34.694\n1,27,,135.503,34.695\n"}, {"duplicate": True}, {"range_status": 404}])
def test_missing_or_ambiguous(tmp_path, settings, kwargs):
    geo, _, _ = geocoder(tmp_path, settings, **kwargs)
    with pytest.raises(GeocodingNotPreciseError): geo.geocode(ADDRESS)


def test_land_lot(tmp_path, settings):
    geo, _, _ = geocoder(tmp_path, settings, rsdt=False)
    assert geo.geocode(ADDRESS).precision == "EXACT_ADDRESS"


@pytest.mark.parametrize("kwargs", [{"range_status": 200}, {}])
def test_range_and_full_file(tmp_path, settings, kwargs):
    geo, _, _ = geocoder(tmp_path, settings, **kwargs)
    assert geo.geocode(ADDRESS).coordinates


def test_bad_range(tmp_path, settings):
    geo, _, _ = geocoder(tmp_path, settings, content_range="bytes 0-10/100")
    with pytest.raises(GeocodingUnavailableError): geo.geocode(ADDRESS)


@pytest.mark.parametrize("mode", ["timeout", "broken", "notfound", "redirect", "oversize"])
def test_transport_problems(tmp_path, settings, mode):
    def handler(request):
        if mode == "timeout": raise httpx.ReadTimeout("offline", request=request)
        if mode == "broken": return httpx.Response(200, content=b"not json")
        if mode == "notfound": return httpx.Response(404)
        if mode == "redirect": return httpx.Response(302, headers={"Location": "https://evil.example"})
        return httpx.Response(200, content=b"x" * 2048)
    cfg = replace(settings.geocoding, max_download_bytes=1024, retries=0)
    geo = GeoloniaV2(httpx.Client(transport=httpx.MockTransport(handler)), cfg, GeocodeCache(tmp_path / "cache.db"))
    with pytest.raises(GeocodingUnavailableError): geo.geocode(ADDRESS)


def test_prefecture_omission_ambiguity(tmp_path, settings):
    base_handler, _ = fixture_api()
    def handler(request):
        if request.url.path.endswith('/ja.json'):
            return httpx.Response(200, json={"meta": {"updated": 1}, "data": [
                {"pref": "大阪府", "cities": [{"city": "同名市"}]},
                {"pref": "別県", "cities": [{"city": "同名市"}]}]})
        return base_handler(request)
    geo = GeoloniaV2(httpx.Client(transport=httpx.MockTransport(handler)), settings.geocoding, GeocodeCache(tmp_path / 'ambiguous.db'))
    with pytest.raises(GeocodingNotPreciseError): geo.geocode('同名市住所1-2-3')


def test_county_alias(tmp_path, settings):
    base_handler, _ = fixture_api()
    def handler(request):
        if request.url.path.endswith('/ja.json'):
            return httpx.Response(200, json={"meta": {"updated": 1}, "data": [
                {"pref": "大阪府", "cities": [{"county": "テスト郡", "city": "テスト町"}]}]})
        if request.url.path.endswith('.json'):
            assert 'テスト郡テスト町.json' in request.url.path
        return base_handler(request)
    geo = GeoloniaV2(httpx.Client(transport=httpx.MockTransport(handler)), settings.geocoding, GeocodeCache(tmp_path / 'county.db'))
    assert geo.geocode('大阪府テスト町中之島1丁目1-27').precision == 'EXACT_ADDRESS'


def test_prefecture_precision(tmp_path, settings):
    geo, _, _ = geocoder(tmp_path, settings)
    with pytest.raises(GeocodingNotPreciseError, match='CITY_OR_PREF'): geo.geocode('大阪府')
