from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3

from ..errors import CacheError, ValidationError
from ..models import Coordinates, GeoResult, Precision


class GeocodeCache:
    def __init__(self, path: str | Path, *, ttl_days: int = 30):
        self.path = Path(path)
        self.ttl = timedelta(days=ttl_days)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as db:
                db.execute("""CREATE TABLE IF NOT EXISTS geocodes (
                    address_key TEXT PRIMARY KEY, original_address TEXT NOT NULL,
                    matched_address TEXT NOT NULL, provider TEXT NOT NULL, dataset_version TEXT NOT NULL,
                    latitude REAL NOT NULL, longitude REAL NOT NULL, precision TEXT NOT NULL,
                    verified_by_user INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL)""")
                db.execute("CREATE TABLE IF NOT EXISTS resources (url TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
        except OSError as exc:
            raise CacheError("キャッシュの保存先を作成できません") from exc

    @contextmanager
    def _connect(self):
        try:
            with sqlite3.connect(self.path, timeout=5) as db:
                yield db
        except sqlite3.Error as exc:
            raise CacheError("SQLiteキャッシュを読書きできません") from exc

    def _fresh(self, timestamp: str) -> bool:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)
        return timedelta(0) <= age < self.ttl

    def get(self, key: str) -> GeoResult | None:
        with self._connect() as db:
            row = db.execute("SELECT payload,updated_at FROM geocodes WHERE address_key=?", (key,)).fetchone()
        if not row:
            return None
        try:
            if not self._fresh(row[1]): return None
            value = json.loads(row[0])
            value["coordinates"] = Coordinates(**value["coordinates"])
            value["precision"] = Precision(value["precision"])
            return GeoResult(**value)
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            raise CacheError("住所キャッシュが破損しています。バックアップ後に削除してください") from exc

    def put(self, key: str, original_address: str, result: GeoResult):
        if result.precision != Precision.EXACT_ADDRESS or result.coordinates is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        c = result.coordinates
        with self._connect() as db:
            db.execute("""INSERT INTO geocodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(address_key) DO UPDATE SET original_address=excluded.original_address,
                matched_address=excluded.matched_address,provider=excluded.provider,dataset_version=excluded.dataset_version,
                latitude=excluded.latitude,longitude=excluded.longitude,precision=excluded.precision,
                verified_by_user=excluded.verified_by_user,updated_at=excluded.updated_at,payload=excluded.payload""",
                (key, original_address, result.matched_address, result.provider, result.dataset_version,
                 c.latitude, c.longitude, result.precision, result.verified_by_user, now, now, json.dumps(asdict(result))))

    def get_resource(self, url: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT payload,updated_at FROM resources WHERE url=?", (url,)).fetchone()
        if not row: return None
        try:
            return json.loads(row[0]) if self._fresh(row[1]) else None
        except (ValueError, TypeError) as exc:
            raise CacheError("マスターキャッシュが破損しています") from exc

    def put_resource(self, url: str, value: dict):
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO resources VALUES (?,?,?)", (url, json.dumps(value), datetime.now(timezone.utc).isoformat()))
