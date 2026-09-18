"""戦略の質だけを測る（対戦なし・時間制限なし・同じ並び）。戦略をいじったら、まずこれで確かめるのが速くて安い。

  python -m arena.solo blobs                          # 推奨戦略の Jev を 80手 × seed 1001,2002
  python -m arena.solo blobs -s build-big-chains,pop-early      # 戦略どうしを比べる
  python -m arena.solo blocks -a jev,code,random     # コードだけの評価関数・ランダムと比べる
  python -m arena.solo blobs -a jev,claude-api:claude-haiku-4-5,openai:gpt-5.6-luna   # LLM にも同じ戦略で打たせる
  python -m arena.solo blobs -a code -n 120 --seeds 1,2,3,4,5                        # API を使わない確認

結果は bench/solo-<game>-<日時>.json に保存する。
"""
import argparse
import asyncio
import json
import random
import statistics
from datetime import datetime

from . import agents as A
from .games import GAMES
from .strategy import ROOT, Strategy, default_strategy


class _Local:
    async def start(self, system=None): pass
    async def stop(self): pass


class RandomAgent(_Local):
    def __init__(self): self.rng = random.Random(7)
    async def decide(self, req): return A.Decision(self.rng.choice(list(req.feats)), 0.0)


class CodeAgent(_Local):
    """ゲームごとの「コードだけの評価関数」（games/*.py の baseline_value）。戦略ファイルは使わない。"""
    def __init__(self, gmod): self.gmod, self.cands = gmod, None
    async def decide(self, req): return A.Decision(max(self.cands, key=self.gmod.baseline_value)["id"], 0.0)


def make_agent(spec, gmod):
    if spec == "random": return RandomAgent()
    if spec == "code": return CodeAgent(gmod)
    if spec == "jev": return A.JevAgent()
    prov, model = spec.split(":", 1)
    return A.PROVIDERS[prov]["cls"](model, "off")


class _Idle:
    """対戦相手なし（盤面は空のまま、何も送ってこない）。"""
    def __init__(self, gmod): self.board, self.pending = gmod.empty_board(), 0


async def play(gmod, spec, strategy, moves, seed):
    agent, game = make_agent(spec, gmod), gmod.Game(gmod.Sequence(seed), seed, {})
    opp, ms, tok, cost, bad = _Idle(gmod), [], 0, 0.0, 0
    await agent.start(strategy.llm_system())
    try:
        for _ in range(moves):
            if not game.spawn():
                break
            cands, view = game.candidates(), game.view(opp)
            view["candidates"] = cands
            if isinstance(agent, CodeAgent):
                agent.cands = cands
            ctx = strategy.context(view)
            d = await agent.decide(A.Request(strategy, ctx, {c["id"]: strategy.features(c, view) for c in cands}))
            c = next((x for x in cands if x["id"] == d.pick), None)
            if c is None:
                bad += 1
            else:
                game.steer(c)
            ms.append(d.ms); tok += d.tok_in; cost += d.cost_usd or 0.0
            await game.lock(opp, lambda _s: asyncio.sleep(0), spec)
            opp.pending = 0
    finally:
        await agent.stop()
    return {"agent": spec, "strategy": None if spec in ("random", "code") else strategy.name, "seed": seed, **game.result(), "survived": game.alive,
            "ms_p50": round(statistics.median(ms)) if ms else None, "tok_in": tok, "cost_usd": round(cost, 5), "unreadable": bad}


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game", choices=list(GAMES))
    ap.add_argument("-s", "--strategies", default="", help="戦略名をカンマ区切りで（strategies/<game>/<名前>.py）")
    ap.add_argument("-a", "--agents", default="jev", help="jev / code / random / claude-api:<model> / openai:<model> / claude-sdk:<model>")
    ap.add_argument("-n", "--moves", type=int, default=80)
    ap.add_argument("--seeds", default="1001,2002")
    ap.add_argument("-o", "--out", default="", help="結果の保存先（既定: bench/solo-<game>-<日時>.json。docs に載せるなら docs/results/solo-<game>-….json）")
    args = ap.parse_args()
    gmod, out = GAMES[args.game], []
    names = (args.strategies or default_strategy(args.game)).split(",")
    for sname in names:
        strategy = Strategy(gmod, sname)
        for spec in args.agents.split(","):
            if spec in ("random", "code") and sname != names[0]:
                continue  # 戦略を使わないエージェントは1回でよい
            for seed in map(int, args.seeds.split(",")):
                r = await play(gmod, spec, strategy, args.moves, seed)
                out.append(r)
                show = {k: v for k, v in r.items() if k not in ("agent", "strategy", "seed")}
                print(f"{spec:<28} {r['strategy'] or '-':<12} seed {seed}: {show}", flush=True)
    path = ROOT / (args.out or f"bench/solo-{args.game}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"game": args.game, "date": datetime.now().isoformat(timespec="seconds"), "moves": args.moves, "results": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("→", path.relative_to(ROOT))


if __name__ == "__main__":
    asyncio.run(main())
