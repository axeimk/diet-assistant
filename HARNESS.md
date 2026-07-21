# ハーネス採否の記録

汎用ハーネスの提案（verify スキル、hooks、CONTEXT.md など）の採否を記録する。
「使わない」と記録された項目をエージェントは再提案しない。

- 使用ツール: Claude Code と Codex（2026-07-21 確認）
- CLAUDE.md / AGENTS.md（「作業の進め方」節を含む）: 作成済み（`AGENTS.md` が実体、`CLAUDE.md` はそこへの symlink）
- verify スキル: 使う（`.agents/skills/verify/` が実体、`.claude/skills/verify` は symlink）
- tdd スキル: 使わない（2026-07-21 ユーザー判断）
- domain-modeling スキル: 導入済み（`.agents/skills/domain-modeling/` が実体、`.claude/skills/domain-modeling` は symlink）
- grilling スキル: 使わない（2026-07-21 ユーザー判断）
- CONTEXT.md（用語集）: 保留。最初の用語が確定した時点で作る（2026-07-21 ユーザー判断。「使わない」ではないので、その時点で提案してよい）
- ADR（設計記録）: 導入済み（`docs/adr/`）
- permissions / hooks: 導入済み
  - Claude Code: `.claude/settings.json`。permissions（pytest / ruff / basedpyright / `diet --root` / 読み取り系 git）と hooks（`PostToolUse` で編集した `.py` に `ruff check`）
  - Codex: `.codex/hooks.json` + `.codex/hooks/lint-on-stop.sh`。`Stop` で `ruff check` を回し、失敗時は `{"continue":false}` で止める。Codex はファイル編集を `shell` / `apply_patch` 経由で行い `tool_input.file_path` が取れる保証がないため、Claude 側の `PostToolUse` 方式は移植せず `Stop` 方式にした
  - Codex にはコマンド単位の permissions が無い（`config.toml` の `[projects."...".trust_level]` は信頼レベルのみ）ため、permissions は Claude Code 側だけ
