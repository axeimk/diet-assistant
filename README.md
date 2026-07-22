# Diet Assistant

SQLiteを正本に、食事・運動・体重・目標を長期間記録し、AIエージェントとCLIから傾向分析や助言を得る個人向けツールです。Webアプリではなく、ローカルでデータを管理したい1人の利用者を対象にしています。

画像認識API、医療診断、治療、厳密な栄養・代謝計算、複数ユーザー、複数端末からの同時更新は対象外です。詳細な判断と仮定は[設計概要](docs/design.md)を参照してください。

## 使い方の全体像

**基本はエージェント（Claude Code / Codex）に日本語で頼みます。** リポジトリ内でエージェントを起動し、「この写真を昼食として記録して」「今週どうだった？」のように話しかけてください。エージェントは`AGENTS.md`のルールに従って`diet`コマンドを組み立て、登録後に再取得して結果を報告します。

自分でコマンドを打つのは、セットアップ・バックアップ・写真削除など、エージェントに任せない管理作業だけです。CLIの全コマンドは[付録](#付録-cliリファレンス)にあります。

```
あなた ──依頼──> エージェント ──diet CLI──> SQLite (data/diet.db)
                                              └─> Markdownレポート (reports/)
```

## 前提

必須:

- **Python 3.14以上**
- **Claude Code または Codex**
- **macOS または Linux**

任意:

- **uv**（推奨）。標準の`venv`＋`pip`でも構いません。
- **iCloud Drive と iPhoneのショートカットApp**。[iPhoneから記録する](#iphoneから記録する)場合だけ必要です。

実行時の外部パッケージ依存はありません。DBはPython標準の`sqlite3`を使い、ネットワーク接続も不要です（エージェント自体の通信を除く）。開発用の`pytest`・`ruff`・`basedpyright`は`.[dev]`でまとめて入ります。

## セットアップ（初回だけ）

```bash
uv venv --python 3.14
source .venv/bin/activate
uv pip install -e '.[dev]'
diet init
diet doctor
```

`uv`を使わない場合は`python3.14 -m venv .venv`と`python -m pip install -e '.[dev]'`に読み替えてください。

続けてプロフィールを用意します。`config/profile.json`はGit管理されません。

```bash
cp config/profile.example.json config/profile.json
diet profile validate
```

`doctor`が`ok: true`を返せば完了です。

### プロフィールの項目

**必須の項目はありません。** ファイルが無くてもCLIは動きます（`doctor`が`profile_exists: false`と出るだけ）。書いた項目だけが検証されます。

| キー | 型・制約 | 使われ方 |
| --- | --- | --- |
| `photo_retention_days` | 1以上の整数 | `diet photo cleanup`の既定保持日数。**未設定なら30** |
| `height_cm` | 100〜250の数値 | 暫定維持カロリーの計算 |
| `birth_date` | `YYYY-MM-DD` | 年齢を求め、暫定維持カロリーの計算に使用 |
| `sex` | `female` / `male` / `unspecified` | 暫定維持カロリーの計算。`unspecified`では計算しない |
| `activity_level` | `sedentary` / `light` / `moderate` / `active` / `very_active` | 暫定維持カロリーの活動係数 |
| `meals_per_day` | 1〜10の整数 | 食事登録後に残り食数と1食あたりの目安を計算。**未設定なら3** |
| `dietary_restrictions` | 文字列の配列 | 助言の文脈 |
| `allergies` | 文字列の配列 | 助言の文脈 |
| `advice_preference` | 文字列（自由記述） | 助言の方針 |
| `routine` | ステップ名の配列 | 1日のルーティーン（実施順）。記録・レポート時に先行ステップの抜けを確認 |
| `timezone` | IANAタイムゾーン名 | **現在は未使用**。日時はOSのタイムゾーンで扱う |

`height_cm`・`birth_date`・`sex`・`activity_level`が揃い、`sex`が`female`または`male`なら、目標の追加・再計算時にMifflin–St Jeor式で暫定維持カロリーを計算します。`meals_per_day`は食事登録後の目安、`photo_retention_days`は写真整理に使います。食事制限・アレルギー・助言方針はエージェントが助言を組み立てるときの文脈です。

`routine`は1日のステップ（`weight`・`breakfast`・`lunch`・`snack`・`dinner`・`exercise`・`report`）を実施順に並べたものです。時刻は持ちません。設定しておくと、エージェントが記録やレポートの依頼を受けたときに当日の記録と照合し、先行ステップに抜けがあれば「朝食はどうでしたか？」のように一言だけ確認します。`snack`は食べない日があるのが普通なので、抜けていても確認されません。CLIのコードは参照せず、エージェントだけが読みます。

形式の正本は[`src/diet_assistant/profile.schema.json`](src/diet_assistant/profile.schema.json)（JSON Schema draft 2020-12）です。`diet profile validate`はこのスキーマで検証し、VS Codeでは`config/profile.json`の編集中に補完と警告が効きます。未知のキーはタイプミス検出のためエラーになります。

## 日常の使い方

以下はすべて、リポジトリ内でエージェントに話しかける例です。

### 食事を記録する

写真を渡して:

```text
この画像を今日の夕食として記録して。ご飯は普通盛り、唐揚げは5個です。
```

エージェントは食品と量を範囲で推定し、最小値・最大値・代表値と確信度（`low`/`medium`/`high`）を付けて登録します。写真だけで断定はしません。パッケージの栄養表示があれば伝えてください。写真より優先されます。

テキストだけでも記録できます:

```text
昼にラーメン食べた。塩分は表示で5.2gだった。
```

大きな誤差につながる不明点があるときだけ質問が返ります。細かい点は合理的に推定し、仮定を記録に残します。

### 運動と体重を記録する

```text
35分歩いた。3.2kmくらい。
スクワット3セット10回、40kgでやった。
今朝の体重72.4kg、体脂肪21.0%。
```

### 目標を決める

```text
10月13日までに80kgから74kgにしたい。
```

期限までの日数、週あたりの必要変化、理論上の日次エネルギー差、安全性の目安を計算します。プロフィールの身長・生年月日・性別・活動量が揃っていれば、Mifflin–St Jeor式による暫定維持カロリーと摂取目標範囲も計算します。理論赤字が維持カロリーの25%を超える場合、摂取目標には25%を上限として適用し、その食事計画だけを続けた場合の期限時点の予測体重と、理論ペースを満たさないことを明示します。過去の計画は`superseded`として残るので、目標を変えても履歴は消えません。

挑戦目標とは別に達成最低ラインを設定でき、期限日の単発体重ではなく指定日数の平均体重で判定できます。7日評価では4日以上の測定がない場合、データ不足と判定します。

### 振り返る

```text
今日の分をまとめて。
今週どうだった？
```

食事登録後は、その日の摂取目標に対する残量、残り食数、次の食事の目安を返します。日次は食事・推定範囲・栄養素・運動・体重・目標に基づく助言・達成判定を、週次は7日平均・前週差・運動・体重・データ不足・最優先行動を出します。Markdownが`reports/daily/`と`reports/weekly/`に残ります（Git管理外）。

食事登録後と日次の助言は当日の状況を案内し、週次の助言は7日平均と直前7日との差を重視します。1日の超過を翌日の極端な制限で相殺するような提案はしません。行動変更は原則1つに絞って提示されます。

### 記録を直す

```text
さっきの夕食、唐揚げ5個じゃなくて3個だった。
```

対象を確認してから更新し、何をどう変えたかを説明します。黙って書き換えることはありません。削除は実行前に必ず確認が入ります。

## iPhoneから記録する

iCloud Driveに`DietAssistant/inbox`フォルダを作り、Mac側でこのリポジトリの`inbox`へ同期またはコピーされるようにします。**画像とJSONのベース名は必ず同じにします。**

<details>
<summary>ショートカットの作成手順（初回だけ）</summary>

1. ショートカットAppで新規ショートカット「食事を記録」を作る。
2. 詳細設定で「共有シートに表示」を有効にし、受け入れ対象に「イメージ」を選ぶ。
3. 共有入力がない場合の分岐を追加し、「写真を撮る」または「写真を選択」を実行する。
4. 「イメージのサイズを変更」で長辺を1280〜1600px程度にする。
5. 「メニューから選択」を追加し、朝食=`breakfast`、昼食=`lunch`、夕食=`dinner`、間食=`snack`、その他=`other`を用意する。
6. 「入力を要求」で補足文を尋ねる。音声入力も使用でき、空欄も許可する。
7. 「現在の日付」を取得し、「日付をフォーマット」で`yyyyMMdd-HHmmss`にする。この値をベース名として変数へ保存する。
8. 画像をJPEGへ変換し、「ファイルを保存」で`iCloud Drive/DietAssistant/inbox/<ベース名>.jpg`へ保存する。「保存先を尋ねる」はオフにする。
9. 「辞書」を作り、`captured_at`に現在日時（ISO 8601）、`meal_type`に選択値、`note`に補足文、`source`に`iphone-shortcut`を設定する。
10. 「入力からJSONを取得」（環境により「JSONを作成」）で辞書をJSONテキストへ変換する。
11. JSONテキストから`<ベース名>.json`という名前のファイルを作り、同じinboxへ保存する。
12. 画像＋テキスト、画像だけ、テキストだけの分岐を試す。画像なしの場合もJSONだけ保存すれば取り込める。

生成するJSON例：

```json
{
  "captured_at": "2026-07-21T12:35:00+09:00",
  "meal_type": "lunch",
  "note": "ご飯は大盛り。唐揚げ5個。",
  "source": "iphone-shortcut"
}
```

</details>

Macへ同期したら`diet inbox import`を実行するか、エージェントに「inboxを取り込んで」と頼みます。JSONと画像内容のSHA-256を`external_id`に使うため、同じ入力を再取り込みしても重複しません。解析済み画像は`photos/temporary`へ移動します。

inboxは食事の登録までで、画像の栄養推定はしません。推定が要る場合はエージェントに続けて頼んでください。

## 自分で実行する管理作業

エージェントに任せず、自分で実行するコマンドです。

```bash
diet doctor          # DBとスキーマの状態確認
diet backup create   # backups/diet-YYYYMMDD-HHMMSS.db を作る
diet backup list
diet inbox import    # iPhoneから同期したファイルを取り込む
```

バックアップはSQLite Backup APIで整合性のあるコピーを作ります。iCloud Drive、外付けSSD、NASへ定期コピーしてください。

**写真の削除は必ずdry-runから。** 一時画像の既定保持期間は30日です。残したい画像は手動で`photos/keep`へ移してください。

```bash
diet photo cleanup              # dry-run。消える対象を確認する
diet photo cleanup --days 30 --apply
```

**複数PCで使う場合**、稼働中のSQLiteファイルを同期フォルダへ直接置いて同時更新すると破損の恐れがあります。更新する正本は常に1台のローカルディスクに置き、端末間では停止中のバックアップを受け渡してください。

## プライバシー

DB、画像、プロフィール、バックアップ、生成レポート、環境変数は`.gitignore`の対象です。外部サービスへの送信はありません。エージェントはDBや写真、プロフィールをコミットする依頼には応じません。

ログにはコマンド結果が表示されます。共有ターミナルやCIへ個人データを出さないでください。

## 困ったとき

- `DBがありません`: `diet init`を実行する。
- `profile_exists: false`: exampleを`config/profile.json`へコピーする。DB利用だけなら必須ではない。
- inboxが取り込まれない: JSON拡張子、JSON構文、`meal_type`、同期完了を確認する。
- `failed`のまま: `diet inbox list --status failed`で`error_message`を確認し、元データを直して`diet inbox retry <id>`する。
- 日次に記録が出ない: ISO 8601日時のタイムゾーンと`--date`を確認する。
- カロリー目標が空: プロフィールの身長・生年月日・性別・活動量を揃え、`sex`を`female`または`male`にしてから`diet goal recalculate <id>`を実行する。不足時は安全側で自動断定しない。
- `doctor`が`ok: false`でスキーマ版が古い: `diet backup create`のあと`diet init`を実行する。未適用のマイグレーションだけが順に適用される。

## 付録: CLIリファレンス

主にエージェントが使います。**正本は`diet <サブコマンド> --help`です。** 以下は一覧の目安なので、オプションの詳細は必ず`--help`で確認してください。

すべてのコマンドはJSONを標準出力へ返します。エラーはJSONを標準エラーへ返し、終了コード2になります。データルートを変える場合は先頭に`--root /path/to/root`を付けます（動作確認は必ず一時ディレクトリを指定し、本番の`data/diet.db`を触らないこと）。

| サブコマンド      | 操作                                                           |
| ----------------- | -------------------------------------------------------------- |
| `init` / `doctor` | DB初期化・マイグレーション適用 / 状態確認                      |
| `profile`         | `show` `validate`                                              |
| `meal`            | `add` `list` `show` `update` `delete`                          |
| `exercise`        | `add` `list` `show` `update` `delete`                          |
| `metric`          | `add` `list` `show` `update` `delete`                          |
| `goal`            | `add` `list` `show` `activate` `recalculate` `evaluate` `update` `delete` |
| `inbox`           | `import` `list` `retry`                                        |
| `report`          | `daily` `weekly`（`--date` `--format json`）                   |
| `advice`          | `today` `weekly`（`--date`）                                   |
| `backup`          | `create` `list`                                                |
| `photo`           | `cleanup`（`--days` `--apply`）                                |

記録の登録・更新は`--json <file>`で構造化データを渡せます。更新JSONには変更する列だけを書きます。削除は`--yes`が必要です。

```bash
diet meal add --type dinner --text '鮭おにぎり2個、サラダチキン、味噌汁'
diet meal add --type lunch --text 'ラーメン' --calories 304 --sodium 5.2
diet meal add --json /tmp/meal.json
diet meal update 1 --json /tmp/meal-update.json
diet meal delete 1 --yes

diet exercise add --type walking --minutes 35 --distance 3.2 --intensity moderate
diet exercise add --type squat --sets 3 --repetitions 10 --weight 40
diet metric add --weight 72.4 --body-fat 21.0 --waist 82

diet goal add --start-weight 91 --target-weight 87 --success-threshold-weight 88 \
  --evaluation-window-days 7 --target-date 2026-08-21 --activate
diet goal evaluate 1 --date 2026-08-21
diet report weekly --date 2026-07-21 --format json
```

`--sodium`は**食塩相当量（g）**です。ナトリウム量（mg）ではありません。

食事の推定JSONには`estimated_calories`、`calories_min`、`calories_max`、`estimation_confidence`を含めます。形式と登録手順の詳細は`AGENTS.md`を参照してください。

## 開発

```bash
pytest
ruff check .
basedpyright
```

BasedpyrightはPython 3.14を対象にRecommendedモードで`src`と`tests`を検査し、警告でも失敗させます。DB初期化、CRUD、不正範囲、目標ペース、計画履歴、日次・7日・週次集計、冪等取り込み、安全なバックアップ、写真削除判定、CLI終了コードをpytestで検証します。

設計上の決定は`docs/adr/`に記録します。エージェント向けの作業ルールは`AGENTS.md`（`CLAUDE.md`はそのsymlink）にあります。

## 今後の拡張候補

- 食品成分・商品栄養表示データベースとの連携
- 記録した体重・摂取量の傾向による暫定維持カロリーの補正と、たんぱく質目標の計算
- 14日・28日トレンドの可視化と助言履歴の重複抑制強化
- Alembic相当のマイグレーション機能拡張（ダウングレード、データ変換を伴う移行）
- HEIC変換、画像メタデータ除去、keep操作のCLI化
- 読み取り専用Webダッシュボードと、単一ライターを守る同期方式
