# -*- coding: utf-8 -*-
"""GUIスモークテスト。

`App()` を生成し、保持必須ウィジェット/変数の存在・結線メソッド・busy切替・
テーマ往復・設定往復・整形の折りたたみ開閉を確認して `destroy()` する。
UI改修の唯一の自動安全網（`core` には影響しないため既存テスト数は不変）。

**Tk初期化不可（DISPLAYなし・`tk.TclError`）や依存不足の環境では skip** するので、
手元のMac(Tk9)では実走し、CIでは安全に飛ばせる。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk


# 他メソッドが参照する（改名・削除不可の）保持必須ウィジェット属性
REQUIRED_WIDGETS = [
    "listbox", "clip_btn", "extract_btn", "progress", "text", "text_font",
    "vvproj_btn", "speaker_cb", "dlg_speaker_cb", "preset_cb", "fmt_cb",
    "unit_cb", "preview_btn", "playall_btn", "resume_btn", "stop_btn",
    "synth_btn", "dict_btn", "rule_cb", "restore_btn", "theme_cb",
    "rule_menu_btn",
]

# 保持必須の tk.*Var / StringVar 群
REQUIRED_VARS = [
    "status_var", "url_var", "engine_var", "speed_var", "pitch_var",
    "into_var", "vol_var", "nlines_var", "gap_var", "srt_var", "find_var",
    "repl_var", "mode_var", "pdf_var", "dpi_var", "pre_var", "blank_var",
    "ascii_var", "smartjoin_var", "join_var", "pruby_var", "norm_var",
    "denoise_var", "dark_var", "dlg_var", "theme_var", "fixconf_var",
]

# 配置を変えても生かす結線メソッド
REQUIRED_METHODS = [
    "_set_busy", "apply_theme", "_register_drop_tree", "_load_settings",
    "_save_settings", "_build_ui", "_settings_dict",
]


def _make_app():
    """App を生成する。表示不可・依存不足なら skip。"""
    try:
        import main
    except Exception as e:  # PIL 等の依存が無いCI
        pytest.skip(f"GUIモジュールを読み込めません: {e}")
    try:
        return main.App()
    except tk.TclError as e:  # DISPLAYなし等
        pytest.skip(f"Tkを初期化できません（表示不可の環境）: {e}")


@pytest.fixture
def app():
    a = _make_app()
    try:
        yield a
    finally:
        try:
            a.destroy()
        except Exception:
            pass


def test_required_widgets_exist(app):
    missing = [n for n in REQUIRED_WIDGETS if not hasattr(app, n)]
    assert not missing, f"必須ウィジェットがありません: {missing}"


def test_required_vars_exist(app):
    missing = [n for n in REQUIRED_VARS if not hasattr(app, n)]
    assert not missing, f"必須変数がありません: {missing}"
    assert hasattr(app, "_font_size0")


def test_wiring_methods_exist(app):
    for name in REQUIRED_METHODS:
        assert callable(getattr(app, name, None)), f"{name} が無い/呼べません"


def test_set_busy_toggles(app):
    """_set_busy が抽出/クリップボードボタンを disable/enable する。"""
    app._set_busy(True)
    assert str(app.extract_btn["state"]) == "disabled"
    assert str(app.clip_btn["state"]) == "disabled"
    app._set_busy(False)
    assert str(app.extract_btn["state"]) == "normal"
    assert str(app.clip_btn["state"]) == "normal"


def test_theme_roundtrip_restores_text_colors(app):
    """ダーク→ライトで本文欄の色が既定へ戻る（テーマ往復で色が残らない）。"""
    app.theme_var.set("light")
    app.apply_theme()
    light_bg = str(app.text.cget("bg"))
    app.theme_var.set("dark")
    app.apply_theme()
    assert str(app.text.cget("bg")) != light_bg
    assert app.dark_var.get() is True    # 旧キー互換の同期
    app.theme_var.set("light")
    app.apply_theme()
    assert str(app.text.cget("bg")) == light_bg
    assert app.dark_var.get() is False


def test_all_themes_apply_safely(app):
    """4テーマすべて適用できて、パレットの背景が反映される。"""
    for key, _label, pal in app.THEMES:
        app.theme_var.set(key)
        app.apply_theme()
        assert str(app.text.cget("bg")) == pal["textbg"], key
    app.theme_var.set("light")
    app.apply_theme()


def test_theme_backcompat_from_dark_flag(app, tmp_path, monkeypatch):
    """旧settings.json（"dark": true のみ・"theme"なし）がダークとして読める。"""
    import json
    import main as main_mod
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"dark": True}), encoding="utf-8")
    monkeypatch.setattr(main_mod, "SETTINGS_PATH", str(p))
    app.theme_var.set("light")
    app._load_settings()
    assert app.theme_var.get() == "dark"


def test_settings_roundtrip_keys_present(app):
    """_settings_dict が旧キーを網羅し、新キー adv_open も含む。"""
    d = app._settings_dict()
    for key in ("mode", "pdf", "dpi", "preprocess", "blank", "ascii", "join",
                "smart_join", "paren_ruby", "normalize", "denoise", "dark",
                "unit", "nlines", "srt", "font_size", "speed", "speaker",
                "pitch", "intonation", "volume", "fmt", "gap", "replace_rules",
                "presets", "dlg_enabled", "dlg_speaker", "bookmark", "base_url",
                "geometry", "adv_open", "theme", "fix_confusables",
                "voice_detail_open"):
        assert key in d, f"設定キー {key} が欠落"


def test_advanced_toggle(app):
    """整形の詳細設定パネルを開閉してもクラッシュせず、状態が反映される。"""
    app._set_advanced(True)
    assert app._adv_open is True
    app._set_advanced(False)
    assert app._adv_open is False
    app._toggle_advanced()
    assert app._adv_open is True


def test_voice_detail_toggle(app):
    """§4のプリセット/セリフ行の開閉（既定は畳む。開閉往復でクラッシュしない）。"""
    app._set_voice_detail(True)
    assert app._vdetail_open is True
    app._set_voice_detail(False)
    assert app._vdetail_open is False
    app._toggle_voice_detail()
    assert app._vdetail_open is True
    app._set_voice_detail(False)


def test_clear_restore_button_toggles(app):
    """全消去/復元の1ボタン切替: 全消去→「復元」表示→復元→「本文を全消去」に戻る。"""
    app.text.insert("1.0", "テスト本文です。")
    app.clear_text()
    assert str(app.restore_btn["text"]) == "復元"
    assert app.text.get("1.0", "end-1c").strip() == ""
    app.restore_text()
    assert str(app.restore_btn["text"]) == "本文を全消去"
    assert "テスト本文です。" in app.text.get("1.0", "end-1c")


def test_step_highlight_follows_body(app):
    """「次に押すボタン」の絞り込み: 本文が空→抽出がPrimary / あり→生成がPrimary。"""
    app.text.delete("1.0", "end")
    app._update_step_highlight()
    assert str(app.extract_btn["style"]) == "Primary.TButton"
    assert str(app.synth_btn["style"]) == "Secondary.TButton"
    app.text.insert("1.0", "本文あり")
    app._update_step_highlight()
    assert str(app.extract_btn["style"]) == "Secondary.TButton"
    assert str(app.synth_btn["style"]) == "Primary.TButton"


def test_kb_invoke_ignores_disabled(app):
    """ショートカット経由のボタン起動は無効中は何もしない（"break"だけ返す）。"""
    app.synth_btn.config(state="disabled")
    assert app._kb_invoke(app.synth_btn) == "break"


def test_portrait_key_mapping(app):
    """話者ラベル→立ち絵キーの対応（立ち絵の有無に依存しない純ロジック）。"""
    assert app._portrait_key_for("四国めたん（ノーマル）") == "metan"
    assert app._portrait_key_for("ずんだもん（あまあま）") == "zundamon"
    assert app._portrait_key_for("春日部つむぎ（ノーマル）") is None
    assert app._portrait_key_for("") is None


def test_portrait_and_resize_no_crash(app):
    """立ち絵更新・パネル自動開閉・メイン枠の存在（資産の有無に関わらず安全）。"""
    assert hasattr(app, "_main")
    assert isinstance(app._portraits, dict)
    app._update_portrait()          # 立ち絵が無くても例外を出さない
    app._on_resize_toggle_side()    # _side が None でも安全


def test_portrait_frames_structure(app):
    """立ち絵は base を必ず持つフレームdict（closed があれば base=closed）。"""
    for key, frames in app._portraits.items():
        assert isinstance(frames, dict) and "base" in frames, key


def test_animation_methods_safe(app):
    """まばたき・口パクの開始/停止が資産の有無に関わらず例外を出さない。"""
    app._start_mouth()
    app._start_mouth(speaker_id=999999)   # 未知IDでも安全
    app._stop_mouth()
    app._blink_tick()
    app._show_frame("open")
    app._show_frame("そんなフレームない")   # 未知名は base にフォールバック
