"""CUIエントリーポイント。料金計算とHTTPはroute_estimator側へ委譲する。"""
import argparse
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sys
import re
from datetime import datetime

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from route_estimator.bootstrap import open_adapters
from route_estimator.config import load_config
from route_estimator.customer_repository import CustomerRepository
from route_estimator.errors import EstimatorError, GeocodingNotPreciseError, GeocodingUnavailableError
from route_estimator.estimate_service import estimate_trip
from route_estimator.models import Location, TripRequest
from route_estimator.scheduling import JST, default_departure, departure_label
from route_estimator.highway_service import HighwayService
from route_estimator.highway_cui import show_highways, toll_flow


def _choose(prompt, choices, read, write):
    while True:
        answer = read(prompt).strip()
        if answer in choices: return answer
        write("表示された選択肢を入力してください。")


def _address(read, write):
    while True:
        value = read("住所 > ").strip()
        if value: return value
        write("住所を入力してください。")


def _search(repository, read, write):
    while True:
        query = read("顧客コードまたは顧客名（b: 戻る） > ").strip()
        if query == "b": return None
        candidates = repository.search(query)
        if not candidates:
            write("該当する顧客がありません。検索し直してください。")
            continue
        page = 0
        while True:
            start = page * 10
            write(f"候補 {start + 1}〜{min(start + 10, len(candidates))} / {len(candidates)}件")
            for index, candidate in enumerate(candidates[start:start + 10], start + 1):
                write(f"{index}. {candidate.display}")
            value = read("番号 / n: 次へ / p: 前へ / r: 再検索 / b: 戻る > ").strip()
            if value == "b": return None
            if value == "r": break
            if value == "n" and start + 10 < len(candidates):
                page += 1
                continue
            if value == "p" and page > 0:
                page -= 1
                continue
            try: index = int(value) - 1
            except ValueError: index = -1
            if start <= index < min(start + 10, len(candidates)):
                customer = candidates[index].customer
                return Location(customer.name, customer.address, customer.code)
            write("このページの番号または有効な操作を入力してください。")


def _location(origin, settings, repository, read, write):
    while True:
        if origin:
            write("[From / 出発地]\n1. 自社\n2. 顧客検索\n3. 住所を直接入力")
            value = _choose("選択 > ", {"1", "2", "3"}, read, write)
            if value == "1": return settings.company
            search = value == "2"
        else:
            write("[To / 到着地]\n1. 顧客検索\n2. 住所を直接入力")
            value = _choose("選択 > ", {"1", "2"}, read, write)
            search = value == "1"
        if search:
            result = _search(repository, read, write)
            if result is not None: return result
        else:
            return Location("手入力住所", _address(read, write))


def _people(default, read, write):
    write("[人員数]")
    while True:
        value = read(f"人数を入力（Enterのみで{default}人） > ").strip()
        if not value: return default
        try:
            result = int(value)
            if result >= 1: return result
        except ValueError: pass
        write("人数は1以上の整数を入力してください。")


def _departure(default, read, write):
    local = default.astimezone(JST)
    write("[出発日時 / 日本時間] Enterで現在の設定を保持。土日祝も手入力できます。")
    while True:
        value = read(f"出発日 YYYY-MM-DD（{local:%Y-%m-%d}） > ").strip()
        if not value:
            day = local.date()
            break
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError
            day = datetime.strptime(value, "%Y-%m-%d").date()
            break
        except ValueError:
            write("実在する日付をYYYY-MM-DD形式で入力してください。")
    while True:
        value = read(f"出発時刻 HH:MM（{local:%H:%M}） > ").strip()
        if not value:
            clock = local.time()
            break
        try:
            if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
                raise ValueError
            clock = datetime.strptime(value, "%H:%M").time()
            break
        except ValueError:
            write("時刻を00:00〜23:59のHH:MM形式で入力してください。")
    return datetime.combine(day, clock, tzinfo=JST)


def _show_departure(value, write):
    if value is None:
        write("出発日時：未指定")
        return
    write("出発日時：" + departure_label(value))
    if value.astimezone(JST).year > 2027:
        write("※ 2028年以降の祝日判定は予測です。日付をご確認ください。")
    write("※ Geoapifyは渋滞なし推定。ETC2.0検索時は入口IC到達予定時刻を使用します。")


def _place(prefix, location, write):
    code = f" [{location.customer_code}]" if location.customer_code else ""
    write(f"  {prefix}：{location.label}{code}")
    write(f"    住所：{location.address}")
    if location.original_address is not None:
        write(f"  修正前の住所：{location.original_address}（CSVは変更していません）")


def _section(title, write):
    write("\n" + "─" * 52)
    write(f"  {title}")
    write("─" * 52)


def _confirm(request, settings, write):
    _section("最終確認 / 片道", write)
    write("[出発地 → 到着地]")
    _place("From", request.origin, write)
    _place("To", request.destination, write)
    write("\n[出発日時]")
    _show_departure(request.departure_at, write)
    write("変更：2. 内容を修正 → 4. 出発日・時刻")
    write("\n[計算条件]")
    write(f"人員数：{request.people}人")
    write(f"人件費：{settings.labor.hourly_rate_yen_per_person:,}円/h（1人あたり）")
    write(f"燃費：{settings.vehicle.fuel_efficiency_km_per_liter}km/L（1台あたり） / 車両：{settings.vehicle.count}台")
    price = settings.vehicle.fuel_price_yen_per_liter
    write("燃料単価：未設定" if price is None else f"燃料単価：{price}円/L")
    write("通行料金：ETC2.0参考額を別途確認（移動費の合計には未反映）")
    if price is None:
        write("[要設定] 燃料単価が未設定のため、燃料費・合計額は確定しません。")
    write("\n1. この内容で計算\n2. 内容を修正\n")


def _result(result, write):
    distance = (result.distance_m / Decimal(1000)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    minutes = int((result.duration_s / Decimal(60)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    liters = result.fuel_liters.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    _section("ルート・移動費 / 片道", write)
    write("【移動費の試算】")
    if result.total_cost_yen is None:
        write("合計額：未確定")
        write("[要設定] 燃料単価が未設定のため、燃料費を算出できません。")
        write(f"算出済みの移動人件費：{result.labor_cost_yen:,}円")
    else:
        write(f"合計額：{result.total_cost_yen:,}円")
    write("対象：移動人件費 + 燃料費（現場作業・通行料金・税は対象外）")
    write("")
    write(f"距離：{distance} km")
    write(f"推定移動時間：{minutes // 60}時間{minutes % 60:02d}分")
    show_highways(result, write)
    _section("費用内訳", write)
    write(f"移動人件費：{result.labor_cost_yen:,}円")
    if result.fuel_cost_yen_or_none is None:
        write("燃料費：未計算（円/L単価が未設定）")
    else:
        write(f"燃料費：{result.fuel_cost_yen_or_none:,}円")
    write("通行料金：合計未反映（ETC2.0参考額は別枠）")
    write(f"燃料消費量：{liters} L（車両{result.vehicle_count}台の合計）")
    _section("出発地 → 到着地", write)
    _place("From", result.origin, write)
    write("")
    _place("To", result.destination, write)
    _section("計算条件", write)
    _show_departure(result.departure_at, write)
    write(f"人員数：{result.people}人 / 人件費単価：{result.hourly_rate:,}円/h")
    write(f"車両：{result.vehicle_count}台 / 燃費：{result.efficiency}km/L（1台あたり）")
    write("時間は分単位に四捨五入して表示。人件費は丸め前の秒数で計算。")
    if result.routing_metadata.get('provider') == 'geoapify':
        write("経路：Geoapify / 自動車・バランス型 / 渋滞なしの推定値")
    else:
        write("経路：OSRM / 自動車 / 実時間の渋滞を含まない推定値")
    _section("補足 / 住所照合・出典", write)
    for name, geo in (("From", result.origin_geocode), ("To", result.destination_geocode)):
        c = geo.coordinates
        write(f"[{name}] 住所精度：{geo.precision}")
        write(f"  提供元：{geo.provider} / データ版：{geo.dataset_version}")
        write(f"  照合住所：{geo.matched_address}")
        write(f"  緯度：{c.latitude} / 経度：{c.longitude}")
        if geo.unmatched_suffix: write(f"  照合対象外の建物名・部屋番号等：{geo.unmatched_suffix}")
        write("")
    if result.routing_metadata.get('provider')=='geoapify':
        write("経路出典："+result.routing_metadata['attribution'])
    write("出典：© OpenStreetMap contributors（ODbL） / Geolonia 日本住所データv2（CC BY 4.0、原典：デジタル庁）")


def run_cli(*, settings, repository, geocoder, router, read=None, write=None, highway_service=None):
    read = input if read is None else read
    write = print if write is None else write
    try:
        origin = _location(True, settings, repository, read, write)
        destination = _location(False, settings, repository, read, write)
        people = _people(settings.labor.default_people, read, write)
        request = TripRequest(origin, destination, people, departure_at=default_departure())
        while True:
            _confirm(request, settings, write)
            choice = _choose("選択 > ", {"1", "2"}, read, write)
            if choice == "2":
                write("修正する項目\n1. From\n2. To\n3. 人員数\n4. 出発日・時刻")
                field = _choose("選択 > ", {"1", "2", "3", "4"}, read, write)
                if field == "1": request = replace(request, origin=_location(True, settings, repository, read, write))
                elif field == "2": request = replace(request, destination=_location(False, settings, repository, read, write))
                elif field == "3": request = replace(request, people=_people(settings.labor.default_people, read, write))
                else: request = replace(request, departure_at=_departure(request.departure_at, read, write))
                continue
            try:
                result = estimate_trip(request, settings=settings, geocoder=geocoder, router=router, highway_service=highway_service)
            except (GeocodingNotPreciseError, GeocodingUnavailableError) as exc:
                write(f"計算保留：{exc}")
                if isinstance(exc, GeocodingUnavailableError):
                    write("通信障害の場合は接続を確認し、最終確認へ戻って再試行してください。")
                write("住所だけを編集できます。字・大字等は自動で追加・削除しません。\n1. Fromの住所を編集\n2. Toの住所を編集\n3. 最終確認へ戻る\n0. 終了")
                field = _choose("選択 > ", {"0", "1", "2", "3"}, read, write)
                if field == "0": return 2
                if field in ("1", "2"):
                    name = "origin" if field == "1" else "destination"
                    previous = getattr(request, name)
                    write(f"現在の住所：{previous.address}")
                    edited = replace(previous, address=_address(read, write), original_address=previous.original_address or previous.address)
                    request = replace(request, **{name: edited})
                continue
            _result(result, write)
            if highway_service is not None:
                toll_flow(result, highway_service, read, write)
            return 0
    except (KeyboardInterrupt, EOFError):
        write("\n中断しました（見積未確定）。")
        return 130
    except EstimatorError as exc:
        write(f"計算できません：{exc}")
        return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="顧客検索・片道移動費の試算")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--customers", type=Path, default=ROOT / "data/customers.csv")
    parser.add_argument("--no-highways", action="store_true", help="IC座標取得とNEXCO検索を省略")
    parser.add_argument("--check", action="store_true", help="設定とキー読込を確認（外部APIへは送信しない）")
    args = parser.parse_args(argv)
    try:
        settings = load_config(args.config)
        if args.check:
            with open_adapters(settings):
                print(f"設定・キー読込OK：{settings.routing.provider}（実API疎通は未確認）")
            return 0
        repository = CustomerRepository.from_csv(args.customers)
        with open_adapters(settings) as (geocoder, router):
            highways = None if args.no_highways else HighwayService(ROOT / "state")
            try:
                return run_cli(settings=settings, repository=repository, geocoder=geocoder, router=router, highway_service=highways)
            finally:
                if highways is not None: highways.close()
    except (KeyboardInterrupt, EOFError):
        print("\n中断しました（見積未確定）。")
        return 130
    except EstimatorError as exc:
        print(f"起動できません：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
