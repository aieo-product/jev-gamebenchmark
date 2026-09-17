"""遊べるゲームの一覧。ゲームを足すときは、blocks.py / blobs.py と同じ形のモジュールを作ってここに登録する。"""
from . import blobs, blocks

GAMES = {"blobs": blobs, "blocks": blocks}
