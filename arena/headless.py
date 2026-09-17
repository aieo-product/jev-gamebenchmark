"""ブラウザなしで1試合まわす（サーバーが起動している必要がある）。CI や連続計測用。

  python -m arena.headless blobs claude-api claude-haiku-4-5 --seconds 60
  python -m arena.headless blocks jev jev-latest --strategy default --strategy-right baseline   # 戦略どうしの対戦
"""
import argparse
import asyncio
import json
import os

import aiohttp


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game")
    ap.add_argument("provider", help="claude-api | openai | jev | claude-sdk")
    ap.add_argument("model")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--strategy", default="default")
    ap.add_argument("--strategy-right", default="")
    ap.add_argument("--thinking", default="off")
    ap.add_argument("--gravity-ms", type=int, default=800)
    ap.add_argument("--garbage-rate", type=int, default=70)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    port = os.environ.get("ARENA_PORT", "8770")
    async with aiohttp.ClientSession() as s, s.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
        await ws.send_str(json.dumps({"cmd": "start", "game": a.game, "provider": a.provider, "model": a.model, "thinking": a.thinking, "strategy": a.strategy,
                                      "strategy_right": a.strategy_right, "gravity_ms": a.gravity_ms, "duration_s": a.seconds, "garbage_rate": a.garbage_rate, "seed": a.seed or None}))
        n = {"jev": 0, "llm": 0}
        async for m in ws:
            d = json.loads(m.data)
            if d["t"] == "phase":
                print("PHASE", d["text"], flush=True)
                if d["phase"] == "error":
                    return
            elif d["t"] == "decision":
                n[d["side"]] += 1
                if n[d["side"]] <= 2 or d["outcome"] != "applied":
                    print(f"{d['side']:<4} #{d['n']} {d['piece']} {d['ms']:.0f}ms in={d['tok_in']} out={d['tok_out']} ${d['cost_usd'] or 0:.6f} {d['outcome']} | {d['summary'][:90]}{' | ' + d['error'] if d['error'] else ''}", flush=True)
            elif d["t"] == "event":
                print("     ", d["text"], flush=True)
            elif d["t"] == "end":
                print(f"END winner={d['winner']} ({d['llm_label']} {d['llm_model']}) strategies={d['strategy']} — {d['reason']}")
                for k, v in d["players"].items():
                    print(f"  {k:<4}", {x: y for x, y in v.items() if x != "ms_hist"})
                return


asyncio.run(main())
