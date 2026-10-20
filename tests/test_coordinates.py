import json
from decimal import Decimal as D
import pytest

from route_estimator.config import load_config
from route_estimator.customer_repository import CustomerRepository
from route_estimator.errors import ConfigError, CustomerDataError
from route_estimator.estimate_service import estimate_trip
from route_estimator.models import Location, Precision, TripRequest
from test_estimate_service import FakeGeocoder, FakeRouter
from pathlib import Path

EXAMPLE = Path(__file__).parents[1] / "config.example.json"


def write_config(tmp_path, **company):
    d = json.loads(EXAMPLE.read_text())
    d["company"].update(company)
    p = tmp_path / "config.json"; p.write_text(json.dumps(d, ensure_ascii=False))
    return p


def test_company_coordinates_skip_geocoding(tmp_path):
    s = load_config(write_config(tmp_path, latitude=34.6268, longitude=135.6013))
    assert s.company.coordinates.latitude == 34.6268
    geo, router = FakeGeocoder(), FakeRouter()
    result = estimate_trip(TripRequest(s.company, Location("顧客", "大阪府A1-2-3")), settings=s, geocoder=geo, router=router)
    assert geo.calls == ["大阪府A1-2-3"]
    assert router.calls[0][0] == s.company.coordinates
    assert result.origin_geocode.precision == Precision.MANUAL_VERIFIED


def test_company_coordinates_optional_and_null(tmp_path):
    assert load_config(write_config(tmp_path)).company.coordinates is None


@pytest.mark.parametrize("company", [{"latitude": 34.6}, {"latitude": 135.6, "longitude": 34.6}, {"latitude": "x", "longitude": 135.6}, {"lat": 1}])
def test_bad_company_coordinates(tmp_path, company):
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, **company))


def test_csv_optional_coordinates(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("customer_code,customer_name,address,latitude,longitude\nA,座標あり,大阪府A,34.62,135.60\nB,座標なし,大阪府B,,\n", encoding="utf-8")
    rows = {c.customer.code: c.customer for c in CustomerRepository.from_csv(p).search("座標")}
    assert rows["A"].coordinates.longitude == 135.60 and rows["B"].coordinates is None


@pytest.mark.parametrize("cells", ["34.62,", "135.6,34.62", "abc,135.6"])
def test_csv_bad_coordinates(tmp_path, cells):
    p = tmp_path / "c.csv"
    p.write_text("customer_code,customer_name,address,latitude,longitude\nA,名,大阪府A," + cells + "\n", encoding="utf-8")
    with pytest.raises(CustomerDataError):
        CustomerRepository.from_csv(p)


def test_manual_coordinate_input(settings):
    from test_cli_flow import run
    code, text, geo, router = run(settings, ["1", "2", "34.2993, 133.9556", "", "1"])
    assert code == 0
    assert "34.2993" in text and "登録済みの緯度経度" in text
    assert len(geo.calls) == 1  # From(自社)だけ住所検索
    assert router.calls[0][1].latitude == 34.2993


def test_manual_swapped_coordinates_are_rejected(settings):
    from test_cli_flow import run
    code, text, _, _ = run(settings, ["1", "2", "133.9556,34.2993", "34.2993,133.9556", "", "1"])
    assert code == 0 and "逆になっていないか" in text
