# -*- coding: utf-8 -*-
"""
ocr_mac.py - macOS 標準の Vision フレームワークを使ったオフラインOCRヘルパー
pyobjc (pyobjc-framework-Vision) 経由でプロセス内で実行する。ocr_win.ps1 の macOS 版。
ネット接続は不要（認識は端末内で完結する）。
"""
import os

# アプリの言語コード → Vision の言語コード
_LANG_MAP = {"ja": ["ja-JP", "en-US"], "en": ["en-US"]}


def recognize_files(image_paths, lang="ja", reflow=True, strip_labels=True):
    """画像パスのリストをOCRし {path: text} を返す。読めなかったファイルは ""。
    reflow=True のとき、Visionが返す行の外接矩形（座標）を使って“折り返しで割れた1文”を
    確実に連結する（見出し・箇条書き・別段落は連結しない）。
    strip_labels=True のとき、連結の前に“映像内オーバーレイ・ラベル行”（局ロゴ・番組名・
    日時・カテゴリ）を座標で除去する（core.strip_overlay_labels）。"""
    try:
        import Vision  # noqa: F401  先にimport可否だけ確認して分かりやすいエラーにする
    except ImportError:
        raise RuntimeError(
            "pyobjc-framework-Vision がインストールされていません。\n"
            "setup.command を実行してください。")
    languages = _LANG_MAP.get(lang, [lang])
    result = {}
    errors = []
    for path in image_paths:
        try:
            result[path] = _recognize_one(path, languages, reflow=reflow,
                                          strip_labels=strip_labels)
        except Exception as e:
            result[path] = ""
            errors.append(f"{os.path.basename(path)}: {e}")
    if errors and not any(result.values()):
        # 全滅した場合のみ致命的エラーとして通知（一部失敗は空文字で続行）
        raise RuntimeError("OCR失敗: " + " / ".join(errors[:3]))
    return result


def _recognize_one(path, languages, reflow=True, strip_labels=True):
    """1枚の画像をVision OCRにかけ、行テキストを返す。
    strip_labels=True なら折り返し連結の前に“オーバーレイ・ラベル行”を座標で除去
    （core.strip_overlay_labels）。reflow=True なら各行の外接矩形を使って折り返しを連結
    （core.reflow_ocr_lines）。"""
    import Vision
    from Foundation import NSURL

    url = NSURL.fileURLWithPath_(os.path.abspath(path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    try:
        # 日本語対応の Revision3 (macOS 13+) を明示する。生成時の既定revisionは
        # 実行環境・リンク方法に依存し、古い版に落ちると日本語精度が下がるため。
        # 未対応OS (macOS 12以前) では設定せず既定のまま（例外時も現状維持）。
        revs = Vision.VNRecognizeTextRequest.supportedRevisions()
        if revs and revs.containsIndex_(3):
            request.setRevision_(3)
    except Exception:
        pass
    try:
        request.setRecognitionLanguages_(languages)
        ok, err = handler.performRequests_error_([request], None)
    except Exception:
        ok, err = False, None
    if not ok:
        # 指定言語が使えない環境では既定言語で再試行
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        ok, err = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(str(err) if err else "Vision OCRの実行に失敗しました")
    lines = []
    for obs in (request.results() or []):
        candidates = obs.topCandidates_(1)
        if not candidates or len(candidates) == 0:
            continue
        text = str(candidates[0].string())
        b = obs.boundingBox()  # 正規化[0,1]・原点は左下
        x0 = float(b.origin.x)
        x1 = x0 + float(b.size.width)
        y_bottom = float(b.origin.y)
        y_top = 1.0 - (y_bottom + float(b.size.height))   # 上端基準に変換
        lines.append({"text": text, "x0": x0, "x1": x1,
                      "y0": y_top, "y1": 1.0 - y_bottom})
    if not lines:
        return ""
    if strip_labels:
        try:
            import core
            lines = core.strip_overlay_labels(lines)
        except Exception:
            pass  # ラベル除去に失敗しても素の行で続行（本文は保持される）
        if not lines:
            return ""
    if reflow:
        try:
            import core
            return core.reflow_ocr_lines(lines)
        except Exception:
            pass  # 座標連結に失敗しても素の行結合で続行
    return "\n".join(l["text"] for l in lines)
