"""全モデルと順番に対戦させて、結果を docs/results/ に保存する（サーバーが起動している必要がある）。

  python -m arena.bench blobs                       # 既定: API 直結の全モデル × seed 1001,2002 × 60秒、思考 off
  python -m arena.bench blocks --seconds 60 --opponents claude-api:claude-haiku-4-5,openai:gpt-5.6-luna
  python -m arena.report                            # docs/results/*.json → docs/benchmarks.md

費用の目安（2026-09 の定価）: 7モデル × 2試合 × 60秒で、LLM 側 $1〜3、Jev 側 $0.1〜0.4。
"""
import argparse
import asyncio
import json
import os
from datetime import datetime

import aiohttp

from .strategy import ROOT

DEFAULT_OPPONENTS = ("claude-api:claude-haiku-4-5,claude-api:claude-sonnet-5,claude-api:claude-opus-5,claude-api:claude-fable-5-1,"
                     "openai:gpt-5.6-luna,openai:gpt-5.6-terra,openai:gpt-5.6-sol")


async def one_match(ws, cfg):
    await ws.send_str(json.dumps({"cmd": "start", **cfg}))
    async for m in ws:
        d = json.loads(m.data)
        if d["t"] == "phase" and d["phase"] == "error":
            return {"error": d["text"]}
        if d["t"] == "end":
            for p in d["players"].values():
                p.pop("ms_hist", None)
            return d
    return {"error": "接続が切れた"}


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game")
    ap.add_argument("--opponents", default=DEFAULT_OPPONENTS, help="provider:model をカンマ区切りで")
    ap.add_argument("--strategy", default="default")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--seeds", default="1001,2002")
    ap.add_argument("--thinking", default="off")
    ap.add_argument("--gravity-ms", type=int, default=800)
    ap.add_argument("--garbage-rate", type=int, default=70)
    a = ap.parse_args()
    out = {"game": a.game, "date": datetime.now().isoformat(timespec="seconds"), "strategy": a.strategy, "seconds": a.seconds, "thinking": a.thinking,
           "gravity_ms": a.gravity_ms, "garbage_rate": a.garbage_rate, "matches": []}
    path = ROOT / "docs" / "results" / f"bench-{a.game}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession() as s, s.ws_connect(f"http://127.0.0.1:{os.environ.get('ARENA_PORT', '8770')}/ws") as ws:
        for opp in a.opponents.split(","):
            provider, model = opp.split(":", 1)
            for seed in map(int, a.seeds.split(",")):
                for attempt in range(3):  # 画面の「停止」などで途中で止められた試合は、やり直す
                    r = await one_match(ws, {"game": a.game, "provider": provider, "model": model, "thinking": a.thinking, "strategy": a.strategy,
                                             "gravity_ms": a.gravity_ms, "duration_s": a.seconds, "garbage_rate": a.garbage_rate, "seed": seed})
                    if not r.get("stopped"):
                        break
                    print(f"{model:<20} seed {seed}: 途中で停止されたので、やり直します", flush=True)
                    await asyncio.sleep(2)
                out["matches"].append({"provider": provider, "model": model, "seed": seed, **{k: r.get(k) for k in ("winner", "reason", "stopped", "elapsed", "players", "error")}})
                if r.get("error"):
                    print(f"{model:<20} seed {seed}: ERROR {r['error']}", flush=True)
                else:
                    j, l = r["players"]["jev"], r["players"]["llm"]
                    print(f"{model:<20} seed {seed}: winner={r['winner']:<4} {r['elapsed']:>5}s  p50 {j['ms_p50']}/{l['ms_p50']}ms  pieces {j['pieces']}/{l['pieces']}  "
                          f"$/move {j['cost_per_move']}/{l['cost_per_move']}  LLM cost ${l['cost_usd']:.3f}", flush=True)
                path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")  # 途中で止めても残るように毎回書く
                await asyncio.sleep(2)
    print("→", path.relative_to(ROOT))


if __name__ == "__main__":
    asyncio.run(main())
