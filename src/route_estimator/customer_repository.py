import csv
from dataclasses import dataclass
from pathlib import Path
import unicodedata

from .errors import CustomerDataError
from .models import Customer


def normalize_search(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


@dataclass(frozen=True)
class CustomerCandidate:
    customer: Customer

    @property
    def display(self) -> str:
        c = self.customer
        return f"{c.name}（{c.address}） [{c.code}]"


class CustomerRepository:
    def __init__(self, customers: list[Customer]):
        self._customers = tuple(customers)

    @classmethod
    def from_csv(cls, path: str | Path):
        customers, codes = [], set()
        try:
            with Path(path).open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, strict=True)
                headers = reader.fieldnames or []
                if not {"顧客コード", "顧客名", "住所"}.issubset(headers) or len(headers) != len(set(headers)):
                    raise CustomerDataError("CSVに重複のない必須列（顧客コード,顧客名,住所）が必要です")
                for line, row in enumerate(reader, 2):
                    if None in row or any(row.get(key) is None or not row[key].strip() for key in ("顧客コード", "顧客名", "住所")):
                        raise CustomerDataError(f"CSV {line}行目に空欄または列数の不整合があります")
                    code = row["顧客コード"]
                    key = normalize_search(code)
                    if key in codes:
                        raise CustomerDataError(f"CSV {line}行目の顧客コードが正規化後に重複しています")
                    codes.add(key)
                    customers.append(Customer(code, row["顧客名"], row["住所"]))
        except (OSError, UnicodeError, csv.Error) as exc:
            raise CustomerDataError("顧客CSVを読めません。パス・UTF-8文字コード・CSV形式を確認してください") from exc
        return cls(customers)

    def search(self, query: str) -> list[CustomerCandidate]:
        key = normalize_search(query)
        if not key:
            return []
        ranked = []
        for customer in self._customers:
            code, name = normalize_search(customer.code), normalize_search(customer.name)
            if code == key:
                rank = 0
            elif code.startswith(key):
                rank = 1
            elif name == key:
                rank = 2
            elif key in name:
                rank = 3
            elif key in code:
                rank = 4
            else:
                continue
            ranked.append((rank, customer.code, customer))
        return [CustomerCandidate(c) for _, _, c in sorted(ranked, key=lambda item: item[:2])]
