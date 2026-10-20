# 全国OSRMの導入・運用

一式の手順と設定項目は [SETUP.md](../SETUP.md) を参照。

## 入口

Windowsはプロジェクトルートの`windows\prepare.bat`で環境/原本/イメージの準備、`windows\setup.bat`で全国の前処理と起動まで行う。Macでは`./mac/prepare.command` / `./mac/setup.command`。`start`、`stop`、`status`、`doctor`、`build`も用意した。

共通処理は標準ライブラリだけの`deploy/setup.py`。Python仮想環境と依存を自動作成し、既存設定・顧客CSVを上書きしない。調整は`deploy/setup.json`（配布ひな形は`setup.example.json`）。

```sh
# Mac。このMacではメモリ割当のため準備までを実施済み。
./mac/prepare.command
./mac/status.command
./mac/doctor.command
# Dockerのメモリ/容量を調整した環境で実行
./mac/setup.command
./mac/start.command --demo
./mac/stop.command
```

## Docker構成

`compose.yaml`は全国用。OSRMイメージはダイジェスト固定。配信は127.0.0.1だけ、地図volumeは読み取り専用、経路アクセスログはWARNINGへ抑制する。前処理は別の一時コンテナでcar.luaによるMLDのextract→partition→customize。

共通処理が`state/setup/runtime.env`を生成し、原本/イメージに対応する固有volumeとリソース設定をComposeへ渡す。全国原本のSHAに対応する完成volumeがない段階で、Composeだけを直接起動しない。

前処理上限24GiB+予備2GiB、配信上限12GiB+予備2GiB、空き80GiBを初期ガードとした。全国の必要量を実測した値ではなく、設定で調整する。ホストとDocker仮想ディスクの空きは別に確認する。約8GiB割当のこのMacでは全国前処理を実施していない。

## 再取得・再構築・復帰

`download --refresh`で新しい日付付きPBFを取得する。MD5照合後、SHA-256・固定URL・バイト数を記録。新しい原本は別構築ID/volumeになり、旧配信を保持する。

`build`はチェックポイントから再開し、別ポートで複数地域の経路を検証する。`start`で候補を再確認後に切替し、起動後に失敗した場合は保存済み設定へ戻す。旧原本/volumeの自動削除は行わない。ログは`state/setup/logs/`、記録は`state/setup/`。

既知の従来近畿構成は`compose.kansai.yaml`へ保存した。全国準備中も既存の近畿コンテナを維持している。再起動するなら次を使う。

```sh
docker compose -f deploy/compose.kansai.yaml up -d
.venv/bin/python deploy/check_ready.py
```

## キャッシュ・バックアップ

道路PBFと住所キャッシュは独立。住所の再取得が必要ならアプリ停止後に該当SQLiteを退避する。TTLや取得日時で原本の鮮度を保証しない。

config/顧客CSV/SQLiteをアクセス制限された保管先へバックアップする。原本PBF・取得/構築記録・イメージのダイジェストを保持する。停止操作は原本やvolumeを削除しない。

最新ABR住所データの取得・不変release・切替は [別の設計](../docs/AddressDataUpdateDesign.md) であり、今回未実装。道路を全国へ変えても住所データが最新になるわけではない。

## 出典

道路：© [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)、ODbL 1.0。配布：[Geofabrik Japan](https://download.geofabrik.de/asia/japan.html)。ルーティング：[OSRM](https://github.com/Project-OSRM/osrm-backend)、BSD 2-Clause。

現行住所：[Geolonia日本住所データv2](https://github.com/geolonia/japanese-addresses-v2)、CC BY 4.0、原典：デジタル庁アドレス・ベース・レジストリ。クライアントは限定的な表記正規化と一意照合を行う。継続商用運用の提供方式と原本ごとの条件は最新住所対応設計で扱う。
