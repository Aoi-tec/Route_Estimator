import codecs
import csv
import io
from dataclasses import dataclass
from pathlib import Path
import unicodedata

from .errors import CustomerDataError
from .models import Customer


REQUIRED_COLUMNS = ("customer_code", "customer_name", "address")


def _decode(data: bytes) -> str:
    """UTF-8（BOM有無）を標準とし、BOM付きUTF-16（Excelの「Unicodeテキスト」）も受け付ける。"""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig")
    return text.replace("\r\n", "\n").replace("\r", "\n")


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
            text = _decode(Path(path).read_bytes())
            first_line = text.split("\n", 1)[0]
            delimiter = "\t" if "\t" in first_line and "," not in first_line else ","
            reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
            headers = [h.strip().lower() for h in next(reader, [])]
            if not set(REQUIRED_COLUMNS).issubset(headers) or len(headers) != len(set(headers)):
                raise CustomerDataError("CSVの1行目に重複のない必須列（" + ",".join(REQUIRED_COLUMNS) + "）が必要です")
            index = {name: headers.index(name) for name in REQUIRED_COLUMNS}
            for line, row in enumerate(reader, 2):
                if not any(cell.strip() for cell in row):
                    continue  # 空行は無視
                if len(row) != len(headers) or any(not row[i].strip() for i in index.values()):
                    raise CustomerDataError(f"CSV {line}行目に空欄または列数の不整合があります")
                code = row[index["customer_code"]]
                key = normalize_search(code)
                if key in codes:
                    raise CustomerDataError(f"CSV {line}行目のcustomer_codeが正規化後に重複しています")
                codes.add(key)
                customers.append(Customer(code, row[index["customer_name"]], row[index["address"]]))
        except (OSError, UnicodeError, csv.Error) as exc:
            raise CustomerDataError("顧客CSVを読めません。パス・文字コード（UTF-8推奨）・CSV形式を確認してください") from exc
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
