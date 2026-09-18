"""Blobsの最適化「前」の戦略（最初に書いた版）。build-big-chains と対戦させて、問いの立て方でどれだけ変わるかを見るためのもの。

build-big-chains との違い:
- 「3連鎖以上」「1列ぶん以上のおじゃまを送る」を最上位に書いている → Jev は基準どおり、小さい連鎖をすぐ打つ
- 「狙える連鎖数」は、ブロブをあと1個足す場合だけを見ている
- 同点は「狙える連鎖数 → 3列目の高さ」で選ぶ
"""
from arena.games.blobs import COLOR_NAME, SPAWN_X, W, chain_potential, find_groups, heights, touching_same_colour

DESCRIPTION = "最初に書いた基準。小さい連鎖をすぐ打ってしまう"  # 画面の選択肢に出る一言

QUESTION = "How good is this placement of the falling pair?"
INSTRUCTIONS = "How good is the pair placement described in `candidates.{id}`?"
LEVELS = [
    "Loses the game, or leaves the third column critical (almost at the top) without popping anything",
    "Nothing pops, neither placed blob touches its own colour, and the chain available next turn gets lower; or a small pop that lowers the chain available next turn while no garbage blobs are incoming",
    "Nothing pops; at most one placed blob joins a same-colour group and the chain available next turn stays the same",
    "Nothing pops, but placed blobs join same-colour groups and the chain available next turn is kept or raised while the third column stays safe; or a two-step chain; or a small pop that cancels incoming garbage blobs",
    "Triggers a big chain of three or more steps, or sends one or more rows of garbage blobs to the opponent, or pops blobs so that a critical third column becomes safe again",
]
TIE_EPS = 0.05
TIE_BREAK_TEXT = "if several candidates are equally good, prefer the higher best_chain_available_next_turn, then the lower third column, then the lower stack"


def tie_break(f):
    return (-f["best_chain_available_next_turn"], f["third_column_height_after"], f["max_height_after"])


def context(view):
    cur, nxt = view["current"], view["next"][0]
    view["_pot"] = chain_potential(view["board"], 1)
    return {"current_pair": {"axis": COLOR_NAME[cur[0]], "child": COLOR_NAME[cur[1]]}, "next_pair": {"axis": COLOR_NAME[nxt[0]], "child": COLOR_NAME[nxt[1]]},
            "incoming_garbage": view["pending"], "best_chain_available_now": view["_pot"],
            "own_third_column_height": heights(view["board"])[SPAWN_X], "opponent_max_height": max(heights(view["opponent_board"]))}


def features(cand, view):
    n, g, h1 = len(cand["steps"]), cand["garbage"], heights(cand["board_after"])
    pot0, pot1 = view["_pot"], chain_potential(cand["board_after"], 1)
    sizes = {p: len(cells) for _, cells in find_groups(cand["board_placed"]) for p in cells}
    h3 = h1[SPAWN_X]
    return {
        "result": ["nothing pops", "small pop: one chain step only", "two-step chain", "big chain: three or more chain steps"][min(n, 3)],
        "chain_steps": n,
        "blobs_popped": sum(len(s["popped"]) for s in cand["steps"]),
        "attack": "sends no garbage blobs" if g == 0 else "sends a few garbage blobs (less than one row)" if g < W else "sends one to four rows of garbage blobs" if g < 5 * W else "sends five or more rows of garbage blobs",
        "garbage_generated": g,
        "incoming_garbage_cancelled": min(g, cand["pending"]),
        "own_garbage_cleared": sum(len(s["garbage"]) for s in cand["steps"]),
        "connection": ["neither placed blob touches its own colour", "one placed blob joins a same-colour group", "both placed blobs join same-colour groups"][touching_same_colour(cand)],
        "largest_group_made": max((sizes.get(p, 1) for p in cand["placed"]), default=1),
        "best_chain_available_next_turn": pot1,
        "chain_potential": "raises the chain available next turn" if pot1 > pot0 else "keeps the chain available next turn" if pot1 == pot0 else "lowers the chain available next turn",
        "third_column_height_after": h3,
        "third_column": "safe" if h3 <= 7 else "high" if h3 <= 9 else "critical: almost at the top" if h3 <= 11 else "fatal: this placement loses the game",
        "max_height_after": max(h1),
    }
