# NEXCO ETC2.0 料金・経路取得モジュール — Architecture v3

**設計日:** 2026-10-20  
**ステータス:** 設計確定案／Web取得・抽出コードの実機動作検証は未完了  
**対象:** NEXCO西日本の全国向け「高速料金・ルート検索」を参照する、Pythonの独立モジュール

## 1. 目的と設計原則

- `NexcoTollService.search(from_ic, to_ic)` の**単一の外部インターフェース**で、名称検索、ICコードの解決、検索、ETC2.0料金および各経路の通過地点の取得までを完結させる。
- 料金は**ETC2.0だけ**を外部返却する。現金／通常ETC料金は返却しないし、代用もしない。
- NEXCO検索で返る**最大3つのルート**を、公式サイトの表示順と対応を保って返す。1〜2経路しかない場合は存在する経路だけ返す。3経路を架空に生成しない。
- 各経路の「出発IC → JCT／分岐／料金所／IC → 到着IC」を**公式ルート詳細に掲載された順番**で返す。道路名・区間距離・区間所要時間も取得できる範囲で保持する。
- **「表示されているすべての通過地点」と「物理的に通過するすべてのIC」は違う。** NEXCO詳細が省略した中間ICまで完全列挙できると宣言しない。必要なら別途、道路ネットワークとの照合で拡張する。
- ICマスターは従来どおり**公式JS原本ファイルを保存**し、実行時にメモリへ読み取る。SQLiteへのコピーは作らない。
- OSRM・住所検索・顧客CSV・CUI・人件費／燃料計算とは独立させる。

## 2. 参照情報と検証範囲

- IC原本：NEXCOサイトが配信する `route_master_v155.js?20261014` 型のファイル。アップロードされた v155 には `icM`（2,290レコード）・`rdM`（266）・`preM`（47）・`regM`（10）が存在する。
- `icM[i][2]` が `route.php` の `fcd` / `tcd` に用いる7桁検索コード。`icM[i][4]` は **`rdM` の配列インデックス**。
- 料金検索のURL形式（確認済み）：`https://search.w-nexco.co.jp/route.php?cartyp=2&fcd=1302420&tcd=1030071`。`cartyp=2` は例示した**普通車**用。
- 公開検索結果の検索エンジン経由の表示から、ルート1〜3のETC2.0料金、距離、所要時間、路線系列、各ルート詳細の中間地点・JCT・区間距離が含まれることを確認。
- **ただし** Python `requests` で返る生HTMLに詳細が含まれるか、動的通信か、DOM上のセレクタが何かは未検証。確定するまでは `requests` のみで稼働すると断言しない。
- NEXCOの自動・継続取得の業務利用許諾は未確認。利用条件・アクセス制限を運用前に確認する。

## 3. ファイル構成

```text
nexco_toll/
├── __init__.py            # 公開API（TollService、モデル、例外）
├── service.py             # IC解決 → 検索 → 結果統合
├── master.py              # 原本JS読込・IC名検索（v2方針継続）
├── updater.py             # 原本JSダウンロード・原子的入替・復旧
├── scraper.py             # 検索URL生成、HTTP取得（必要時にブラウザ取得へ交換）
├── parser.py              # 3経路の概要と詳細の紐付け・ETC2.0抽出
├── models.py              # 型とエラー定義
└── data/
    ├── current.js         # 公式JSを改変せず保存
    ├── previous.js
    └── source.json        # 原本URL・バージョン・SHA256・更新日時
```

`nexco_toll` は**1つのPythonパッケージ（1モジュールとして利用可能）**。内部を小さなファイルに分けることで将来の画面変更への影響を `scraper.py`／`parser.py` に限定する。

## 4. モジュール境界

| 構成要素 | 入力 | 出力 | 責務 |
|---|---|---|---|
| `master.py` | 公式JS、IC名またはコード | 候補／確定IC | 同名ICの列挙、読みによる部分一致、県・道路の判別 |
| `updater.py` | 公式検索ページ、JS URLまたはローカル原本 | 検証済み `current.js` | バージョン・ハッシュ検証、バックアップ |
| `scraper.py` | `from_code`, `to_code`, `vehicle_type` | HTMLまたはHTML相当の取得結果 | 対象ドメイン限定アクセス、低頻度、タイムアウト |
| `parser.py` | 取得した検索結果 | `RouteOption[]` | ルート別ETC2.0、経路詳細、検証 |
| `service.py` | IC名／ICコード・車種 | `TollSearchResult` | 一連の処理を1呼び出しで提供 |

## 5. 公開API

```python
from nexco_toll import TollService

service = TollService()

result = service.search(
    from_ic="大分宮河内",
    to_ic="太宰府",
    vehicle_type="普通車",  # 初期対応の既定値
)

for route in result.routes:
    print(f"ルート{route.number}: ETC2.0 {route.etc2_yen:,}円")
    print(f"{route.distance_km}km / {route.duration_minutes}分")
    print(" → ".join(stop.name for stop in route.stops))
```

- `from_ic`／`to_ic` はIC名（完全一致）、または7桁コードを受ける。曖昧な名前は**候補を返す例外**とし、先頭候補を黙って選ばない。
- 将来の任意条件：`departure_at`、`via_ic`、`exclude_road` など。ただしNEXCOサイトへの正式な渡し方を検証するまで**未実装**とする。
- 返却する3ルートは公式表示順を基本とし、勝手にETC2.0料金順へ並べ替えない。必要なら呼出元で並べ替える。

## 6. データモデル

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

@dataclass(frozen=True)
class RouteStop:
    name: str                       # 日出JCT、鳥栖JCTなど
    kind: Literal["IC", "JCT", "BRANCH", "TOLL", "OTHER", "UNKNOWN"]
    arrival_time: str | None = None # 例: "10:02" (サイトにあれば)

@dataclass(frozen=True)
class RouteSegment:
    road_name: str                   # E34 大分道など
    distance_km: float | None
    duration_minutes: int | None
    from_stop_index: int
    to_stop_index: int

@dataclass(frozen=True)
class RouteOption:
    number: int                      # 1〜3。実在するルート番号だけ
    etc2_yen: int | None             # 取得不可・対象外ならNone（0円にしない）
    distance_km: float | None
    duration_minutes: int | None
    roads: list[str]                # 経由道路の表示順
    stops: list[RouteStop]          # NEXCO詳細に載る通過地点の表示順
    segments: list[RouteSegment]    # 各区間の道路、距離、時間
    stops_complete: bool            # 公式詳細を最後まで読み取れたか

@dataclass(frozen=True)
class TollSearchResult:
    from_ic_name: str
    from_ic_code: str
    to_ic_name: str
    to_ic_code: str
    vehicle_type: str
    routes: list[RouteOption]       # 最大3件
    retrieved_at: datetime
    searched_departure_at: str | None
    source_url: str
    master_version: str
```

ETC2.0の追加割引（休日、深夜等）を必要とする場合は、**ETC2.0欄に掲載された割引内訳だけ**を別フィールドで拡張する。割引条件を勝手に適用したり、区間料金の単純合計をルート全体のETC2.0料金とみなしたりしない。

## 7. ルート詳細の取得方針

1. 公式ルート結果の**概要表**からルート番号ごとのETC2.0全体料金、総距離、所要時間、経由道路の配列を取得。
2. 概要と別に表示される**「ルート1」「ルート2」「ルート3」の各詳細領域**を特定する。ルート番号をキーに紐付ける。
3. 詳細領域内を表示順に解析し、**地点（IC/JCT/分岐等）→道路名・区間距離・区間時間→次地点**の交互関係を保持。
4. 詳細に混在するSA/PA案内、ETC料金一覧、広告、路線注意事項は地点リストへ混入させない。
5. 正常な場合は最初の地点が出発IC、最後が到着IC。見つからない、順序がおかしい、詳細ブロックがない場合は `stops_complete=False` とし、一切でっち上げない。
6. ページ構造が変わっても、`parser.py` のテスト用保存HTMLで差分を確認できるようにする。
7. **通過する全ICの厳密な列挙は対象外**。これは検索結果詳細に省略があり、ICマスターだけでは中間通過ICの道路接続順が分からないため。

## 8. 確認済み実例（2026-10-16に取得された検索結果より）

`大分宮河内 → 太宰府`、普通車、ETC2.0価格（検索時の参考値）

| ルート | ETC2.0料金 | 総距離 | 所要時間 | 公式詳細に掲載された地点・経由道路の例 |
|---|---:|---:|---|---|
| 1 | 4,020円 | 156.7km | 1時間52分 | 大分宮河内 → 日出JCT → 鳥栖JCT → 太宰府 |
| 2 | 4,020円 | 185.2km | 2時間23分 | 大分宮河内 → 速見 → 宇佐 → 椎田南 → みやこ豊津 → 北九州JCT → 太宰府 |
| 3 | 5,880円 | 197.8km | 2時間37分 | 東九州道 → 九州道 → 北九州高速1号線 → 北九州高速4号線 → 九州道（地点一覧は未抽出） |

出典：`https://search.w-nexco.co.jp/route.php?cartyp=2&fcd=1302420&tcd=1030071` の公開検索結果。現時点で実際に `requests` から抽出した結果ではない。

## 9. エラー設計

- `AmbiguousICError`: 名前が一意でない。候補に県・道路・ICコードを付ける。
- `ICNotFoundError`: 公式JSに見つからない。
- `FetchError`: タイムアウト、アクセス失敗、サイト負荷制限等。
- `ParseError`: 料金または結果構造が変化した。誤った0円を返さない。
- `NoRouteError`: サイトが経路なしを返した。
- `IncompleteRouteDetail`: 料金概要があるがルート通過地点が途中までしか取れない（呼び出し元は不完全フラグを判定）。
- 件数0の有効な結果、通信失敗、HTML構造変更を混同しない。

## 10. キャッシュ・更新・利用条件

- 公式JSは更新確認時だけ取得し、`current.js` をアプリケーションで再利用する。既存のv2設計を継承。
- **料金検索**はICペア・車種・実際に検索で指定した日時をキャッシュキーに含める（日時未対応ならページ既定条件も記録）。価格が古くならないよう短期間TTLに限定し、取得日時と検索結果URLを必ず保存する。
- API連打は避け、低頻度アクセス、タイムアウト、リトライ間隔、`robots.txt` と利用条件の確認を実施する。
- 無断の継続業務スクレイピングが利用許諾上問題ないかは未確定であり、導入前の確認事項とする。
- JS配列の `eval`・未検証JavaScript実行は行わない。

## 11. 受入条件・実装順

- [ ] v155原本からIC名→7桁検索コードを引ける。
- [ ] 同名ICの場合は候補を返し、誤選択しない。
- [ ] まず1件のHTML取得を実機で確認（`requests` で不十分なら動的通信を調査）。
- [ ] 保存した実ページのフィクスチャから **ルート1〜3のETC2.0料金**を正確に抽出できる。
- [ ] ルート1〜3の区間名と経路途中のJCT／ICが別ルートへ混入しない。
- [ ] 道路・SA・PAの記述を通過IC/JCTと取り違えない。
- [ ] `stops[0]` と `stops[-1]` が出発・到着地点であることを検証する。
- [ ] 料金0円と取得失敗を区別できる。
- [ ] 検索結果に1または2ルートしかなければ、その件数で返す。
- [ ] アプリ側からは `TollService.search(...)` 以外の内部構成を意識せずに利用できる。
- [ ] サイト利用条件を確認してから定期的な業務運用に移す。

## 12. 明示的な対象外

- OSRMから乗り降りICを自動的に判定する処理。
- IC・JCTの地理座標やナビ向けの道路形状（polyline）の再現。
- NEXCO詳細に表示されない**すべての途中IC**の推測列挙。
- 人件費、燃料費、見積総額の計算。これらは外側の `pricing` が担当。
