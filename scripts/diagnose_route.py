"""Geoapify経路応答の道路詳細(steps)を診断する。APIを1回呼ぶ。キーは表示しない。

使い方（リポジトリ直下で）:
  .venv/bin/python scripts/diagnose_route.py 34.5853 135.6838 34.2993 133.9556
  （出発緯度 出発経度 到着緯度 到着経度）
"""
from collections import Counter
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import httpx  # noqa: E402
from urllib.parse import urlencode  # noqa: E402
from route_estimator.config import load_config  # noqa: E402
from route_estimator.credentials import resolve_api_key  # noqa: E402
from route_estimator.routing.highway_details import extract_highway_details  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    for name in ('from_lat', 'from_lon', 'to_lat', 'to_lon'):
        ap.add_argument(name, type=float)
    ap.add_argument('--config', type=Path, default=ROOT / 'config.json')
    a = ap.parse_args()
    s = load_config(a.config)
    key = resolve_api_key(s.routing.api_key_env, s.routing.api_key_file, s.root)
    if not key:
        print('APIキーが見つかりません'); return 2
    params = {'waypoints': f'{a.from_lat},{a.from_lon}|{a.to_lat},{a.to_lon}', 'mode': 'drive', 'type': 'balanced',
              'traffic': 'free_flow', 'units': 'metric', 'format': 'geojson',
              'details': 'instruction_details,route_details', 'lang': 'ja', 'apiKey': key}
    r = httpx.get('https://api.geoapify.com/v1/routing?' + urlencode(params), timeout=30)
    print('HTTP', r.status_code)
    if r.status_code != 200:
        return 2
    f = r.json()['features'][0]; p = f['properties']
    lines = f['geometry']['coordinates']
    coords = [[pt[1], pt[0]] for pt in lines[0]] if len(lines) == 1 else []
    steps = (p.get('legs') or [{}])[0].get('steps') or []
    print(f"距離 {p.get('distance')} m / 時間 {p.get('time')} s / toll {p.get('toll')}")
    print(f'geometry線数 {len(lines)} / 座標 {sum(len(x) for x in lines)}点 / steps {len(steps)}')
    print('stepの項目:', sorted({k for st in steps for k in st}))
    print('tollなしstep数:', sum('toll' not in st for st in steps), '/ road_classなし:', sum('road_class' not in st for st in steps))
    print('road_class内訳:', dict(Counter(st.get('road_class') for st in steps)))
    print('toll=true step数:', sum(st.get('toll') is True for st in steps))
    info = extract_highway_details(p, coords)
    print('判定:', info['status'], info.get('reason', ''))
    for sec in info.get('sections', []):
        print(f"  区間{sec['number']}: {' / '.join(sec['roads'][:6])}")
    out = ROOT / 'state' / 'route_diagnose.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({'properties': {k: v for k, v in p.items() if k != 'legs'},
                               'steps': [{k: v for k, v in st.items() if k != 'instruction'} for st in steps],
                               'coordinates': coords},
                              ensure_ascii=False, indent=1), encoding='utf-8')
    print('詳細:', out.relative_to(ROOT), '（Git対象外）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
