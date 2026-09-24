# -*- coding: utf-8 -*-
"""土台そのものを確かめる1本。

ここが通らないうちは、ほかのテストを何本書いても意味がありません。
見張り（conftest の仕掛け）が本当に見張っているか、
つまり「わざと悪いことをしたテストが、ちゃんと落ちるか」まで見ます。
"""
import inspect
import io
import json
import os
import subprocess
import sys
import textwrap

import pytest

import conftest as base

APP_DIR = base.APP_DIR
SETTINGS = os.path.join(APP_DIR, "settings.json")


# ------------------------------------------------------------------ 道具が揃う
def test_本体を読み込める():
    import cli
    import core
    import main
    assert hasattr(core, "sweep_play_tmp_on_exit")
    assert hasattr(main, "App")
    assert hasattr(cli, "main")


def test_本物の署名が変わっていないか():
    """テストの偽物（stub）が本体の引数追加に追いつかず、黙って通る事故が
    3回あった。引数が増えたらここで気づけるようにしておく。

    並べてあるのは「どこかのテストが偽物に差し替えている本体の関数」。
    偽物を足したら、ここにも1行足すこと（2026-09-23 に、差し替えているのに
    照合されていない関数が6つあるのが分かって増やした）。"""
    import core
    expected = {
        # OCRの入り口
        "run_ocr": ["image_paths", "lang", "strip_labels", "errors",
                    "progress_cb", "cancel_event", "notices"],
        "run_rapidocr": ["image_paths", "strip_labels", "cancel_event",
                         "progress_cb"],
        "_run_windows_ocr_chunk": ["image_paths", "lang", "strip_labels",
                                   "errors", "meta"],
        "_english_second_pass": ["result", "meta", "strip_labels", "notices",
                                 "cancel_event", "chunk_size", "progress_cb"],
        "_rapidocr_engine": [],
        "rapidocr_available": [],
        # 合成と再生
        "vv_synthesize_cached": ["base_url", "text", "speaker_id", "speed",
                                 "pitch", "intonation", "volume", "timeout",
                                 "engine_ver", "dict_hash"],
        "play_wav_blocking": ["wav_bytes", "stop_event"],
        "synth_cache_get": ["key"],
        "synth_cache_put": ["key", "wav_bytes"],
        "vv_dict_hash": ["base_url", "timeout"],
        "vv_reading": ["base_url", "text", "speaker_id", "timeout"],
    }
    for name, args in expected.items():
        got = list(inspect.signature(getattr(core, name)).parameters)
        assert got == args, (
            "core.%s の引数が変わりました（%s）。偽物を使っているテストが"
            "黙って通っていないか確かめてください。" % (name, got))


# ------------------------------------------------------------------ 種データ
def test_種データの画像が作れる(seed):
    from PIL import Image
    for path in (seed.japanese(), seed.mixed(), seed.technical(),
                 seed.english(), seed.blank()):
        assert os.path.exists(path)
        with Image.open(path) as img:
            assert img.width > 100 and img.height > 50
    assert not os.path.exists(seed.missing())


def test_種データの壊れたPNGは開けない(seed):
    from PIL import Image, UnidentifiedImageError
    with pytest.raises((UnidentifiedImageError, OSError)):
        with Image.open(seed.corrupt()) as img:
            img.load()


def test_種データの無音WAV(seed):
    data = seed.wav(0.2)
    assert data[:4] == b"RIFF" and len(data) > 1000


# ------------------------------------------------------------------ 見張り本体
def _read_settings():
    """設定を読む。まだ一度も起動していない場所には無いので、その場合は空から始める
    （clone したばかりの作業場所がそれ）。"""
    if os.path.exists(SETTINGS):
        with io.open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    return {}


@pytest.mark.writes_settings
def test_わざと設定を書き換えても土台が戻す():
    """marker を付けたテストは落ちない。そして次のテストには波及しない
    （元に戻ったかどうかは、次のテストが確かめる）。"""
    original = _read_settings()
    original["_煙テストの落書き"] = True
    with io.open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(original, f, ensure_ascii=False)
    assert "_煙テストの落書き" in io.open(SETTINGS, encoding="utf-8").read()


def test_前のテストの落書きが残っていない():
    # 元から settings.json が無かった場所では、土台が「生えたファイル」ごと消す
    text = io.open(SETTINGS, encoding="utf-8").read() if os.path.exists(SETTINGS) else ""
    assert "_煙テストの落書き" not in text


# ------------------------------------------------------------------ 入れ子で確認
def _run_probe(body, name):
    """tests/ の中に一時的な台本を置いて、別の pytest で走らせる。
    見張りが「落とすべきものを落とすか」を確かめるにはこれしかない。
    （probe_*.py は conftest の collect_ignore_glob で普段は拾われない）"""
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# -*- coding: utf-8 -*-\n" + textwrap.dedent(body))
    env = dict(os.environ,
               PYTHONIOENCODING="utf-8",   # 子の日本語が化けないように
               T2V_PROBE_RUN="1")          # 子が自分の台本を掃除しないように
    try:
        return subprocess.run(
            [sys.executable, "-m", "pytest", path, "-q", "-p", "no:cacheprovider",
             "-o", "python_files=probe_*.py"],
            cwd=os.path.dirname(os.path.dirname(path)), env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300)
    finally:
        if os.path.exists(path):
            os.remove(path)


def _output(result):
    """子の言い分をぜんぶ見る（UsageError は stderr に出るため）。"""
    return (result.stdout or "") + (result.stderr or "")


def test_黙って設定を書き換えたテストは落ちる():
    result = _run_probe("""
        import io, json, os
        import conftest as base

        def test_黙って書き換える():
            path = os.path.join(base.APP_DIR, "settings.json")
            data = json.load(io.open(path, encoding="utf-8")) if os.path.exists(path) else {}
            data["_見張りの試し"] = 1
            with io.open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        """, "probe_settings.py")
    assert result.returncode != 0, "書き換えを見逃しました:\n" + _output(result)
    assert "書き換えました" in _output(result)
    # そして本人のファイルは元どおり
    assert "_見張りの試し" not in open(SETTINGS, encoding="utf-8").read()


def test_置き土産を残したテストは落ちる():
    result = _run_probe("""
        import os, tempfile

        def test_置き土産を残す():
            os.mkdir(os.path.join(tempfile.gettempdir(), "t2v_見張りの試し"))
        """, "probe_temp.py")
    assert result.returncode != 0, "置き土産を見逃しました:\n" + _output(result)
    assert "置き土産" in _output(result)
    import glob
    assert not glob.glob(os.path.join(
        __import__("tempfile").gettempdir(), "t2v_見張りの試し"))


def test_gui無しでappを使うテストは落ちる():
    result = _run_probe("""
        def test_markerを付け忘れる(app):
            assert app is not None
        """, "probe_gui.py")
    assert result.returncode != 0
    assert "gui" in _output(result)


def test_エラーログを生やしたテストは落ちる():
    result = _run_probe("""
        import io, os
        import conftest as base

        def test_例外を握りつぶした風に書く():
            path = os.path.join(base.APP_DIR, "エラー.log")
            with io.open(path, "a", encoding="utf-8") as f:
                f.write("見張りの試し")
        """, "probe_errorlog.py")
    assert result.returncode != 0, "記録の生えたのを見逃しました:" + _output(result)
    assert "記録を生やしました" in _output(result)
    assert not os.path.exists(os.path.join(APP_DIR, "エラー.log")), "片づけていない"


def test_打ち間違えたマーカーは落ちる():
    """strict にしていないと、engine の打ち間違いが CI をすり抜ける。"""
    result = _run_probe("""
        import pytest

        @pytest.mark.enigne          # わざと打ち間違える
        def test_打ち間違い():
            pass
        """, "probe_marker.py")
    assert result.returncode != 0, "打ち間違いを見逃しました:" + _output(result)
    assert "enigne" in _output(result)


def test_本体の起動が壊れていたら飛ばさずに落ちる():
    """Tcl の一時的な不調だけを作り直す。本体のバグを skip の裏に隠さない。"""
    result = _run_probe("""
        import pytest
        import main

        @pytest.fixture(autouse=True)
        def 壊す(monkeypatch):
            class 壊れたApp:
                def __init__(self, *a, **kw):
                    raise TypeError("起動が壊れている")
            monkeypatch.setattr(main, "App", 壊れたApp)

        @pytest.mark.gui
        def test_画面を作る(app):
            assert app
        """, "probe_broken_app.py")
    assert result.returncode != 0, "壊れた起動を飛ばしました:" + _output(result)
    assert "TypeError" in _output(result)
    assert "skipped" not in _output(result)


def test_種データに日本語フォントが使える(seed):
    """フォントが見つからないと文字が描けず、OCRのテストが意味を失う。"""
    from PIL import ImageFont
    import _seed
    if not any(os.path.exists(path) for path in _seed._FONTS):
        pytest.skip("このPCには日本語フォントが無い（OCRのテストは意味を持たない）")
    assert isinstance(seed.font(30), ImageFont.FreeTypeFont)


# ------------------------------------------------------------------ 画面
@pytest.mark.gui
def test_画面が開いて閉じる(app, pump):
    assert app.winfo_exists()
    pump(0.3)
    assert app.title()


@pytest.mark.gui
def test_on_closeはテストから呼べない(app):
    import main
    with pytest.raises(AssertionError):
        main.App._on_close(app)
