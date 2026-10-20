"""HTTP、CSV、永続化に依存しない純粋計算。金額だけ最後に円単位へ丸める。"""
from decimal import Decimal, ROUND_HALF_UP, localcontext

from .models import decimal_value, positive_integer


def calculate_labor_cost(duration_s: Decimal, people: int, hourly_rate: Decimal) -> Decimal:
    seconds = decimal_value(duration_s, "時間")
    count = positive_integer(people, "人数")
    rate = decimal_value(hourly_rate, "時給")
    with localcontext() as context:
        context.prec = 50
        return (seconds * count * rate / Decimal(3600)).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def calculate_fuel_liters(distance_m: Decimal, efficiency: Decimal, vehicle_count: int = 1) -> Decimal:
    distance = decimal_value(distance_m, "距離")
    km_per_liter = decimal_value(efficiency, "燃費", positive=True)
    count = positive_integer(vehicle_count, "台数")
    with localcontext() as context:
        context.prec = 50
        return distance * count / Decimal(1000) / km_per_liter


def calculate_fuel_cost_optional(fuel_liters: Decimal, price: Decimal | None) -> Decimal | None:
    liters = decimal_value(fuel_liters, "燃料使用量")
    if price is None:
        return None
    rate = decimal_value(price, "燃料単価")
    with localcontext() as context:
        context.prec = 50
        return (liters * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP)
