import json
from pathlib import Path
from decimal import Decimal
import pytest
from route_estimator.config import load_config
from route_estimator.errors import ConfigError

EXAMPLE = Path(__file__).parents[1] / "config.example.json"


def test_valid_and_path_relative_to_config(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(EXAMPLE.read_text())
    config = load_config(p)
    assert config.labor.default_people == 2
    assert config.labor.hourly_rate_yen_per_person == Decimal(3000)
    assert config.vehicle.fuel_price_yen_per_liter is None
    assert config.geocoding.cache_database == tmp_path / "state/geocode_cache.db"


@pytest.mark.parametrize("section,key,value", [
    ("labor", "hourly_rate_yen_per_person", -1),
    ("labor", "hourly_rate_yen_per_person", None),
    ("labor", "default_people", 0), ("labor", "default_people", True),
    ("vehicle", "fuel_efficiency_km_per_liter", 0),
    ("vehicle", "count", 1.5), ("vehicle", "fuel_price_yen_per_liter", -1),
    ("osrm", "base_url", "http://example.com:5000"),
    ("osrm", "profile", "driving/../../"),
    ("osrm", "timeout_seconds", 0),
    ("geocoding", "base_url", "https://evil.example/api/ja"),
    ("geocoding", "allow_approximate", "yes"),
    ("geocoding", "retries", 4),
    ("geocoding", "cache_ttl_days", 0),
])
def test_invalid_fields(tmp_path, section, key, value):
    data = json.loads(EXAMPLE.read_text())
    data[section][key] = value
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ConfigError): load_config(p)


@pytest.mark.parametrize("content", ['{', '[]', '{}', '{"a":1,"a":2}', '{"a":NaN}'])
def test_bad_json(tmp_path, content):
    p = tmp_path / "config.json"
    p.write_text(content)
    with pytest.raises(ConfigError): load_config(p)


def test_float_timeout_overflow(tmp_path):
    content = EXAMPLE.read_text().replace('"timeout_seconds": 15', '"timeout_seconds": 1e10000')
    p = tmp_path / "config.json"
    p.write_text(content)
    with pytest.raises(ConfigError): load_config(p)
