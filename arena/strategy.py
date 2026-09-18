"""戦略ファイル（strategies/<game>/<name>.py）の読み込みと、書かれていない部分の既定の動作。

戦略ファイルに書くもの（* は必須）:
    DESCRIPTION    … 画面の選択肢に出る一言。RECOMMENDED = True を書くと先頭に出る
  * QUESTION       … 何を判断させるか（LLM のプロンプトに入る）
  * INSTRUCTIONS   … Jev の各問いの文。`{id}` が候補 ID（p00, p01, …）に置き換わる
  * LEVELS         … Score の評価基準（悪い → 良い の順、2〜10 段階）
  * features(cand, view) … 候補1つぶんの「モデルに見せる特徴量」（JSON にできる dict）
    context(view)  … 全候補に共通の状況（dict）。省略すると {}
    TIE_EPS / tie_break(features) … スコア差が TIE_EPS 以内の候補を同点とみなし、tie_break の返す値が小さいものを選ぶ
    TIE_BREAK_TEXT … 同じ優先順を LLM に伝える英文
  上級者向け（Jev への問いの立て方そのものを変える）:
    questions(feats, ctx) … Jev に送る questions の dict をまるごと作る（例: 観点ごとに Score を分けて合成する）
    pick(answers, feats, ctx) … Jev の answers から (候補 ID, ログ用のメモ) を決める
    llm_system(intro)     … LLM のシステムプロンプトをまるごと作る

試合を始めるたびに読み込み直すので、サーバーを再起動せずに編集を試せる。
"""
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DIR = ROOT / "strategies"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def list_strategies(game_id):
    """[{"name", "description", "recommended"}, ...]。推奨（RECOMMENDED = True）を先頭に、あとは名前順。"""
    out = []
    for p in sorted((STRATEGY_DIR / game_id).glob("*.py")):
        if not NAME_RE.match(p.stem):
            continue
        src = p.read_text(encoding="utf-8")
        m = re.search(r'^DESCRIPTION\s*=\s*"([^"]*)"', src, re.M)  # 読み込まずに（実行せずに）先頭の定数だけ拾う
        out.append({"name": p.stem, "description": m.group(1) if m else "", "recommended": bool(re.search(r"^RECOMMENDED\s*=\s*True", src, re.M))})
    return sorted(out, key=lambda s: (not s["recommended"], s["name"]))


def default_strategy(game_id):
    names = list_strategies(game_id)
    if not names:
        raise StrategyError(f"strategies/{game_id}/ に戦略ファイルがありません")
    return names[0]["name"]


class StrategyError(Exception):
    pass


class Strategy:
    def __init__(self, game, name):
        if not NAME_RE.match(name or ""):
            raise StrategyError(f"戦略名が不正です: {name!r}")
        self.game, self.name = game, name
        self.path = STRATEGY_DIR / game.ID / f"{name}.py"
        if not self.path.exists():
            raise StrategyError(f"戦略ファイルがありません: {self.path.relative_to(ROOT)}")
        self.source = self.path.read_text(encoding="utf-8")
        spec = importlib.util.spec_from_file_location(f"strategy_{game.ID}_{name}", self.path)
        self.mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(self.mod)
        except Exception as e:
            raise StrategyError(f"{self.path.name} の読み込みに失敗: {type(e).__name__}: {e}") from e
        for attr in ("QUESTION", "INSTRUCTIONS", "LEVELS", "features"):
            if not hasattr(self.mod, attr):
                raise StrategyError(f"{self.path.name} に {attr} がありません")
        self.levels = list(self.mod.LEVELS)
        if not 2 <= len(self.levels) <= 10:
            raise StrategyError("LEVELS は 2〜10 段階にしてください（Score の仕様）")
        self.tie_eps = float(getattr(self.mod, "TIE_EPS", 0.0))
        self.model = getattr(self.mod, "MODEL", "jev-latest")

    # ---- コード側の前処理
    def context(self, view):
        return self.mod.context(view) if hasattr(self.mod, "context") else {}

    def features(self, cand, view):
        f = self.mod.features(cand, view)
        json.dumps(f)  # JSON にできないものが混ざっていたら、ここで分かる
        return f

    # ---- Jev への問い
    def state(self, feats, ctx):
        return {"game": self.game.INTRO, **ctx, "candidates": feats}

    def questions(self, feats, ctx):
        if hasattr(self.mod, "questions"):
            return self.mod.questions(feats, ctx)
        return {cid: {"type": "score", "instructions": self.mod.INSTRUCTIONS.replace("{id}", cid), "criteria": self.levels} for cid in feats}

    def pick(self, answers, feats, ctx):
        """戻り値: (候補 ID, ログ用のメモ)"""
        if hasattr(self.mod, "pick"):
            r = self.mod.pick(answers, feats, ctx)
            return r if isinstance(r, tuple) else (r, "")
        ranked = sorted(((k, v) for k, v in answers.items() if k in feats), key=lambda kv: -kv[1]["score"])
        best = ranked[0][1]["score"]
        pool = [k for k, v in ranked if v["score"] >= best - self.tie_eps]
        # 同点の候補どうしの優劣は数値の大小比較なので、モデルではなくコードが決める
        pick = min(pool, key=lambda k: self.mod.tie_break(feats[k])) if len(pool) > 1 and hasattr(self.mod, "tie_break") else pool[0]
        a = answers[pick]
        note = f"score {a['score']:.2f} conf {a['confidence']:.2f}"
        if len(pool) > 1:
            note += f" ｜同点{len(pool)}件はコードが tie_break で選択"
        elif len(ranked) > 1:
            note += f" ｜2位 {ranked[1][0]} {ranked[1][1]['score']:.2f}"
        return pick, note

    # ---- LLM への指示（Jev と同じ基準・同じ特徴量を渡す）
    def llm_system(self):
        if hasattr(self.mod, "llm_system"):
            return self.mod.llm_system(self.game.INTRO)
        tie = getattr(self.mod, "TIE_BREAK_TEXT", "")
        return (
            f"You are playing this game: {self.game.INTRO}\nFor each turn you receive a JSON object with candidate placements "
            "(the result of each placement is already computed by code) and must choose exactly one.\n\n"
            f"Question to judge each candidate by: {self.mod.QUESTION}\n"
            f"Quality levels, from worst (0) to best ({len(self.levels) - 1}){'; ' + tie if tie else ''}:\n" + "\n".join(f"{i}: {t}" for i, t in enumerate(self.levels)) + "\n\n"
            'Reply with ONLY a JSON object of the form {"pick": "pNN"} — no code fences, no explanation.'
        )

    def describe(self):
        return {"name": self.name, "file": str(self.path.relative_to(ROOT)), "question": self.mod.QUESTION, "levels": self.levels, "tie_eps": self.tie_eps, "source": self.source}
