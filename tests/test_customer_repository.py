import pytest

from route_estimator.customer_repository import CustomerRepository
from route_estimator.errors import CustomerDataError
from route_estimator.models import Customer


def test_search_ranking_unicode_and_duplicate_names():
    repository = CustomerRepository([
        Customer("X山田", "別会社", "東京都A"),
        Customer("B", "有限会社山田", "大阪府B"),
        Customer("A", "山田", "大阪府A"),
        Customer("山田01", "別会社", "大阪府C"),
        Customer("山田", "別会社", "大阪府D"),
        Customer("C", "山田", "大阪府E"),
        Customer("0003", "ＡＢＣ　 株式会社", "東京都F"),
    ])
    assert [r.customer.code for r in repository.search("山田")] == ["山田", "山田01", "A", "C", "B", "X山田"]
    assert repository.search("ABC  株式会社")[0].customer.code == "0003"
    assert not repository.search(" ")
    assert not repository.search("なし")


def test_bom_and_leading_zero(tmp_path):
    path = tmp_path / "customers.csv"
    path.write_text("顧客コード,顧客名,住所\n001,同じ社名,住所A\n002,同じ社名,住所B\n", encoding="utf-8-sig")
    rows = CustomerRepository.from_csv(path).search("同じ")
    assert [r.customer.code for r in rows] == ["001", "002"]
    assert "住所A" in rows[0].display


@pytest.mark.parametrize("contents", [
    "顧客コード,顧客名\n001,社名\n",
    "顧客コード,顧客名,住所\n001,,住所\n",
    "顧客コード,顧客名,住所\n001,社名,住所\n001,社名2,住所2\n",
    "顧客コード,顧客名,住所\n００１,社名,住所\n001,社名2,住所2\n",
    "顧客コード,顧客名,住所\n001,社名,住所,余分\n",
    "顧客コード,顧客名,住所\n001,社名\n",
])
def test_bad_csv(tmp_path, contents):
    path = tmp_path / "bad.csv"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(CustomerDataError):
        CustomerRepository.from_csv(path)


def test_shared_fixture_yamada():
    from pathlib import Path
    rows = CustomerRepository.from_csv(Path(__file__).parent / "fixtures/customers.csv").search("山田")
    assert len(rows) == 3
    assert {r.customer.code for r in rows} == {"C001", "C002", "0003"}
