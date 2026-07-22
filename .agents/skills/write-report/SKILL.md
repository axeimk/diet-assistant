---
name: write-report
description: diet-assistantの記録から日次・週次レポートをMarkdownの定型体裁で作成して提示する手順。ユーザーが「今日のレポート書いて」「昨日のレポート出して」「週次レポート」「今週のまとめ」「最近どう？」など、特定の日や週の記録の要約・振り返り・レポートを求めたときは、言い回しがカジュアルでも必ずこのスキルを使う。dietコマンドの出力を自己流に整形せず、まずこの手順に従う。
---

# write-report — 日次・週次レポートを定型体裁で出す

`diet report` の生成するMarkdownを正とし、7日傾向の補足を添えて提示する。
レポート本文を自分で作文しない（数値の正本はSQLiteで、その集計はCLIが行う）。

## 手順

1. 対象日を依頼から読み取る。省略時は今日（`date +%F` で確認する）。
2. レポートを生成する。`--root` は付けない（本番データの読み取りとレポート保存が目的のため）:

   ```bash
   diet report daily --date <YYYY-MM-DD> --format markdown
   ```

   戻り値のJSONの `path`（`reports/daily/<日付>.md`）を読む。
   注意: `--stdout` を付けてもMarkdownは `{"markdown": "..."}` のJSONに包まれて返るので、
   ファイルを読むほうが確実。
3. 傾向データを取得する:

   ```bash
   diet report weekly --date <YYYY-MM-DD> --format json
   ```

   使う値: `average_calories`・`average_weight`・`exercise_minutes`・`recorded_meal_days`・
   `changes`（前週比）・`target_weekly_weight_change`・`pace_difference`。
4. 「出力体裁」に従って回答を組み立てる。補足はチャット回答にだけ付け、
   `reports/` のファイルはCLI生成のまま書き換えない（ファイルはCLI出力の正本のため）。

週次レポートを求められた場合は、手順2を `diet report weekly --date <YYYY-MM-DD> --format markdown`
に替える（`reports/weekly/` に保存される）。手順3以降は同じで、`changes` を前週比の補足に使う。

## 出力体裁

回答はこの形で返す。前置きの説明文は付けない:

```markdown
（CLIが生成した日次レポートMarkdownをそのまま貼る）

## 7日間の傾向
- 平均摂取カロリー: 1,850 kcal/日（前週比 +120）
- 平均体重: 72.4 kg（前週比 -0.3 kg、目標ペースとの差 +0.1 kg/週）
- 運動時間: 合計210分、食事記録 6/7日
```

- 値が `null` の項目は「記録なし」と書く。数値をでっち上げない。
- 傾向への一言コメントは、`pace_difference` が目標から大きく外れているなど
  言う価値がある場合だけ1〜2文で添える。毎回の感想は書かない。
- 記録の少ない週（`recorded_meal_days` が小さい）は、傾向の解釈より先に
  記録の欠けを事実として示す。

## 注意点

- 記録の追加・修正はこのスキルの範囲外。レポート中に明らかな記録漏れを見つけても
  黙って直さず、事実として指摘するにとどめる。
- `--date` のタイムゾーンは記録の `eaten_at` とローカル時刻に依存する。
  「今日の食事が出ない」ように見えたら、まず日付のずれを疑う。
