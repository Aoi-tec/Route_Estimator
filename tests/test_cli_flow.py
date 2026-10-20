from dataclasses import replace
from decimal import Decimal as D
import pytest
import from_to

from route_estimator.customer_repository import CustomerRepository
from route_estimator.errors import GeocodingNotPreciseError, RoutingUnavailableError
from route_estimator.models import Customer
from test_estimate_service import FakeGeocoder, FakeRouter


def run(settings, inputs, *, geo=None, router=None, repository=None):
    # 入力が尽きたら算出後メニューを「0. 終了」で閉じる（最大3回まで。無限ループ防止）
    iterator = iter(list(inputs) + ["0"] * 3)
    output = []
    geo = geo or FakeGeocoder()
    router = router or FakeRouter()
    code = from_to.run_cli(settings=settings, repository=repository or CustomerRepository([
        Customer("C001", "山田株式会社", "大阪府A1-2-3"),
        Customer("C002", "有限会社山田", "大阪府B1-2-3"),
        Customer("C003", "山田プロダクト株式会社", "大阪府C1-2-3"),
    ]), geocoder=geo, router=router, read=lambda _: next(iterator), write=output.append)
    return code, "\n".join(output), geo, router


def test_acceptance_customer_and_correct_people(settings):
    code, text, geo, router = run(settings, ["1", "1", "山田", "9", "2", "", "2", "3", "3", "1"])
    assert code == 0
    assert "移動人件費：18,000円" in text
    assert "② ガソリン代：未計算" in text and "合計：未確定" in text
    assert "有限会社山田" in text
    assert geo.calls == [settings.company.address, "大阪府B1-2-3"]
    assert len(router.calls) == 1


def test_direct_addresses_and_correct_from_to(settings):
    code, text, geo, _ = run(settings, ["3", "", "From住所", "2", "To住所", "-1", "0", "1.5", "x", "2", "2", "1", "3", "修正From", "2", "2", "2", "修正To", "1"])
    assert code == 0
    assert geo.calls == ["修正From", "修正To"]
    assert "From住所" in text and "To住所" in text


def test_zero_matches_and_search_back(settings):
    code, text, _, _ = run(settings, ["2", "なし", "b", "1", "1", "山田", "1", "", "1"])
    assert code == 0
    assert "該当する顧客がありません" in text


def test_paging_and_research(settings):
    repository = CustomerRepository([Customer(f"C{i:03}", "山田", f"住所{i}") for i in range(25)])
    code, text, geo, _ = run(settings, ["1", "1", "山田", "n", "p", "r", "C", "n", "n", "25", "", "1"], repository=repository)
    assert code == 0
    assert geo.calls[-1] == "住所24"
    assert "21〜25 / 25件" in text


def test_address_edit_keeps_customer_and_original(settings):
    class NeedsEdit(FakeGeocoder):
        def geocode(self, address):
            if address == "大阪府A1-2-3":
                raise GeocodingNotPreciseError("住所表記を確認してください")
            return super().geocode(address)
    code, text, _, router = run(settings, ["1", "1", "C001", "1", "", "1", "2", "大阪府字A1-2-3", "1"], geo=NeedsEdit())
    assert code == 0
    assert "修正前の住所：大阪府A1-2-3" in text
    assert "山田株式会社 [C001]" in text
    assert len(router.calls) == 1


def test_error_never_prints_money(settings):
    class OfflineRouter:
        def route(self, a, b): raise RoutingUnavailableError("停止中")
    code, text, _, _ = run(settings, ["1", "2", "住所", "", "1"], router=OfflineRouter())
    assert code == 2
    assert "移動人件費：" not in text and "距離：" not in text


def test_geo_error_cancel(settings):
    code, text, _, router = run(settings, ["1", "2", "住所", "", "1", "0"], geo=FakeGeocoder("BLOCK_OR_TOWN"))
    assert code == 2 and not router.calls
    assert "移動人件費：" not in text


@pytest.mark.parametrize("exc", [KeyboardInterrupt, EOFError])
def test_interrupt_before_estimate(settings, exc):
    router = FakeRouter()
    def read(_): raise exc()
    assert from_to.run_cli(settings=settings, repository=CustomerRepository([]), geocoder=FakeGeocoder(), router=router, read=read, write=lambda _: None) == 130
    assert not router.calls


def test_config_default_and_fuel_total(settings):
    settings = replace(settings, labor=replace(settings.labor, default_people=3), vehicle=replace(settings.vehicle, fuel_price_yen_per_liter=D(170)))
    code, text, _, _ = run(settings, ["1", "2", "住所", "", "1"])
    assert code == 0 and "人員数：3人" in text and "② ガソリン代：2,040円" in text and "確定分 20,040円" in text


def test_geocoding_transport_retry_keeps_inputs(settings):
    from route_estimator.errors import GeocodingUnavailableError
    class TemporaryFailure(FakeGeocoder):
        def __init__(self):
            super().__init__()
            self.failed = False
        def geocode(self, address):
            if not self.failed:
                self.failed = True
                raise GeocodingUnavailableError('通信障害')
            return super().geocode(address)
    code, text, geo, router = run(settings, ['1', '2', '住所', '', '1', '3', '1'], geo=TemporaryFailure())
    assert code == 0 and '通信障害の場合' in text
    assert geo.calls == [settings.company.address, '住所']
    assert len(router.calls) == 1


def test_town_point_fallback_is_shown(fallback_settings):
    code, text, _, router = run(fallback_settings, ["1", "2", "住所", "", "1"], geo=FakeGeocoder("BLOCK_OR_TOWN"))
    assert code == 0 and len(router.calls) == 1
    assert "[住所精度] From・Toは番地が見つからないため" in text
    assert "住所精度：BLOCK_OR_TOWN（丁目・町字の代表点）" in text



def test_change_fuel_price_and_toll_after_calculation(settings):
    # 計算後: 1→ガソリン単価170、2→高速1台1,500円、0→終了
    code, text, _, router = run(settings, ["1", "2", "住所", "", "1", "1", "170", "2", "1,500", "0"])
    assert code == 0 and len(router.calls) == 1  # 変更しても経路は再計算しない
    final = text.split("最終算出結果")[-1]
    assert "① 移動人件費：12,000円" in final
    assert "② ガソリン代：2,040円" in final and "12 L × 170円/L" in text.replace("12.00 L", "12 L")
    assert "③ 高速料金：1,500円" in final and "手入力 1台1,500円 × 1台" in final
    assert "合計：15,540円（① + ② + ③）" in final


def test_no_highway_by_manual_zero_and_invalid_amount(settings):
    code, text, _, _ = run(settings, ["1", "2", "住所", "", "1", "2", "-5", "abc", "0", "1", "", "0"])
    assert code == 0
    assert "0以上の整数を入力してください" in text
    final = text.split("最終算出結果")[-1]
    assert "③ 高速料金：0円" in final and "高速を使わない" in final
    assert "合計：未確定（確定分 12,000円 / 未確定：②ガソリン代）" in final
