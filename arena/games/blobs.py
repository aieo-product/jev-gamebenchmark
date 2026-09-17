"""Blobsのルール（ゲームロジック。戦略を試すだけなら、このファイルを変える必要はない）。

戦略ファイル（strategies/blobs/*.py）から使える道具:
  heights(board) / find_groups(board) / resolve(board) / chain_potential(board, max_add) / W / H / SPAWN_X / COLOR_NAME
候補（candidates() が返す dict）の中身:
  id, rot, x, y           … 置き方（rot は軸ブロブから見た子ブロブの位置: 0=上 1=右 2=下 3=左。x は軸ブロブの列）
  board_before            … 置く前の盤面（H 行 × W 列。空きは ""、おじゃまブロブは "O"、行 0 は画面外の隠し段）
  board_placed / placed   … 置いた直後（消える前）の盤面と、置いた2個のマス [(x, y), ...]
  board_after / steps     … 連鎖が終わったあとの盤面と、連鎖の各段 [{"popped": [...], "garbage": [...], "score": 点}, ...]
  garbage                … この手で生まれるおじゃまブロブの数（得点 ÷ レート）
  pending                 … 自分に予告されているおじゃまブロブの数
"""
import random

ID = "blobs"
TITLE = "Blobs（連鎖パズル）"
INTRO = ("2-player versus colour-matching chain puzzle. Pairs of coloured blobs fall into a board 6 wide and 12 tall. Four or more connected blobs of one colour pop, blobs above fall and can pop again (a chain). "
         "Chains send garbage blobs to the opponent. A player loses when their third column from the left reaches the top.")
W, H = 6, 13                  # 行 0 は画面外の隠し段（置けるが、つながらない・消えない）。見える盤面は 6×12
COLORS = "RGBY"
COLOR_NAME = {"R": "red", "G": "green", "B": "blue", "Y": "yellow"}
SPAWN_X, DEATH_Y = 2, 1       # 左から3列目の最上段（×印）が埋まったら負け
ROT = [(0, -1), (1, 0), (0, 1), (-1, 0)]
NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))
# 得点は定番の連鎖ボーナス式: 10 × 消した数 × (連鎖ボーナス + 色数ボーナス + 連結ボーナス)
CHAIN_POWER = [0, 8, 16, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 480, 512]
COLOR_BONUS = {1: 0, 2: 3, 3: 6, 4: 12}
GARBAGE_RATE = 70             # 得点 70 点でおじゃまブロブ 1 個（画面で変更できる）
MAX_GARBAGE_DROP = 30         # 一度に降るのは 5 段まで
POP_DELAY, FALL_DELAY = 0.28, 0.14  # 連鎖1段の演出時間（この間は次のペアを操作できない）


# ------------------------------------------------------------------ 盤面の道具（戦略からも使える）
def group_bonus(size):
    return 0 if size <= 4 else 10 if size >= 11 else size - 3


def empty_board():
    return [[""] * W for _ in range(H)]


def heights(board):
    return [next((H - y for y in range(H) if board[y][x]), 0) for x in range(W)]


def drop_pair(board, pair, rot, x):
    """ペアを、軸ブロブの列 x・向き rot で落とす。横置きはちぎれて、それぞれの列の一番上に乗る。"""
    nb = [row[:] for row in board]
    dx, dy = ROT[rot]
    parts = [(x, pair[0]), (x + dx, pair[1])]
    if dy == 1:  # 子ブロブが下 → 子を先に置く
        parts.reverse()
    placed = []
    for cx, col in parts:
        y = next((yy for yy in range(H - 1, -1, -1) if not nb[yy][cx]), None)
        if y is not None:
            nb[y][cx] = col
            placed.append((cx, y))
    return nb, placed


def find_groups(board):
    """同じ色でつながったブロブのまとまり [(色, [(x, y), ...]), ...]（隠し段とおじゃまブロブは除く）。"""
    seen, groups = set(), []
    for y in range(1, H):
        for x in range(W):
            c = board[y][x]
            if not c or c == "O" or (x, y) in seen:
                continue
            stack, cells = [(x, y)], []
            seen.add((x, y))
            while stack:
                px, py = stack.pop()
                cells.append((px, py))
                for ax, ay in NEIGHBOURS:
                    nx, ny = px + ax, py + ay
                    if 0 <= nx < W and 1 <= ny < H and (nx, ny) not in seen and board[ny][nx] == c:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            groups.append((c, cells))
    return groups


def apply_gravity(board):
    nb = empty_board()
    for x in range(W):
        col = [board[y][x] for y in range(H) if board[y][x]]
        for i, c in enumerate(reversed(col)):
            nb[H - 1 - i][x] = c
    return nb


def resolve(board):
    """4つ以上つながったブロブを消し、落とし、連鎖が止まるまで繰り返す。戻り値: (最終盤面, 連鎖の各段)。"""
    steps = []
    while True:
        pops = [(c, cells) for c, cells in find_groups(board) if len(cells) >= 4]
        if not pops:
            return board, steps
        nb = [row[:] for row in board]
        popped = [p for _, cells in pops for p in cells]
        garbage = set()
        for px, py in popped:
            nb[py][px] = ""
            for ax, ay in NEIGHBOURS:  # 消えたブロブに隣り合うおじゃまブロブも消える
                nx, ny = px + ax, py + ay
                if 0 <= nx < W and 1 <= ny < H and board[ny][nx] == "O":
                    garbage.add((nx, ny))
        for ox, oy in garbage:
            nb[oy][ox] = ""
        bonus = CHAIN_POWER[min(len(steps), len(CHAIN_POWER) - 1)] + COLOR_BONUS[len({c for c, _ in pops})] + sum(group_bonus(len(cells)) for _, cells in pops)
        board = apply_gravity(nb)
        steps.append({"popped": popped, "garbage": sorted(garbage), "score": 10 * len(popped) * max(1, min(999, bonus)), "after": board})


def chain_potential(board, max_add=1):
    """この盤面から狙える最大の連鎖数。
    同じ色のブロブを 1〜max_add 個、どこか1列に積んだときに起きる連鎖の最大段数で測る（色と列は総当たり）。
    max_add=1 は「あと1個で発火できる連鎖」、2 にすると「あと2個で発火できる連鎖」まで数える。"""
    hs, gid = heights(board), {}
    for i, (_, cells) in enumerate(find_groups(board)):
        for p in cells:
            gid[p] = (i, len(cells))
    best = 0
    for x in range(W):
        for k in range(1, max_add + 1):
            ys = [H - 1 - hs[x] - j for j in range(k)]
            if ys[-1] < 1:
                break
            for col in COLORS:
                seen, total = set(), k
                for y in ys:
                    for ax, ay in NEIGHBOURS:
                        nx, ny = x + ax, y + ay
                        if 0 <= nx < W and 1 <= ny < H and board[ny][nx] == col and gid[(nx, ny)][0] not in seen:
                            seen.add(gid[(nx, ny)][0])
                            total += gid[(nx, ny)][1]
                if total >= 4:  # 消えない置き方は試すまでもなく 0 連鎖
                    nb = [row[:] for row in board]
                    for y in ys:
                        nb[y][x] = col
                    best = max(best, len(resolve(nb)[1]))
    return best


def enumerate_placements(board, pair, pending=0, rate=GARBAGE_RATE):
    """置き方（向き×列）をすべて列挙し、結果をシミュレーションする（特徴量の計算は戦略の仕事）。"""
    h0, out = heights(board), []
    for rot, (dx, dy) in enumerate(ROT):
        if pair[0] == pair[1] and rot >= 2:  # 同色の組は上下・左右を入れ替えても同じ
            continue
        for x in range(W):
            x2 = x + dx
            if not 0 <= x2 < W:
                continue
            if dx == 0:
                if h0[x] > H - 2:
                    continue
                rest_y = H - 1 - h0[x] - (1 if dy == 1 else 0)
            else:
                if h0[x] > H - 1 or h0[x2] > H - 1:
                    continue
                rest_y = H - 1 - max(h0[x], h0[x2])
            placed_board, placed = drop_pair(board, pair, rot, x)
            final, steps = resolve(placed_board)
            out.append({"id": f"p{len(out):02d}", "rot": rot, "x": x, "y": rest_y, "board_before": board, "board_placed": placed_board, "placed": placed,
                        "board_after": final, "steps": steps, "garbage": sum(s["score"] for s in steps) // rate, "pending": pending})
    return out


def touching_same_colour(cand):
    """置いた2個のうち、すでにあった同色ブロブに触れている個数（0〜2）。消える前の状態で数える。"""
    b, placed = cand["board_placed"], cand["placed"]
    return sum(1 for px, py in placed if py >= 1 and any(
        0 <= px + ax < W and 1 <= py + ay < H and (px + ax, py + ay) not in placed and b[py + ay][px + ax] == b[py][px] for ax, ay in NEIGHBOURS))


def baseline_value(cand):
    """参考用の「コードだけの評価関数」（一手読み）。solo の code エージェントが使う。
    5連鎖以上か、盤面が詰まってきたら発火。それ以外は、狙える連鎖数を伸ばし、3列目を低く保つ。"""
    h0, h1, n = heights(cand["board_before"]), heights(cand["board_after"]), len(cand["steps"])
    danger = h0[SPAWN_X] >= 9 or max(h0) >= 11 or sum(h0) >= 54
    fire = 1000 + 100 * n if n >= 5 or (danger and n >= 1) else -50 * n
    return fire + 10 * chain_potential(cand["board_after"], 2) - 3 * max(0, h1[SPAWN_X] - 6) - 0.3 * max(h1) - (1000 if h1[SPAWN_X] >= 12 else 0)


class Sequence:
    """ペアの並び（4色）。両プレイヤーが同じ並びを引く。"""

    def __init__(self, seed):
        self.rng, self.seq = random.Random(seed), []

    def get(self, i):
        while len(self.seq) <= i:
            self.seq.append(self.rng.choice(COLORS) + self.rng.choice(COLORS))
        return self.seq[i]


# ------------------------------------------------------------------ 1人分のゲーム進行
class Game:
    def __init__(self, seq, seed, cfg):
        self.seq, self.board, self.idx, self.pending, self.leftover, self.alive, self.piece = seq, empty_board(), 0, 0, 0, True, None
        self.rng = random.Random(seed)
        self.rate = int(cfg.get("garbage_rate") or GARBAGE_RATE)
        self.popping, self.chain_now = [], 0
        self.pieces = self.chains = self.max_chain = self.score = self.sent = self.received = 0
        self.chain_log = []

    def spawn(self):
        if self.board[DEATH_Y][SPAWN_X]:  # 左から3列目の最上段が埋まった
            self.alive = False
            return False
        self.piece = {"pair": self.seq.get(self.idx), "rot": 0, "x": SPAWN_X, "y": DEATH_Y}
        self.idx += 1
        return True

    def label(self):
        return "+".join(COLOR_NAME[c] for c in self.piece["pair"])

    def candidates(self):
        return enumerate_placements(self.board, self.piece["pair"], self.pending, self.rate)

    def view(self, opp):
        """戦略の context() に渡す、いまの状況。"""
        return {"board": self.board, "current": self.piece["pair"], "next": [self.seq.get(self.idx + i) for i in range(2)],
                "pending": self.pending, "opponent_board": opp.board, "opponent_pending": opp.pending, "rate": self.rate}

    def _cells(self):
        p = self.piece
        dx, dy = ROT[p["rot"]]
        return [(p["x"], p["y"]), (p["x"] + dx, p["y"] + dy)]

    def step_down(self):
        if not all(y + 1 < H and (y + 1 < 0 or not self.board[y + 1][x]) for x, y in self._cells()):
            return False
        self.piece["y"] += 1
        return True

    def steer(self, cand):
        if cand["y"] < self.piece["y"]:
            return False
        self.piece.update(rot=cand["rot"], x=cand["x"])
        return True

    async def lock(self, opp, sleep, name):
        p = self.piece
        self.board, _ = drop_pair(self.board, p["pair"], p["rot"], p["x"])
        self.piece = None
        self.pieces += 1
        _, steps = resolve(self.board)
        total = 0
        for i, st in enumerate(steps, 1):  # 連鎖を1段ずつ見せる
            self.chain_now, self.popping = i, st["popped"] + st["garbage"]
            await sleep(POP_DELAY)
            self.board, self.popping = st["after"], []
            total += st["score"]
            self.score += st["score"]
            await sleep(FALL_DELAY)
        self.chain_now = 0
        events = []
        if steps:  # おじゃまブロブ: まず自分に来ている分を相殺し、残りを相手へ送る
            self.chains += 1
            self.chain_log.append(len(steps))
            self.max_chain = max(self.max_chain, len(steps))
            atk, self.leftover = divmod(total + self.leftover, self.rate)
            cancel = min(atk, self.pending)
            self.pending -= cancel
            atk -= cancel
            if atk:
                opp.pending += atk
                self.sent += atk
            if atk or cancel or len(steps) >= 2:
                events.append(f"{name} が {len(steps)}連鎖（{total}点）" + (f" → 相殺 {cancel}個" if cancel else "") + (f" → おじゃまブロブ {atk}個を送った" if atk else ""))
        elif self.pending:  # 消さずに置いた手のあとに、予告ぶんが降る（一度に最大 5 段。端数は毎回ちがう列）
            n = min(self.pending, MAX_GARBAGE_DROP)
            self.pending -= n
            self.received += n
            rows, rem = divmod(n, W)
            self.board = [row[:] for row in self.board]
            for x in list(range(W)) * rows + self.rng.sample(range(W), rem):
                y = next((yy for yy in range(H - 1, -1, -1) if not self.board[yy][x]), None)
                if y is not None:
                    self.board[y][x] = "O"
        return events

    # ---- 表示と判定
    def snapshot(self):
        p = self.piece
        return {"board": ["".join(c or "." for c in row) for row in self.board],
                "piece": p and {"cells": [[x, y, col] for (x, y), col in zip(self._cells(), p["pair"])]},
                "next": [self.seq.get(self.idx + i) for i in range(2)], "pending": self.pending, "pending_max": MAX_GARBAGE_DROP,
                "popping": [list(c) for c in self.popping], "chain_now": self.chain_now}

    def result(self):
        return {"pieces": self.pieces, "score": self.score, "chains": self.chains, "max_chain": self.max_chain, "chain_log": self.chain_log, "sent": self.sent, "received": self.received}

    def tiles(self):
        return [["得点", f"{self.score:,}"], ["連鎖 回数 / 最大", f"{self.chains} / {self.max_chain}"], ["送った / 受けたおじゃま", f"{self.sent} / {self.received}"]]

    def attack_units(self):
        return "送ったおじゃま1個", self.sent

    def rank(self):
        return (self.sent, self.score)

    RANK_TEXT = "送ったおじゃまブロブの数（同数なら得点）"
    LOSE_TEXT = "相手が積み上がって終了（左から3列目の最上段が埋まった）"
