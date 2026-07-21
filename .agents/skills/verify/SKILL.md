---
name: verify
description: diet-assistant（SQLite正本の個人向けダイエット記録CLI）の変更を、実際に`diet`コマンドを起動・操作して確認するためのレシピ。CLI・リポジトリ層・サービス層（intake / reporting / advice / planning / maintenance）・スキーマを変更したときに、報告の前にこの手順で動作を確かめる。
---

# verify — diet-assistant の動作確認レシピ

変更したコードが実際に動くことを、`diet` CLI を起動・操作して確認するための手順。
テスト・lint・型チェックのコマンドは書かない（`AGENTS.md` の「コマンド」節を参照）。

## 重要: 本番データを触らない

このリポジトリの `data/diet.db` はユーザーの記録の正本。**検証では絶対に使わない。**
すべての `diet` 実行に `--root <一時ディレクトリ>` を付け、使い捨てのデータルートを作る。

```bash
source .venv/bin/activate
export DIET_VERIFY_ROOT=$(mktemp -d)
```

以降 `diet --root "$DIET_VERIFY_ROOT" ...` の形で実行する。終わったら
`rm -rf "$DIET_VERIFY_ROOT"` で捨てる。

## 起動と起動確認

```bash
diet --root "$DIET_VERIFY_ROOT" init
diet --root "$DIET_VERIFY_ROOT" doctor
```

- `init` が `{"database": ..., "schema_version": 1}` を返せば DB を作れている。
- `doctor` の `"ok": true` が起動確認。`profile_exists` は false でよい（プロフィール未配置）。
- すべてのコマンドは JSON を標準出力へ返す。エラーは JSON を標準エラーへ返し終了コード 2。
  **終了コードも確認する**（`echo $?`）。

## 確認すべき代表フロー

変更箇所に関係するフローを選んで実行する。全部を毎回回す必要はない。

### 記録の CRUD（cli.py / repository.py を変更したとき）

```bash
diet --root "$DIET_VERIFY_ROOT" meal add --type dinner --text '鮭おにぎり2個、味噌汁'
diet --root "$DIET_VERIFY_ROOT" meal show 1
diet --root "$DIET_VERIFY_ROOT" meal list
diet --root "$DIET_VERIFY_ROOT" exercise add --type walking --minutes 35 --distance 3.2
diet --root "$DIET_VERIFY_ROOT" metric add --weight 72.4 --body-fat 21.0
```

期待: `meal add` が採番した `id` を返し、`meal show <id>` で同じ日時・区分・本文が読み戻せる。
書き込み系を変えたときは **必ず `show` / `list` で読み戻して確認する**（戻り値だけを信じない）。

構造化 JSON 経由の登録（Codex 経路）も変更に関係するなら確認する:

```bash
cat > "$DIET_VERIFY_ROOT/meal.json" <<'JSON'
{"eaten_at":"2026-07-21T12:35:00+09:00","meal_type":"lunch","text":"唐揚げ弁当",
 "estimated_calories":850,"calories_min":700,"calories_max":1000,
 "estimation_confidence":"medium","source":"codex",
 "items":[{"name":"ご飯","amount_text":"大盛り","confidence":"medium"}]}
JSON
diet --root "$DIET_VERIFY_ROOT" meal add --json "$DIET_VERIFY_ROOT/meal.json"
diet --root "$DIET_VERIFY_ROOT" meal show 2
```

期待: 範囲（min / max）、確信度、`items` が欠落せず読み戻せる。

### 目標と計画（services/planning.py）

```bash
diet --root "$DIET_VERIFY_ROOT" goal add --start-weight 80 --target-weight 74 \
  --target-date 2026-10-13 --activate
diet --root "$DIET_VERIFY_ROOT" goal show 1
diet --root "$DIET_VERIFY_ROOT" goal recalculate 1
```

期待: 期限までの日数・週あたり必要変化・安全性の目安が返る。`recalculate` 後に
古い計画が消えず `superseded` として残ること。

### レポートと助言（services/reporting.py, advice.py）

```bash
diet --root "$DIET_VERIFY_ROOT" report daily --date 2026-07-21 --format json
diet --root "$DIET_VERIFY_ROOT" report weekly --date 2026-07-21 --format json
diet --root "$DIET_VERIFY_ROOT" advice today --date 2026-07-21
```

期待: 上で登録した食事・運動が `daily` の `meals` / `exercises` に現れ、`totals` に反映される。
範囲が不明な食事は `uncertain_meal_ids` に入る。Markdown 形式（`--format` 省略時）では
`$DIET_VERIFY_ROOT/reports/daily/` にファイルが生成されることも確認する。

### inbox 取り込み（services/intake.py）

```bash
mkdir -p "$DIET_VERIFY_ROOT/inbox"
cat > "$DIET_VERIFY_ROOT/inbox/20260721-123500.json" <<'JSON'
{"captured_at":"2026-07-21T12:35:00+09:00","meal_type":"lunch",
 "note":"テスト取り込み","source":"iphone-shortcut"}
JSON
diet --root "$DIET_VERIFY_ROOT" inbox import
diet --root "$DIET_VERIFY_ROOT" inbox list
diet --root "$DIET_VERIFY_ROOT" inbox import   # 2 回目
diet --root "$DIET_VERIFY_ROOT" meal list
```

期待: **2 回目の import で重複が増えない**（SHA-256 由来の `external_id` による冪等性）。
失敗させた場合は `inbox list --status failed` に `error_message` が出て、`inbox retry <id>` で再開できる。

### バックアップと写真削除（services/maintenance.py）

```bash
diet --root "$DIET_VERIFY_ROOT" backup create
diet --root "$DIET_VERIFY_ROOT" backup list
diet --root "$DIET_VERIFY_ROOT" photo cleanup          # dry-run
```

期待: `backups/diet-YYYYMMDD-HHMMSS.db` が生成される。`photo cleanup` は
**引数なしでは必ず dry-run**（候補一覧のみ、削除しない）。`--apply` はこの検証では実行しない。

### エラー経路

```bash
diet --root "$DIET_VERIFY_ROOT" meal show 9999; echo "exit=$?"
```

期待: エラー JSON が標準エラーへ出て `exit=2`。

## 落とし穴

- **`--root` を忘れると本番 `data/diet.db` を触る。** 検証コマンドには例外なく付ける。
- venv を有効化しないと `diet` が見つからない（`source .venv/bin/activate`）。
  未インストールなら `uv pip install -e '.[dev]'`。
- Python 3.14 以降が必要（`.python-version` 参照）。
- `--date` を渡す日次・週次レポートは ISO 8601 のタイムゾーンに依存する。
  記録した `eaten_at` と `--date` のタイムゾーンがずれると「記録が出ない」ように見える。
- `meal add --json` の更新用 JSON は変更する列だけを書く形式。全列を書き直そうとしない。
- 検証後は `rm -rf "$DIET_VERIFY_ROOT"` で片付ける。生成物をリポジトリ内に残さない。
