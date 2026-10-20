"""日本時間の出発条件。料金検索への送信は料金アダプター側の責務。"""
from datetime import datetime, time, timedelta, timezone
import jpholiday

JST = timezone(timedelta(hours=9), "JST")


def default_departure(now: datetime | None = None) -> datetime:
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("nowはタイムゾーン付きで指定してください")
    today = (now or datetime.now(JST)).astimezone(JST).date()
    candidate = today + timedelta(days=1)
    while candidate.weekday() >= 5 or jpholiday.is_holiday(candidate):
        candidate += timedelta(days=1)
    return datetime.combine(candidate, time(9), tzinfo=JST)


def departure_label(value: datetime) -> str:
    local = value.astimezone(JST)
    weekday = "月火水木金土日"[local.weekday()]
    return f"{local:%Y-%m-%d}（{weekday}）{local:%H:%M} 日本時間"
