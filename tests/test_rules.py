"""ルールと戦略ファイルの確認（API を使わない）。  python -m tests.test_rules"""
import asyncio

from arena.games import GAMES, blobs, blocks
from arena.solo import CodeAgent, play
from arena.strategy import Strategy, list_strategies


def test_blobs_chain():
    b = blobs.empty_board()
    def put(x, col):
        y = next(yy for yy in range(blobs.H - 1, -1, -1) if not b[yy][x]); b[y][x] = col
    for c in "RRRB": put(0, c)
    for c in "BB": put(1, c)
    put(2, "B")
    assert blobs.chain_potential(b, 1) == 2
    placed, _ = blobs.drop_pair(b, "RY", 0, 1)          # 赤を足すと 赤4つ → 青が落ちて4つ、の2連鎖
    final, steps = blobs.resolve(placed)
    assert [s["score"] for s in steps] == [40, 320], steps   # Blobs通の式: 1連鎖 40点、2連鎖目 10×4×8
    assert sum(1 for row in final for c in row if c) == 1


def test_blobs_candidates():
    assert len(blobs.enumerate_placements(blobs.empty_board(), "RB")) == 22
    assert len(blobs.enumerate_placements(blobs.empty_board(), "RR")) == 11


def test_blocks_lines():
    b = blocks.empty_board()
    b[-1] = ["G"] * 6 + [""] * 4
    cands = blocks.enumerate_placements(b, "I")
    assert max(c["lines"] for c in cands) == 1
    assert blocks.count_holes(b) == 0 and blocks.bumpiness(blocks.heights(b)) == 1


def test_strategies_load_and_play():
    """すべての戦略ファイルが読み込めて、features が全候補で JSON になること。コードの評価関数で20手進めて確かめる。"""
    for gid, gmod in GAMES.items():
        for name in list_strategies(gid):
            st = Strategy(gmod, name)
            assert st.llm_system() and 2 <= len(st.levels) <= 10
            r = asyncio.run(play(gmod, "code", st, 20, 1))
            assert r["pieces"] == 20, r


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
