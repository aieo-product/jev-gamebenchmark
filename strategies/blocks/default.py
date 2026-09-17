"""Blocksの既定の戦略（最適化後）。このファイルをコピーして、自分の戦略を作ってください。

考え方: 幾何（穴・高さ・凹凸の計算）はコード、良し悪しの評価は Jev。
- 競合する要素（穴・高さ・凹凸）を、評価基準の同じ段に並べて書く（「穴あり＝最低」だけにすると、穴を避けて塔を積んで自滅した）
- 置く前後の差分（増えた高さ、凹凸の変化）はコードで計算して渡す。引き算をモデルにさせない
- スコアが同点の候補は多い。同点どうしの優劣は数値比較なので、コード（tie_break）が決める
"""
from arena.games.blocks import bumpiness, count_holes, heights

QUESTION = "How good is this piece placement?"
INSTRUCTIONS = "How good is the piece placement described in `candidates.{id}`?"
LEVELS = [
    "Creates two or more new holes, or leaves the stack dangerously close to the top",
    "Creates one new hole, or makes the surface much more uneven, or makes the stack much taller",
    "No new holes; the surface gets slightly more uneven or the stack slightly taller",
    "No new holes; the surface stays as flat as before or gets flatter, and the stack does not grow",
    "Clears one or more lines without creating new holes",
]

TIE_EPS = 0.05  # スコア差がこれ以内なら同点とみなす
TIE_BREAK_TEXT = "if several candidates are equally good, prefer the lower stack, then the flatter surface"


def tie_break(f):
    """同点の候補から選ぶ順（小さいほど優先）: 低い → 平ら"""
    return (f["stack_height_after"], f["surface_unevenness_after"])


def context(view):
    """全候補に共通の状況。"""
    return {"current_piece": view["current"], "incoming_garbage_lines": view["pending"],
            "own_max_height": max(heights(view["board"])), "opponent_max_height": max(heights(view["opponent_board"]))}


def features(cand, view):
    """候補1つぶんの、モデルに見せる特徴量。"""
    h0, h1 = heights(cand["board_before"]), heights(cand["board_after"])
    holes0, holes1 = count_holes(cand["board_before"]), count_holes(cand["board_after"])
    return {
        "lines_cleared": cand["lines"],
        "new_holes": max(0, holes1 - holes0),
        "holes_removed": max(0, holes0 - holes1),
        "stack_height_after": max(h1),
        "stack_height_increase": max(h1) - max(h0),
        "surface_unevenness_after": bumpiness(h1),
        "surface_unevenness_change": bumpiness(h1) - bumpiness(h0),
    }
