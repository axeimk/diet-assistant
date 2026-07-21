# Diet Assistant

SQLiteを正本に、食事・運動・体重・目標を長期間記録し、CodexとCLIから傾向分析や助言を得る個人向けツールです。Webアプリではなく、ローカルでデータを管理したい1人の利用者を対象にしています。

画像認識API、医療診断、治療、厳密な栄養・代謝計算、複数ユーザー、複数端末からの同時更新は対象外です。詳細な判断と仮定は[設計概要](docs/design.md)を参照してください。

## セットアップ

Python 3.14以降が必要です。`uv`を使う場合：

```bash
uv venv --python 3.14
source .venv/bin/activate
uv pip install -e '.[dev]'
diet init
diet doctor
```

標準のvenvを使う場合：

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
diet init
```

`config/profile.example.json`を`config/profile.json`へコピーし、自分の値に変更してください。後者はGit管理されません。

```bash
cp config/profile.example.json config/profile.json
diet profile validate
```

すべてのコマンドはJSONを標準出力へ返します。エラーはJSONを標準エラーへ返し、終了コード2になります。別の場所をデータルートにする場合は先頭に`--root /path/to/root`を指定できます。

## 基本的な記録

食事をテキストで記録：

```bash
diet meal add --type dinner --text '鮭おにぎり2個、サラダチキン、味噌汁'
```

Codexが画像を解析した構造化JSONから記録：

```bash
diet meal add --json /tmp/meal.json
diet meal show 1
```

推定JSONには`estimated_calories`、`calories_min`、`calories_max`、`estimation_confidence`を含めてください。公式表示や申告値を優先し、写真だけの場合は仮定を`text`や品目の`note`に残します。

運動と身体測定：

```bash
diet exercise add --type walking --minutes 35 --distance 3.2 --intensity moderate
diet exercise add --type squat --sets 3 --repetitions 10 --weight 40
diet metric add --weight 72.4 --body-fat 21.0 --waist 82
```

更新・削除：

```bash
diet meal list
diet meal update 1 --json /tmp/meal-update.json
diet meal delete 1 --yes
diet goal update 1 --json /tmp/goal-update.json
diet goal delete 1 --yes
diet exercise update 1 --json /tmp/exercise-update.json
diet exercise delete 1 --yes
diet metric update 1 --json /tmp/metric-update.json
diet metric delete 1 --yes
```

更新JSONには変更する列だけを書きます。ユーザーの記録を変更する前に、対象を`show`または`list`で確認してください。

## 目標と計画

```bash
diet goal add --start-weight 80 --target-weight 74 --target-date 2026-10-13 --activate
diet goal list
diet goal show 1
diet goal recalculate 1
```

期限までの日数、週あたりの必要変化、理論上の日次エネルギー差、安全性の目安を決定的なコードで計算します。過去の計画は`superseded`として残ります。基礎代謝などが不足するMVPでは摂取カロリー目標を断定しません。

## レポートと助言

```bash
diet report daily --date 2026-07-21
diet report weekly --date 2026-07-21
diet report daily --date 2026-07-21 --format json
diet advice today --date 2026-07-21
diet advice weekly --date 2026-07-21
```

Markdownは`reports/daily/`と`reports/weekly/`に生成され、Git管理されません。日次は食事、推定範囲、栄養素、運動、体重、不確実な記録を表示します。週次は7日平均、前週差、運動、体重、データ不足、最優先行動を表示します。助言は単日ではなく傾向を重視し、極端な帳尻合わせを勧めません。

## iPhoneショートカット

iCloud Driveに`DietAssistant/inbox`フォルダを作り、Mac側ではこのリポジトリの`inbox`へ同期またはコピーされるようにします。画像とJSONのベース名を必ず同じにします。

初心者向けのショートカット作成手順：

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

Macへ同期後に実行します。

```bash
diet inbox import
diet inbox list
diet inbox list --status failed
diet inbox retry 1
```

JSONと画像内容のSHA-256を`external_id`として使うため、同じ入力を再取り込みしても重複しません。解析済み画像は`photos/temporary`へ移動します。inboxは食事の登録まで行いますが、画像自体の栄養推定はしません。必要なら先にCodexで推定JSONを完成させます。

## 写真の保持と削除

一時画像の既定保持期間は30日です。残したい画像は手動で`photos/keep`へ移してください。削除は必ずdry-runから行います。

```bash
diet photo cleanup
diet photo cleanup --days 45
diet photo cleanup --days 30 --apply
```

原寸保存は不要です。ショートカット側で長辺1280〜1600pxへ縮小すると容量と解析精度のバランスを取りやすくなります。

## バックアップと複数PC

```bash
diet backup create
diet backup list
```

SQLite Backup APIで整合性のある`backups/diet-YYYYMMDD-HHMMSS.db`を作ります。バックアップ先をiCloud Drive、外付けSSD、NASへ定期コピーしてください。バックアップ自体もGit管理されません。

稼働中のSQLiteファイルを同期フォルダへ直接置き、複数PCから同時更新すると競合や破損の恐れがあります。更新する正本は常に1台のローカルディスクに置き、端末間では停止中のバックアップを受け渡してください。

## Codexでの利用例

リポジトリ内でCodexを起動し、次のように依頼します。

```text
この画像を今日の夕食として記録してください。
ご飯は普通盛り、唐揚げは5個です。
```

Codexは`AGENTS.md`に従い、推定範囲と確信度を構造化し、CLIで登録後に再取得と当日集計を行います。DBや写真、プロフィールをコミットする依頼には応じません。

## プライバシー

DB、画像、プロフィール、バックアップ、生成レポート、環境変数は`.gitignore`の対象です。外部サービスへの送信はありません。ログにはコマンド結果が表示されるため、共有ターミナルやCIへ個人データを出さないでください。

## テストと静的チェック

```bash
pytest
ruff check .
basedpyright
```

BasedpyrightはPython 3.14を対象にRecommendedモードで`src`と`tests`を検査し、警告でも失敗させます。DB初期化、CRUD、不正範囲、目標ペース、計画履歴、日次・7日・週次集計、冪等取り込み、安全なバックアップ、写真削除判定、CLI終了コードをpytestで検証します。

## よくあるトラブル

- `DBがありません`: `diet init`を実行する。
- `profile_exists: false`: exampleを`config/profile.json`へコピーする。DB利用だけなら必須ではない。
- inboxが取り込まれない: JSON拡張子、JSON構文、`meal_type`、同期完了を確認する。
- `failed`のまま: `diet inbox list --status failed`で`error_message`を確認し、元データを直して`retry`する。
- 日次に記録が出ない: ISO 8601日時のタイムゾーンと`--date`を確認する。
- カロリー目標が空: MVPではプロフィール不足時に安全側で自動断定しない設計である。

## 今後の拡張候補

- 食品成分・商品栄養表示データベースとの連携
- プロフィールからの維持カロリー範囲とたんぱく質目標の計算
- 14日・28日トレンドの可視化と助言履歴の重複抑制強化
- Alembic相当の逐次マイグレーションランナー
- HEIC変換、画像メタデータ除去、keep操作のCLI化
- 読み取り専用Webダッシュボードと、単一ライターを守る同期方式
