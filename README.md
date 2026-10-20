# Route Estimator

住所間の片道ルートから、移動時間・距離・移動人件費・燃料費を試算するPython製CUIです。高速道路を使う経路では入口・出口ICの候補を表示し、NEXCO西日本の検索結果からETC2.0参考料金と通過IC/JCTを確認できます。

経路計算はGeoapifyを利用します。Dockerや全国道路データのダウンロード・前処理は不要です。

## 主な機能

- 自社・顧客CSVの検索・住所直接入力による出発地／到着地の指定。
- 番地・号まで照合した住所の座標を使い、距離と所要時間を取得。
- 人数・時間単価・燃費・車両台数・燃料単価に基づく片道移動費の試算。
- 国土交通省のIC座標データと経路・道路名を照合し、入口／出口ICの候補を表示。
- ICの確認・修正と車種指定後に、最大3経路のETC2.0参考額・通過IC/JCT・区間距離／時間を表示。
- 出発日は土日・日本の祝日を除く翌営業日、時刻は09:00（日本時間）を既定とし、日付・時刻を変更可能。

ETC2.0料金は1台あたりの参考額として別枠に表示します。Geoapify経路との一致・採用が未確定のため、移動費の合計には加算しません。通常ETCや現金料金での代用もしません。

## 動作環境

- Python 3.12以上
- macOS／Windows用の起動スクリプト
- インターネット接続と利用者自身のGeoapify APIキー

このMacでは実通信とCUIの一連の動作を確認済みです。Windows用BATは用意していますが、Windows実機での検証は未実施です。

## 起動する

通常はフォルダ内の **`start.command`** を実行してください。Windowsでは **`start.bat`** です。

端末から起動する場合：

```sh
cd Route_Estimator   # クローンしたフォルダ
./start.command
```

初めて導入するPC（`git clone`直後など）では、次の順に実行します。

| 操作 | macOS | Windows |
| --- | --- | --- |
| 仮想環境・依存パッケージの準備 | `setup.command` | `setup.bat` |
| APIキーの登録（非表示入力） | `configure_api_key.command` | `configure_api_key.bat` |
| 設定・キーの読込確認 | `doctor.command` | `doctor.bat` |
| 通常起動 | `start.command` | `start.bat` |

Geoapifyキーは [Geoapifyの管理画面](https://myprojects.geoapify.com/) で取得し、登録スクリプトへ入力します。キーはリポジトリ直下の `.env`（Git対象外）に `GEOAPIFY_API_KEY=...` の形で保存されます。手動で作る場合は `.env.example` を `.env` にコピーして値を入れてください。`config.json`・`.env.example`・Gitへキーを記載しないでください。`doctor`は外部APIへ送信しない読込確認です。

公開地点のデモは `./start.command --demo`（Windowsは `start.bat --demo`）で起動できます。デモでもGeoapifyキーが必要です。

## CUIの操作

1. 出発地・到着地と人数を指定します。
2. 最終確認で住所・人数・単価・出発日時を確認します。日時の変更は「内容を修正 → 出発日・時刻」から行えます。
3. 計算すると移動費の試算、距離・時間、高速道路とIC候補を表示します。
4. ETC2.0参考額が必要な区間で、IC候補を確認するか名称検索で修正し、車種を選択します。
5. NEXCOの経路候補・参考額・検索日時・出典を確認します。料金検索を保留して終了することもできます。

NEXCOの出発日時には、住所からの出発時刻に入口ICまでのGeoapify推定時間を加えた値を使います。手動でICを変更した場合は、元の経路接続点の予定時刻を使う参考検索であり、変更ICへの経路や時間は再計算しません。

## 設定・データ

| ファイル／フォルダ | 用途 |
| --- | --- |
| `config.json` | 使用中の自社住所・人件費単価・燃費・車両台数・燃料単価・API設定 |
| `config.example.json` | 新規導入用の設定ひな形 |
| `data/customers.csv` | 使用中の顧客データ。列は `顧客コード,顧客名,住所`（詳細は [data/README.md](data/README.md)） |
| `config.demo.json` / `data/customers.demo.csv` | 公開地点デモ用の設定・CSV |
| `.env` | 利用者のAPIキー（`GEOAPIFY_API_KEY=...`）。環境変数 `GEOAPIFY_API_KEY` があればそちらを優先。ひな形は `.env.example` |
| `state/` | 住所キャッシュ、IC原本・座標・版・ハッシュなどの実行データ |
| `deploy/setup.json` | 共通セットアップ処理の設定。API方式でも現在は読込が必要 |
| `.venv/` | このPC・配置先用の仮想環境 |

`.env`、`config.json`、`deploy/setup.json`、CSV全般（ひな形・デモ・テスト用を除く）、`state/`、`.venv/`はGit対象外です。ひな形は架空データなので、新規導入時には実際の設定へ変更してください。

旧版の `state/geoapify_api_key.txt` も読込可能です（`.env` が無い場合の互換用）。`configure_api_key` を再実行すると `.env` へ保存されます。

IC座標は国土数値情報の2025年現況データを初回に取得・保存します。NEXCOのIC名・コード原本も保存して再利用します。再取得する場合：

```sh
.venv/bin/python update_ic_data.py --force
```

国交省データの年度は2025年に固定しており、このコマンドで新年度へ自動更新はしません。

## 算出条件と制限

- 経路はGeoapifyの自動車・バランス型、渋滞なしの推定です。指定時刻の交通状況を反映した所要時間ではありません。
- 人件費は丸め前の秒数から計算します。燃料単価が未設定の場合、燃料費と合計額は未確定と表示します。
- 入口／出口ICは自動候補です。データの版・名称・出入口制限などにより、手動確認が必要な場合があります。
- NEXCOはIC間、Geoapifyは住所間の別の計算です。通過地点はNEXCO詳細に掲載された地点であり、物理的に通過するすべてのICを列挙するものではありません。
- NEXCO取得は公式ページが使う公開通信を参照しています。サイト構造変更・制限・通信失敗時は保留し、欠損料金を0円にはしません。継続業務利用の条件確認は別途必要です。
- 現場作業・税・往復費用は計算対象外です。最新ABR住所データへの更新は設計段階です。
- 2028年以降の祝日判定には予測が含まれます。

## 開発・検証

```sh
.venv/bin/python -m pytest -q
.venv/bin/python from_to.py --check
```

現行テストは200件です。IC座標の追加取得とNEXCO検索を省略する場合は、`from_to.py --no-highways`で起動できます。

Pythonからは `estimate_trip(request, *, settings, geocoder, router, highway_service=None)` を利用できます。IC候補も付加する場合は `HighwayService` を注入します。戻り値の `to_dict()` でJSON化できます。詳細は下記資料を参照してください。

## 関連資料

- [セットアップ・操作手順](SETUP.md)
- [IC・ETC2.0の実装仕様と検証範囲](docs/IC_ETC_Implementation.md)
- [Architecture](ai/Architecture.md)
- [実装規約](ai/Skills.MD)
- [タスク](ai/Task.MD)
- [NEXCOモジュールの基礎設計](docs/NEXCO_ETC20_Architecture_v3.md)
- [最新住所データへの更新設計](docs/AddressDataUpdateDesign.md)

## 出典

経路：[Geoapify](https://www.geoapify.com/) ／ [© OpenStreetMap contributors](https://www.openstreetmap.org/copyright)。住所：Geolonia日本住所データv2（CC BY 4.0、原典：デジタル庁）。IC座標：[国土数値情報「高速道路時系列・2025年現況」](https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N06-2025.html)（国土交通省）を加工して作成。ETC2.0参考料金・経路詳細：[NEXCO西日本](https://search.w-nexco.co.jp/)。
