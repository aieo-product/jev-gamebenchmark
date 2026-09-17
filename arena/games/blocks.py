"""Blocksのルール（ゲームロジック。戦略を試すだけなら、このファイルを変える必要はない）。

戦略ファイル（strategies/blocks/*.py）から使える道具:
  heights(board) / count_holes(board) / bumpiness(heights) / W / H
候補（candidates() が返す dict）の中身:
  id, rot, x, y          … 置き方（y は着地したときの行）
  board_before / board_after … 置く前と、置いてラインを消したあとの盤面（H 行 × W 列。空きは ""）
  lines                  … 消えるライン数
"""
import random

ID = "blocks"
TITLE = "Blocks（ライン消しパズル）"
INTRO = "2-player versus falling-block line-clearing puzzle (tetromino pieces). The board is 10 wide and 20 tall. Clearing two or more lines at once sends garbage lines to the opponent; a player loses when the stack reaches the top."
W, H = 10, 20
SHAPES = {
    "I": [[(0, 0), (1, 0), (2, 0), (3, 0)], [(0, 0), (0, 1), (0, 2), (0, 3)]],
    "O": [[(0, 0), (1, 0), (0, 1), (1, 1)]],
    "T": [[(0, 0), (1, 0), (2, 0), (1, 1)], [(0, 0), (0, 1), (0, 2), (1, 1)], [(1, 0), (0, 1), (1, 1), (2, 1)], [(1, 0), (1, 1), (1, 2), (0, 1)]],
    "S": [[(1, 0), (2, 0), (0, 1), (1, 1)], [(0, 0), (0, 1), (1, 1), (1, 2)]],
    "Z": [[(0, 0), (1, 0), (1, 1), (2, 1)], [(1, 0), (1, 1), (0, 1), (0, 2)]],
    "L": [[(0, 0), (0, 1), (0, 2), (1, 2)], [(0, 0), (1, 0), (2, 0), (0, 1)], [(0, 0), (1, 0), (1, 1), (1, 2)], [(2, 0), (0, 1), (1, 1), (2, 1)]],
    "J": [[(1, 0), (1, 1), (1, 2), (0, 2)], [(0, 0), (0, 1), (1, 1), (2, 1)], [(0, 0), (1, 0), (0, 1), (0, 2)], [(0, 0), (1, 0), (2, 0), (2, 1)]],
}
ATTACK = {0: 0, 1: 0, 2: 1, 3: 2, 4: 4}  # 消したライン数 → 相手に送る邪魔ブロックの段数


# ------------------------------------------------------------------ 盤面の道具（戦略からも使える）
def empty_board():
    return [[""] * W for _ in range(H)]


def heights(board):
    return [next((H - y for y in range(H) if board[y][x]), 0) for x in range(W)]


def count_holes(board):
    """上にブロックがある空きマスの数。"""
    return sum(1 for x in range(W) for y in range(H) if not board[y][x] and any(board[yy][x] for yy in range(y)))


def bumpiness(hs):
    """隣り合う列の高さの差の合計（表面の凹凸）。"""
    return sum(abs(hs[i] - hs[i + 1]) for i in range(W - 1))


def collides(board, cells, x, y):
    for cx, cy in cells:
        px, py = x + cx, y + cy
        if px < 0 or px >= W or py >= H or (py >= 0 and board[py][px]):
            return True
    return False


def drop_y(board, cells, x, y=0):
    while not collides(board, cells, x, y + 1):
        y += 1
    return y


def place(board, cells, x, y, mark):
    nb = [row[:] for row in board]
    for cx, cy in cells:
        if 0 <= y + cy < H:
            nb[y + cy][x + cx] = mark
    full = [i for i in range(H) if all(nb[i])]
    for i in full:
        del nb[i]
        nb.insert(0, [""] * W)
    return nb, len(full)


def enumerate_placements(board, ptype):
    """真上からまっすぐ落とせる置き方をすべて列挙する（特徴量の計算は戦略の仕事）。"""
    out = []
    for rot, cells in enumerate(SHAPES[ptype]):
        width = max(c[0] for c in cells) + 1
        for x in range(W - width + 1):
            if collides(board, cells, x, 0):
                continue
            y = drop_y(board, cells, x)
            nb, lines = place(board, cells, x, y, ptype)
            out.append({"id": f"p{len(out):02d}", "rot": rot, "x": x, "y": y, "board_before": board, "board_after": nb, "lines": lines})
    return out


def baseline_value(cand):
    """参考用の「コードだけの評価関数」（定番の重み）。solo の code エージェントが使う。"""
    hs = heights(cand["board_after"])
    return -0.51 * sum(hs) + 0.76 * cand["lines"] - 0.36 * count_holes(cand["board_after"]) - 0.18 * bumpiness(hs)


class Sequence:
    """7種のピースを1巡ずつ、順番を混ぜて引く。両プレイヤーが同じ並びを引く。"""

    def __init__(self, seed):
        self.rng, self.seq = random.Random(seed), []

    def get(self, i):
        while len(self.seq) <= i:
            bag = list("IOTSZLJ")
            self.rng.shuffle(bag)
            self.seq += bag
        return self.seq[i]


# ------------------------------------------------------------------ 1人分のゲーム進行
class Game:
    def __init__(self, seq, seed, cfg):
        self.seq, self.board, self.idx, self.pending, self.alive, self.piece = seq, empty_board(), 0, 0, True, None
        self.rng = random.Random(seed)
        self.pieces = self.lines = self.sent = self.received = 0

    # ---- 1手の流れ: spawn → candidates/view →（落下 step_down）→ steer → lock
    def spawn(self):
        ptype = self.seq.get(self.idx)
        self.idx += 1
        cells = SHAPES[ptype][0]
        x = (W - (max(c[0] for c in cells) + 1)) // 2
        if collides(self.board, cells, x, 0):
            self.alive = False
            return False
        self.piece = {"type": ptype, "rot": 0, "x": x, "y": 0}
        return True

    def label(self):
        return self.piece["type"]

    def candidates(self):
        return enumerate_placements(self.board, self.piece["type"])

    def view(self, opp):
        """戦略の context() に渡す、いまの状況。"""
        return {"board": self.board, "current": self.piece["type"], "next": [self.seq.get(self.idx + i) for i in range(3)],
                "pending": self.pending, "opponent_board": opp.board}

    def step_down(self):
        p = self.piece
        if collides(self.board, SHAPES[p["type"]][p["rot"]], p["x"], p["y"] + 1):
            return False
        p["y"] += 1
        return True

    def steer(self, cand):
        """回答の置き方へ移す。すでにその高さより下まで落ちていたら False（届いたが置けず）。"""
        if cand["y"] < self.piece["y"]:
            return False
        self.piece.update(rot=cand["rot"], x=cand["x"])
        return True

    async def lock(self, opp, sleep, name):
        p = self.piece
        cells = SHAPES[p["type"]][p["rot"]]
        y = drop_y(self.board, cells, p["x"], p["y"])
        self.board, lines = place(self.board, cells, p["x"], y, p["type"])
        self.piece = None
        self.pieces += 1
        self.lines += lines
        events = []
        atk = ATTACK[lines]  # 邪魔ブロック: まず自分に来ている分を相殺し、残りを相手へ送る
        cancel = min(atk, self.pending)
        self.pending -= cancel
        atk -= cancel
        if atk:
            opp.pending += atk
            self.sent += atk
            events.append(f"{name} が {lines} ライン消去 → 邪魔ブロック {atk} 段を送った")
        if lines == 0 and self.pending:
            n, self.pending = self.pending, 0
            self.received += n
            hole = self.rng.randrange(W)
            if any(any(row) for row in self.board[:n]):
                self.alive = False  # 押し上げられて天井を超えた
            self.board = self.board[n:] + [["G" if x != hole else "" for x in range(W)] for _ in range(n)]
        return events

    # ---- 表示と判定
    def snapshot(self):
        p = self.piece
        return {"board": ["".join(c or "." for c in row) for row in self.board],
                "piece": p and {"cells": [[p["x"] + cx, p["y"] + cy, p["type"]] for cx, cy in SHAPES[p["type"]][p["rot"]]]},
                "next": [self.seq.get(self.idx + i) for i in range(3)], "pending": self.pending, "pending_max": 20}

    def result(self):
        return {"pieces": self.pieces, "lines": self.lines, "sent": self.sent, "received": self.received}

    def tiles(self):
        return [["消去ライン", self.lines], ["送った / 受けた邪魔", f"{self.sent} / {self.received}"]]

    def attack_units(self):
        return "消去1ライン", self.lines

    def rank(self):
        return (self.sent, self.lines)

    RANK_TEXT = "送った邪魔ブロック数（同数なら消去ライン数）"
    LOSE_TEXT = "相手が積み上がって終了"
