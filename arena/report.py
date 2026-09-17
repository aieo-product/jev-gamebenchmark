"""docs/results/*.json（arena.bench と arena.solo -o の出力）から docs/benchmarks.md を作る。

  python -m arena.report
"""
import json
import statistics
from collections import defaultdict

from .strategy import ROOT

RESULTS = ROOT / "docs" / "results"
GAME_NAME = {"blobs": "Blobs（連鎖パズル）", "blocks": "Blocks（ライン消しパズル）"}


def usd(v):
    return "—" if v is None else f"${v:.4f}" if v >= 0.01 else f"${v:.5f}" if v >= 0.0001 else f"${v:.7f}"


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def bench_section(d):
    def interrupted(m):  # 途中で止められた試合（古い結果ファイルには stopped がないので、「両者生存のまま制限時間前に終了」で見分ける）
        return m.get("stopped") or (not m.get("error") and (m.get("reason") or "").startswith("時間切れ") and m["elapsed"] < d["seconds"] - 1)
    ok = [m for m in d["matches"] if not m.get("error") and not interrupted(m)]
    out = [f"### {GAME_NAME.get(d['game'], d['game'])} — 対戦（{d['date'][:10]}）", "",
           f"条件: 戦略 `{d['strategy']}`（両者に同じ特徴量・同じ評価基準）、1試合 {d['seconds']}秒まで、思考 {d['thinking']}、開始時の落下 {d['gravity_ms']}ms/段"
           + (f"、おじゃまレート {d['garbage_rate']}点/個" if d["game"] == "blobs" else "") + f"、API 直結、各モデル {len({m['seed'] for m in ok})} 試合（seed 固定）。", ""]
    extra = ("最大連鎖", "max_chain") if d["game"] == "blobs" else ("消去ライン", "lines")
    out += [f"| 相手 | 勝敗（Jev-相手-分） | 決着までの秒数 | 回答時間の中央値 ms（Jev / 相手） | 置いた数/分（Jev / 相手） | {extra[0]}（Jev / 相手） | 送った攻撃（Jev / 相手） | 1手の費用（Jev / 相手） | 費用の倍率 | 相手の 間に合わず・形式逸脱・無効・エラー |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    by_model = defaultdict(list)
    for m in ok:
        by_model[m["model"]].append(m)
    for model, ms in by_model.items():
        w = sum(m["winner"] == "jev" for m in ms), sum(m["winner"] == "llm" for m in ms), sum(m["winner"] == "draw" for m in ms)
        J, L = [m["players"]["jev"] for m in ms], [m["players"]["llm"] for m in ms]
        ppm = lambda ps: mean([p["pieces"] / m["elapsed"] * 60 for p, m in zip(ps, ms) if m["elapsed"]])  # noqa: E731
        cj, cl = mean([p["cost_per_move"] for p in J]), mean([p["cost_per_move"] for p in L])
        agg = (max if extra[1] == "max_chain" else sum)
        out.append(f"| {model} | {w[0]}-{w[1]}-{w[2]} | {' / '.join(str(round(m['elapsed'])) for m in ms)} | {round(mean([p['ms_p50'] for p in J]))} / {round(mean([p['ms_p50'] for p in L]))} "
                   f"| {ppm(J):.0f} / {ppm(L):.0f} | {agg(p[extra[1]] for p in J)} / {agg(p[extra[1]] for p in L)} | {sum(p['sent'] for p in J)} / {sum(p['sent'] for p in L)} "
                   f"| {usd(cj)} / {usd(cl)} | {cl / cj:.1f}× | {sum(p['not_in_time'] for p in L)}・{sum(p['format_dev'] for p in L)}・{sum(p['invalid'] for p in L)}・{sum(p['errors'] for p in L)} |")
    allJ = [m["players"]["jev"] for m in ok]
    total_j, total_l = sum(p["cost_usd"] for p in allJ), sum(m["players"]["llm"]["cost_usd"] for m in ok)
    out += ["", f"- Jev 全 {len(ok)} 試合: 回答時間の中央値の平均 {round(mean([p['ms_p50'] for p in allJ]))}ms、1手の費用 {usd(mean([p['cost_per_move'] for p in allJ]))}、"
                f"間に合わず {sum(p['not_in_time'] for p in allJ)}・無効 {sum(p['invalid'] for p in allJ)}・エラー {sum(p['errors'] for p in allJ)}。",
            f"- この計測の費用: Jev ${total_j:.2f} + 相手 ${total_l:.2f}（定価 × 使用トークン）。"]
    errs = [m for m in d["matches"] if m.get("error")]
    if errs:
        out.append("- 実行できなかった試合: " + "、".join(f"{m['model']} seed {m['seed']}（{'API の利用上限に達した' if 'usage limits' in m['error'] else m['error'][:90]}）" for m in errs))
    cut = [m for m in d["matches"] if interrupted(m)]
    if cut:
        out.append("- 途中で停止されたため集計から外した試合: " + "、".join(f"{m['model']} seed {m['seed']}（{m['elapsed']}秒）" for m in cut))
    return out + [""]


def solo_section(d):
    blobs = d["game"] == "blobs"
    out = [f"### {GAME_NAME.get(d['game'], d['game'])} — 単独プレイ（{d.get('date', '')[:10]}）", "",
           f"対戦なし・時間制限なし・同じ並びで {d['moves']} 手。戦略（問いの立て方）やモデルごとの「置き方の質」だけを比べる。", "",
           "| エージェント | 戦略 | seed | 置けた手数 | " + ("得点 | 連鎖（発火した順） | 最大連鎖 | 作れたおじゃま |" if blobs else "消去ライン | 作れた邪魔ブロック |") + " 回答時間の中央値 | 費用 |",
           "|---|---|---|---|" + ("---|---|---|---|" if blobs else "---|---|") + "---|---|"]
    for r in d["results"]:
        mid = (f"{r['score']:,} | {' '.join(map(str, r['chain_log']))} | {r['max_chain']} | {r['sent']} |" if blobs else f"{r['lines']} | {r['sent']} |")
        out.append(f"| {r['agent']} | {r['strategy'] or '（戦略なし）'} | {r['seed']} | {r['pieces']}{'' if r['survived'] else '（積み上がり）'} | {mid} {r['ms_p50']}ms | ${r['cost_usd']:.3f} |")
    return out + [""]


def main():
    files = sorted(RESULTS.glob("*.json"))
    head = (ROOT / "docs" / "benchmarks.head.md").read_text(encoding="utf-8") if (ROOT / "docs" / "benchmarks.head.md").exists() else "# ベンチマーク結果\n"
    body = []
    for kind, fn in (("bench-", bench_section), ("solo-", solo_section)):
        for f in files:
            if f.name.startswith(kind):
                body += fn(json.loads(f.read_text(encoding="utf-8"))) + [f"元データ: [`results/{f.name}`](results/{f.name})", ""]
    (ROOT / "docs" / "benchmarks.md").write_text(head.rstrip() + "\n\n" + "\n".join(body), encoding="utf-8")
    print("→ docs/benchmarks.md", f"（{len(files)} ファイルから）")


if __name__ == "__main__":
    main()
