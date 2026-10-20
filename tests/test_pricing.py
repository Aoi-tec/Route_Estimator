from decimal import Decimal as D
import pytest

from route_estimator.errors import ValidationError
from route_estimator.pricing import calculate_labor_cost, calculate_fuel_liters, calculate_fuel_cost_optional


def test_reference_and_multiple_vehicles():
    assert calculate_labor_cost(D(7200), 2, D(3000)) == D(12000)
    assert calculate_fuel_liters(D(120000), D(10)) == D(12)
    assert calculate_fuel_liters(D(120000), D(10), 3) == D(36)
    assert calculate_fuel_cost_optional(D(12), D(170)) == D(2040)
    assert calculate_fuel_cost_optional(D(12), None) is None


def test_rounding_once_and_zero():
    assert calculate_labor_cost(D("0.6"), 1, D(3000)) == 1
    assert calculate_labor_cost(D("0.2"), 3, D(3000)) == 1
    assert calculate_labor_cost(D(0), 2, D(3000)) == 0
    assert calculate_fuel_liters(D(0), D(10)) == 0
    liters = calculate_fuel_liters(D(1), D(3))
    assert calculate_fuel_cost_optional(liters, D(1500)) == 1


@pytest.mark.parametrize("call", [
    lambda: calculate_labor_cost(D(-1), 2, D(3000)),
    lambda: calculate_labor_cost(D(1), 0, D(3000)),
    lambda: calculate_labor_cost(D(1), True, D(3000)),
    lambda: calculate_labor_cost(D(1), 2, D("NaN")),
    lambda: calculate_fuel_liters(D(1), D(0)),
    lambda: calculate_fuel_liters(D(1), D(10), 0),
    lambda: calculate_fuel_cost_optional(D(1), D(-1)),
])
def test_invalid(call):
    with pytest.raises(ValidationError):
        call()
