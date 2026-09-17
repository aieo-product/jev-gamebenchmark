"""Blocksの最適化「前」の戦略（開発初期の設定の再現）。default と対戦させて、問いの立て方でどれだけ変わるかを見るためのもの。

default との違い:
- 評価基準が4段階で、「穴あり」を最低の段にまとめている（→ 穴を避けて高く積みがち）
- 置く前と後の値をそのまま渡し、差分はモデルに読ませている
- 同点は先頭の候補を採用（tie_break なし）
"""
from arena.games.blocks import bumpiness, count_holes, heights

QUESTION = "How good is this piece placement for long-term survival?"
INSTRUCTIONS = "How good is the piece placement described in `candidates.{id}` for long-term survival?"
LEVELS = [
    "Creates new holes (empty cells covered from above) and clears no lines",
    "No new holes, but the surface becomes more uneven or the stack gets noticeably taller",
    "No new holes, and the surface stays flat and the stack stays low",
    "Clears one or more lines without creating new holes",
]


def features(cand, view):
    h0, h1 = heights(cand["board_before"]), heights(cand["board_after"])
    holes0, holes1 = count_holes(cand["board_before"]), count_holes(cand["board_after"])
    return {"lines_cleared": cand["lines"], "new_holes": max(0, holes1 - holes0), "holes_removed": max(0, holes0 - holes1),
            "max_height_before": max(h0), "max_height_after": max(h1),
            "surface_bumpiness_before": bumpiness(h0), "surface_bumpiness_after": bumpiness(h1)}
