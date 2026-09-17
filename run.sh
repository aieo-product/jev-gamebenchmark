#!/bin/sh
# Jev Game Benchmark を起動する → http://localhost:8770 （変更: ARENA_PORT=9000 ./run.sh）
#
# API キーは環境変数で渡す:
#   TYPESAFE_API_KEY   必須（Jev。https://console.typesafe.ai で発行）
#   ANTHROPIC_API_KEY  任意（Claude を API 直結で動かす）
#   OPENAI_API_KEY     任意（GPT を API 直結で動かす）
# どちらもなくても、Claude Code にログイン済みなら Claude の Agent SDK（サブスク認証）モードは使える。
#
# macOS で akc（AI KeyChain CLI）を使っている場合は、キーをキーチェーンから子プロセスにだけ渡す（環境変数が未設定のときだけ）。
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null 2>&1; then uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
  else python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; fi || exit 1
fi
echo "→ http://localhost:${ARENA_PORT:-8770}"
if command -v akc >/dev/null 2>&1 && [ -z "$TYPESAFE_API_KEY" ]; then
  export TYPESAFE_API_KEY=keychain://TYPESAFE_API_KEY
  [ -z "$ANTHROPIC_API_KEY" ] && akc check ANTHROPIC_API_KEY >/dev/null 2>&1 && export ANTHROPIC_API_KEY=keychain://ANTHROPIC_API_KEY
  if [ -z "$OPENAI_API_KEY" ]; then
    for k in ${OPENAI_KEY_NAME:-OPENAI_API_KEY_SHARE OPENAI_API_KEY}; do
      if akc check "$k" >/dev/null 2>&1; then export OPENAI_API_KEY="keychain://$k" ARENA_OPENAI_KEY_NAME="$k"; break; fi
    done
  fi
  exec akc run -- .venv/bin/python -m arena
fi
exec .venv/bin/python -m arena
