# -*- coding: utf-8 -*-
"""
ocr_mac.py - macOS 標準の Vision フレームワークを使ったオフラインOCRヘルパー
pyobjc (pyobjc-framework-Vision) 経由でプロセス内で実行する。ocr_win.ps1 の macOS 版。
ネット接続は不要（認識は端末内で完結する）。
"""
import os

# アプリの言語コード → Vision の言語コード
_LANG_MAP = {"ja": ["ja-JP", "en-US"], "en": ["en-US"]}


def recognize_files(image_paths, lang="ja"):
    """画像パスのリストをOCRし {path: text} を返す。読めなかったファイルは ""。"""
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
            result[path] = _recognize_one(path, languages)
        except Exception as e:
            result[path] = ""
            errors.append(f"{os.path.basename(path)}: {e}")
    if errors and not any(result.values()):
        # 全滅した場合のみ致命的エラーとして通知（一部失敗は空文字で続行）
        raise RuntimeError("OCR失敗: " + " / ".join(errors[:3]))
    return result


def _recognize_one(path, languages):
    """1枚の画像をVision OCRにかけ、行テキストを改行結合して返す。"""
    import Vision
    from Foundation import NSURL

    url = NSURL.fileURLWithPath_(os.path.abspath(path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
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
        if candidates and len(candidates) > 0:
            lines.append(str(candidates[0].string()))
    return "\n".join(lines)
