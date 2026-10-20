"""CUIエントリーポイント。料金計算とHTTPはroute_estimator側へ委譲する。"""
import argparse
from dataclasses import replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import sys
import re
import unicodedata
from datetime import datetime

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from route_estimator.bootstrap import open_adapters
from route_estimator.config import load_config
from route_estimator.customer_repository import CustomerRepository
from route_estimator.errors import EstimatorError, GeocodingNotPreciseError, GeocodingUnavailableError
from route_estimator.estimate_service import estimate_trip
from route_estimator.models import Location, TripRequest, japan_coordinates
from route_estimator.errors import ValidationError
from route_estimator.pricing import calculate_fuel_cost_optional
from route_estimator.scheduling import JST, default_departure, departure_label
from route_estimator.highway_service import HighwayService
from route_estimator.highway_cui import show_highways, toll_flow

PRECISION_LABEL = {'MANUAL_VERIFIED': '登録済みの緯度経度', 'EXACT_ADDRESS': '番地・号まで一致', 'BLOCK_OR_TOWN': '丁目・町字の代表点', 'CITY_OR_PREF': '市区町村', 'UNRESOLVED': '未確定'}


def _choose(prompt, choices, read, write):
    while True:
        answer = read(prompt).strip()
        if answer in choices: return answer
        write("表示された選択肢を入力してください。")


COORDINATE_INPUT = re.compile(r"\s*(-?\d+(?:\.\d+)?)\s*[,、，\s]\s*(-?\d+(?:\.\d+)?)\s*")


def _address(read, write):
    while True:
        value = read("住所 > ").strip()
        if value: return value
        write("住所を入力してください。")


def _manual_location(read, write):
    """住所、または「緯度,経度」（例: 34.6853,135.6013）を受け付ける。"""
    while True:
        value = _address(read, write)
        match = COORDINATE_INPUT.fullmatch(value)
        if not match:
            return Location("手入力住所", value)
        try:
            coordinates = japan_coordinates(match[1], match[2], "入力")
        except ValidationError as exc:
            write(str(exc))
            continue
        return Location("手入力座標", f"緯度{coordinates.latitude}, 経度{coordinates.longitude}", coordinates=coordinates)


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
                return Location(customer.name, customer.address, customer.code, coordinates=customer.coordinates)
            write("このページの番号または有効な操作を入力してください。")


def _location(origin, settings, repository, read, write):
    while True:
        if origin:
            write("[From / 出発地]\n1. 自社\n2. 顧客検索\n3. 住所または緯度経度を直接入力")
            value = _choose("選択 > ", {"1", "2", "3"}, read, write)
            if value == "1": return settings.company
            search = value == "2"
        else:
            write("[To / 到着地]\n1. 顧客検索\n2. 住所または緯度経度を直接入力")
            value = _choose("選択 > ", {"1", "2"}, read, write)
            search = value == "1"
        if search:
            result = _search(repository, read, write)
            if result is not None: return result
        else:
            return _manual_location(read, write)


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
    if location.coordinates is not None:
        write(f"    座標：{location.coordinates.latitude}, {location.coordinates.longitude}（登録済み・住所検索は省略）")
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
    write("高速料金：計算後にETC2.0参考額を検索（手入力・変更も可）")
    if price is None:
        write("※ ガソリン単価が未設定です。計算後の画面で入力できます。")
    write("\n1. この内容で計算\n2. 内容を修正\n")


def _duration_label(duration_s):
    minutes = int((duration_s / Decimal(60)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return f"{minutes // 60}時間{minutes % 60:02d}分"


def _result(result, write):
    """経路・IC・地点・条件の詳細。金額は最後の「最終算出結果」にまとめて表示する。"""
    distance = (result.distance_m / Decimal(1000)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    _section("ルート / 片道", write)
    write(f"距離：{distance} km")
    write(f"推定移動時間：{_duration_label(result.duration_s)}")
    approx = [name for name, geo in (("From", result.origin_geocode), ("To", result.destination_geocode)) if str(geo.precision) == 'BLOCK_OR_TOWN']
    if approx:
        write(f"[住所精度] {'・'.join(approx)}は番地が見つからないため、丁目・町字の代表点で計算しています。")
    show_highways(result, write)
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
        write(f"[{name}] 住所精度：{geo.precision}（{PRECISION_LABEL.get(str(geo.precision), '')}）")
        if str(geo.precision) == 'BLOCK_OR_TOWN':
            write("  ※ 番地・号が見つからないため、丁目・町字の代表点で計算しています（数百m程度ずれる場合があります）")
        write(f"  提供元：{geo.provider} / データ版：{geo.dataset_version}")
        write(f"  照合住所：{geo.matched_address}")
        write(f"  緯度：{c.latitude} / 経度：{c.longitude}")
        if geo.unmatched_suffix: write(f"  照合対象外の建物名・部屋番号等：{geo.unmatched_suffix}")
        write("")
    if result.routing_metadata.get('provider')=='geoapify':
        write("経路出典："+result.routing_metadata['attribution'])
    write("出典：© OpenStreetMap contributors（ODbL） / Geolonia 日本住所データv2（CC BY 4.0、原典：デジタル庁）")


# ---- 最終算出結果（人件費・ガソリン代・高速料金を別々に算出し、算出後に変更可能） ----

def _toll_from_search(result, selections):
    """ETC2.0検索の結果から高速料金（全車両分）を決める。未確定ならyen=None。"""
    info = result.highway_information
    if info.get('status') == 'available' and not info.get('sections'):
        return {'yen': Decimal(0), 'label': '高速・有料道路の利用なし', 'details': []}
    if info.get('status') != 'available':
        return {'yen': None, 'label': '経路から高速区間を判定できないため未取得', 'details': []}
    details = []
    for s in selections:
        if s['yen'] is None:
            details.append(f"区間{s['section']}：未取得（保留）")
        else:
            ic = '（IC修正）' if s.get('manual_ic') else ''
            details.append(f"区間{s['section']}：{s['from']} → {s['to']}{ic} ルート{s['route']} / {s['yen']:,}円")
    if not selections or any(s['yen'] is None for s in selections) or len(selections) < len(info['sections']):
        return {'yen': None, 'label': '未取得の区間があります', 'details': details}
    per_vehicle = sum(Decimal(s['yen']) for s in selections)
    return {'yen': per_vehicle * result.vehicle_count, 'per_vehicle': per_vehicle,
            'label': f"ETC2.0参考額 1台{per_vehicle:,}円 × {result.vehicle_count}台", 'details': details}


def _final(result, fuel_price, toll, write):
    liters = result.fuel_liters.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    distance = (result.distance_m / Decimal(1000)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    fuel = calculate_fuel_cost_optional(result.fuel_liters, fuel_price)
    _section("最終算出結果 / 片道", write)
    write(f"{result.origin.label} → {result.destination.label}")
    write(f"距離 {distance} km / 推定移動時間 {_duration_label(result.duration_s)} / {result.people}人・車両{result.vehicle_count}台")
    write("")
    write(f"① 移動人件費：{result.labor_cost_yen:,}円")
    write(f"     {result.people}人 × {result.hourly_rate:,}円/h × {_duration_label(result.duration_s)}")
    if fuel is None:
        write("② ガソリン代：未計算（ガソリン単価が未設定 → 下の 1 で入力）")
        write(f"     使用量 {liters} L（燃費 {result.efficiency}km/L・{result.vehicle_count}台分）")
    else:
        write(f"② ガソリン代：{fuel:,}円")
        write(f"     {liters} L × {fuel_price}円/L（燃費 {result.efficiency}km/L・{result.vehicle_count}台分）")
    if toll['yen'] is None:
        write(f"③ 高速料金：未取得（{toll['label']} → 下の 2 か 3 で設定）")
    else:
        write(f"③ 高速料金：{toll['yen']:,}円")
        write(f"     {toll['label']}")
    for line in toll.get('details', []):
        write(f"     ・{line}")
    write("─" * 40)
    parts = [result.labor_cost_yen, fuel, toll['yen']]
    known = sum(v for v in parts if v is not None)
    if all(v is not None for v in parts):
        write(f"合計：{known:,}円（① + ② + ③）")
    else:
        missing = '・'.join(name for name, v in (('②ガソリン代', fuel), ('③高速料金', toll['yen'])) if v is None)
        write(f"合計：未確定（確定分 {known:,}円 / 未確定：{missing}）")
    write("※ 現場作業費・税は含みません。高速料金はNEXCOのETC2.0参考額（Geoapifyの経路と完全一致するとは限りません）。")


def _ask_amount(prompt, read, write, *, integer=False, maximum=Decimal(10_000_000)):
    """Enterのみで変更しない（None）。0以上の数値を返す。"""
    while True:
        value = unicodedata.normalize("NFKC", read(prompt)).strip().replace(",", "")
        if not value: return None
        try:
            amount = Decimal(value)
            if amount.is_finite() and Decimal(0) <= amount <= maximum and (not integer or amount == amount.to_integral_value()):
                return amount
        except (InvalidOperation, ValueError):
            pass
        write("0以上の" + ("整数" if integer else "数値") + "を入力してください（Enterで変更しない）。")


def _adjust(result, settings, selections, highway_service, read, write):
    fuel_price = settings.vehicle.fuel_price_yen_per_liter
    toll = _toll_from_search(result, selections)
    can_search = highway_service is not None and result.highway_information.get('status') == 'available' and bool(result.highway_information.get('sections'))
    while True:
        _final(result, fuel_price, toll, write)
        write("\n[算出後の変更]")
        write(f"1. ガソリン単価を変更（現在：{'未設定' if fuel_price is None else f'{fuel_price}円/L'}）")
        write("2. 高速料金を手入力（1台あたりの金額。0 = 高速を使わない）")
        choices = {"0", "1", "2"}
        if can_search:
            write("3. 高速のIC・ルートを選び直して再検索")
            choices.add("3")
        write("0. 終了")
        choice = _choose("選択 > ", choices, read, write)
        if choice == "0":
            return
        if choice == "1":
            value = _ask_amount("ガソリン単価 円/L（Enterで変更しない） > ", read, write, maximum=Decimal(1000))
            if value is not None:
                fuel_price = value
                write("※ この計算だけに反映します。毎回使う場合は config.json の vehicle.fuel_price_yen_per_liter に設定してください。")
        elif choice == "2":
            value = _ask_amount("高速料金 1台あたり 円（Enterで変更しない） > ", read, write, integer=True)
            if value is not None:
                toll = {'yen': value * result.vehicle_count, 'per_vehicle': value,
                        'label': '高速を使わない' if value == 0 else f"手入力 1台{value:,}円 × {result.vehicle_count}台", 'details': []}
        else:
            result.toll_results.clear()
            selections = toll_flow(result, highway_service, read, write)
            toll = _toll_from_search(result, selections)


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
            selections = toll_flow(result, highway_service, read, write) if highway_service is not None else []
            _adjust(result, settings, selections, highway_service, read, write)
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
