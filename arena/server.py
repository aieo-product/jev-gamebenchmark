"""対戦の進行と配信（ゲームにも戦略にも依存しない）。

サーバーがゲームを進行し、ブラウザは描画とログ表示だけを行う。
  左 : Jev     … 戦略ファイルの問い（既定: 候補ごとに Score 1問）を1リクエストで送り、戦略の pick() で手を決める
        または 人間 … ブラウザのキーボードで操作する（← → 移動、↑/X 回転、Z 逆回転、↓ 1段落とす、Space 一気に落とす）
  右 : LLM     … 同じ特徴量・同じ評価基準を渡し、{"pick": "pNN"} を返させる（Claude / GPT）。または別の戦略の Jev
"""
import asyncio
import json
import os
import random
import statistics
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp
from aiohttp import web

from . import agents as A
from .games import GAMES
from .strategy import ROOT, Strategy, StrategyError, default_strategy, list_strategies

LOG_DIR = ROOT / "logs"
PORT = int(os.environ.get("ARENA_PORT", "8770"))


@dataclass
class Stats:
    decisions: int = 0
    not_in_time: int = 0      # 着地までに回答が間に合わなかった
    infeasible: int = 0       # 回答が届いたとき、もうその置き方ができない高さまで落ちていた
    invalid: int = 0          # 候補にない ID／読み取れない出力
    format_dev: int = 0       # 指定形式からの逸脱（読み取れた場合も含む）
    errors: int = 0
    tok_in: int = 0
    tok_out: int = 0
    cost_usd: float = 0.0
    ms: list = field(default_factory=list)


class HumanAgent:
    """左側を人間が操作するときの置き物。手はブラウザから届くキーで決まる。"""
    model = model_reported = "human"

    def __init__(self):
        self.keys = asyncio.Queue()

    async def start(self, system=None):
        pass

    async def stop(self):
        pass


KEY_LABEL = {"left": "←", "right": "→", "cw": "↻", "ccw": "↺", "down": "↓", "drop": "⤓"}
HUMAN_LOCK_GRACE = 0.5  # 着地してから固定されるまでの猶予（この間は動かせる）


class Player:
    def __init__(self, side, agent, strategy, game, match):
        self.side, self.agent, self.strategy, self.game, self.match = side, agent, strategy, game, match
        self.thinking_since, self.stats, self._late = None, Stats(), None
        self.human = isinstance(agent, HumanAgent)

    async def run_human(self):
        m, g = self.match, self.game
        opp = m.players["llm"].game
        loop, q = asyncio.get_running_loop(), self.agent.keys
        while g.alive and not m.over:
            if not g.spawn():
                break
            label, keys, t0 = g.label(), [], time.perf_counter()
            n = self.stats.decisions = self.stats.decisions + 1
            self.thinking_since = time.time() * 1000
            while not q.empty():  # 前の手の余ったキーは捨てる
                q.get_nowait()
            next_fall, landed_at = loop.time() + m.gravity(), None
            while not m.over:
                deadline = next_fall if landed_at is None else min(next_fall, landed_at + HUMAN_LOCK_GRACE)
                try:
                    key = await asyncio.wait_for(q.get(), timeout=max(0.0, deadline - loop.time()))
                except asyncio.TimeoutError:
                    key = None
                if key:
                    keys.append(KEY_LABEL.get(key, "?"))
                    if key == "left":
                        g.move(-1)
                    elif key == "right":
                        g.move(1)
                    elif key == "cw":
                        g.rotate(1)
                    elif key == "ccw":
                        g.rotate(-1)
                    elif key == "down":
                        g.step_down()
                        next_fall = loop.time() + m.gravity()
                    elif key == "drop":
                        while g.step_down():
                            pass
                        break
                    if landed_at is not None and g.can_fall():
                        landed_at = None
                if landed_at is not None and loop.time() >= landed_at + HUMAN_LOCK_GRACE:
                    break
                if loop.time() >= next_fall:
                    if not g.step_down() and landed_at is None:
                        landed_at = loop.time()
                    next_fall = loop.time() + m.gravity()
            if m.over:
                break
            d = A.Decision(pick="human", ms=(time.perf_counter() - t0) * 1000, raw_output="".join(keys), summary=f"操作: {''.join(keys) or '（なし）'}")
            self._account(d)
            await m.log_decision(self, n, label, d, "applied", None)
            self.thinking_since = None
            for text in await g.lock(opp, asyncio.sleep, m.label(self.side)):
                await m.event(text)
            await asyncio.sleep(m.lock_delay)
        self.thinking_since, g.piece = None, None

    def snapshot(self):
        s, ms, g = self.stats, self.stats.ms, self.game
        unit, count = g.attack_units()
        return {
            **g.snapshot(), "alive": g.alive, "thinking_since": self.thinking_since, "tiles": g.tiles(), "cost_unit": unit,
            "stats": {
                **g.result(), "decisions": s.decisions, "not_in_time": s.not_in_time, "infeasible": s.infeasible, "invalid": s.invalid,
                "format_dev": s.format_dev, "errors": s.errors, "tok_in": s.tok_in, "tok_out": s.tok_out, "cost_usd": round(s.cost_usd, 6),
                "cost_per_move": round(s.cost_usd / len(ms), 7) if ms else None, "cost_per_unit": round(s.cost_usd / count, 7) if count else None,
                "ms_last": round(ms[-1]) if ms else None, "ms_avg": round(statistics.mean(ms)) if ms else None,
                "ms_p50": round(statistics.median(ms)) if ms else None, "ms_max": round(max(ms)) if ms else None, "ms_hist": [round(v) for v in ms[-60:]],
            },
        }

    async def run(self):
        if self.human:
            return await self.run_human()
        m, g = self.match, self.game
        opp = m.players["llm" if self.side == "jev" else "jev"].game
        loop = asyncio.get_running_loop()
        while g.alive and not m.over:
            if self._late:  # 前の手の回答がまだ返っていなければ、返るまで次を聞けない
                await asyncio.gather(self._late, return_exceptions=True)
                self._late = None
            if not g.spawn():
                break
            label, cands, view = g.label(), g.candidates(), g.view(opp)
            view["candidates"] = cands  # 戦略が候補どうしを見比べられるように
            by_id = {c["id"]: c for c in cands}
            # ここから戦略の仕事: 状況と候補を「モデルに見せる形」にする
            ctx = self.strategy.context(view)
            feats = {c["id"]: self.strategy.features(c, view) for c in cands}
            n = self.stats.decisions = self.stats.decisions + 1

            self.thinking_since = time.time() * 1000
            task = asyncio.create_task(self.agent.decide(A.Request(self.strategy, ctx, feats)))
            next_fall, landed = loop.time() + m.gravity(), False
            while not task.done():  # 考えている間も落ち続ける
                await asyncio.wait({task}, timeout=max(0.0, next_fall - loop.time()))
                if task.done() or m.over:
                    break
                if not g.step_down():
                    landed = True
                    break
                next_fall += m.gravity()
            if m.over:
                self._late = task  # 途中の問い合わせは捨てるが、接続を壊さないよう完了は待つ
                break

            outcome = "applied"
            if landed:  # 回答より先に着地 → 出現位置のまま固定
                outcome = "not_in_time"
                self.stats.not_in_time += 1
                self._late = asyncio.create_task(self._log_late(task, n, label))
            else:
                d = task.result()
                c = by_id.get(d.pick or "")
                if c is None and not d.raw_output:       # 通信・SDK の失敗で出力そのものがない
                    outcome = "error"
                    self.stats.errors += 1
                elif c is None:                           # 出力はあるが、読み取れない／候補にない ID
                    outcome = "invalid"
                    self.stats.invalid += 1
                elif not g.steer(c):
                    outcome = "infeasible"
                    self.stats.infeasible += 1
                self._account(d)
                await m.log_decision(self, n, label, d, outcome, c and feats[c["id"]])
            self.thinking_since = None
            for text in await g.lock(opp, asyncio.sleep, m.label(self.side)):
                await m.event(text)
            await asyncio.sleep(m.lock_delay)
        self.thinking_since, g.piece = None, None

    def _account(self, d):
        s = self.stats
        s.ms.append(d.ms)
        s.tok_in += d.tok_in
        s.tok_out += d.tok_out
        s.cost_usd += d.cost_usd or 0.0
        s.format_dev += not d.format_ok

    async def _log_late(self, task, n, label):
        d = await task
        self._account(d)
        await self.match.log_decision(self, n, label, d, "not_in_time", None)


class Match:
    def __init__(self, hub, cfg):
        self.hub, self.cfg = hub, cfg
        self.gmod = GAMES[cfg.get("game", "blobs")]
        self.seed = cfg.get("seed") or random.randrange(1 << 30)
        self.g0, self.duration = float(cfg.get("gravity_ms", 800)) / 1000, float(cfg.get("duration_s", 180))
        self.lock_delay, self.over, self.t0, self.stopped = 0.12, False, None, False
        self.provider = cfg.get("provider", "claude-api")
        self.left = "human" if cfg.get("left") == "human" else "jev"
        left = Strategy(self.gmod, cfg.get("strategy") or default_strategy(self.gmod.ID))
        # 右側は、LLM なら左と同じ戦略（同じ特徴量・同じ基準で比べる）。Jev どうしなら別の戦略を指定できる
        right = Strategy(self.gmod, cfg.get("strategy_right") or left.name) if self.provider == "jev" else left
        seq = self.gmod.Sequence(self.seed)
        agents = {"jev": HumanAgent() if self.left == "human" else A.JevAgent(), "llm": A.PROVIDERS[self.provider]["cls"](cfg["model"], cfg.get("thinking", "off"))}
        self.players = {k: Player(k, agents[k], s, self.gmod.Game(seq, self.seed + i + 1, cfg), self) for i, (k, s) in enumerate((("jev", left), ("llm", right)))}
        LOG_DIR.mkdir(exist_ok=True)
        self.log_path = LOG_DIR / f"{self.gmod.ID}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
        self.log_file = self.log_path.open("w", encoding="utf-8")

    def label(self, side):
        return ("YOU" if self.left == "human" else "JEV") if side == "jev" else A.PROVIDERS[self.provider]["label"]

    def gravity(self):
        """1段落ちるまでの秒数。20秒ごとに 15% 速くなる（下限 60ms）。"""
        steps = int((time.monotonic() - self.t0) // 20) if self.t0 else 0
        return max(0.06, self.g0 * 0.85 ** steps)

    def write(self, rec):
        self.log_file.write(json.dumps({"ts": datetime.now().isoformat(timespec="milliseconds"), **rec}, ensure_ascii=False) + "\n")
        self.log_file.flush()

    async def event(self, text):
        self.write({"type": "event", "text": text})
        await self.hub.send({"t": "event", "text": text, "at": self.elapsed()})

    def elapsed(self):
        return round(time.monotonic() - self.t0, 1) if self.t0 else 0

    async def log_decision(self, player, n, piece, d, outcome, feats):
        rec = {"type": "decision", "side": player.side, "n": n, "piece": piece, "outcome": outcome, "pick": d.pick, "ms": round(d.ms, 1),
               "tok_in": d.tok_in, "tok_out": d.tok_out, "cost_usd": d.cost_usd, "format_ok": d.format_ok, "error": d.error,
               "raw_output": d.raw_output, "summary": d.summary, "chosen_features": feats, "at": self.elapsed()}
        self.write({**rec, "request": d.request, "response": d.response})
        await self.hub.send({"t": "decision", **rec, "request": d.request, "response": d.response})

    async def run(self):
        P = self.players
        try:
            await self.hub.send({"t": "phase", "phase": "connecting", "text": "接続中…（両者とも接続を張ってウォームアップ）"})
            t = time.perf_counter()
            await asyncio.gather(*(p.agent.start(p.strategy.llm_system()) for p in P.values()))
            await self.hub.send({"t": "phase", "phase": "ready", "text": f"準備完了（{time.perf_counter() - t:.1f}秒）。3秒後に開始"})
            self.write({"type": "start", "game": self.gmod.ID, "left": self.left, "config": self.cfg, "seed": self.seed, "llm_model": P["llm"].agent.model, "llm_provider": self.provider,
                        "strategy": {k: p.strategy.describe() for k, p in P.items()}, "llm_system_prompt": P["llm"].strategy.llm_system(),
                        "pricing": {"jev_usd_per_mtok": {"input": 0.042, "output": 0.0}, "llm_list_usd_per_mtok": A.llm_list_price(P["llm"].agent.model),
                                    "note": "API 直結は定価×使用トークン（実請求）。claude-sdk はサブスク認証で実請求なし、SDK が返す API 換算額"}})
            await asyncio.sleep(3)
            self.t0 = time.monotonic()
            await self.hub.send({"t": "phase", "phase": "playing", "text": "対戦中"})
            tasks = [asyncio.create_task(p.run()) for p in P.values()]
            ticker = asyncio.create_task(self._tick())
            crashed = None
            while not self.over:
                await asyncio.sleep(0.05)
                crashed = next((t.exception() for t in tasks if t.done() and t.exception()), None)  # 戦略ファイルのバグなど
                if crashed or any(not p.game.alive for p in P.values()) or self.elapsed() >= self.duration:
                    self.over = True
            await asyncio.gather(*tasks, return_exceptions=True)
            late = [p._late for p in P.values() if p._late]
            if late:
                await asyncio.wait(late, timeout=15)
            ticker.cancel()
            if crashed:
                tb = "".join(traceback.format_exception(crashed)[-3:])
                self.write({"type": "crash", "error": tb})
                await self.hub.send({"t": "phase", "phase": "error", "text": f"試合を中断: {type(crashed).__name__}: {crashed}（戦略ファイルを確認してください）"})
            else:
                await self._finish()
        except asyncio.CancelledError:
            self.over = True
            raise
        except Exception as e:
            self.over = True
            await self.hub.send({"t": "phase", "phase": "error", "text": f"エラー: {type(e).__name__}: {e}"})
        finally:
            for p in P.values():
                try:
                    await asyncio.wait_for(p.agent.stop(), timeout=15)
                except Exception:
                    pass
            self.log_file.close()

    async def _tick(self):
        while True:
            await self.hub.send(self.state())
            await asyncio.sleep(1 / 15)

    def state(self):
        P = self.players
        return {"t": "state", "game": self.gmod.ID, "left": self.left, "elapsed": self.elapsed(), "duration": self.duration, "gravity_ms": round(self.gravity() * 1000),
                "llm_model": P["llm"].agent.model, "llm_provider": self.provider, "llm_label": A.PROVIDERS[self.provider]["label"], "jev_model": P["jev"].agent.model_reported,
                "strategy": {k: p.strategy.name for k, p in P.items()}, "players": {k: p.snapshot() for k, p in P.items()}}

    async def _finish(self):
        j, c = self.players["jev"].game, self.players["llm"].game
        if self.stopped and j.alive and c.alive and self.elapsed() < self.duration:
            winner, reason = "draw", "途中で停止された（結果は無効）"
        elif j.alive != c.alive:
            winner, reason = ("jev" if j.alive else "llm"), j.LOSE_TEXT
        else:
            winner = "jev" if j.rank() > c.rank() else "llm" if c.rank() > j.rank() else "draw"
            reason = f"時間切れ。{j.RANK_TEXT}で判定" if j.alive else "両者同時に終了"
        summary = {"type": "end", "game": self.gmod.ID, "left": self.left, "winner": winner, "llm_label": A.PROVIDERS[self.provider]["label"], "llm_model": self.players["llm"].agent.model,
                   "reason": reason, "stopped": reason.startswith("途中で停止"), "elapsed": self.elapsed(), "seed": self.seed, "strategy": {k: p.strategy.name for k, p in self.players.items()},
                   "players": {k: p.snapshot()["stats"] for k, p in self.players.items()}, "log": str(self.log_path.relative_to(ROOT))}
        self.write(summary)
        await self.hub.send(self.state())
        await self.hub.send({"t": "end", **summary})


class Hub:
    def __init__(self):
        self.clients, self.match_task, self.match = set(), None, None

    async def send(self, msg):
        data = json.dumps(msg, ensure_ascii=False)
        for ws in list(self.clients):
            try:
                await ws.send_str(data)
            except Exception:
                self.clients.discard(ws)

    async def stop(self):
        if self.match_task and not self.match_task.done():
            self.match.over = self.match.stopped = True  # 途中で止めた試合は、結果に stopped を付ける（ベンチマークの集計から外すため）
            try:
                await asyncio.wait_for(self.match_task, timeout=25)
            except Exception:
                self.match_task.cancel()

    async def start(self, cfg):
        await self.stop()
        try:
            self.match = Match(self, cfg)  # 戦略ファイルはここで読み込み直す
        except StrategyError as e:
            await self.send({"t": "phase", "phase": "error", "text": f"戦略エラー: {e}"})
            return
        self.match_task = asyncio.create_task(self.match.run())


hub = Hub()


def hello():
    return {"t": "hello", "jev_key": bool(A.JEV_KEY),
            "games": {k: {"title": g.TITLE, "strategies": list_strategies(k)} for k, g in GAMES.items()},
            "providers": {k: {"name": v["name"] + (f"（{A.OPENAI_KEY_NAME}）" if k == "openai" and A.OPENAI_KEY_NAME != "OPENAI_API_KEY" else ""), "label": v["label"], "models": v["models"],
                              "unlisted": [m for m in v["models"] if k == "openai" and A.OPENAI_LISTED and m not in A.OPENAI_LISTED],
                              "available": v["available"](), "need": v["need"]} for k, v in A.PROVIDERS.items()}}


async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    hub.clients.add(ws)
    await ws.send_str(json.dumps(hello(), ensure_ascii=False))
    try:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            cmd = json.loads(msg.data)
            if cmd.get("cmd") in ("start", "stop"):
                print(datetime.now().strftime("%H:%M:%S"), "cmd", cmd.get("cmd"), {k: cmd.get(k) for k in ("game", "provider", "model", "strategy", "seed")}, "from", request.headers.get("User-Agent", "?")[:40], flush=True)
            if cmd.get("cmd") == "start":
                prov = A.PROVIDERS.get(cmd.get("provider"))
                if not prov or not prov["available"]() or cmd.get("model") not in prov["models"] or cmd.get("game") not in GAMES:
                    continue
                await hub.start(cmd)
            elif cmd.get("cmd") == "stop":
                await hub.stop()
            elif cmd.get("cmd") == "key":  # 人間が操作する左側へのキー入力
                m = hub.match
                if m and not m.over and m.left == "human" and cmd.get("key") in KEY_LABEL:
                    m.players["jev"].agent.keys.put_nowait(cmd["key"])
            elif cmd.get("cmd") == "hello":  # 戦略ファイルの一覧を取り直す
                await ws.send_str(json.dumps(hello(), ensure_ascii=False))
    finally:
        hub.clients.discard(ws)
    return ws


async def index(_):
    return web.FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


def main():
    app = web.Application()
    app.on_startup.append(lambda _app: A.discover_openai_models())
    app.add_routes([web.get("/", index), web.get("/ws", ws_handler)])
    print(f"Jev Game Benchmark → http://localhost:{PORT}   (Jev key: {'OK' if A.JEV_KEY else 'なし'} / Anthropic key: {'OK' if A.ANTHROPIC_KEY else 'なし'} / OpenAI key: {'OK' if A.OPENAI_KEY else 'なし'})", flush=True)
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
