# 設計概要

## 要件整理と仮定

- SQLiteを唯一の正式な記録とし、Codexを含む利用者はCLI経由で更新する。
- MVPは外部AI APIを呼ばない。画像を解釈したCodexが構造化JSONをCLIへ渡す。
- Python 3.14標準ライブラリだけで実行可能にし、開発時のみBasedpyright、pytest、Ruffを使う。
- プロフィールは機微性が高いため、DBの`profiles`表ではなくGit対象外のJSONを正本とする。
- 日時はISO 8601のタイムゾーン付き文字列で保存する。省略時はOSのローカルタイムゾーンを使う。
- 距離、重量、胴囲はそれぞれkm、kg、cmとする。
- プロフィールの身長・生年月日・性別・活動量が揃う場合は、Mifflin–St Jeor式で暫定維持カロリーと摂取目標範囲を計算する。プロフィールが不足する場合や性別が`unspecified`の場合は自動断定しない。
- 目標体重と期限から求めた理論上の日次赤字は計画根拠として保持するが、摂取目標へ適用する赤字は暫定維持カロリーの25%を上限とする。

## アーキテクチャ

```text
CLI / Codex / iPhone inbox JSON
             │
             ▼
       application services ── 計画・集計・助言・取り込み・保守
             │
             ▼
          repository ───────── CRUDとトランザクション
             │
             ▼
           SQLite ──────────── 正本
```

`cli.py`は入出力、`repository.py`は永続化、`services/`は決定的な業務ロジックを担当する。Web UIを追加する場合もservicesを再利用できる。標準`sqlite3`を選び、MVPで不要なORM依存と抽象化を避けた。

## DBスキーマ

スキーマの正本は`db.py`の`SCHEMA_SQL`、バージョンは`PRAGMA user_version`で管理する。既存DBの変更は`db.py`の`MIGRATIONS`に版番号付きで追加し、`diet init`が未適用分だけを順に適用する。`migrations/*.sql`は履歴の記録で、実行はしない。

| テーブル | 役割 | 主な関係・制約 |
|---|---|---|
| `goals` | 体重目標 | partial unique indexでactiveは1件、達成最低ラインと1〜28日の評価期間を保持 |
| `plans` | 再計算履歴 | goalに従属、暫定維持カロリーと摂取目標を保持、旧計画はsuperseded |
| `meals` | 食事 | カロリー範囲とconfidenceを検査、`sodium`は食塩相当量g |
| `meal_items` | 食事内訳 | meal削除時にcascade |
| `exercises` | 運動 | 種目ごとの任意属性 |
| `body_metrics` | 身体測定 | 体重・体脂肪率・胴囲 |
| `intake_entries` | 共通取り込み | external_id一意、状態遷移と結果ID |
| `advice_history` | 助言履歴 | 期間、根拠、優先度を保存 |

## CLI設計

要求された`init`、`doctor`、`profile`、`goal`、`meal`、`exercise`、`metric`、`inbox`、`report`、`advice`、`backup`、`photo`の全コマンド群を設ける。出力は原則JSONで、失敗はJSONを標準エラーへ出して終了コード2を返す。レポートはMarkdown保存またはJSON出力に対応する。破壊的な食事・運動削除は`--yes`、写真削除は`--apply`が必要。

## MVPと将来拡張

MVPは手動・JSON入力、inbox取り込み、CRUD、目標ペース、プロフィールから求める暫定維持カロリーと摂取目標、評価期間の平均体重による目標判定、日次・週次集計、定型助言、レポート、バックアップ、写真整理を扱う。画像認識API、食品データベース連携、実績トレンドによる維持カロリー補正、YAML読込、Web/モバイルUI、クラウド同期は将来拡張とする。
