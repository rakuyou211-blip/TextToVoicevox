# -*- coding: utf-8 -*-
"""強い文字読み取り（PP-OCRv6）を動かす裏方。アプリとは別の Python で動く（Windows）。

アプリの Python は新しい版のことがあり、読み取りの部品（onnxruntime）が対応していない。
そこで ocr_engine/ に小さな Python と部品を一式置いて（winsetup.py ocr が入れる）、
そちらでこのファイルを動かす。アプリとは、標準入出力で1行ずつ JSON をやりとりする。

  ← {"ready": true}                  読み込みが済んだ（失敗なら {"fatal": "理由"}）
  → {"path": "C:\\...\\page.png"}    この画像を読んで
  ← {"ok": true, "width": 800, "height": 600,
     "items": [[[[x, y], [x, y], [x, y], [x, y]], "文字", 確からしさ], ...]}
  ← {"ok": false, "error": "理由"}   この1枚は読めなかった

座標は元の画像のもの（読むために広げた分は戻してある。逆さの画像を回して読んだときは
"turned": true が付き、座標と幅・高さは回したあとの画像のものになる）。
アプリが閉じれば標準入力が切れるので、ここも終わる。しばらく頼まれなくても終わる
（メモリを使い続けないため）。次に頼まれたら、アプリがまた起こす。
画像はこのPCの中だけで読む。モデルは部品に同梱で、ネットには一切つながない。

  python ocr_helper.py              上のやりとりを始める
  python ocr_helper.py --selftest   本当に読めるかを試す（winsetup.py が入れた直後に使う）
"""
import json
import os
import sys
import threading
import time

# しばらく頼まれなければ終わる（1枚読んだあとで約140MB 使っているので、早めに返す）。
# 起こし直すのは約1秒で、スクリーンショットの範囲を選んでいるあいだに先に起こすので、待たない
IDLE_EXIT_SEC = 300

# 読み方（2026-11-05 に、試験の画像258枚とページ15枚で実測して決めた）。
# ・小さい画像は2倍に広げ、周りに余白を足す（端の文字と、数文字だけのラベルを落とさない）
# ・文字の場所を探すときだけ長い辺を DET_LIMIT まで縮める。既定（短い辺を736まで広げる）
#   だと小さな切り抜きが巨大になり、読み違いが増えるうえ4〜5倍遅い。上限を上げても、
#   ページの正解率はほぼ変わらず遅くなるだけ（1280〜2560 で 1.2〜1.6倍）
# ・文字そのものは大きいままの画像から切り出して読む（既定の2000まで縮めない）
SCALE_BELOW = 1600
MARGIN = 16
DET_LIMIT = 960
SIDE_LIMIT = 4000
POOR_CONFIDENCE = 0.8   # これ未満なら、向きが違うかもしれないので回して読み直す


def _forbid_network():
    """このプロセスはネットを使わない。部品が勝手につなぎに行かないよう、入口を塞ぐ。"""
    import socket

    def refuse(*_args, **_kwargs):
        raise OSError("文字の読み取りはネットを使いません")
    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse
    socket.create_connection = refuse
    socket.getaddrinfo = refuse


def build_engine():
    from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR
    # スレッド数は4まで。既定の「全コア」は、コアの多い機体でかえって遅い
    # （core._rapidocr_engine と同じ。実測 20コア: 全コア1.48秒 / 4スレッド0.65秒）
    threads = max(1, min(4, os.cpu_count() or 4))
    return RapidOCR(params={
        "Global.log_level": "error",
        "Global.max_side_len": SIDE_LIMIT,
        "Det.engine_type": EngineType.ONNXRUNTIME,
        "Det.lang_type": LangDet.CH,
        "Det.model_type": ModelType.SMALL,
        "Det.ocr_version": OCRVersion.PPOCRV6,
        "Det.limit_type": "max",
        "Det.limit_side_len": DET_LIMIT,
        "Rec.engine_type": EngineType.ONNXRUNTIME,
        "Rec.lang_type": LangRec.JAPAN,
        "Rec.model_type": ModelType.SMALL,
        "Rec.ocr_version": OCRVersion.PPOCRV6,
        "EngineConfig.onnxruntime.intra_op_num_threads": threads,
        "EngineConfig.onnxruntime.inter_op_num_threads": threads,
    })


def scale_for(width, height):
    """読む前に何倍に広げるか。大きなページは、広げても遅くなるだけ。"""
    return 2 if max(width, height) < SCALE_BELOW else 1


def to_original(box, scale, margin):
    """広げて余白を足した画像の座標を、元の画像の座標に戻す。"""
    return [[(float(x) - margin) / scale, (float(y) - margin) / scale] for x, y in box]


def read_image(path):
    """画像を読み、広げて余白を足す。(絵, 元の幅, 元の高さ, 倍率) を返す。
    cv2.imread は日本語のパスを開けないので、バイト列にしてから渡す。"""
    import cv2
    import numpy as np
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("画像を開けませんでした")
    height, width = img.shape[:2]
    scale = scale_for(width, height)
    if scale != 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    edge = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]])
    color = tuple(int(v) for v in np.median(edge, axis=0))
    img = cv2.copyMakeBorder(img, MARGIN, MARGIN, MARGIN, MARGIN,
                             cv2.BORDER_CONSTANT, value=color)
    return img, width, height, scale


def recognize(engine, loaded):
    """読み込んだ画像の文字を読む。文字が1つも無ければ items は空。"""
    img, width, height, scale = loaded
    result = engine(img, use_cls=False)
    boxes = getattr(result, "boxes", None)
    txts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    items = []
    if boxes is not None and txts:
        for box, text, score in zip(boxes, txts, scores):
            items.append([to_original(box.tolist(), scale, MARGIN), str(text), float(score)])
    return {"ok": True, "width": width, "height": height, "items": items}


def weight(items):
    """読めた字数（確からしさで重みをつける）。"""
    return sum(len(text) * score for _box, text, score in items)


def confidence(items):
    """字数で重みをつけた、確からしさの平均。何も読めなければ 0。"""
    chars = sum(len(text) for _box, text, _score in items)
    return weight(items) / chars if chars else 0.0


def recognize_upright(engine, loaded, rotations=None):
    """読む。逆さ・横倒しの画像（スキャンの向き違いなど）は、回して読み直す。
    実測（2026-11-05）: 正しい向きなら確からしさの平均は 0.978 以上、違う向きでは 0.68 以下で
    字もほとんど残らないので、はっきり見分けられる。正しい向きのページは1回で済む。
    回して読んだときは、座標は回したあとの画像のもの（"turned" を付けて知らせる）。
    横向きの中で、行が上から下へ流れる向きだけは、縦書きの列として正しく読める。"""
    best = recognize(engine, loaded)
    if confidence(best["items"]) >= POOR_CONFIDENCE:
        return best
    img, width, height, scale = loaded
    if rotations is None:
        import cv2
        rotations = [(lambda i, c=c: cv2.rotate(i, c), swap) for c, swap in
                     ((cv2.ROTATE_180, False), (cv2.ROTATE_90_CLOCKWISE, True),
                      (cv2.ROTATE_90_COUNTERCLOCKWISE, True))]
    for turn, swap in rotations:
        size = (height, width) if swap else (width, height)
        again = recognize(engine, (turn(img), size[0], size[1], scale))
        if weight(again["items"]) > weight(best["items"]):
            best = dict(again, turned=True)
        if confidence(best["items"]) >= POOR_CONFIDENCE:
            break
    return best


def serve(read, lines, write, clock=time.monotonic, seen=None):
    """頼まれた画像を順に読む。read(パス) が返事を作る。1枚の失敗は他を止めない。"""
    for line in lines:
        if seen is not None:
            seen[0] = clock()
        try:
            reply = read(json.loads(line)["path"])
        except Exception as e:
            reply = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        write(reply)
        if seen is not None:
            seen[0] = clock()


def _exit_when_idle(seen):
    while True:
        time.sleep(30)
        if time.monotonic() - seen[0] > IDLE_EXIT_SEC:
            os._exit(0)


def selftest():
    """絵に英数字を書いて読ませる（日本語のフォントが無い機械でも試せるように）。"""
    import cv2
    import numpy as np
    _forbid_network()
    began = time.monotonic()
    engine = build_engine()
    img = np.full((80, 460, 3), 255, np.uint8)
    cv2.putText(img, "TextToVoicevox 2026", (14, 52), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
                (20, 20, 20), 2, cv2.LINE_AA)
    reply = recognize(engine, (img, 460, 80, 1))
    text = "".join(item[1] for item in reply["items"])
    good = "2026" in text and "voice" in text.lower()
    print(json.dumps({"ok": good, "text": text, "sec": round(time.monotonic() - began, 1)},
                     ensure_ascii=True), flush=True)
    return 0 if good else 1


def main(argv):
    if "--selftest" in argv:
        return selftest()
    # 返事の通り道は取っておき、標準出力は標準エラーへ向ける（部品が何か print しても、
    # やりとりに混ざらないように）
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)

    def write(obj):
        out.write(json.dumps(obj, ensure_ascii=True).encode("ascii") + b"\n")
        out.flush()

    try:
        _forbid_network()
        engine = build_engine()
    except Exception as e:
        write({"fatal": "%s: %s" % (type(e).__name__, e)})
        return 1
    write({"ready": True})
    seen = [time.monotonic()]
    threading.Thread(target=_exit_when_idle, args=(seen,), daemon=True).start()
    serve(lambda path: recognize_upright(engine, read_image(path)), sys.stdin.buffer, write, seen=seen)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
