#!/bin/sh
# Jev Game Benchmark を起動する → http://localhost:8770 （変更: ARENA_PORT=9000 ./run.sh）
#
# API キーの渡し方は2通り。どちらにするかは、初回の起動時に聞く（ARENA_KEYS=env / ARENA_KEYS=akc で固定もできる）。
#   env : 環境変数 TYPESAFE_API_KEY（必須）、ANTHROPIC_API_KEY / OPENAI_API_KEY（任意）を自分で export しておく
#   akc : macOS の akc（AI KeyChain CLI）に同じ名前で登録しておくと、キーチェーンから子プロセスにだけ渡す（`akc set TYPESAFE_API_KEY`）
# API キーがなくても、Claude Code のログイン（Agent SDK）と Codex CLI のログイン（app-server）での対戦は使える。
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null 2>&1; then uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
  else python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; fi || exit 1
fi
KEYS="$ARENA_KEYS"
if [ -z "$KEYS" ] && [ -f .arena-keys ]; then KEYS=$(cat .arena-keys); fi
if [ -z "$KEYS" ]; then
  if ! command -v akc >/dev/null 2>&1; then KEYS=env
  elif [ -n "$TYPESAFE_API_KEY" ]; then KEYS=env
  elif [ -t 0 ]; then
    echo "API キーの渡し方を選んでください（次回からは .arena-keys に保存。変更は ARENA_KEYS=env|akc ./run.sh）:"
    echo "  1) env — 環境変数 TYPESAFE_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY を使う"
    echo "  2) akc — macOS の akc（キーチェーン）に登録した同名のキーを使う"
    printf "> "; read -r ans
    case "$ans" in 2|akc) KEYS=akc ;; *) KEYS=env ;; esac
    echo "$KEYS" > .arena-keys
  else KEYS=env; fi
fi
echo "→ http://localhost:${ARENA_PORT:-8770}  （キー: ${KEYS}）"
if [ "$KEYS" = akc ]; then
  for k in TYPESAFE_API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY; do
    if eval "[ -z \"\$$k\" ]" && akc check "$k" >/dev/null 2>&1; then export "$k=keychain://$k"; fi
  done
  exec akc run -- .venv/bin/python -m arena
fi
[ -n "$TYPESAFE_API_KEY" ] || echo "注意: TYPESAFE_API_KEY が未設定です（Jev が動きません）。export TYPESAFE_API_KEY=... のあと起動し直してください"
exec .venv/bin/python -m arena
