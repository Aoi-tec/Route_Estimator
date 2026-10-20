"""住所がGeoloniaで見つからない理由を段階ごとに表示する（通信は公開住所データのみ・APIキー不要）。

使い方（リポジトリ直下で）:
  .venv/bin/python scripts/diagnose_address.py "大阪府八尾市太田新町6丁目98"
"""
from pathlib import Path
import csv
import io
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import httpx  # noqa: E402
from route_estimator.config import GEOLONIA_BASE  # noqa: E402
from route_estimator.geocoding.geolonia_v2 import normalize_address, _number_tail  # noqa: E402


def get_json(client, url):
    r = client.get(url); r.raise_for_status(); return r.json()


def main():
    if len(sys.argv) != 2:
        print(__doc__); return 2
    address = normalize_address(sys.argv[1])
    print('正規化後:', address)
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        master = get_json(client, GEOLONIA_BASE + '.json')
        pref = next((p for p in master['data'] if address.startswith(p['pref'])), None)
        if not pref:
            print('× 都道府県が一致しません'); return 1
        rest = address[len(pref['pref']):]
        cities = []
        for c in pref['cities']:
            canonical = c.get('county', '') + c['city'] + c.get('ward', '')
            for alias in {canonical, c['city'] + c.get('ward', ''), c.get('ward') or canonical}:
                if rest.startswith(alias): cities.append((len(alias), canonical, rest[len(alias):]))
        if not cities:
            print('× 市区町村が一致しません'); return 1
        _, city, rest = max(cities)
        print(f'○ 市区町村: {pref["pref"]}{city} / 残り「{rest}」')
        city_url = GEOLONIA_BASE + '/' + quote(pref['pref'], safe='') + '/' + quote(city, safe='')
        towns = get_json(client, city_url + '.json')['data']
        print(f'  町字データ {len(towns)}件')
        head = rest[:2]
        similar = sorted({''.join(t.get(k, '') for k in ('oaza_cho', 'chome', 'koaza'))
                          for t in towns if t.get('oaza_cho', '').startswith(head)})
        print(f'  「{head}」で始まる町字:', ' / '.join(similar) if similar else 'なし')
        hits = []
        for t in towns:
            name = ''.join(t.get(k, '') for k in ('oaza_cho', 'chome', 'koaza'))
            n = normalize_address(name)
            for alias in {n, n.replace('丁目', '-')}:
                if alias and rest.startswith(alias): hits.append((len(alias), name, t, rest[len(alias):].lstrip()))
        if not hits:
            print('× 町字が一致しません（上の一覧と表記を比べてください）'); return 1
        best = max(h[0] for h in hits)
        hits = [h for h in hits if h[0] == best]
        for _, name, t, tail in hits:
            print(f'○ 町字: {name} / 住居表示={t.get("rsdt")} / 代表点={t.get("point")} / 残り「{tail}」')
            print('  番地データ:', {k: v.get('length') for k, v in t.get('csv_ranges', {}).items()} or 'なし')
            kind = '住居表示' if t.get('rsdt') is True else '地番'
            span = t.get('csv_ranges', {}).get(kind)
            if not span:
                print(f'× {kind}の番地データがありません → 町字の代表点までしか分かりません'); continue
            r = client.get(city_url + '-' + quote(kind, safe='') + '.txt',
                           headers={'Range': f'bytes={span["start"]}-{span["start"] + span["length"] - 1}', 'Accept-Encoding': 'identity'})
            rows = list(csv.reader(io.StringIO(r.content.decode('utf-8'))))[2:]
            numbers = ['-'.join(x for x in row[:3] if x) for row in rows]
            with_coord = sum(1 for row in rows if row[3] and row[4])
            print(f'  {kind}データ {len(rows)}件（座標あり {with_coord}件）')
            first = sorted({n.split('-')[0] for n in numbers}, key=lambda v: (len(v), v))
            print('  収録されている先頭番号:', ' '.join(first[:60]), '…' if len(first) > 60 else '')
            want = _number_tail(tail)
            match = [(n, row) for n, row in zip(numbers, rows) if want == n or want.startswith(n + '-') or want.startswith(n)]
            print(f'  入力の番号「{want}」に前方一致:', [(n, bool(row[3] and row[4])) for n, row in match[:10]] or 'なし')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
