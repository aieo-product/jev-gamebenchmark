# 戦略ファイルの書き方

`strategies/<game>/<名前>.py` が1つの戦略です（名前は英数字・`_`・`-`）。置くだけで画面の「戦略」に出ます。試合を始めるたびに読み込み直すので、サーバーの再起動は不要です。

## 1手の流れ

```
ゲームロジック: 置き方をすべて列挙し、結果をシミュレーション → cand（候補）× 最大22〜34個
      ↓
戦略: context(view)        … 全候補に共通の状況
戦略: features(cand, view) … 候補ごとの、モデルに見せる特徴量
      ↓
Jev : 候補ごとに Score 1問（INSTRUCTIONS + LEVELS）を、1リクエストでまとめて採点
LLM : 同じ context / features / LEVELS を渡して {"pick": "pNN"} を返させる
      ↓
戦略: スコア最高の候補を採用。TIE_EPS 以内の同点は tie_break(features) が小さいものを選ぶ
```

## 使える材料

`features(cand, view)` の `cand`:

| ゲーム | キー |
|---|---|
| 共通 | `id`, `rot`, `x`, `y`, `board_before`, `board_after`（盤面は 行×列 のリスト。空きは `""`） |
| blobs | `board_placed`（消える前）, `placed`（置いた2マス）, `steps`（連鎖の各段: `popped` / `garbage` / `score`）, `garbage`（生まれるおじゃま数）, `pending`（予告おじゃま数） |
| blocks | `lines`（消えるライン数） |

`view`: `board`, `current`, `next`, `pending`, `opponent_board`, `candidates`（全候補。候補どうしを見比べたいとき）。blobs は `opponent_pending`, `rate` も。

道具（import して使う）:

- `arena.games.blobs`: `heights`, `find_groups`, `resolve`, `chain_potential(board, max_add)`, `touching_same_colour(cand)`, `drop_pair`, 定数 `W` `H` `SPAWN_X` `COLOR_NAME`
- `arena.games.blocks`: `heights`, `count_holes`, `bumpiness`, 定数 `W` `H`

自分で特徴量の関数を書いてもかまいません（例: 連鎖の形、2手読み）。ただし `features()` は1手ごとに全候補ぶん呼ばれ、その間ゲームは止まらないので、合計で数十ms 以内に収めてください。

## 問いの構成そのものを変える（任意）

```python
def questions(feats, ctx):
    """Jev に送る questions をまるごと作る。ID は自由。1リクエストで並列に答えが返る。"""
    q = {}
    for cid in feats:
        q[f"{cid}.safe"] = {"type": "noul", "instructions": f"Does the placement in `candidates.{cid}` keep the third column safe?"}
        q[f"{cid}.build"] = {"type": "score", "instructions": f"How much does the placement in `candidates.{cid}` help build a long chain?", "criteria": BUILD_LEVELS}
    return q

def pick(answers, feats, ctx):
    """answers から手を決める。戻り値は 候補ID、または (候補ID, ログ用メモ)。"""
    def value(cid):
        return answers[f"{cid}.build"]["score"] * (answers[f"{cid}.safe"]["noul"] > 0.5)   # Noul の答えは 0（no）〜1（yes）
    return max(feats, key=value)
```

LLM 側のプロンプトを変えたいときは `llm_system(intro)` を定義します（既定は QUESTION と LEVELS から自動生成）。

## うまくいった考え方（同梱の build-big-chains / keep-flat から）

1. **幾何・探索・数値比較はコード、良し悪しの評価はモデル。** 盤面をそのまま読ませない。差分（増えた高さ など）はコードで計算して渡す。
2. **Jev は LEVELS を字義どおり、各段を別々に読む。** 「3連鎖以上＝最上位」と書けば、3連鎖ができた瞬間に打つ。意図しない行動は、たいてい基準の書き方が原因。
3. **段には状況を書く。** 「やや良い」のような程度ではなく、「何も消えず、置いたブロブが同色につながり、狙える連鎖が伸びる」のように。
4. **条件の掛け合わせは、コードで1つの言葉にまとめる。** 「連鎖数 × 盤面の余裕 × 予告おじゃま」を `pop_timing: "early: ..."` のようにして渡すと、採点が安定した。
5. **同点は多い。** 2.00 や 3.00 ちょうどが並ぶので、`tie_break` の設計が効く。ただし TIE_EPS を広げすぎると、モデルの判断を上書きしてしまう。
6. **うまくいかないときは、候補ごとのスコアと確率分布を見る。** 画面の回答ログの行をクリックすると、その手の全リクエスト／レスポンスが見られる（`logs/*.jsonl` にも残る）。

公式ドキュメント: https://docs.typesafe.ai/llms.txt （Score の段の書き方は `primitives/score`、観点の合成は `patterns/composite-scoring`）
