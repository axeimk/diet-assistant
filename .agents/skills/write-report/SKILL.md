---
name: write-report
description: diet-assistantの記録から日次・週次レポートを作成して提示する手順。分析結果（findings）を取得し、フィードバックを自分の言葉で書いて保存し、レポートを生成する。ユーザーが「今日のレポート書いて」「昨日のレポート出して」「週次レポート」「今週のまとめ」「最近どう？」など、特定の日や週の記録の要約・振り返り・レポートを求めたときは、言い回しがカジュアルでも必ずこのスキルを使う。dietコマンドの出力を自己流に整形せず、まずこの手順に従う。
---

# write-report — 分析結果からフィードバックを書き、レポートを出す

数値の正本はSQLiteで、その集計と分析（findings）はCLIが行う。**フィードバックの文面だけは自分が書く**
（ADR 0013）。集計値を自分で計算し直さない。findingsに無い数値をフィードバックに書かない。

## 手順

1. 対象日を依頼から読み取る。明示された日付はその日を使う。「今日」または省略時は
   `--date` を付けず、CLIにプロフィールの `day_start_time` を使ったレポート日を決めさせる。
   たとえば開始時刻が04:00なら、7月23日00:08の記録は7月22日分になる。
2. 分析結果を取得する。`--root` は付けない（本番データを読む）。
   日付が明示された場合だけ `--date <YYYY-MM-DD>` を加える:

   ```bash
   diet feedback today     # 日次。findings と保存済みフィードバックが返る
   diet feedback weekly    # 週次（直近7日）
   ```

3. `findings` からフィードバックを書く。書き方は下の「フィードバックの書き方」に従う。
   `findings` が空、または `meal_records_missing` が先頭なら、フィードバックではなく
   記録の欠けを事実として伝える。
4. 書いたフィードバックを保存する。JSONファイルに書いてから渡す:

   ```bash
   diet feedback save --json /tmp/feedback.json                          # 日次
   diet feedback save --json /tmp/feedback.json --kind period --days 7   # 週次
   ```

   使える項目は `situation`・`priority_action`（必須）と
   `keep`・`alternative`・`plan_change`・`next_review_date`・`note`。
   それ以外のキーはエラーになる。`evidence` は渡さない（CLIがfindingsを付ける）。
5. レポートを生成する。保存したフィードバックが埋め込まれる:

   ```bash
   diet report daily --format markdown
   diet report weekly --format markdown
   ```

   戻り値のJSONの `path`（`reports/daily/<日付>.md`）を読む。
   注意: `--stdout` を付けてもMarkdownは `{"markdown": "..."}` のJSONに包まれて返るので、
   ファイルを読むほうが確実。
6. 週次では前週比の補足に `diet report weekly --format json` の
   `changes`・`target_weekly_weight_change`・`pace_difference` を使う。
7. 「出力体裁」に従って回答を組み立てる。`reports/` のファイルはこの手順で生成したものが
   正本なので、あとから書き換えない。

## フィードバックの書き方

findings は優先順位の高い順に並んでいる。**先頭のfindingを最優先行動にする**。
順序はCLIが決めており、カロリー管理が栄養素より先に来る（ADR 0014）。並べ替えない。

- `situation`: 先頭のfindingの数値をそのまま使って状況を1文で書く。
  例: 夕食が平均672 kcalで1日の43%を占めている。
- `priority_action`: 行動はひとつだけ。`resolution` に従う:
  - `reduce` — 減らす・配分を変える
  - `substitute` — **置き換える**。追加で食べるフィードバックにしない
  - `increase` — 増やしてよい（カロリーに余裕があるときだけこの値になる）
  - `record` — まず記録する。フィードバックより記録の欠けを先に伝える
  - `none` — 参考情報。行動を作らない
- `alternative`（週次）: 2番目以降のfindingから、続けやすい代替案をひとつ。
- `keep`（週次）: 続いていること（記録日数、範囲内の項目）を事実として書く。
- `plan_change`: `plan_basis_weight_stale` があれば `diet goal recalculate` を促す。
  それ以外は原則「変更なし」。

守ること:

- **findingsに無い数値を書かない。** 品目名や頻度を推測で書かない
  （「最も頻度の高い間食」のような、測っていない主張をしない）。
- **カロリーが超過しているときに追加摂取を勧めない。** `calorie_headroom` が0以下なら
  不足の解消は置き換えだけ。栄養素を満たすために食べ足すフィードバックは書かない。
- 単日の値で断定せず、`sample_days` が小さいfindingは「まだ判断材料が少ない」と添える。
- 文面は `config/profile.json` の `feedback_preference`（自由記述のフィードバック方針。
  例: 「まずは継続可能な変更を優先」）に沿わせる。未設定なら平坦に書く。
- 医療的な判断をしない。体調や疾病に関わる話は専門家への相談を促すにとどめる。

## 出力体裁

回答はこの形で返す。前置きの説明文は付けない:

```markdown
（CLIが生成したレポートMarkdownをそのまま貼る）

## 7日間の傾向
- 平均摂取カロリー: 1,850 kcal/日（前週比 +120）
- 平均体重: 72.4 kg（前週比 -0.3 kg、目標ペースとの差 +0.1 kg/週）
- 運動時間: 合計210分、食事記録 6/7日
```

- 値が `null` の項目は「記録なし」と書く。数値をでっち上げない。
- 記録の少ない週（`recorded_meal_days` が小さい）は、傾向の解釈より先に
  記録の欠けを事実として示す。

## 注意点

- `config/profile.json` の `routine` にレポートより前のステップがあり、当日の記録に
  抜けがある場合（例: 夕食が未記録のまま「今日のレポート」と言われた）、レポートは
  そのまま出したうえで末尾に一言だけ確認を添える（例: 「夕食はどうでしたか？
  記録してからレポートを作り直せます」）。`snack` は任意ステップなので、
  抜けていても確認しない。
- 記録の追加・修正はこのスキルの範囲外。レポート中に明らかな記録漏れを見つけても
  黙って直さず、事実として指摘するにとどめる。
- 同じ日・同じ期間のフィードバックは上書きされる（種別・期間ごとに最新1件だけ保持。ADR 0010）。
  書き直したら `diet feedback save` をもう一度実行し、レポートを再生成する。
- `--date` のタイムゾーンは記録の `eaten_at` とローカル時刻に依存する。
  `config/profile.json` の `day_start_time` より前の記録は前日のレポートへ入る。
  「今日の食事が出ない」ように見えたら、暦日だけでなくこの境界も確認する。
