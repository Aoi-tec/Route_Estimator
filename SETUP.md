# Geoapify API版 セットアップ・操作手順

IC自動候補・ETC2.0参考額の取得を追加しました。経路計算後に入口/出口候補を表示し、確認してNEXCOの候補を取得できます。[使い方と検証範囲](docs/IC_ETC_Implementation.md)。料金は合計未反映です。

2026-10-19。通常構成は全国経路API方式。Dockerや地図ダウンロード・前処理は不要です。住所をGeoloniaで座標に変換し、Geoapifyから距離・所要時間を取得して、人件費と燃料使用量を計算します。

## 1. 初回導入

Windowsは`windows\setup.bat`、Macは`mac/setup.command`を実行します。Python 3.12以上と`.venv`・依存パッケージを準備します。WindowsでPythonがなければwingetで導入を試みます。MacでPythonがない場合は https://www.python.org/downloads/ から導入してください。既存のconfig/顧客CSVを勝手に上書きしません。新規配布のconfig.example.jsonはGeoapify方式です。

旧configを使う場合は`routing`をconfig.example.jsonから追加し、`provider`を`geoapify`にしてください。routing省略の旧configはOSRM方式として動きます。

## 2. 無料APIキーの作成と登録

1. https://myprojects.geoapify.com/ で本人のアカウントを登録します。登録時の規約を本人が確認します。
2. FreeプランでProjectを作り、API Keysでキーを取得します。無料枠は https://www.geoapify.com/pricing/ を参照してください。アプリからプラン変更はしません。
3. Windowsは`windows\configure_api_key.bat`、Macは`mac/configure_api_key.command`を実行します。
4. キーを端末の入力欄へ貼り付けてEnterを押します。文字は表示されません。チャットやconfig.jsonにはキーを貼り付けません。

キーはリポジトリ直下の`.env`（Git対象外）に`GEOAPIFY_API_KEY=...`の形で保存します。`.env`の他の行は保持します。旧版の`state/geoapify_api_key.txt`は`.env`が無い場合のみ読み込みます。Mac/Linuxではファイルを0600で作成します。Windowsではユーザー用フォルダのアクセス権を引き継ぎます。別PCへの配布には含めず、そのPCで登録します。GEOAPIFY_API_KEY環境変数があればファイルより優先されます。キーをコマンド引数へ指定する機能はありません。

キーは設定JSONやZIP、アプリの通常ログへ出力しません。HTTPのリダイレクトへは追従せず、接続先は公式HTTPSに固定しています。

## 3. 設定確認・起動

Windowsは`windows\doctor.bat`、Macは`mac/doctor.command`で設定とキー読込を確認できます。これは外部APIへ送信せず、キーの有効性をAPI側で確認する検査ではありません。

通常起動は`windows\start.bat` / `mac/start.command`です。公開地点のデモを使う場合は、端末から次のように起動します。

```sh
./mac/start.command --demo
```

```bat
windows\start.bat --demo
```

公開デモは大阪市役所→大阪市中央公会堂。顧客検索・From/To修正・人数指定を行い、確認後に計算します。デモでも本人のAPIキーが必要です。初回起動時のconfig.jsonとdata/customers.csvは架空サンプルなので、通常利用前に自社住所・単価・顧客情報を設定してください。

## 4. 算出条件と出典

- 自動車・Geoapify標準balanced（距離/時間等のバランス型）・free_flow（渋滞なし）・メートル/秒。最速経路と同じとは限りません。
- 返された経路形状の始点・終点と入力座標の距離が200m以下か確認します（丁目・町字の代表点で計算する端点は1,000m以下）。この検査は住所の正確さを保証するものではありません。住所は番地・号まで一致する座標を優先し、見つからない場合は丁目・町字の代表点にフォールバックします（`geocoding.allow_approximate: false`で無効化）。
- 人件費は所要秒÷3600×人数×時間単価。燃料使用量は距離km÷燃費×台数。燃料単価が未設定なら燃料費/総額は未確定です。
- リアルタイム渋滞・通行料金・現場作業・往復・税は含みません。フェリーを含む経路もあり得ます。
- 結果画面と返却データにGeoapify/OpenStreetMapの出典を含めます。上位のWeb等へ組み込む場合も出典とリンクを引き継いでください。

APIへ送信するのは出発地・目的地の座標と経路条件です。顧客名・コード・人数・単価は送りません。住所解決のためGeoloniaの公開住所データへもアクセスします。

## 5. 無料枠とエラー

確認時点ではGeoapify Freeは3,000 credits/日。2地点間の通常の短距離経路は基本1 creditですが、500kmを超える経路などは消費量が増えます。日0〜1件の今回の想定には余裕がありますが、実際の上限/利用条件は提供元の最新情報を確認してください。

アプリは失敗時に自動再試行せず、別APIや直線距離への自動切替もしません。キー未登録・401/403・429・通信失敗・不正な数値・経路形状不足・端点ずれでは見積金額を確定しません。429の場合は提供元の利用状況を確認し、時間を置いて再実行します。キー変更はconfigure_api_keyを再実行してください。経路キャッシュは今回追加していません。

## 6. Pythonから呼び出す

公開関数`estimate_trip(request, *, settings, geocoder, router)`を維持しています。`open_adapters(settings)`がrouting.providerに応じたアダプターを生成します。返却値の`routing_metadata`に提供元・経路条件・出典が入ります。

## 7. 不要ファイルの整理

2026-10-20、API方式で使用しない全国/近畿道路PBFとその取得情報、過去の実行ログ・試算JSON・メモリ測定ファイル、旧OSRMのbuild/download/start_serverランチャーとデモ設定を削除しました。API用の設定・キー・顧客CSV・IC原本/座標・仮想環境は維持しています。

mac/setup.command/start.commandは引き続きdeploy/setup.pyを使うため、deploy/setup.jsonとsetup.example.jsonは必要です。削除しないでください。Dockerコンテナ・volumeはこの整理では変更していません。旧OSRM互換コードと試験は保持していますが、地図の再取得・構築は別途準備が必要です。


## 8. 検証境界

実装はHTTPのダミー応答で正常・異常系を確認しています。Geoapify本人キーは未登録のため、日本の実地点での実疎通・距離/時間はまだ未検証です。登録後、大阪・東京・札幌・那覇と東京→大阪で確認してください。Windows BATはMac上で内容/改行を確認した範囲で、Windows実機では未検証です。全国ABR住所更新機能は従来どおり設計のみです。

### 本人キー登録後の全国API確認（任意・初回のみ）

```sh
.venv/bin/python verify_api.py
```

Windowsでは`.venv\Scripts\python.exe verify_api.py`。大阪・東京・札幌・那覇と東京→大阪を最大5回のAPI呼出しで確認します。異常時はその地点で停止し、結果をstate/geoapify_smoke.jsonへ保存します。これらは公開地点の試験であり、自社/顧客住所の一致率を保証しません。


## 出発日時の入力（2026-10-20実装）

- 既定は日本時間の現在日付から明日以降の最初の営業日、09:00。土日・日本の国民の祝日、振替休日、祝日に挟まれた休日を除外する。会社独自の休業日は含めない。
- CUI最終確認の「2. 内容を修正 → 4. 出発日・時刻」からYYYY-MM-DD/HH:MMで変更可能。空Enterは現在の値を保持。手入力なら土日祝も選択できる。
- TripRequest/EstimateResult.departure_atへタイムゾーン付き日時を保持し、JSONではISO8601で返す。既存呼出しで省略した場合はNoneとし、CUI起動時だけ既定を設定する。
- NEXCOには入口到達予定日時を送信する。Geoapifyは引き続きfree_flowであり日時は送信しない。
- jpholiday==1.0.3を使用。2026/2027年の公式公表の範囲を確認。2028年以降は予測である旨をCUIに表示し、制度変更時は依存版・内閣府情報を再確認する。
- 12件の追加検証を含め172 passed。土日、祝日を含む連休、振替休日、国民の休日、年越し、UTC/JST境界、入力エラーと編集、JSON保持を確認。その後のIC・ETC2.0連携で実料金検索を検証済み。最新の検証はdocs/IC_ETC_Implementation.mdを参照。

参考：https://www8.cao.go.jp/chosei/shukujitsu/gaiyou.html / https://github.com/Lalcs/jpholiday
