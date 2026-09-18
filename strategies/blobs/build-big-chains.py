"""Blobsの既定の戦略（連鎖を育ててから打つ版）。このファイルをコピーして、自分の戦略を作ってください。

考え方: 探索と計算（連鎖のシミュレーション、あと何個で何連鎖が打てるか）はコード、良し悪しの評価は Jev。
- Jev は評価基準を字義どおり正確に読む。「3連鎖以上＝最上位」と書けば、3連鎖ができた瞬間に打つ（pop-early がそれ）。
  ここでは「盤面に余裕があるうちの小〜中連鎖は無駄打ち」と低い段に明記し、5連鎖以上か、盤面が詰まってから打たせる
- 数値の大小比較をモデルにさせない。攻撃の強さ・連鎖の伸び縮み・盤面の余裕は、コードが言葉の区分にして渡す
- 「狙える連鎖数」は、同色ブロブをあと2個足すところまで見る（chain_potential の max_add=2）。1個だけだと育ちかけの連鎖を評価できない
- 同点の候補は、コード（tie_break）が「攻撃が大きい → 狙える連鎖が長い → 3列目が低い」の順で選ぶ
"""
from arena.games.blobs import COLOR_NAME, SPAWN_X, W, chain_potential, heights, touching_same_colour

DESCRIPTION = "連鎖を育ててから打つ（推奨）"  # 画面の選択肢に出る一言
RECOMMENDED = True  # 選択肢の先頭に出す

QUESTION = "How good is this placement of the falling pair?"
INSTRUCTIONS = "How good is the pair placement described in `candidates.{id}`?"
LEVELS = [
    "Loses the game, or leaves the third column critical (almost at the top) without popping anything",
    "An early pop: the board still has room and no garbage blobs are incoming, so the chain is spent before it could grow bigger; or nothing pops, neither placed blob touches its own colour, and the buildable chain gets shorter",
    "Nothing pops, at most one placed blob joins a same-colour group, and the buildable chain stays the same; or a forced pop that is not the biggest chain on offer",
    "Nothing pops, placed blobs join same-colour groups, and the buildable chain is kept or gets longer while the third column stays safe; or a defensive pop that cancels incoming garbage blobs",
    "A ripe pop: a big chain of five or more steps; or a forced pop of the biggest chain on offer because the board is nearly full; or pops blobs so that a critical third column becomes safe again",
]

TIE_EPS = 0.15
TIE_BREAK_TEXT = ("if several candidates are equally good, prefer one that is not an early pop, then the larger garbage_generated, "
                  "then the longer buildable_chain_after, then the lower third column")
MAX_ADD = 2  # 「狙える連鎖」を、同色ブロブをあと何個足すところまで見るか


def tie_break(f):
    """同点の候補から選ぶ順（小さいほど優先）。育ち途中の連鎖を崩す手は後回しにする。"""
    return (f["pop_timing"].startswith("early"), -f["garbage_generated"], -f["buildable_chain_after"], f["third_column_height_after"], f["max_height_after"])


def _full(hs):
    return hs[SPAWN_X] >= 9 or max(hs) >= 11 or sum(hs) >= 54


def _room(hs):
    return "nearly full: time to fire" if _full(hs) else "has room to keep building"


def _timing(n, biggest, cand):
    """消す手の「タイミング」。連鎖数・盤面の余裕・予告おじゃまの3つを、コードが1つの言葉にまとめる（モデルに条件の掛け合わせをさせない）。"""
    if n == 0:
        return "no pop"
    if n >= 5:
        return "ripe: a big chain of five or more steps"
    if _full(heights(cand["board_before"])):
        return "forced: the board is nearly full, and this is the biggest chain on offer" if n == biggest else "forced: the board is nearly full, but this is not the biggest chain on offer"
    if min(cand["garbage"], cand["pending"]) > 0:
        return "defensive: cancels incoming garbage blobs"
    return "early: the board still has room and no garbage blobs are incoming, so this chain could have grown bigger"


def _attack(g):
    if g == 0:
        return "sends no garbage blobs"
    return ("weak attack: less than one row of garbage blobs" if g < W else "light attack: one or two rows of garbage blobs" if g < 3 * W
            else "solid attack: three or four rows of garbage blobs" if g < 5 * W else "huge attack: five or more rows of garbage blobs")


def _column(h):
    return "safe" if h <= 7 else "high" if h <= 9 else "critical: almost at the top" if h <= 11 else "fatal: this placement loses the game"


def context(view):
    cur, nxt, hs = view["current"], view["next"][0], heights(view["board"])
    view["_buildable"] = chain_potential(view["board"], MAX_ADD)  # features() で使い回す
    return {"current_pair": {"axis": COLOR_NAME[cur[0]], "child": COLOR_NAME[cur[1]]}, "next_pair": {"axis": COLOR_NAME[nxt[0]], "child": COLOR_NAME[nxt[1]]},
            "incoming_garbage": view["pending"], "board": _room(hs), "buildable_chain_now": view["_buildable"],
            "own_third_column_height": hs[SPAWN_X], "opponent_max_height": max(heights(view["opponent_board"]))}


def features(cand, view):
    n, h1 = len(cand["steps"]), heights(cand["board_after"])
    before, after = view["_buildable"], chain_potential(cand["board_after"], MAX_ADD)
    biggest = max(len(c["steps"]) for c in view["candidates"])
    return {
        "result": ["nothing pops", "small pop: one chain step only", "two-step chain", "medium chain: three or four steps", "medium chain: three or four steps", "big chain: five or more steps"][min(n, 5)],
        "chain_steps": n,
        "pop_timing": _timing(n, biggest, cand),
        "attack": _attack(cand["garbage"]),
        "garbage_generated": cand["garbage"],
        "incoming_garbage_cancelled": min(cand["garbage"], cand["pending"]),
        "own_garbage_cleared": sum(len(s["garbage"]) for s in cand["steps"]),
        "connection": ["neither placed blob touches its own colour", "one placed blob joins a same-colour group", "both placed blobs join same-colour groups"][touching_same_colour(cand)],
        "buildable_chain_after": after,
        "buildable_chain": "gets longer" if after > before else "stays the same" if after == before else "gets shorter",
        "third_column_height_after": h1[SPAWN_X],
        "third_column": _column(h1[SPAWN_X]),
        "max_height_after": max(h1),
    }
