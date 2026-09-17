"""対戦するエージェント（Jev / Claude / GPT）。ゲームにも戦略にも依存しない通信部分。

API キーは環境変数から読む（TYPESAFE_API_KEY は必須、ANTHROPIC_API_KEY・OPENAI_API_KEY は任意）。
読み込んだら環境から消す（Claude Agent SDK の子プロセスに渡さないため）。
"""
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent


def _take_key(name):
    v = os.environ.pop(name, "")
    return "" if v.startswith("keychain://") else v  # akc run を通していない（参照のまま）なら未設定として扱う


JEV_KEY = _take_key("TYPESAFE_API_KEY")
ANTHROPIC_KEY = _take_key("ANTHROPIC_API_KEY")
OPENAI_KEY = _take_key("OPENAI_API_KEY")
for _k in ("ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
    os.environ.pop(_k, None)

try:
    import anthropic
except ImportError:  # 任意の依存
    anthropic = None
try:
    import openai
except ImportError:
    openai = None
try:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage
except ImportError:
    ClaudeSDKClient = None

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
# Jev の公式価格（typesafe.ai のトップページと紹介ブログ、2026-09-17 確認）: 入力 $0.042 / 100万トークン、出力は無料
JEV_USD_PER_INPUT_TOKEN = 0.042 / 1_000_000
# 定価 $ / 100万トークン。Claude: (input, output)。OpenAI: (input, cached input, output)（標準・短いコンテキスト、2026-09-17 確認）
CLAUDE_LIST_PRICE = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-5": (2.0, 10.0), "claude-opus-5": (5.0, 25.0), "claude-fable-5-1": (10.0, 50.0)}
OPENAI_LIST_PRICE = {"gpt-5.6-sol": (4.0, 0.40, 20.0), "gpt-5.6-terra": (2.0, 0.20, 12.0), "gpt-5.6-luna": (0.20, 0.02, 1.20)}
CLAUDE_MODELS, OPENAI_MODELS = list(CLAUDE_LIST_PRICE), list(OPENAI_LIST_PRICE)
OPENAI_LISTED = set()  # 起動時に models.list() で確認できたもの
OPENAI_KEY_NAME = os.environ.get("ARENA_OPENAI_KEY_NAME", "OPENAI_API_KEY")  # 表示用（値ではない）


@dataclass
class Request:
    """1手ぶんの問い合わせ。feats は 候補 ID → 特徴量（戦略の features() の結果）。"""
    strategy: object
    ctx: dict
    feats: dict


@dataclass
class Decision:
    pick: str | None
    ms: float
    tok_in: int = 0
    tok_out: int = 0
    cost_usd: float | None = None
    raw_output: str = ""          # そのエージェントが返した「操作の根拠になった出力」
    summary: str = ""             # ログ1行用
    request: object = None
    response: object = None
    format_ok: bool = True        # 指定した出力形式どおりだったか
    error: str | None = None


WARMUP_USER = '{"candidates": {"p00": {"note": "warmup"}}}'
REMINDER = '\n\nReply with ONLY {"pick": "pNN"} for the single best candidate. No analysis, no code fences.'
STRICT_JSON = re.compile(r'^\s*\{\s*"pick"\s*:\s*"(p\d{2})"\s*\}\s*$')
LOOSE_PICK = re.compile(r'"pick"\s*:\s*"(p\d{2})"')


def parse_pick(raw):
    strict, loose = STRICT_JSON.match(raw), LOOSE_PICK.search(raw)
    return (strict.group(1) if strict else loose.group(1) if loose else None), bool(strict)


# ------------------------------------------------------------------------------- Jev
class JevAgent:
    def __init__(self, model="jev-latest", thinking="off"):
        self.session = None
        self.model = self.model_reported = "jev-latest"

    async def start(self, system=None):
        if not JEV_KEY:
            raise RuntimeError("TYPESAFE_API_KEY がありません（README の「起動」を参照）。")
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=4, keepalive_timeout=120), timeout=aiohttp.ClientTimeout(total=20),
            headers={"Authorization": f"Bearer {JEV_KEY}", "Content-Type": "application/json"},
        )
        # TLS 接続を先に張っておく（1手目だけ不利にならないように）
        await self._post({"state": "warmup", "model": "jev-latest", "questions": {"w": {"type": "noul", "instructions": "Is this a warmup?"}}})

    async def stop(self):
        if self.session:
            await self.session.close()

    async def _post(self, body):
        for attempt in range(3):
            async with self.session.post(JEV_ENDPOINT, data=json.dumps(body)) as r:
                data = await r.json(content_type=None)
                if r.status in (408, 429, 529) or r.status >= 500:
                    await asyncio.sleep(0.3 * 2 ** attempt)
                    continue
                return r.status, data
        return r.status, data

    async def decide(self, req: Request) -> Decision:
        s = req.strategy
        body = {"state": s.state(req.feats, req.ctx), "model": s.model, "questions": s.questions(req.feats, req.ctx)}
        t0 = time.perf_counter()
        try:
            status, data = await self._post(body)
        except Exception as e:  # ネットワーク断など
            return Decision(None, (time.perf_counter() - t0) * 1000, request=body, error=f"{type(e).__name__}: {e}")
        ms = (time.perf_counter() - t0) * 1000
        if status != 200:
            return Decision(None, ms, request=body, response=data, error=f"HTTP {status}: {json.dumps(data, ensure_ascii=False)[:200]}")
        self.model_reported = data.get("model", self.model_reported)
        ans = data["answers"]
        pick, note = s.pick(ans, req.feats, req.ctx)
        u = data.get("usage", {})
        tin, tout = u.get("input_tokens", 0), u.get("output_tokens", 0)
        top = sorted(({"id": k, "score": round(v["score"], 3), "confidence": round(v.get("confidence", 0), 3)} for k, v in ans.items() if "score" in v), key=lambda t: -t["score"])[:3]
        return Decision(pick=pick, ms=ms, tok_in=tin, tok_out=tout, cost_usd=tin * JEV_USD_PER_INPUT_TOKEN,
                        raw_output=json.dumps({f"top3_of_{len(ans)}_answers": top, "picked": pick, "note": note}, ensure_ascii=False),
                        summary=f"{len(ans)}問 → {pick} {note}", request=body, response=data)


# ---------------------------------------------------------------------------- LLM 共通
def llm_payload(req):
    return {**req.ctx, "candidates": req.feats}


def llm_decision(raw, ms, tok_in, tok_out, cost, req_log, resp_log, err=None):
    pick, strict = parse_pick(raw)
    if err is None and pick is None:
        err = "出力から pick を読み取れない"
    return Decision(pick=pick, ms=ms, tok_in=tok_in, tok_out=tok_out, cost_usd=cost, raw_output=raw,
                    summary=f"raw: {raw.strip()[:80]!r}" + ("" if strict else " ｜形式逸脱"), request=req_log, response=resp_log, format_ok=strict, error=err)


class ClaudeApiAgent:
    """Anthropic API を直接呼ぶ（公式 SDK、接続は使い回し）。速度比較はこちらが本筋。"""

    def __init__(self, model, thinking):
        self.model, self.thinking, self.client, self.system = model, thinking, None, ""

    def _params(self):
        kw, max_tokens = {}, 256
        if self.model == "claude-haiku-4-5":  # Haiku 4.5 は adaptive 非対応。思考ありは固定バジェット（最小 1024）で代用する
            if self.thinking != "off":
                kw["thinking"], max_tokens = {"type": "enabled", "budget_tokens": 1024}, 2048
        elif "fable" in self.model:  # 思考は常時オンで無効化できない
            max_tokens = 4096
            if self.thinking == "adaptive-low":
                kw["output_config"] = {"effort": "low"}
        elif self.thinking == "off":
            kw["thinking"] = {"type": "disabled"}
        else:
            kw["thinking"], max_tokens = {"type": "adaptive"}, 4096
            if self.thinking == "adaptive-low":
                kw["output_config"] = {"effort": "low"}
        return kw, max_tokens

    async def start(self, system):
        if not ANTHROPIC_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY がありません。")
        self.system = system
        self.client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY, timeout=30.0)
        kw, max_tokens = self._params()  # ウォームアップ（TLS 接続を先に張る）。認証やモデル指定の誤りもここで分かる
        await self.client.messages.create(model=self.model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": WARMUP_USER + REMINDER}], **kw)

    async def stop(self):
        if self.client:
            await self.client.close()

    async def decide(self, req: Request) -> Decision:
        payload = llm_payload(req)
        kw, max_tokens = self._params()
        log = {"via": "anthropic-api", "model": self.model, "max_tokens": max_tokens, **kw, "system": self.system, "user_message": payload, "user_message_suffix": REMINDER}
        t0 = time.perf_counter()
        try:
            r = await self.client.messages.create(model=self.model, max_tokens=max_tokens, system=self.system,
                                                  messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False) + REMINDER}], **kw)
        except anthropic.APIStatusError as e:
            return Decision(None, (time.perf_counter() - t0) * 1000, request=log, error=f"HTTP {e.status_code}: {e.message}")
        except anthropic.APIConnectionError as e:
            return Decision(None, (time.perf_counter() - t0) * 1000, request=log, error=f"接続エラー: {e}")
        ms = (time.perf_counter() - t0) * 1000
        raw, u = "".join(b.text for b in r.content if b.type == "text"), r.usage
        tok_in = u.input_tokens + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0)
        pi, po = CLAUDE_LIST_PRICE.get(self.model, (0.0, 0.0))
        err = "refusal" if r.stop_reason == "refusal" else "max_tokens で打ち切り" if r.stop_reason == "max_tokens" else None
        return llm_decision(raw, ms, tok_in, u.output_tokens, (tok_in * pi + u.output_tokens * po) / 1_000_000, log,
                            {"text": raw, "usage": u.to_dict(), "stop_reason": r.stop_reason, "request_id": r._request_id}, err)


class OpenAIAgent:
    """OpenAI API（Responses API）を公式 SDK で直接呼ぶ。Claude と同じシステムプロンプト・同じ候補 JSON を渡す。"""
    EFFORT = {"off": "none", "adaptive-low": "low", "adaptive": "medium"}

    def __init__(self, model, thinking):
        self.model, self.thinking, self.client, self.system = model, thinking, None, ""

    def _params(self):
        effort = self.EFFORT.get(self.thinking, "none")
        return {"reasoning": {"effort": effort}, "max_output_tokens": 256 if effort == "none" else 4096, "store": False}

    async def start(self, system):
        if not OPENAI_KEY:
            raise RuntimeError("OPENAI_API_KEY がありません。")
        self.system = system
        self.client = openai.AsyncOpenAI(api_key=OPENAI_KEY, timeout=30.0)
        await self.client.responses.create(model=self.model, instructions=system, input=WARMUP_USER + REMINDER, **self._params())

    async def stop(self):
        if self.client:
            await self.client.close()

    async def decide(self, req: Request) -> Decision:
        payload, kw = llm_payload(req), self._params()
        log = {"via": "openai-responses-api", "model": self.model, **kw, "instructions": self.system, "user_message": payload, "user_message_suffix": REMINDER}
        t0 = time.perf_counter()
        try:
            r = await self.client.responses.create(model=self.model, instructions=self.system, input=json.dumps(payload, ensure_ascii=False) + REMINDER, **kw)
        except openai.APIStatusError as e:
            return Decision(None, (time.perf_counter() - t0) * 1000, request=log, error=f"HTTP {e.status_code}: {e.message}")
        except openai.APIConnectionError as e:
            return Decision(None, (time.perf_counter() - t0) * 1000, request=log, error=f"接続エラー: {e}")
        ms = (time.perf_counter() - t0) * 1000
        raw, u = r.output_text or "", r.usage
        cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
        pi, pc, po = OPENAI_LIST_PRICE.get(self.model, (0.0, 0.0, 0.0))
        cost = ((u.input_tokens - cached) * pi + cached * pc + u.output_tokens * po) / 1_000_000  # 出力には思考トークンも含まれる
        return llm_decision(raw, ms, u.input_tokens, u.output_tokens, cost, log, {"text": raw, "usage": u.to_dict(), "status": r.status, "response_id": r.id},
                            None if r.status == "completed" else f"status={r.status}")


class ClaudeSdkAgent:
    """Claude Agent SDK（Claude Code のログイン＝サブスク認証）。API キーを使わない。常駐クライアントで、毎手のあとに履歴を消す。"""

    def __init__(self, model, thinking):
        self.model, self.thinking, self.client, self.system = model, thinking, None, ""
        self.lock, self._clearing = asyncio.Lock(), None
        self._last_total_cost, self._last_session = 0.0, None

    async def start(self, system):
        self.system = system
        kw = {}
        if "fable" not in self.model:  # Fable は thinking を無効化できないので指定しない
            kw["thinking"] = {"type": "disabled"} if self.thinking == "off" else {"type": "adaptive"}
        if self.thinking == "adaptive-low":
            kw["effort"] = "low"
        self.client = ClaudeSDKClient(options=ClaudeAgentOptions(model=self.model, tools=[], system_prompt=system, max_turns=1,
                                                                 setting_sources=[], strict_mcp_config=True, cwd=str(ROOT), **kw))
        await self.client.connect()
        await self._ask(WARMUP_USER + REMINDER)
        await self._clear()

    async def stop(self):
        if self._clearing:
            await asyncio.gather(self._clearing, return_exceptions=True)
        if self.client:
            await self.client.disconnect()

    async def _ask(self, prompt):
        await self.client.query(prompt)
        text, result = "", None
        async for m in self.client.receive_response():
            if isinstance(m, AssistantMessage):
                text += "".join(getattr(b, "text", "") for b in m.content)
            elif isinstance(m, ResultMessage):
                result = m
        return text, result

    async def _clear(self):
        """会話履歴を消して、毎手を独立した判断にする（Jev も手をまたいだ記憶は持たない）。"""
        try:
            await self._ask("/clear")
        except Exception:
            pass

    async def decide(self, req: Request) -> Decision:
        payload = llm_payload(req)
        log = {"via": "claude-agent-sdk", "model": self.model, "thinking": self.thinking, "system_prompt": self.system, "user_message": payload, "user_message_suffix": REMINDER}
        async with self.lock:
            if self._clearing:
                await asyncio.gather(self._clearing, return_exceptions=True)
            t0 = time.perf_counter()
            try:
                text, res = await self._ask(json.dumps(payload, ensure_ascii=False) + REMINDER)
            except Exception as e:
                return Decision(None, (time.perf_counter() - t0) * 1000, request=log, error=f"{type(e).__name__}: {e}")
            ms = (time.perf_counter() - t0) * 1000
            self._clearing = asyncio.create_task(self._clear())  # 計測の外で履歴を消す
        raw = (res.result if res and res.result else text) or ""
        u = (res.usage if res else None) or {}
        tok_in = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        cost = None
        if res and res.total_cost_usd is not None:
            same = res.session_id == self._last_session and res.total_cost_usd >= self._last_total_cost
            cost = res.total_cost_usd - self._last_total_cost if same else res.total_cost_usd
            self._last_total_cost, self._last_session = res.total_cost_usd, res.session_id
        if cost is None:
            pi, po = CLAUDE_LIST_PRICE.get(self.model, (0.0, 0.0))
            cost = (tok_in * pi + u.get("output_tokens", 0) * po) / 1_000_000
        err = f"SDK error: {getattr(res, 'subtype', 'no result')}" if res is None or res.is_error else None
        return llm_decision(raw, ms, tok_in, u.get("output_tokens", 0), cost, log,
                            {"text": raw, "usage": u, "duration_api_ms": getattr(res, "duration_api_ms", None), "stop_reason": getattr(res, "stop_reason", None)}, err)


PROVIDERS = {
    "claude-api": {"label": "CLAUDE", "name": "Claude — API 直結", "cls": ClaudeApiAgent, "models": CLAUDE_MODELS, "available": lambda: bool(ANTHROPIC_KEY and anthropic), "need": "ANTHROPIC_API_KEY"},
    "openai": {"label": "GPT", "name": "GPT — OpenAI API 直結", "cls": OpenAIAgent, "models": OPENAI_MODELS, "available": lambda: bool(OPENAI_KEY and openai), "need": "OPENAI_API_KEY"},
    "jev": {"label": "JEV 2", "name": "Jev — 戦略どうしの対戦", "cls": JevAgent, "models": ["jev-latest"], "available": lambda: bool(JEV_KEY), "need": "TYPESAFE_API_KEY"},
    "claude-sdk": {"label": "CLAUDE", "name": "Claude — Agent SDK（サブスク）", "cls": ClaudeSdkAgent, "models": CLAUDE_MODELS, "available": lambda: ClaudeSDKClient is not None, "need": "pip install claude-agent-sdk"},
}


def llm_list_price(model):
    return CLAUDE_LIST_PRICE.get(model) or OPENAI_LIST_PRICE.get(model)


async def discover_openai_models():
    """このキーで実際に使えるモデルを確認する（一覧にないモデルも選べるが、権限がなければ開始時に 403 になる）。"""
    if not (OPENAI_KEY and openai):
        return
    try:
        async with openai.AsyncOpenAI(api_key=OPENAI_KEY, timeout=15.0) as c:
            ids = {m.id for m in (await c.models.list()).data}
        OPENAI_LISTED.update(m for m in OPENAI_LIST_PRICE if m in ids)
        OPENAI_MODELS.sort(key=lambda m: m not in OPENAI_LISTED)
    except Exception as e:
        print("OpenAI のモデル一覧を取得できませんでした:", type(e).__name__)
