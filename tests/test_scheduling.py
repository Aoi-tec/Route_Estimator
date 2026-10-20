from datetime import datetime, timezone
from decimal import Decimal
import json
import pytest
import from_to
from route_estimator.scheduling import JST, default_departure
from route_estimator.models import TripRequest
from route_estimator.errors import ValidationError
from route_estimator.estimate_service import estimate_trip
from test_estimate_service import FakeGeocoder, FakeRouter
from test_cli_flow import run


@pytest.mark.parametrize('today, expected', [
    ('2026-10-20','2026-10-21'),
    ('2026-10-23','2026-10-26'),
    ('2026-10-24','2026-10-26'),
    ('2026-10-09','2026-10-13'),
    ('2026-05-01','2026-05-07'),
    ('2026-09-18','2026-09-24'),
    ('2026-12-31','2027-01-04'),
])
def test_next_business_day(today, expected):
    result = default_departure(datetime.fromisoformat(today).replace(tzinfo=JST))
    assert result.isoformat() == expected + 'T09:00:00+09:00'


def test_date_boundary_uses_japan():
    result = default_departure(datetime(2026, 10, 20, 16, tzinfo=timezone.utc))
    assert result.isoformat() == '2026-10-22T09:00:00+09:00'


def test_cui_modify_departure_and_invalid_input(settings, monkeypatch):
    monkeypatch.setattr(from_to, 'default_departure', lambda: datetime(2026,10,21,9,tzinfo=JST))
    code, text, _, router = run(settings, ['1','2','住所','','2','4','2026-02-30','2026-10-24','24:00','08:30','1'])
    assert code == 0
    assert '2026-10-24（土）08:30 日本時間' in text
    assert '実在する日付' in text and '00:00〜23:59' in text
    assert '入口IC到達予定時刻を使用' in text
    assert len(router.calls) == 1


def test_departure_preserved_and_json(settings):
    departure = datetime(2026,10,21,9,tzinfo=JST)
    request = TripRequest(settings.company, settings.company, departure_at=departure)
    result = estimate_trip(request, settings=settings, geocoder=FakeGeocoder(), router=FakeRouter())
    assert result.departure_at == departure
    assert json.loads(json.dumps(result.to_dict()))['departure_at'] == departure.isoformat()
    assert result.labor_cost_yen == Decimal(12000)


def test_empty_edit_preserves_departure():
    departure = datetime(2026,10,21,9,tzinfo=JST)
    values = iter(['',''])
    assert from_to._departure(departure, lambda _: next(values), lambda _: None) == departure


def test_naive_departure_rejected(settings):
    with pytest.raises(ValidationError):
        TripRequest(settings.company,settings.company,departure_at=datetime(2026,10,21,9))
