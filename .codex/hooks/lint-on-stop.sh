#!/bin/sh

set -u

repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
ruff="$repo_root/.venv/bin/ruff"

# venv が未作成なら黙って通す（セットアップ前のセッションを妨げない）
[ -x "$ruff" ] || exit 0

lint_output=$("$ruff" check "$repo_root" 2>&1)
lint_status=$?

if [ "$lint_status" -eq 0 ]; then
  exit 0
fi

printf '%s\n' "$lint_output" >&2
printf '%s\n' '{"continue":false,"stopReason":"ruff check が失敗しました。lint エラーを修正して再実行してください。"}'
