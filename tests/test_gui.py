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
    "listbox", "clip_btn", "screen_btn", "extract_btn", "progress", "text", "text_font",
    "vvproj_btn", "speaker_cb", "dlg_speaker_cb", "preset_cb", "fmt_cb",
    "unit_cb", "preview_btn", "playall_btn", "resume_btn", "stop_btn",
    "synth_btn", "dict_btn", "rule_cb", "restore_btn", "theme_cb",
    "rule_menu_btn", "sample_btn", "pause_btn", "report_btn", "engine_lbl",
    "char_cb", "nlines_sb", "gap_sb",
]

# 保持必須の tk.*Var / StringVar 群
REQUIRED_VARS = [
    "status_var", "url_var", "engine_var", "speed_var", "pitch_var",
    "into_var", "vol_var", "nlines_var", "gap_var", "srt_var", "find_var",
    "repl_var", "mode_var", "pdf_var", "dpi_var", "pre_var", "blank_var",
    "ascii_var", "smartjoin_var", "join_var", "pruby_var", "norm_var",
    "denoise_var", "dark_var", "dlg_var", "theme_var", "fixconf_var",
    "urlskip_var", "read_fix_var",
]

# 配置を変えても生かす結線メソッド
REQUIRED_METHODS = [
    "_set_busy", "apply_theme", "_register_drop_tree", "_load_settings",
    "_save_settings", "_build_ui", "_settings_dict",
]


def _make_app():
    """App を生成する。表示不可・依存不足なら skip。
    起動中の小窓（スプラッシュ）は出さない（テストでは見た目だけの機能で、
    macOS の Tk が何十回目かの App でこの窓の update 中に落ちることがあるため）。"""
    os.environ["T2V_NO_SPLASH"] = "1"
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
    assert str(app.screen_btn["state"]) == "disabled"
    app._set_busy(False)
    assert str(app.extract_btn["state"]) == "normal"
    assert str(app.clip_btn["state"]) == "normal"
    assert str(app.screen_btn["state"]) == "normal"


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
                "voice_detail_open", "remove_urls", "opt_open", "replace_open",
                "furigana_only", "engine_autostart", "para_pause",
                "font_auto", "read_fix"):
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
    """大事な操作の3つ（抽出・音声を生成・▶ 再生）はテーマの色で塗る。本文が空の
    あいだは、まだ押しても意味のない音声を生成と ▶ 再生には色を付けない。"""
    from tkinter import ttk
    app.text.delete("1.0", "end")
    app._update_step_highlight()
    assert str(app.extract_btn["style"]) == "Primary.TButton"
    assert str(app.synth_btn["style"]) == "Secondary.TButton"
    assert str(app.playall_btn["style"]) == "Bold.TButton"
    app.text.insert("1.0", "本文あり")
    app._update_step_highlight()
    assert str(app.extract_btn["style"]) == "Primary.TButton"
    assert str(app.synth_btn["style"]) == "Primary.TButton"
    assert str(app.playall_btn["style"]) == "Accent.TButton"
    # 塗る色は、ふつうのボタンの面とはっきり違う（どのテーマでも）
    s = ttk.Style(app)
    for key in ("light", "dark", "hc", "zunda"):
        app.theme_var.set(key)
        app.apply_theme()
        for name in ("Primary.TButton", "Accent.TButton"):
            assert s.lookup(name, "background") != s.lookup("TButton", "background"), key


def test_kb_invoke_ignores_disabled(app):
    """ショートカット経由のボタン起動は無効中は何もしない（"break"だけ返す）。"""
    app.synth_btn.config(state="disabled")
    assert app._kb_invoke(app.synth_btn) == "break"


def test_conn_compact_toggle(app):
    """接続クラスタのコンパクト化往復（成功時に畳み・失敗時に戻す想定の状態遷移）。"""
    app._set_conn_compact(True)
    assert not app._conn_detail.winfo_manager()      # 詳細が畳まれている
    assert app._conn_edit_btn.winfo_manager()
    app._set_conn_compact(False)
    assert app._conn_detail.winfo_manager()
    assert not app._conn_edit_btn.winfo_manager()


def test_shape_report_merge_enables_button(app):
    """整形レポートの蓄積でボタンが有効化され、件数が正しく返る。"""
    assert str(app.report_btn["state"]) == "disabled"
    n_r, n_c = app._merge_report({"removed": ["NEWS"],
                                  "confusables": [("口ボット", "ロボット")]})
    assert (n_r, n_c) == (1, 1)
    assert str(app.report_btn["state"]) == "normal"


def test_synth_button_restore(app):
    """生成キャンセル系のボタン復帰（テキスト・コマンドが元に戻る）。"""
    import threading
    app._synth_cancel = threading.Event()
    app.synth_btn.config(text="⛔ キャンセル", command=app.cancel_synth)
    app._synth_restore_button()
    assert app._synth_cancel is None
    assert str(app.synth_btn["text"]) == "♪ 音声を生成"


def test_portrait_key_normalization():
    """キャラ名/ファイル名の正規化（純ロジック・立ち絵の有無に依存しない）。
    ローマ字別名の吸収と、ファイル名禁止文字・空白の除去。"""
    import main as main_mod
    assert main_mod._portrait_key("zundamon") == "ずんだもん"
    assert main_mod._portrait_key("metan") == "四国めたん"
    assert main_mod._portrait_key("春日部つむぎ") == "春日部つむぎ"
    assert main_mod._portrait_key("小夜/SAYO") == "小夜SAYO"     # 「/」を除く
    assert main_mod._portrait_key("†聖騎士 紅桜†") == "†聖騎士紅桜†"  # 空白を除く
    # macOSのNFD（濁点分解）ファイル名も NFC のキャラ名と一致する（見た目同じ→照合可）
    import unicodedata
    nfd = unicodedata.normalize("NFD", "ずんだもん")
    assert nfd != "ずんだもん"
    assert main_mod._portrait_key(nfd) == main_mod._portrait_key("ずんだもん")


def test_portrait_key_mapping(app):
    """話者ラベル→立ち絵キー: 立ち絵が置かれているキャラだけキーを返す。
    _portraits を差し替えて、有無に依存しない対応を確認する（全43キャラ対応）。"""
    app._portraits = {"ずんだもん": {"base": None}, "春日部つむぎ": {"base": None}}
    assert app._portrait_key_for("ずんだもん（あまあま）") == "ずんだもん"
    assert app._portrait_key_for("春日部つむぎ（ノーマル）") == "春日部つむぎ"
    # 立ち絵を置いていないキャラは None（別キャラを誤表示しない）
    assert app._portrait_key_for("四国めたん（ノーマル）") is None
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


def test_report_window_single_instance(app):
    """整形レポートは多重に開かない（開き直すと最新内容で作り直される）。"""
    app._merge_report({"removed": ["NEWS"]})
    app.show_shape_report()
    app.show_shape_report()
    assert app._report_win.title() == "直した所"
    tops = [w for w in app.winfo_children()
            if isinstance(w, __import__("tkinter").Toplevel)]
    assert len(tops) == 1
    app._report_win.destroy()


def test_check_engine_guarded_while_previewing(app):
    """連続再生（_previewing）中は接続確認を実行しない（⏸ボタン無効化の取り残し防止）。"""
    app._previewing = True
    before = app.engine_var.get()
    app.check_engine()
    assert app.engine_var.get() == before      # 「接続確認中...」に変わらない
    assert app.busy is False
    app._previewing = False


def test_move_selected_reorders_files(app):
    """ファイル並べ替え: 選択行が上下に動き、listboxとfilesの順序が同期する。"""
    app.files = ["/tmp/a.txt", "/tmp/b.txt", "/tmp/c.txt"]
    app.listbox.delete(0, "end")
    for p in app.files:
        app.listbox.insert("end", os.path.basename(p))
    app.listbox.selection_set(1)
    app._move_selected(-1)
    assert app.files == ["/tmp/b.txt", "/tmp/a.txt", "/tmp/c.txt"]
    assert app.listbox.get(0) == "b.txt"
    assert app.listbox.curselection() == (0,)
    app._move_selected(-1)   # 先頭ではそれ以上動かない
    assert app.files == ["/tmp/b.txt", "/tmp/a.txt", "/tmp/c.txt"]
    app.listbox.selection_clear(0, "end")
    app.listbox.selection_set(2)
    app._move_selected(+1)   # 末尾でも動かない
    assert app.files == ["/tmp/b.txt", "/tmp/a.txt", "/tmp/c.txt"]


def test_toggle_memo_lines(app):
    """＃メモ行の切替: 付ける→外すの往復で本文が元に戻る。"""
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "本文の行")
    app.text.mark_set("insert", "1.0")
    app._toggle_memo_lines()
    assert app.text.get("1.0", "1.end") == "# 本文の行"
    app._toggle_memo_lines()
    assert app.text.get("1.0", "1.end") == "本文の行"


def test_insert_and_remove_speaker_tag(app):
    """@話者タグ: 挿入→別話者で置き換え→解除で本文が元に戻る。"""
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "こんにちは")
    app.text.mark_set("insert", "1.0")
    app._insert_speaker_tag("ずんだもん")
    assert app.text.get("1.0", "1.end") == "@ずんだもん: こんにちは"
    app._insert_speaker_tag("四国めたん")
    assert app.text.get("1.0", "1.end") == "@四国めたん: こんにちは"
    app._remove_speaker_tag()
    assert app.text.get("1.0", "1.end") == "こんにちは"


def test_text_menu_exists(app):
    """右クリックメニューが構築されている（実ポップアップはヘッドレスで不可）。"""
    assert isinstance(app._text_menu, tk.Menu)


def test_done_dialog_smoke(app, tmp_path):
    """完了ダイアログ: 生成・破棄がクラッシュしない（ボタンは押さない）。"""
    app._show_done_dialog("保存しました:\n/tmp/x.wav", str(tmp_path),
                          "VOICEVOX:ずんだもん")
    tops = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
    assert tops
    for t in tops:
        t.destroy()


def test_save_text_cache_skips_unchanged(app, tmp_path, monkeypatch):
    """自動保存: 同じ内容なら再書き込みしない（_cache_saved による抑止）。"""
    import main as main_mod
    p = tmp_path / "last_text.txt"
    monkeypatch.setattr(main_mod, "TEXT_CACHE_PATH", str(p))
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "自動保存テスト")
    app._cache_saved = None
    app._save_text_cache()
    assert p.read_text(encoding="utf-8") == "自動保存テスト"
    mtime = p.stat().st_mtime_ns
    app._save_text_cache()   # 無変化 → 書き込まない
    assert p.stat().st_mtime_ns == mtime


def _fake_speakers():
    return [("ずんだもん（ノーマル）", 3, "uz"),
            ("ずんだもん（あまあま）", 1, "uz"),
            ("四国めたん（ノーマル）", 2, "um")]


def test_two_stage_speaker_selection(app):
    """話者2段選択: キャラ→スタイルの対応・フルラベル復元・現在話者の取得。"""
    app.speakers = _fake_speakers()
    app._build_char_map()
    assert list(app._char_map) == ["ずんだもん", "四国めたん"]
    assert app._select_speaker_label("ずんだもん（あまあま）") is True
    assert app.char_cb.get() == "ずんだもん"
    assert app.speaker_cb.get() == "あまあま"
    sp = app._current_speaker()
    assert sp == ("ずんだもん（あまあま）", 1, "uz")
    assert app._current_speaker_label() == "ずんだもん（あまあま）"
    assert app._select_speaker_label("存在しない（ラベル）") is False


def test_char_selected_defaults_first_style(app):
    app.speakers = _fake_speakers()
    app._build_char_map()
    app.char_cb.set("四国めたん")
    app._char_selected()
    assert app._current_speaker() == ("四国めたん（ノーマル）", 2, "um")


def test_settings_dict_survives_empty_numeric_fields(app):
    """数値欄が空でも設定辞書は既定値へフォールバックして完成する
    （従来は TclError で保存全体が失敗し、その回の設定変更が全て消えた）。"""
    # Spinboxを空にした状態＝Tcl変数が空文字 → .get() が TclError を投げる
    app.tk.globalsetvar(str(app.dpi_var), "")
    app.tk.globalsetvar(str(app.speed_var), "")
    d = app._settings_dict()
    assert d["dpi"] == 300 and d["speed"] == 1.0
    assert "replace_rules" in d           # 他のキーは普通に保存される


def test_settings_dict_preserves_saved_speaker_when_disconnected(app):
    """エンジン未接続（コンボ空）でも保存済みの話者ラベルを空文字で潰さない。"""
    app._saved_speaker = "四国めたん（ノーマル）"
    d = app._settings_dict()
    assert d["speaker"] == "四国めたん（ノーマル）"


def test_save_settings_atomic_and_robust(app, tmp_path, monkeypatch):
    """設定保存: 数値欄が空でも既存ファイルが0バイトに壊れない。"""
    import json
    import main as main_mod
    p = tmp_path / "settings.json"
    p.write_text('{"theme": "dark"}', encoding="utf-8")
    monkeypatch.setattr(main_mod, "SETTINGS_PATH", str(p))
    app.tk.globalsetvar(str(app.dpi_var), "")   # 空欄相当
    app._save_settings()
    data = json.loads(p.read_text(encoding="utf-8"))   # 壊れていない
    assert data["dpi"] == 300


def test_corrupt_settings_backed_up(app, tmp_path, monkeypatch):
    """壊れた settings.json は .bak に退避される（無言で捨てない）。"""
    import main as main_mod
    p = tmp_path / "settings.json"
    p.write_text("{broken json", encoding="utf-8")
    monkeypatch.setattr(main_mod, "SETTINGS_PATH", str(p))
    app._load_settings()
    assert not p.exists()
    assert (tmp_path / "settings.json.bak").exists()


def test_toggle_memo_excludes_col0_boundary(app):
    """選択終端が行頭（列0）の行は1文字も選択されていない＝メモ化しない。"""
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "一行目\n二行目\n三行目")
    app.text.tag_add("sel", "1.0", "3.0")   # 3行目は0文字選択
    app._toggle_memo_lines()
    assert app.text.get("1.0", "1.end") == "# 一行目"
    assert app.text.get("2.0", "2.end") == "# 二行目"
    assert app.text.get("3.0", "3.end") == "三行目"   # 巻き込まれない


def test_search_prev_from_start_goes_last(app):
    """検索で最初に「↑前」を押すと末尾ヒットへ（従来は末尾から2番目に飛んだ）。"""
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "犬と犬と犬")
    app.open_search()
    app._search_var.set("犬")
    app._search_refresh()
    assert len(app._search_hits) == 3
    app._search_jump(-1)
    assert app._search_idx == 2           # 3/3件（末尾）
    app._close_search()


def test_unit_dependent_spinbox_state(app):
    """まとめ方に応じてN行・無音スピンボックスが有効/無効になる。"""
    keys = list(app._UNITS.keys())
    app.unit_cb.current(keys.index("each"))
    app._on_unit_selected()
    assert str(app.nlines_sb["state"]) == "disabled"
    assert str(app.gap_sb["state"]) == "disabled"
    app.unit_cb.current(keys.index("nlines"))
    app._on_unit_selected()
    assert str(app.nlines_sb["state"]) == "normal"
    assert str(app.gap_sb["state"]) == "normal"
    app.unit_cb.current(keys.index("combine"))
    app._on_unit_selected()
    assert str(app.nlines_sb["state"]) == "disabled"
    assert str(app.gap_sb["state"]) == "normal"


def test_player_gap_spinbox_syncs_and_waits(app):
    """再生バーの「間」スピンボックスが§4の文間の無音(gap_var)と連動し、
    「1行=1ファイル」選択中も再生バー側は常時使え、行間ウェイトに反映される。"""
    import io
    import struct
    import threading
    import wave
    import core
    assert hasattr(app, "player_gap_sb")
    assert str(app.player_gap_sb.cget("textvariable")) == str(app.gap_var)
    keys = list(app._UNITS.keys())
    app.unit_cb.current(keys.index("each"))
    app._on_unit_selected()
    assert str(app.gap_sb["state"]) == "disabled"
    assert str(app.player_gap_sb["state"]) != "disabled"
    app.gap_var.set(0.7)
    assert abs(app._play_gap - 0.7) < 1e-6
    # 停止イベントが立っていれば即座に抜ける
    stop = threading.Event()
    pause = threading.Event()
    stop.set()
    app._wait_playall_gap(stop, pause)
    app.gap_var.set(0.4)
    # 再生用WAVパディング（末尾のDCオフセットをフェードアウトして無音を付加）
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(struct.pack("<4h", -120, -120, -120, -120))
    raw_wav = buf.getvalue()
    padded = core._pad_wav_for_play(raw_wav, tail_sec=0.1)
    assert abs(core.wav_duration(padded) - (4 / 24000.0 + 0.1)) < 1e-4
    with wave.open(io.BytesIO(padded), "rb") as w:
        samples = struct.unpack(f"<{w.getnframes()}h", w.readframes(w.getnframes()))
    assert samples[:4] == (-120, -120, -120, -120)
    assert -120 < samples[120] < 0  # フェードアウト中盤
    assert samples[-1] == 0         # 末尾は完全な無音
    assert core._pad_wav_for_play(b"not-a-wav") == b"not-a-wav"


def test_playall_gap_matches_saved_file_gap(app):
    """連続再生の行間ウェイトは、再生側で足した語尾の無音を差し引いた長さになる
    （「間」の数字と実際に聞こえる無音、保存ファイルの文間の無音がそろう）。"""
    import threading
    import time
    import core
    stop = threading.Event()
    pause = threading.Event()
    app._play_gap = core.PLAY_TAIL_SEC + 0.2
    t0 = time.monotonic()
    app._wait_playall_gap(stop, pause)
    assert 0.15 <= time.monotonic() - t0 < 0.5
    # 足した無音より短い指定なら、追加では待たない
    app._play_gap = core.PLAY_TAIL_SEC / 2
    t0 = time.monotonic()
    app._wait_playall_gap(stop, pause)
    assert time.monotonic() - t0 < 0.05
    # 一時停止で行間待ちに入っていたら、解除後すぐ抜ける
    app._play_gap = core.PLAY_TAIL_SEC + 5.0
    pause.set()
    threading.Timer(0.2, pause.clear).start()
    t0 = time.monotonic()
    app._wait_playall_gap(stop, pause)
    assert time.monotonic() - t0 < 1.0
    app.gap_var.set(0.4)

def test_playall_longer_pause_at_paragraph_and_chapter(app, monkeypatch):
    """連続再生でも、段落の切れ目（空行のあと）と章の見出しの前は長めに空ける
    （保存する音声と同じ）。再生中に切り替えても、「間」を変えても、すぐ効く。"""
    spawned = []
    monkeypatch.setattr(app, "_current_speaker", lambda: ("ずんだもん", 3, "u"))
    monkeypatch.setattr(main_mod().core, "can_play", lambda: True)
    monkeypatch.setattr(app, "_safe_voice_params", lambda: {})
    monkeypatch.setattr(app, "_spawn", lambda target, args=(): spawned.append(args))
    app.gap_var.set(0.4)
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "一文目。\n二文目。\n\n次の段落。\n# メモ\n第一章 出発\n本文。")
    app.text.mark_set("insert", "2.0")
    app.play_from_cursor()
    targets = spawned[0][0]
    assert [t[0] for t in targets] == [2, 4, 6, 7]       # メモ行は読まない
    assert app._play_breaks == ["", "para", "chapter", ""]
    assert [app._gap_before(i) for i in (1, 2, 3)] == pytest.approx([1.0, 2.0, 0.4])
    app._play_gap = 0.2                                   # 再生中に「間」を変えた
    assert app._gap_before(1) == pytest.approx(0.5)
    cb = next(w for w in _descendants(app) if w.winfo_class() == "TCheckbutton"
              and str(w.cget("text")) == "段落・章は長め")
    cb.invoke()                                           # 外すと、どこも「間」のまま
    assert app.para_pause_var.get() is False
    assert [app._gap_before(i) for i in (1, 2, 3)] == pytest.approx([0.2, 0.2, 0.2])
    cb.invoke()
    assert app._gap_before(2) == pytest.approx(1.0)
    app._previewing = False


def test_kb_synth_guard_during_generation(app):
    """生成中の Ctrl/Cmd+G は誤キャンセルにならない（何もしない）。"""
    import threading
    app._synth_cancel = threading.Event()
    assert app._kb_synth() == "break"
    assert not app._synth_cancel.is_set()
    app._synth_cancel = None


def test_extract_restore_button(app):
    """抽出キャンセル系のボタン復帰（テキスト・コマンドが元に戻る）。"""
    import threading
    app._extract_cancel = threading.Event()
    app.extract_btn.config(text="⛔ キャンセル", command=app.cancel_extract)
    app._extract_restore_button()
    assert app._extract_cancel is None
    assert str(app.extract_btn["text"]) == "▶ テキスト抽出 実行"


def test_dispatch_msg_error_does_not_kill_pump(app):
    """不正メッセージが1つ来てもポンプは死なず、後続メッセージが処理される。"""
    app.q.put(("progress",))              # 要素不足 → _dispatch_msg内で例外
    app.q.put(("progress", 1, 2, "続行できてるよ"))
    app._poll_queue()
    assert app.status_var.get() == "続行できてるよ"


def test_undo_redo_helpers_safe_when_empty(app):
    """履歴が無い状態のUndo/Redoは落ちずに状態欄で知らせる。"""
    app.text.delete("1.0", "end")
    app.text.edit_reset()
    app._edit_undo()
    assert "戻せる操作" in app.status_var.get()
    app._edit_redo()
    assert "やり直せる操作" in app.status_var.get()


def test_mark_bookmark_tags_line(app):
    """しおりマーカー: 該当行にbookmarkタグが付き、None時は消える。"""
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "一行目\n二行目\n三行目")
    app._bookmark = 2
    app._mark_bookmark()
    assert app.text.tag_ranges("bookmark")
    assert str(app.text.tag_ranges("bookmark")[0]).startswith("2.")
    app._bookmark = None
    app._mark_bookmark()
    assert not app.text.tag_ranges("bookmark")


def test_restore_view_clamps(app):
    """置換後のカーソル復元: 行が消えて無効になった位置でも例外を出さない。"""
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "短い")
    app._restore_view("99.0", 0.5)   # 存在しない行 → クランプされ例外なし


def test_m4b_disables_nlines_spinbox(app):
    """B8修正: M4B選択時は「N行」も灰色（値を変えても無視されるため）。"""
    keys = list(app._UNITS.keys())
    app.unit_cb.current(keys.index("nlines"))
    app._on_unit_selected()
    assert str(app.nlines_sb["state"]) == "normal"
    if app._FMT_LABELS["m4b"] in app._format_choices():
        app.fmt_cb.set(app._FMT_LABELS["m4b"])
        assert app._out_format() == "m4b"
        app._on_format_selected()
        assert str(app.nlines_sb["state"]) == "disabled"
        app.fmt_cb.set("WAV")
        app._on_format_selected()


def test_save_preset_with_empty_numeric_field(app, monkeypatch):
    """B7修正: 話速欄が空でもプリセット保存が無反応にならず既定値で保存される。"""
    from tkinter import simpledialog
    monkeypatch.setattr(simpledialog, "askstring",
                        lambda *a, **k: "空欄テスト")
    app.tk.globalsetvar(str(app.speed_var), "")
    app.save_preset()
    assert any(p["name"] == "空欄テスト" and p["speed"] == 1.0
               for p in app.presets)
    app.presets = [p for p in app.presets if p["name"] != "空欄テスト"]


def test_text_stats_display(app):
    """行数・文字数・めやすの常時表示: 本文ありで表示され、空で消える。"""
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "# メモ行\n本文の一行目です。\n二行目。")
    app._update_text_stats()
    s = app.stats_var.get()
    assert "3行" in s and "字" in s and "めやす" in s
    app.text.delete("1.0", "end")
    app._update_text_stats()
    assert app.stats_var.get() == ""


def test_cache_dialog_smoke(app):
    """「声の保管」（合成キャッシュの管理）ダイアログ: 開閉と多重表示防止。
    ボタンと題名には「キャッシュ」と書かない。"""
    app.open_cache_dialog()
    assert app._cache_win.winfo_exists()
    assert app._cache_win.title() == "声の保管"
    labels = [str(w.cget("text")) for w in _descendants(app._cache_win)
              if w.winfo_class() == "TButton"]
    assert "しまった声を消す" in labels
    assert not any("キャッシュ" in t for t in labels), labels
    first = app._cache_win
    app.open_cache_dialog()   # 2回目は既存を前面に出すだけ
    assert app._cache_win is first
    app._cache_win.destroy()

def _help_texts(app):
    return ([str(w.cget("text")) for w in _descendants(app._ocr_help_win) if w.winfo_class() == "TButton"],
            " ".join(str(w.cget("text")) for w in _descendants(app._ocr_help_win)
                     if w.winfo_class() == "TLabel"))


def test_strong_ocr_help_uses_plain_words(app, monkeypatch):
    """読み取りを強くする案内: 重さと行き先を先に言い、OCR と言わない。押すまで何も入らない。"""
    import main as main_mod
    monkeypatch.setattr(main_mod.core, "IS_WIN", True)
    monkeypatch.setattr(main_mod.core, "IS_MAC", False)
    monkeypatch.setattr(main_mod.core, "strong_ocr_available", lambda: False)
    app.open_strong_ocr_help()   # winsetup.py はアプリのフォルダにある
    buttons, text = _help_texts(app)
    assert str(app._ocr_help_buttons["install"].cget("text")) == "入れる"
    assert not any("OCR" in t for t in buttons), buttons
    assert "OCR" not in text, text
    assert "入っていません" in text
    assert "約122MB" in text and "このPCの中だけ" in text
    first = app._ocr_help_win
    app.open_strong_ocr_help()   # 2回目は既存を前面に出すだけ
    assert app._ocr_help_win is first
    app._ocr_help_win.destroy()


def test_strong_ocr_help_when_installed_offers_nothing_to_install(app, monkeypatch):
    import main as main_mod
    monkeypatch.setattr(main_mod.core, "IS_WIN", True)
    monkeypatch.setattr(main_mod.core, "IS_MAC", False)
    monkeypatch.setattr(main_mod.core, "strong_ocr_available", lambda: True)
    app.open_strong_ocr_help()
    buttons, text = _help_texts(app)
    assert buttons == ["閉じる"]
    assert "入っています" in text and "ocr_engine" in text
    app._ocr_help_win.destroy()


def test_install_button_opens_the_setup_once(app, monkeypatch):
    """入れるボタンは黒い窓で winsetup.py ocr を動かす。2つ同時に動かないように、押せなくする。"""
    import subprocess
    import main as main_mod
    monkeypatch.setattr(main_mod.core, "IS_WIN", True)
    monkeypatch.setattr(main_mod.core, "IS_MAC", False)
    monkeypatch.setattr(main_mod.core, "strong_ocr_available", lambda: False)
    ran = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: ran.append(args))
    app.open_strong_ocr_help()
    app._ocr_help_buttons["install"].invoke()
    assert ran[0][1].endswith("winsetup.py") and ran[0][2] == "ocr"
    assert app._ocr_help_buttons["install"].instate(["disabled"])
    assert "黒い画面" in app.status_var.get()
    app._ocr_help_win.destroy()


def test_english_warning_opens_the_help_with_a_reason(app, monkeypatch):
    import main as main_mod
    monkeypatch.setattr(main_mod.core, "IS_WIN", True)
    monkeypatch.setattr(main_mod.core, "IS_MAC", False)
    monkeypatch.setattr(main_mod.core, "strong_ocr_available", lambda: False)
    app._warn_with_english([main_mod.core.OCR_ENGLISH_MISSING_MSG])
    _buttons, text = _help_texts(app)
    assert "英語の文が多く" in text
    app._ocr_help_win.destroy()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows だけのボタン")
def test_settings_has_the_strong_ocr_button_without_jargon(app):
    """部品が入らなかったときだけ出る、入れ直す入口。"""
    app._set_opt_rows(True)   # ① の「設定 ▷」は、初めて開いたときに作る
    assert str(app._ocr_btn.cget("text")) == "読み取りを強くする"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows だけのボタン")
def test_strong_ocr_button_is_gone_when_the_engine_is_there(app, monkeypatch):
    """最初から入っているなら、押すものは要らない。"""
    import main as main_mod
    monkeypatch.setattr(main_mod.core, "strong_ocr_available", lambda: True)
    app._set_opt_rows(True)
    assert app._ocr_btn is None


def test_playall_done_dispatch(app):
    """完走メッセージ（skipped込み）が出る。世代番号は末尾に付く。"""
    app._previewing = True
    app.q.put(("playall_done", True, False, 12, 2, app._play_gen))
    app._poll_queue()
    assert "12行・2行スキップ" in app.status_var.get()
    assert app._previewing is False


def test_playall_done_of_old_generation_is_ignored(app):
    """ひとつ前の再生から遅れて届いた完了通知で、いまの再生を止めない。
    止めた直後に次を始めたとき、古い通知に巻き添えで待機表示へ戻されていた。"""
    app._play_gen += 1              # 新しい再生が始まった
    app._previewing = True
    app.q.put(("playall_done", True, False, 3, 0, app._play_gen - 1))
    app._poll_queue()
    assert app._previewing is True


def test_playall_skip_dispatch(app):
    app.q.put(("playall_skip", 7, app._play_gen))
    app._poll_queue()
    assert "7行目" in app.status_var.get()


def test_synth_partial_decline_cleans_up(app, tmp_path, monkeypatch):
    """部分保存の辞退: partファイルが消え、busyが解除される。"""
    part = tmp_path / "out.wav.part.wav"
    part.write_bytes(b"RIFF0000")
    monkeypatch.setattr(messagebox := __import__("tkinter.messagebox",
                                                 fromlist=["askyesno"]),
                        "askyesno", lambda *a, **k: False)
    app._set_busy(True)
    app.q.put(("synth_partial", {
        "part": str(part), "done": 3, "total": 10, "durs": [0.1] * 3,
        "lines": ["a", "b", "c"], "sids": [1, 1, 1],
        "target": str(tmp_path / "out.wav"), "fmt": "wav", "srt": False,
        "gap": 0.4}))
    app._poll_queue()
    assert not part.exists()
    assert app.busy is False
    assert "キャンセル" in app.status_var.get()


def test_synth_worker_mkdtemp_failure_unlocks_ui(app, monkeypatch):
    """spool作成失敗（ディスクフル等）でもbusyが解除される（永久ロック防止）。"""
    import main as main_mod
    monkeypatch.setattr(main_mod.tempfile, "mkdtemp",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no space")))
    app._set_busy(True)
    app._synth_cancel = None
    # ワーカーを直接呼ぶ（スレッドなし）: 例外が ("error",…) として積まれる
    app._synth_worker([("あ", 3, 0)], [[0]], {"speed": 1.0, "pitch": 0.0,
                      "intonation": 1.0, "volume": 1.0}, str(app), "combine",
                      [0.0], "wav", False)
    kinds = []
    try:
        while True:
            kinds.append(app.q.get_nowait()[0])
    except Exception:
        pass
    assert "error" in kinds

def test_start_synth_passes_longer_pauses_from_checkbox(app, monkeypatch, tmp_path):
    """「音声を生成」も、空行と章の見出しから行ごとの間を決めてワーカーに渡す。
    「段落・章は長め」を外すと、どこも「文の間」のまま。"""
    from tkinter import filedialog
    spawned = []
    monkeypatch.setattr(app, "_current_speaker", lambda: ("ずんだもん", 3, "u"))
    monkeypatch.setattr(app, "_confirm_speaker_tags", lambda: True)
    monkeypatch.setattr(app, "_spawn", lambda target, args=(): spawned.append(args))
    monkeypatch.setattr(filedialog, "asksaveasfilename",
                        lambda **kw: str(tmp_path / "o.wav"))
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "一文目。\n二文目。\n\n次の段落。\n第一章 出発\n本文。")
    app.unit_cb.current(list(app._UNITS).index("combine"))
    app._on_unit_selected()
    app.gap_var.set(0.4)
    app.start_synth()
    assert spawned[-1][5] == pytest.approx([0.0, 0.4, 1.0, 2.0, 0.4])
    app._set_busy(False)
    app._synth_restore_button()
    app.para_pause_var.set(False)
    app.start_synth()
    assert spawned[-1][5] == pytest.approx([0.0] + [0.4] * 4)
    app._set_busy(False)
    app._synth_restore_button()


def test_synth_worker_saves_longer_pause_between_paragraphs(app, monkeypatch, tmp_path):
    """まとめて保存する音声と字幕も、段落の切れ目の前は長めの無音（連続再生と同じ）。"""
    import main as main_mod
    core = main_mod.core
    wav = _pcm_wav(0.05, 1000)
    monkeypatch.setattr(core, "vv_synthesize_cached",
                        lambda base, text, sid, **kw: wav)
    monkeypatch.setattr(core, "synth_cache_protect", lambda since: None)
    app._engine_ver = "x"
    app._dict_hash_tracker = lambda: (lambda: "h")   # エンジンに問い合わせない
    app._set_busy(True)
    app._synth_cancel = None
    jobs = [("一文目。", 3, 0), ("次の段落。", 3, 1), ("同じ段落。", 3, 1)]
    gaps = core.line_gaps([j[0] for j in jobs], [j[2] for j in jobs], 0.1)
    target = tmp_path / "out.wav"
    app._synth_worker(jobs, [[0, 1, 2]], {"speed": 1.0, "pitch": 0.0,
                      "intonation": 1.0, "volume": 1.0}, str(target), "combine",
                      gaps, "wav", True)
    one = _pcm(wav)
    assert _pcm(target.read_bytes()) == (one + b"\x00" * 6000 * 2 + one
                                         + b"\x00" * 2400 * 2 + one)
    srt = (tmp_path / "out.srt").read_text(encoding="utf-8")
    assert "00:00:00,300 --> 00:00:00,350" in srt     # 0.05 + 0.25
    assert "00:00:00,450 --> 00:00:00,500" in srt     # 同じ段落の中は 0.1


def test_confirm_speaker_tags_no_tags_passes(app):
    """@タグが無ければ確認をスキップして True。"""
    app.speakers = [("ずんだもん（ノーマル）", 3, "u")]
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "普通の本文です。")
    assert app._confirm_speaker_tags() is True


def test_confirm_speaker_tags_jump_uses_widget_line(app, monkeypatch):
    """未解決タグで「いいえ」→ウィジェットの絶対行にジャンプ（先頭空行分ずれない）。"""
    from tkinter import messagebox
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)
    app.speakers = [("ずんだもん（ノーマル）", 3, "u")]
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "\n\n@すんだもん: タイプミス\n地の文")
    assert app._confirm_speaker_tags() is False
    # カーソルが3行目（タグ行）に移動している
    assert app.text.index("insert").startswith("3.")


def test_recover_if_stuck_resets_preview(app):
    """停止後も再生状態が残ったら強制復帰する（試聴が無反応になるのを防ぐ安全網）。"""
    app.speakers = [("ずんだもん（ノーマル）", 3, "u")]
    app._build_char_map()
    app.char_cb.current(0); app._char_selected()
    # ボタンを戻すかどうかは接続状態で決まる（つながっていないのに押せると、
    # 押した先で失敗する）。ここでは「つながっている」側を見たいので整えておく
    app._set_conn_state("ok", "0.0.0-test")
    app._previewing = True          # ワーカーが応答せず残った状態を模擬
    app.preview_btn.config(state="disabled")
    app._recover_if_stuck(app._play_gen)
    assert app._previewing is False
    assert str(app.preview_btn["state"]) == "normal"


def test_recover_if_stuck_keeps_buttons_off_when_disconnected(app):
    """つながっていないときは、待機に戻しても音声のボタンは戻さない。"""
    app.conn_state = "lost"
    app._previewing = True
    app.preview_btn.config(state="disabled")
    app._recover_if_stuck(app._play_gen)
    assert app._previewing is False
    assert str(app.preview_btn["state"]) == "disabled"


def test_recover_if_stuck_leaves_a_newer_playback_alone(app):
    """安全網は「止めた再生の世代」のときだけ働く。すぐ次を再生し始めた場合に
    1.5秒後のこれが効くと、鳴っている最中なのに待機表示へ戻ってしまう。"""
    app._previewing = True
    old_gen = app._play_gen
    app._play_gen += 1              # 次の再生が始まった
    app._recover_if_stuck(old_gen)
    assert app._previewing is True


def test_preview_blocked_gives_feedback(app):
    """再生中に試聴を押すと無音returnせず理由を状態欄に出す。"""
    app._previewing = True
    app.status_var.set("")
    app.preview_selected()
    assert app.status_var.get() != ""   # フィードバックが出る
    app._previewing = False


def test_preview_no_engine_shows_dialog(app, monkeypatch):
    """未接続で試聴すると案内ダイアログ（無音で終わらない）。"""
    from tkinter import messagebox
    shown = []
    monkeypatch.setattr(messagebox, "showinfo",
                        lambda *a, **k: shown.append(a))
    app.speakers = []
    app._char_map = {}
    app._previewing = False
    app.busy = False
    app.preview_selected()
    assert shown   # ダイアログが出た


# ---------------- 📷 画面から読む ----------------
class _Ev:
    """範囲選択のドラッグを再現する最小のイベント。"""
    def __init__(self, x, y):
        self.x = self.x_root = x
        self.y = self.y_root = y


def _grab_now(app):
    """撮影の待ち時間（予約）を取り消して、すぐ撮る。"""
    h = app._ticks.pop("screen", None)
    if h is not None:
        app.after_cancel(h)
    app._screen_grab()


def _screen_canvas(app):
    win = app._screen_win
    assert isinstance(win, tk.Toplevel), "範囲選択の窓が開いていません"
    return win.winfo_children()[0]


@pytest.fixture
def fake_screen(app, monkeypatch):
    """実画面の代わりに 400x300 の画像を“撮れた”ことにする。"""
    from PIL import Image, ImageGrab
    shot = Image.new("RGB", (400, 300), "white")
    monkeypatch.setattr(ImageGrab, "grab", lambda *a, **k: shot)
    monkeypatch.setattr(app, "_screen_region",
                        lambda: ((0, 0, 400, 300), False))
    spawned = []
    monkeypatch.setattr(app, "_spawn",
                        lambda target, args=(): spawned.append((target, args)))
    return shot, spawned


def test_screen_read_selects_region_and_starts_ocr(app, fake_screen):
    """囲んだ所だけを切り抜いてOCRへ回し、隠した窓は元に戻る。"""
    shot, spawned = fake_screen
    app.screen_read()
    assert app.state() == "withdrawn"      # 撮り込まないよう自分は隠れる
    _grab_now(app)                          # 待ち時間を飛ばして撮る
    cv = _screen_canvas(app)
    app.update()
    ox, oy = cv.winfo_rootx(), cv.winfo_rooty()
    # 画面座標 (50,40)-(250,140) を囲む（Canvas内座標は窓の位置を引いたもの）
    cv.event_generate("<ButtonPress-1>", x=50 - ox, y=40 - oy,
                      rootx=50, rooty=40)
    cv.event_generate("<B1-Motion>", x=250 - ox, y=140 - oy,
                      rootx=250, rooty=140)
    cv.event_generate("<ButtonRelease-1>", x=250 - ox, y=140 - oy,
                      rootx=250, rooty=140)
    app.update()
    assert app._screen_win is None
    assert app.state() != "withdrawn"
    assert len(spawned) == 1
    target, args = spawned[0]
    assert target == app._clipboard_worker
    assert args[0].size == (200, 100)      # 囲んだ大きさで切り抜かれている
    assert args[-1] == "screen"
    assert args[4] is False                # 囲んだ範囲の行はノイズ扱いで捨てない
    assert app.busy is True
    app._set_busy(False)


def test_screen_read_escape_cancels(app, fake_screen):
    """Esc でやめると、何も読まずに窓だけ戻る。"""
    _, spawned = fake_screen
    app.screen_read()
    _grab_now(app)
    cv = _screen_canvas(app)
    if main_mod().core.IS_MAC:
        # macOS では枠なし窓に入力を強制で向けない（Tk が落ちることがあるため）。
        # キーは届かないことがあるので、Esc の結線があることだけ確かめ、
        # 実際にやめる動きは右クリックのテストで確かめる
        assert cv.bind("<Escape>")
        app._screen_selected(None)
    else:
        cv.focus_force()
        app.update()
        cv.event_generate("<Escape>")
    app.update()
    assert app._screen_win is None
    assert app.state() != "withdrawn"
    assert not spawned
    assert "やめました" in app.status_var.get()


def test_screen_read_right_click_cancels(app, fake_screen):
    """右クリックでもやめられる（Macの枠なし窓はEscが届かないことがあるため）。"""
    _, spawned = fake_screen
    app.screen_read()
    _grab_now(app)
    cv = _screen_canvas(app)
    app.update()
    cv.event_generate("<Button-3>", x=20, y=20)
    app.update()
    assert app._screen_win is None
    assert app.state() != "withdrawn"
    assert not spawned


def test_screen_picker_closed_by_window_manager_restores_app(app, fake_screen):
    """Alt+F4 などで範囲選択の窓が閉じられても、隠した本体の窓は戻る。"""
    _, spawned = fake_screen
    app.screen_read()
    _grab_now(app)
    win = app._screen_win
    assert isinstance(win, tk.Toplevel)
    win.destroy()                      # ウィンドウマネージャに壊された想定
    app.update()
    assert app._screen_win is None
    assert app.state() != "withdrawn"
    assert not spawned
    app.screen_read()                  # 次もちゃんと開ける（固まった状態が残らない）
    assert app._screen_win is True
    _grab_now(app)
    app._screen_selected(None)


def test_screen_read_click_only_keeps_picking(app, fake_screen):
    """クリックだけ（囲めていない）ではやめずに、選び直せるまま待つ。"""
    _, spawned = fake_screen
    app.screen_read()
    _grab_now(app)
    cv = _screen_canvas(app)
    app.update()
    cv.event_generate("<ButtonPress-1>", x=10, y=10, rootx=10, rooty=10)
    cv.event_generate("<ButtonRelease-1>", x=11, y=11, rootx=11, rooty=11)
    app.update()
    assert isinstance(app._screen_win, tk.Toplevel)
    assert not spawned
    app._screen_selected(None)


def test_screen_grab_twice_opens_one_picker(app, fake_screen):
    """撮影の予約と連打が重なっても、選択の窓は1枚だけ。"""
    app.screen_read()
    _grab_now(app)
    first = app._screen_win
    app._screen_grab()
    assert app._screen_win is first
    assert sum(isinstance(w, tk.Toplevel) and w.winfo_exists()
               and w.overrideredirect() for w in app.winfo_children()) == 1
    app._screen_selected(None)


def test_screen_read_blocked_while_busy(app, fake_screen):
    """処理中は窓を隠さず、理由を状態欄に出す。"""
    app.busy = True
    app.status_var.set("")
    app.screen_read()
    assert app._screen_win is None
    assert app.state() != "withdrawn"
    assert app.status_var.get() != ""
    app.busy = False


def test_screen_grab_failure_restores_window(app, monkeypatch):
    """画面を撮れなかったら、隠した窓を必ず戻してから知らせる。"""
    from PIL import ImageGrab
    from tkinter import messagebox
    shown = []

    def boom(*a, **k):
        raise OSError("no permission")
    monkeypatch.setattr(ImageGrab, "grab", boom)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: shown.append(a))
    app.screen_read()
    _grab_now(app)
    assert app._screen_win is None
    assert app.state() != "withdrawn"
    assert shown


def test_screen_done_appends_and_starts_from_new_text(app, monkeypatch):
    """画面から読んだ文章は本文の最後に足し、足した1行目から読み上げる。"""
    played = []
    monkeypatch.setattr(app, "_engine_ready", lambda: True)
    monkeypatch.setattr(app, "_current_speaker", lambda: ("ずんだもん", 3, "u"))
    monkeypatch.setattr(main_mod().core, "can_play", lambda: True)
    monkeypatch.setattr(app, "play_from_cursor",
                        lambda: played.append(app.text.index("insert")))
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "前からある一行目\n前からある二行目")
    app.q.put(("clip_done", "画面の文章です。\n二行目です。", {}, [], "screen"))
    app._poll_queue()
    assert app.text.get("3.0", "3.end") == "画面の文章です。"
    assert played == ["3.0"]                # 足した所から読む（前の本文は読まない）


def test_screen_done_without_engine_just_inserts(app, monkeypatch):
    """VOICEVOX未接続なら、ダイアログで止めずに本文へ入れて案内だけ出す。"""
    from tkinter import messagebox
    shown = []
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(app, "_engine_ready", lambda: False)
    app.text.delete("1.0", "end")
    app.q.put(("clip_done", "画面の文章です。", {}, [], "screen"))
    app._poll_queue()
    assert app.text.get("1.0", "1.end") == "画面の文章です。"
    assert app.text.index("insert") == "1.0"
    assert not shown
    assert "VOICEVOX" in app.status_var.get()


def test_clipboard_done_still_goes_to_top(app):
    """「コピーした画像を読む」の結果の受け取り方は従来どおり（カーソルは先頭へ）。
    お知らせには「クリップボード」「OCR」を使わない（ふだんの言葉で伝える）。"""
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "既存")
    app.q.put(("clip_done", "追記分", {}, [], "clip"))
    app._poll_queue()
    assert app.text.get("2.0", "2.end") == "追記分"
    assert app.text.index("insert") == "1.0"
    assert "コピーした画像" in app.status_var.get()
    assert "クリップボード" not in app.status_var.get() and "OCR" not in app.status_var.get()


def main_mod():
    import main
    return main


def test_screen_read_again_reuses_region(app, fake_screen, monkeypatch):
    """一度囲んだら、「同じ範囲を読む」は選び直さずに同じ場所を切り出して読む。"""
    from PIL import Image, ImageGrab
    shot, spawned = fake_screen
    assert app.screen_again_btn is None     # まだ囲んでいない＝ボタンも出さない
    app.screen_read()
    _grab_now(app)
    cv = _screen_canvas(app)
    app.update()
    ox, oy = cv.winfo_rootx(), cv.winfo_rooty()
    cv.event_generate("<ButtonPress-1>", x=30 - ox, y=20 - oy, rootx=30, rooty=20)
    cv.event_generate("<ButtonRelease-1>", x=130 - ox, y=70 - oy, rootx=130, rooty=70)
    app.update()
    app._set_busy(False)
    assert app.screen_again_btn is not None  # 一度囲んだらボタンが出る
    assert str(app.screen_again_btn["state"]) == "normal"
    # ページをめくった＝別の画面。範囲選択の窓は出ずに、同じ場所だけ読む
    page2 = Image.new("RGB", (400, 300), "gray")
    monkeypatch.setattr(ImageGrab, "grab", lambda *a, **k: page2)
    app.screen_read_again()
    assert app.state() == "withdrawn"
    h = app._ticks.pop("screen")
    app.after_cancel(h)
    app._screen_grab_again()
    assert app._screen_win is None
    assert app.state() != "withdrawn"
    assert len(spawned) == 2
    img = spawned[1][1][0]
    assert img.size == (100, 50)
    assert img.getpixel((0, 0)) == (128, 128, 128)   # 新しいページから切り出した
    app._set_busy(False)


def test_screen_read_again_without_region_starts_picking(app, fake_screen):
    """まだ一度も囲んでいなければ、囲むところから始める。"""
    app._screen_last = None
    app.screen_read_again()
    assert app._screen_win is True       # 撮影待ち（範囲選択の手前）
    _grab_now(app)
    assert isinstance(app._screen_win, tk.Toplevel)
    app._screen_selected(None)


# ---------------- 自動めくり読み・読み上げ中の📷 ----------------
def _page_img(color):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (300, 120), "white")
    d = ImageDraw.Draw(img)
    for i in range(4):
        d.rectangle((10, 10 + i * 25, 290, 20 + i * 25), fill=color)
    return img


def test_auto_page_reads_only_after_change_settles(app, monkeypatch):
    """同じページでは読まない。変わったら、めくりが止まってから（2回続けて同じ）読む。"""
    spawned = []
    monkeypatch.setattr(app, "_spawn",
                        lambda target, args=(): spawned.append(args))
    app._screen_last = ((0, 0), (300, 120))
    app.auto_page_var.set(True)
    a, b = _page_img("black"), _page_img("gray")
    app._auto_sig_read = main_mod().core.page_signature(a)
    app._auto_page_step(a)                 # 同じページ
    assert not spawned
    app._auto_page_step(b)                 # 変わり始めた（まだ読まない）
    assert not spawned
    app._auto_page_step(b)                 # 止まった → 読む
    assert len(spawned) == 1
    assert spawned[0][-1] == "screen_auto"
    app._set_busy(False)
    app._auto_page_step(b)                 # 読んだページはもう読まない
    assert len(spawned) == 1
    app.auto_page_var.set(False)


def test_auto_page_skips_same_text(app):
    """画面が少し変わっても、読み取った文章が前と同じなら読み直さない。"""
    app.text.delete("1.0", "end")
    app._screen_last_text = "同じ文章です。"
    app.q.put(("clip_done", "同じ文章です。", {}, [], "screen_auto"))
    app._poll_queue()
    assert app.text.get("1.0", "end").strip() == ""
    assert "同じ文章" in app.status_var.get()


def test_app_covers_region(app):
    """アプリの窓が囲んだ範囲に重なっているかを見分ける（重なると自分を撮ってしまう）。"""
    app.geometry("300x200+100+100")
    app.update()
    ax, ay = app.winfo_rootx(), app.winfo_rooty()
    app._screen_last = ((ax + 10, ay + 10), (ax + 50, ay + 50))
    assert app._app_covers_region() is True
    app._screen_last = ((ax + 2000, ay), (ax + 2100, ay + 50))
    assert app._app_covers_region() is False


def test_screen_read_while_playing_stops_then_opens(app, fake_screen, monkeypatch):
    """読み上げ中に📷を押すと、止めてから範囲選択へ進む（「停止してから」と言わない）。"""
    stopped = []

    def fake_stop():
        stopped.append(True)
        app._previewing = False      # 止まった
    monkeypatch.setattr(app, "stop_playall", fake_stop)
    app._previewing = True
    app.screen_read()
    assert stopped
    h = app._ticks.pop("screen_wait")
    app.after_cancel(h)
    app._after_stopping(app.screen_read, 1)
    assert app._screen_win is True       # 撮影待ちへ進んだ
    _grab_now(app)
    app._screen_selected(None)


def test_next_page_hint_after_screen_reading(app):
    """画面から読んだ文章を読み終えたら、次のページの読み方を案内する。"""
    app._screen_last = ((0, 0), (100, 100))
    app._screen_reading = True
    app._previewing = True
    app.q.put(("playall_done", True, False, 3, 0, app._play_gen))
    app._poll_queue()
    assert "同じ範囲を読む" in app.status_var.get()
    assert app._screen_reading is False


def test_fix_tcl_library_points_venv_to_base_python(tmp_path, monkeypatch):
    """Mac で、自分用 Python から作った venv でも Tcl/Tk の部品を見つけられるよう、
    元の Python の lib/tcl8.x・lib/tk8.x を教える。既に指定があれば触らない。
    （本物の環境変数を書き換えると、このあとのテストの Tk が偽の init.tcl を読むので、辞書で試す）"""
    base = tmp_path / "py"
    for d, marker in (("tcl8.6", "init.tcl"), ("tk8.6", "tk.tcl")):
        (base / "lib" / d).mkdir(parents=True)
        (base / "lib" / d / marker).write_text("")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "base_prefix", str(base))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    env = {"TK_LIBRARY": "/keep"}
    main_mod()._fix_tcl_library(env)
    assert env["TCL_LIBRARY"] == str(base / "lib" / "tcl8.6")
    assert env["TK_LIBRARY"] == "/keep"
    # venv でなければ何もしない
    monkeypatch.setattr(sys, "prefix", str(base))
    env = {}
    main_mod()._fix_tcl_library(env)
    assert env == {}


def test_screen_read_skips_photo_retries(app, monkeypatch):
    """画面から読んだ文字は、写真向けの読み直し（照明平坦化・90度回転）をしない。
    短い範囲だと「低品質」と見なされ、最大3回読み直して待ちが延びていた。"""
    from PIL import Image
    import core as core_mod
    calls = []
    monkeypatch.setattr(core_mod, "run_ocr", lambda paths, **kw: {paths[0]: "テスト"})
    monkeypatch.setattr(core_mod, "ocr_retry_if_poor",
                        lambda text, *a, **kw: calls.append(text) or text)
    img = Image.new("RGB", (40, 20), "white")
    opts = app._gather_clean_opts()
    app._clipboard_worker(img, False, opts, source="screen")
    app._clipboard_worker(img, False, opts, source="screen_auto")
    assert calls == []
    app._clipboard_worker(img, False, opts, source="clip")
    assert calls == ["テスト"]


class _ClockOut:
    """連続再生の音の流れの代わり。実時間で鳴っていくふりをする（音は出ない）。"""
    opened = []

    def __init__(self, rate, channels=1, sampwidth=2):
        import time
        self.fmt = (rate, channels, sampwidth)
        self.rate = rate
        self.frame_bytes = channels * sampwidth
        self.data = bytearray()
        self.written = 0
        self.paused = False
        self.closed = False
        self._t = time.monotonic()
        self._played = 0.0
        _ClockOut.opened.append(self)

    def _tick(self):
        import time
        now = time.monotonic()
        if not self.paused and not self.closed:
            self._played += now - self._t
        self._t = now

    def write(self, pcm):
        self._tick()
        self._played = min(self._played, self.written / self.rate)  # 空なら今から鳴る
        self.data += pcm
        self.written += len(pcm) // self.frame_bytes

    def position(self):
        self._tick()
        return min(self.written, int(self._played * self.rate))

    def pause(self):
        self._tick()
        self.paused = True

    def resume(self):
        self._tick()
        self.paused = False

    def close(self):
        self._tick()
        self.closed = True


def _pcm_wav(sec, value, rate=24000):
    import io
    import struct
    import wave
    n = int(round(rate * sec))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{n}h", *([value] * n)))
    return buf.getvalue()


def _pcm(wav):
    import io
    import wave
    with wave.open(io.BytesIO(wav), "rb") as w:
        return w.readframes(w.getnframes())


def _run_playall(app, monkeypatch, wavs, gap=0.1, factory=_ClockOut, actions=(),
                 timeout=15, breaks=()):
    """連続再生のワーカーを、エンジンも音も使わずに最後まで動かし、UIへの通知を返す。"""
    import queue
    import threading
    import time
    import core
    _ClockOut.opened = []
    monkeypatch.setattr(core, "vv_synthesize_cached",
                        lambda base, text, sid, **kw: wavs[text])
    app._engine_ver = "x"
    app._dict_hash_tracker = lambda: (lambda: "h")   # エンジンに問い合わせない
    app._play_stream_factory = factory
    app._playall_stop = threading.Event()
    app._playall_pause = threading.Event()
    app._play_gap = gap
    app._play_breaks = list(breaks)   # 各行の前の切れ目（空なら、どこも「間」のまま）
    app._play_longer = True
    while True:
        try:
            app.q.get_nowait()
        except queue.Empty:
            break
    targets = [(i + 1, name, 1) for i, name in enumerate(wavs)]
    t0 = time.monotonic()
    th = threading.Thread(target=app._playall_worker,
                          args=(targets, {}, app._play_gen))
    th.start()
    for delay, fn in actions:
        threading.Timer(delay, fn).start()
    th.join(timeout)
    assert not th.is_alive(), "連続再生が終わらない"
    elapsed = time.monotonic() - t0
    msgs = []
    while True:
        try:
            msgs.append(app.q.get_nowait())
        except queue.Empty:
            break
    return msgs, elapsed


def test_playall_stream_plays_lines_back_to_back_with_exact_gap(app, monkeypatch):
    """Windows の連続再生（1本の音の流れ）: 行は「間」の秒数ちょうどの無音をはさんで
    並び、行のハイライトは鳴り始め、鳴り終わりの通知は行ごとに順に届く。"""
    wavs = {"A": _pcm_wav(0.20, 1000), "B": _pcm_wav(0.15, 2000), "C": _pcm_wav(0.10, 3000)}
    msgs, _ = _run_playall(app, monkeypatch, wavs, gap=0.1)
    out = _ClockOut.opened[0]
    gap = b"\x00" * int(0.1 * 24000) * 2
    assert bytes(out.data) == (_pcm(wavs["A"]) + gap + _pcm(wavs["B"]) + gap
                               + _pcm(wavs["C"]))
    assert out.closed
    seq = [(m[0], m[1]) for m in msgs if m[0] in ("playall_line", "playall_line_end")]
    assert seq == [("playall_line", 1), ("playall_line_end", 1),
                   ("playall_line", 2), ("playall_line_end", 2),
                   ("playall_line", 3), ("playall_line_end", 3)]
    lines = [m for m in msgs if m[0] == "playall_line"]
    assert [(m[4], m[5]) for m in lines] == [(0, 3), (1, 3), (2, 3)]   # 何行目/全部
    done = msgs[-1]
    assert done[0] == "playall_done" and done[1] is True
    assert done[2] is False and done[3] == 3 and done[4] == 0   # 止めていない・3行・スキップ0

def test_playall_stream_longer_gap_at_paragraph_break(app, monkeypatch):
    """段落の切れ目の前は、「間」の2.5倍の無音を流れに入れる（保存する音声と同じ長さ）。"""
    wavs = {"A": _pcm_wav(0.20, 1000), "B": _pcm_wav(0.15, 2000), "C": _pcm_wav(0.10, 3000)}
    _run_playall(app, monkeypatch, wavs, gap=0.1, breaks=["", "para", ""])
    out = _ClockOut.opened[0]
    para = b"\x00" * 6000 * 2                 # 0.1秒 × 2.5 = 0.25秒
    gap = b"\x00" * int(0.1 * 24000) * 2
    assert bytes(out.data) == (_pcm(wavs["A"]) + para + _pcm(wavs["B"]) + gap
                               + _pcm(wavs["C"]))


def test_playall_stream_pause_continues_from_where_it_stopped(app, monkeypatch):
    """一時停止は、その場で止まって続きから鳴る（行の頭から読み直さない）。"""
    wavs = {"A": _pcm_wav(0.6, 1000), "B": _pcm_wav(0.2, 2000)}
    pause_ev = []

    def pause():
        pause_ev.append(app._playall_pause)
        app._playall_pause.set()

    msgs, elapsed = _run_playall(
        app, monkeypatch, wavs, gap=0.1,
        actions=[(0.2, pause), (0.7, lambda: pause_ev[0].clear())])
    out = _ClockOut.opened[0]
    gap = b"\x00" * int(0.1 * 24000) * 2
    assert bytes(out.data) == _pcm(wavs["A"]) + gap + _pcm(wavs["B"])   # A は1回だけ
    assert elapsed >= 0.6 + 0.1 + 0.2 + 0.4     # 鳴らした分＋止めていた0.5秒ほど
    assert msgs[-1][0] == "playall_done" and msgs[-1][3] == 2


def test_playall_stream_stop_silences_immediately(app, monkeypatch):
    """停止したら、鳴っている行の途中でもすぐ音を止めて終わる。"""
    wavs = {"A": _pcm_wav(3.0, 1000), "B": _pcm_wav(1.0, 2000)}
    msgs, elapsed = _run_playall(
        app, monkeypatch, wavs, actions=[(0.3, lambda: app._playall_stop.set())])
    assert elapsed < 1.5
    assert _ClockOut.opened[0].closed
    done = msgs[-1]
    assert done[0] == "playall_done" and done[2] is True and done[3] == 0


def test_playall_falls_back_to_per_line_when_output_cannot_open(app, monkeypatch):
    """音の出力を開けない環境では、従来の行ごとの再生で最後まで読む。"""
    import core
    played = []

    def no_output(*a, **k):
        raise OSError("音の出力を開けませんでした")

    def fake_play(wb, stop_event=None):
        played.append(wb)
        return True

    monkeypatch.setattr(core, "play_wav_blocking", fake_play)
    wavs = {"A": _pcm_wav(0.1, 1000), "B": _pcm_wav(0.1, 2000)}
    msgs, _ = _run_playall(app, monkeypatch, wavs, gap=0.0, factory=no_output)
    assert played == [wavs["A"], wavs["B"]]
    assert msgs[-1][0] == "playall_done" and msgs[-1][3] == 2


@pytest.mark.parametrize("finished, expected", [(True, ["A", "B"]),
                                                (False, ["A", "A", "B"])])
def test_playall_lines_rereads_only_when_paused_mid_voice(app, monkeypatch,
                                                         finished, expected):
    """行ごとの再生（Mac・出力を開けないとき）: 声の途中で一時停止したら再開時に
    同じ行を頭から読み直す。声が鳴り終わってからの一時停止なら、読み直さずに次の行へ。"""
    import threading
    import core
    calls = []

    def no_output(*a, **k):
        raise OSError("no device")

    def fake_play(wb, stop_event=None):
        name = "A" if wb == wavs["A"] else "B"
        calls.append(name)
        if len(calls) == 1:   # 1行目を鳴らしている間に一時停止が押された
            app._playall_pause.set()
            threading.Timer(0.2, app._playall_pause.clear).start()
            return finished
        return True

    monkeypatch.setattr(core, "play_wav_blocking", fake_play)
    wavs = {"A": _pcm_wav(0.1, 1000), "B": _pcm_wav(0.1, 2000)}
    msgs, _ = _run_playall(app, monkeypatch, wavs, gap=0.0, factory=no_output)
    assert calls == expected
    assert msgs[-1][0] == "playall_done" and msgs[-1][3] == 2


def _layout_report(app):
    """CI で並びのテストが落ちたときに原因を追えるよう、画面と字と部品の幅を書き出す。"""
    import main
    import tkinter.font as tkfont
    app.update()
    print("画面", app.winfo_screenwidth(), app.winfo_screenheight(), "倍率", main.UI_SCALE,
          "字", tkfont.Font(app, font=main.UI_FONT or "TkDefaultFont").actual())


def _layout_widths(app):
    ctl = app._player_rows[0]
    bar, tools = app._rbar[0], app._rbar[1]
    def kids(f):
        return [(str(c.cget("text")) if "text" in c.keys() else c.winfo_class(), c.winfo_reqwidth())
                for c in f.pack_slaves()]
    return (f"窓 {app.geometry()} 左の列 {app._steps_col.winfo_width()} / 再生バー {ctl.winfo_width()} {kids(ctl)}"
            f" / 本文の見出し {bar.winfo_width()} 見出し {app._rbar_head.winfo_reqwidth()} {kids(tools)}")


def test_player_bar_wraps_instead_of_clipping(app):
    """いちばん狭い窓でも、再生バーの部品が切れない（入らなければ「速さ・間」と
    「何行目か」を2行目へ送る）。広い窓では1行に戻る。"""
    import main
    _layout_report(app)
    ctl, row2, params, sep = app._player_rows

    def settle():
        for _ in range(6):
            app.update()

    def visible(w, box):
        x = w.winfo_rootx() - box.winfo_rootx()
        return (w.winfo_ismapped() and x >= 0
                and x + w.winfo_reqwidth() <= box.winfo_width() + 1
                and w.winfo_width() + 1 >= w.winfo_reqwidth())

    app._pos_var.set("12,345 / 12,345行・あと約25時間")
    app.geometry(f"{main._px(860)}x{main._px(720)}")
    settle()
    box = row2 if not app._player_one else ctl
    assert params.pack_info()["in"] == box
    for w in [c for c in ctl.winfo_children() if c.winfo_manager()]:
        assert visible(w, ctl), f"再生バーの部品が切れている: {w} {_layout_widths(app)}"
    assert visible(params, box)
    assert app._play_prog.winfo_width() >= main._px(40)
    if app._player_one:
        return   # この画面の文字の大きさなら、いちばん狭い窓でも1行に入る
    assert row2.winfo_ismapped() and not sep.winfo_ismapped()
    pos = app._pos_lbl   # 「何行目か」は2行目の右端まで全部見える（線路のほうが縮む）
    assert pos.winfo_width() + 1 >= pos.winfo_reqwidth()
    assert (pos.winfo_rootx() + pos.winfo_width()
            <= row2.winfo_rootx() + row2.winfo_width() + 1)
    # 1行に戻る幅（ボタン・速さと間・何行目かが全部入る幅）。窓は、それに再生バー
    # 以外の幅（左の列など）を足した大きさにする
    wide = (ctl.winfo_reqwidth() + params.winfo_reqwidth()
            + app._player_tail.winfo_reqwidth() + main._px(200))
    if app._player_compact:   # 記号だけにしたボタンは、元の文に戻るぶん広くなる
        wide += sum(app._text_width(full) - app._text_width(short)
                    for _btn, full, short in app._player_short)
    app.geometry(f"{wide + app.winfo_width() - ctl.winfo_width()}x{main._px(720)}")
    settle()
    if ctl.winfo_width() < wide:
        pytest.skip("画面が狭く、1行に戻る幅まで広げられない")
    assert app._player_one and params.pack_info()["in"] == ctl
    assert not row2.winfo_ismapped() and sep.winfo_ismapped()


@pytest.mark.parametrize("ratio", [0.9, 1.0, 1.1])
def test_narrow_labels_settle_at_every_width(app, monkeypatch, ratio):
    """ボタン名を縮める・戻すは、どの幅でも一度決まったら行き来しない。字の幅の
    見積もりが実際とずれていても（CI の Windows で、縮める・戻すを繰り返して固まった）。"""
    import main
    real = app._text_width
    monkeypatch.setattr(app, "_text_width", lambda text: int(real(text) * ratio))
    app._pos_var.set("12,345 / 12,345行・あと約25時間")
    for w in range(main._px(860), main._px(1160), max(1, main._px(6))):
        app.geometry(f"{w}x{main._px(720)}")
        for _ in range(4):
            app.update()
        state = (app._player_compact, app._player_layout, app._rbar_compact,
                 app._rbar_level)
        for _ in range(3):   # もう一度並べ直しても変わらない
            app._reflow()
            app.update()
            assert (app._player_compact, app._player_layout, app._rbar_compact,
                    app._rbar_level) == state, w


def test_pause_closes_mouth_and_resume_reopens_only_mid_line(app):
    """一時停止で口パクを止め、再開時は行の途中のときだけ口パクを戻す。"""
    import threading
    app._playall_pause = threading.Event()
    started = []
    app._start_mouth = lambda speaker_id=None: started.append(speaker_id)
    app._line_sounding = True
    app.toggle_pause()
    assert app._playall_pause.is_set()
    assert getattr(app, "_mouth_after", None) is None   # 立ち絵が無い環境でも落ちない
    app.toggle_pause()
    assert not app._playall_pause.is_set() and started == [None]
    app._line_sounding = False     # 行間で止めて再開 → 次の行が鳴るまで口は閉じたまま
    app.toggle_pause()
    app.toggle_pause()
    assert started == [None]
    app._playall_pause = None


def _furi_pump(app, cond, sec=5.0):
    import time
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.update()
        if cond():
            return True
        time.sleep(0.02)
    return False


def _furi_ready(app, monkeypatch, rubies_by_text):
    """エンジンも辞書も使わずに、ふりがなの表示だけを確かめられるようにする。"""
    import core
    monkeypatch.setattr(core, "vv_furigana",
                        lambda base, text, sid, use_dictionary=True, timeout=15, read_fix=None:
                        rubies_by_text.get(text, []))
    monkeypatch.setattr(app, "_engine_ready", lambda: True)
    monkeypatch.setattr(app, "_current_speaker", lambda: ("ずんだもん（ノーマル）", 3, "u"))
    monkeypatch.setattr(app, "_furi_dict_installed", lambda: True)


def test_furigana_is_on_from_the_start_but_waits_for_engine(app, monkeypatch):
    """ふりがなは最初からオン。ただし行の上の余白は、エンジンにつながってから取る
    （つながる前から行間だけ空くと、壊れて見えるため）。"""
    assert app.furigana_var.get() is True
    s1 = int(app.text.cget("spacing1"))
    monkeypatch.setattr(app, "_engine_ready", lambda: False)
    app._furi_refresh()
    assert int(app.text.cget("spacing1")) == s1 and not app._furi_spaced
    monkeypatch.setattr(app, "_engine_ready", lambda: True)
    monkeypatch.setattr(app, "_current_speaker", lambda: ("ずんだもん（ノーマル）", 3, "u"))
    import core
    monkeypatch.setattr(core, "vv_furigana",
                        lambda base, text, sid, use_dictionary=True, timeout=15, read_fix=None: [])
    app._furi_refresh()
    assert int(app.text.cget("spacing1")) > s1 and app._furi_spaced


def test_furigana_background_follows_playing_line(app, monkeypatch):
    """読んでいる行の印（再生中の色）を移したら、その行のふりがなの下地も塗り直す。
    本文が動かないときも（前は前の色のまま残り、ダークでは黒い四角に見えた）。"""
    import core
    import main
    lines = ["今日は晴れ。", "東京へ行った。"]
    _furi_ready(app, monkeypatch, {lines[0]: [(0, 2, "きょう", core.FURI_OK)],
                                   lines[1]: [(0, 2, "とうきょう", core.FURI_OK)]})
    app.geometry(f"{main._px(1100)}x{main._px(760)}")
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "\n".join(lines))
    app._furi_refresh()
    shown = lambda: {lab.cget("text"): lab for lab in app._furi_labels
                     if lab.winfo_manager()}
    assert _furi_pump(app, lambda: len(shown()) == 2), "ふりがなが出ない"
    base = str(app.text.cget("bg"))
    play = str(app.text.tag_cget("playing", "background"))
    bg = lambda reading: str(shown()[reading].cget("bg"))
    app._play_jump(2)
    assert _furi_pump(app, lambda: bg("とうきょう") == play), "読んでいる行の下地が変わらない"
    assert bg("きょう") == base
    app._play_jump(1)
    assert _furi_pump(app, lambda: bg("きょう") == play and bg("とうきょう") == base)
    # ダークでも、明るい印の上は濃い色で書く（印の外はダークの色のまま）
    app.theme_var.set("dark")
    app.apply_theme()
    dark_plain = app._FURI_COLORS["dark"][0]
    fg = lambda reading: str(shown()[reading].cget("fg"))
    assert _furi_pump(app, lambda: fg("とうきょう") == dark_plain)
    assert fg("きょう") == app._FURI_COLORS_DEFAULT[0]
    app.theme_var.set("light")
    app.apply_theme()


def test_furigana_toggle_places_readings_above_kanji(app, monkeypatch):
    """オンのときは行の上に余白を取り、読みをその文字の真上に置く。
    チェックを外すと消して、行間を元に戻す。"""
    import core
    import main
    line = "今日は東京へ行った。"
    _furi_ready(app, monkeypatch, {line: [(0, 2, "きょう", core.FURI_OK),
                                          (3, 5, "とうきょう", core.FURI_OK),
                                          (6, 7, "イ", core.FURI_DIFF)]})
    app.geometry(f"{main._px(1100)}x{main._px(760)}")   # 最小幅より広く
    app.text.delete("1.0", "end")
    app.text.insert("1.0", line)
    app.furigana_var.set(False)
    app._toggle_furigana()
    app._furi_refresh()
    s1 = int(app.text.cget("spacing1"))
    app.furigana_var.set(True)
    app._toggle_furigana()
    shown = lambda: [lab for lab in app._furi_labels if lab.winfo_manager()]
    assert _furi_pump(app, lambda: len(shown()) == 3), "ふりがなが出ない"
    assert int(app.text.cget("spacing1")) > s1
    app.update()
    by_text = {lab.cget("text"): lab for lab in shown()}
    for reading, (a, b) in (("きょう", (0, 2)), ("とうきょう", (3, 5))):
        lab = by_text[reading]
        x0 = app.text.bbox(f"1.{a}")
        x1 = app.text.bbox(f"1.{b - 1}")
        center = lab.winfo_x() + lab.winfo_width() / 2
        assert x0[0] - 2 <= center <= x1[0] + x1[2] + 2          # 文字の真上
        assert lab.winfo_y() + lab.winfo_height() <= x0[1] + 2   # 文字より上
    assert by_text["イ"].cget("fg") != by_text["きょう"].cget("fg")   # 辞書と違う読みは色つき
    app.furigana_var.set(False)
    app._toggle_furigana()
    assert _furi_pump(app, lambda: not shown())
    assert int(app.text.cget("spacing1")) == s1


def test_furigana_line_skips_memo_and_shifts_past_speaker_tag(app):
    """メモ行（#）には出さない。@話者タグは外して読むので、位置もタグの後ろからにずらす。"""
    assert app._furi_line("# メモです") is None
    assert app._furi_line("   ") is None
    assert app._furi_line("今日は晴れ") == (0, "今日は晴れ")
    off, spoken = app._furi_line("  今日は晴れ  ")
    assert (off, spoken) == (2, "今日は晴れ")


def test_furigana_setting_is_saved(app):
    s = app._settings_dict()
    assert s["furigana"] is True          # 最初からオン
    app.furigana_var.set(False)
    assert app._settings_dict()["furigana"] is False
    app._toggle_furigana()


def test_furigana_repair_install_uses_requirements_spec(app):
    """辞書が入っていないときの入れ直しは、requirements.txt と同じ版の決まりで入れる。"""
    assert app._furi_requirement().replace(" ", "").startswith("janome>=")


def test_furigana_waits_and_retries_when_engine_fails(app, monkeypatch):
    """エンジンに聞けなかった（つながらない）ときは覚えず、少し待ってから取り直す
    （止まっている間に連打しない）。"""
    import core
    import requests
    _furi_ready(app, monkeypatch, {})

    def down(*a, **k):
        raise requests.ConnectionError("engine down")
    monkeypatch.setattr(core, "vv_furigana", down)
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "東京")
    app.furigana_var.set(True)
    app._toggle_furigana()
    assert _furi_pump(app, lambda: app._furi_retry_at > 0 and not app._furi_busy)
    assert not app._furi_cache
    app.furigana_var.set(False)
    app._toggle_furigana()


def test_furigana_one_bad_line_does_not_block_others(app, monkeypatch):
    """1行だけエンジンに断られても（読めない文字など）、ほかの行のふりがなは出る。
    以前は1行のせいで、全部の行が出なかった。"""
    import core
    import requests
    _furi_ready(app, monkeypatch, {})

    def fake(base, text, sid, use_dictionary=True, timeout=15, read_fix=None):
        if "壊" in text:
            raise requests.HTTPError("422")
        if "変" in text:
            raise ValueError("unexpected")
        return [(0, 2, "とうきょう", core.FURI_OK)]
    monkeypatch.setattr(core, "vv_furigana", fake)
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "\n".join(["壊れた行", "変な行", "東京へ行く"]))
    app.furigana_var.set(True)
    app._toggle_furigana()
    assert _furi_pump(app, lambda: any(lab.winfo_manager() for lab in app._furi_labels))
    texts = {k[0]: v for k, v in app._furi_cache.items()}
    assert texts["壊れた行"] == [] and texts["変な行"] == []      # 覚えて、取り直さない
    assert texts["東京へ行く"] and app._furi_retry_at == 0.0
    app.furigana_var.set(False)
    app._toggle_furigana()


def test_furigana_notice_when_voicevox_is_not_running(app, monkeypatch):
    """ふりがながオンなのに VOICEVOX につながっていないときは、本文のすぐ上に、出ない
    理由と「画面なしで起動」を出す（出ない理由が分からず「壊れている」と思われないように。
    チェックの横の小さな字では気づかれなかった）。起動を待っている間はボタンを隠す。
    ふりがなを外す・本文が空・つながった・起動して最初の確認の前は出さない。"""
    launched = []
    monkeypatch.setattr(app, "launch_engine_only", lambda: launched.append(True))
    notice = app._furi_notice
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "東京へ行く")
    app.furigana_var.set(True)
    app.conn_state = "unknown"
    app._update_step_highlight()
    assert not notice.winfo_manager()            # 最初の接続確認の前は出さない
    app._set_conn_state("refused")
    assert notice.winfo_manager()
    assert "VOICEVOX を起動すると出ます" in app._furi_notice_var.get()
    assert app.furigana_chk.cget("text") == "ふりがな"   # チェックの文字は変えない
    slaves = notice.master.pack_slaves()
    assert slaves.index(notice) < slaves.index(app._rbar[4])   # 本文の上に並ぶ
    app._furi_notice_btn.invoke()
    assert launched == [True]
    app._set_conn_state("waiting")
    assert "待っています" in app._furi_notice_var.get()
    assert not app._furi_notice_btn.winfo_manager()
    app._set_conn_state("refused")
    assert app._furi_notice_btn.winfo_manager()
    app.furigana_var.set(False)
    app._toggle_furigana()
    assert not notice.winfo_manager()
    app.furigana_var.set(True)
    app._toggle_furigana()
    assert notice.winfo_manager()
    monkeypatch.setattr(app, "_engine_ready", lambda: True)
    app._sync_furi_notice()
    assert not notice.winfo_manager()            # つながったら消す
    monkeypatch.setattr(app, "_engine_ready", lambda: False)
    app.text.delete("1.0", "end")
    app._update_step_highlight()
    assert not notice.winfo_manager()            # 本文が空なら出さない


# ---------------- 2列の画面（左に手順・右に本文） ----------------

def _settle(app, n=8):
    for _ in range(n):
        app.update()


def _descendants(w):
    for c in w.winfo_children():
        yield c
        yield from _descendants(c)


def _not_clipped(w):
    """部品が、置かれた枠の中に収まって全部見えているか。"""
    box = w.pack_info()["in"]
    x = w.winfo_rootx() - box.winfo_rootx()
    return (w.winfo_ismapped() and x >= 0
            and x + w.winfo_width() <= box.winfo_width() + 1
            and w.winfo_width() + 1 >= w.winfo_reqwidth())


def test_two_column_layout_steps_left_text_right(app):
    """左の列に手順（①取り込む ②声 ③保存）と接続、右の列に本文と再生バー。
    本文欄が窓の幅の半分以上を使える（以前は4つの区画を縦に積み、本文欄が低かった）。"""
    import main
    app.geometry(f"{main._px(980)}x{main._px(760)}")
    _settle(app)
    steps = str(app._steps_view) + "."
    for w in (app.screen_btn, app.extract_btn, app.char_cb, app.preview_btn,
              app.fmt_cb, app.synth_btn):
        assert str(w).startswith(steps), w       # 手順の列の中
    for w in (app.extract_btn, app.synth_btn, app.engine_lbl):
        assert w.winfo_rootx() + w.winfo_width() <= app.text.winfo_rootx()
    assert app.text.winfo_width() > app.winfo_width() // 2
    # 再生バーは本文のすぐ下
    assert app.playall_btn.winfo_rooty() >= app.text.winfo_rooty() + app.text.winfo_height()


def test_steps_column_scrolls_only_when_it_does_not_fit(app):
    """左の列は、入りきらない低い窓のときだけスクロールバーを出し、ホイールで動く。"""
    import main
    app.minsize(1, 1)
    app._set_opt_rows(True)       # 設定を開いて、列を長くする
    app._set_advanced(True)
    app.geometry(f"{main._px(980)}x{main._px(600)}")
    _settle(app)
    view = app._steps_view
    assert app._steps.winfo_reqheight() > view.winfo_height()
    assert app._steps_sb.winfo_manager()
    assert view.yview()[0] == 0.0
    wheel = type("E", (), {"widget": app.extract_btn, "delta": -120, "num": "??"})()
    app._on_steps_wheel(wheel)
    assert view.yview()[0] > 0.0                  # 下へ動いた
    app._set_opt_rows(False)
    app._set_advanced(False)
    _settle(app)
    fits = app._steps.winfo_reqheight() <= view.winfo_height()
    assert bool(app._steps_sb.winfo_manager()) != fits
    if fits:
        assert view.yview()[0] == 0.0             # 入るなら先頭に戻す


def test_wheel_over_field_in_steps_does_not_change_value(app):
    """左の列の数値欄の上でホイールを回しても、カーソルを入れていなければ値は変わらない
    （列をスクロールしているつもりで、話速が勝手に変わらないように）。"""
    _settle(app)
    app.speed_var.set(1.0)
    spin = next(w for w in _descendants(app._steps)
                if w.winfo_class() == "TSpinbox"
                and str(w.cget("textvariable")) == str(app.speed_var))
    wheel = type("E", (), {"widget": spin, "delta": -120, "num": "??"})()
    assert app._wheel_on_field(wheel, spin) == "break"
    spin.event_generate("<MouseWheel>", delta=-120)
    app.update()
    assert app.speed_var.get() == 1.0


def test_text_header_moves_items_down_instead_of_clipping(app, monkeypatch):
    """本文の見出しの行: 入りきらない幅では、行数など→ふりがな・辞書の順に本文の下の
    行へ送り、どの部品も切れない。広げると上の行に戻る。"""
    import main
    bar, tools, info, foot, body = app._rbar
    monkeypatch.setattr(app, "_engine_ready", lambda: False)
    app.furigana_var.set(True)
    app._furi_refresh()                  # オンのまま、VOICEVOX にはつながっていない
    app.stats_var.set("12,345行・123,456字・めやす約12時間34分")
    app.geometry(f"{main._px(860)}x{main._px(720)}")
    _settle(app)
    narrow = app._rbar_level
    assert narrow >= 1
    for w in (app._rbar_head, tools, app.furigana_chk, app.furi_next_btn, app.dict_btn,
              app._rep_btn, app.restore_btn):
        assert _not_clipped(w), w
    # 行数などの文は、入りきらなければ縮んで受ける（左端は見えている）
    assert info.winfo_ismapped() and info.winfo_rootx() >= foot.winfo_rootx()
    app.geometry(f"{main._px(1300)}x{main._px(720)}")
    _settle(app)
    assert app._rbar_level <= narrow
    for w in (app._rbar_head, tools, info, app.furigana_chk, app.dict_btn):
        assert _not_clipped(w), w
    app.furigana_var.set(False)
    app._furi_refresh()


def test_file_list_shows_drop_hint_only_when_empty(app):
    """ファイルの一覧が空のときだけ、中に「ここに落とせる」案内を出す。"""
    app.clear_files()
    assert app._list_hint.winfo_manager() == "place"
    app._add_one(__file__)
    assert not app._list_hint.winfo_manager()
    app.listbox.selection_set(0)
    app.remove_selected()
    assert app._list_hint.winfo_manager() == "place"


def test_open_panels_are_remembered(app, tmp_path, monkeypatch):
    """① の設定と一括置換の開閉を覚える。一括置換は、ルールを登録している人には
    最初から開いておく（前の版ではいつも見えていたので、使い方を変えない）。"""
    import json
    import main as main_mod
    d = app._settings_dict()
    assert d["opt_open"] is False and d["replace_open"] is False
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"replace_rules": [["ｶﾞ", "ガ"]], "opt_open": True}),
                 encoding="utf-8")
    monkeypatch.setattr(main_mod, "SETTINGS_PATH", str(p))
    app._load_settings()
    assert app._opt_open and app._opt_panel.winfo_manager()
    assert app._rep_open and app._rep_frame.winfo_manager()
    app._toggle_replace()
    app._toggle_opt_rows()
    d = app._settings_dict()
    assert d["opt_open"] is False and d["replace_open"] is False
    assert not app._rep_frame.winfo_manager() and not app._opt_panel.winfo_manager()


# ---------------- ボタンの見分け・起動の速さ・選択枠・空の本文・ふりがなで直す ----------------

# 日本語の字体に無い字。Windows は初めて描くときに字体を探して 0.2〜1秒止まる
_SLOW_GLYPHS = set("▸▾⏮⏭⏪⏸⏵⚙↻✕⛔💬📷🔊🍂🌙🌿🍵☀")


def _widget_texts(w):
    for c in [w, *_descendants(w)]:
        try:
            yield str(c.cget("text"))
        except tk.TclError:
            pass   # 文字を持たない部品


def test_startup_screen_has_no_slow_glyphs(app):
    """起動直後に見える文字に、絵文字や日本語の字体に無い記号を使わない
    （そのぶん窓が早く出る。お知らせの絵文字は、窓が出たあとに先に読み込む）。"""
    seen = ("".join(_widget_texts(app)) + app.status_var.get()
            + "".join(str(v) for v in app.theme_cb.cget("values")))
    bad = sorted({ch for ch in seen if ch in _SLOW_GLYPHS or ord(ch) >= 0x1F000})
    assert not bad, f"起動直後の画面に、描くのに時間のかかる字: {bad}"
    app._warm_emoji_font()        # 先読み（例外を出さない）


def test_buttons_show_their_role(app):
    """ボタンの見た目を役割で分ける: 主役は色・ふつうは淡い面・ときどき使うのは枠だけ・
    設定と開閉は文字だけ・消すのは赤い文字。"""
    from tkinter import ttk
    assert str(app.pause_btn["style"]) == "Accent.TButton"
    app._set_opt_rows(True)       # ① の設定は、開いたときに作る
    for w in (app._opt_toggle, app._adv_btn, app._vdetail_btn, app._rep_btn,
              app.cache_btn, app._conn_edit_btn, app.dict_btn):
        assert str(w["style"]) == "Tool.TButton", w
    # 設定のボタンには歯車の印が付く（ほかの道具と見分けられるように）
    def images(w):
        return [str(x) for x in app.tk.splitlist(w.cget("image"))]
    for w in (app._opt_toggle, app._adv_btn, app._vdetail_btn, app.cache_btn,
              app._conn_edit_btn):
        assert images(w) == [str(app._gear_img)], w
    assert images(app._rep_btn) == [] and images(app.dict_btn) == []
    # ① の4つのボタンには、何をするかの印（書類・フォルダ・カメラ・写真）。
    # 押せないときは薄い色の版に替わり、テーマを変えると文字と同じ色になる
    assert str(app.screen_btn["text"]) == "スクリーンショット"
    marks = (("ファイル追加", app._file_img, app._file_img_off),
             ("フォルダ追加", app._folder_img, app._folder_img_off),
             ("スクリーンショット", app._camera_img, app._camera_img_off),
             ("コピーした画像を読む", app._picture_img, app._picture_img_off))
    for label, on, off in marks:
        btn = next(w for w in _descendants(app._steps) if w.winfo_class() == "TButton"
                   and str(w.cget("text")) == label)
        assert images(btn) == [str(on), "disabled", str(off)], label
    s = ttk.Style(app)
    for key in ("light", "dark"):
        app.theme_var.set(key)
        app.apply_theme()
        for label, on, _off in marks:
            color = app.tk.call(str(on), "cget", "-foreground")
            assert str(color) == str(s.lookup("TButton", "foreground")), (key, label)
    # ボタン・見出し・チェックには、ふだん使わない言葉（クリップボード・OCR・キャッシュ・
    # レポート・エンジン・話者・プリセット・置換・解像度・整形・話速・音高・文間・結合）を
    # 出さない。何をするかの言葉で呼ぶ。同じ設定は、どこでも同じ名前で呼ぶ
    jargon = ("クリップボード", "OCR", "キャッシュ", "レポート", "エンジン", "話者",
              "プリセット", "置換", "解像度", "整形", "話速", "音高", "文間", "結合")
    for w in _descendants(app):
        if w.winfo_class() in ("TButton", "TLabel", "TCheckbutton", "TRadiobutton",
                               "TMenubutton"):
            label = str(w.cget("text"))
            assert not any(word in label for word in jargon), label
    for cb in (app.unit_cb, app.fmt_cb):
        for v in app.tk.splitlist(cb.cget("values")):
            assert not any(word in str(v) for word in jargon), v
    assert not any(word in app.engine_var.get() for word in jargon)
    labels = {str(w.cget("text")) for w in _descendants(app) if w.winfo_class() == "TLabel"}
    assert {"速さ", "文の間", "文の間:"} <= labels       # ② と再生バー、③ と再生バーで同じ名前
    assert str(app.cache_btn["text"]) == "声の保管…"
    assert str(app.engine_only_btn["text"]) == "画面なしで起動"
    assert str(app.report_btn["text"]) == "直した所"
    for w in (app.report_btn, app.vvproj_btn):
        assert str(w["style"]) == "Quiet.TButton", w
    assert str(app.restore_btn["style"]) == "Danger.Tool.TButton"
    app.text.insert("1.0", "本文")
    app.clear_text()
    assert str(app.restore_btn["style"]) == "Tool.TButton"     # 「復元」は消す色にしない
    app.restore_text()
    assert str(app.restore_btn["style"]) == "Danger.Tool.TButton"
    s = ttk.Style(app)   # 設定・開閉の文字に、主役の色（オレンジ）を使わない
    assert s.lookup("Tool.TButton", "foreground") != s.lookup("Primary.TButton", "background")


def test_keyboard_focus_is_visible(app):
    """Tab で動いたとき、どこにいるか見える（選択枠を面と同じ色にしていて見えなかった）。"""
    from tkinter import ttk
    s = ttk.Style(app)
    for name in ("TButton", "Tool.TButton", "Quiet.TButton", "TCheckbutton"):
        assert s.lookup(name, "focuscolor") not in ("", s.lookup(name, "background")), name
    assert str(app.text.cget("highlightcolor")) != str(app.text.cget("highlightbackground"))


def test_empty_text_shows_hint(app):
    """本文が空のときだけ、本文欄の中に何をすればいいかを出す。"""
    app.text.delete("1.0", "end")
    app._update_step_highlight()
    assert app._text_hint.winfo_manager() == "place"
    app.text.insert("1.0", "本文")
    app._update_step_highlight()
    assert not app._text_hint.winfo_manager()


def test_clicking_suspicious_reading_opens_dictionary(app, monkeypatch):
    """色つきのふりがな（辞書と違う読み）を押すと、その語を入れて読み方辞書を開く。
    辞書と同じ読みのふりがなは、これまでどおり本文にカーソルを置くだけ。"""
    import core
    import main
    line = "今日は東京へ行った。"
    _furi_ready(app, monkeypatch, {line: [(0, 2, "きょう", core.FURI_OK),
                                          (6, 7, "イ", core.FURI_DIFF)]})
    opened = []
    monkeypatch.setattr(app, "_register_word_to_dict", lambda w: opened.append(w))
    app.geometry(f"{main._px(1100)}x{main._px(760)}")
    app.text.delete("1.0", "end")
    app.text.insert("1.0", line)
    app.furigana_var.set(True)
    app._toggle_furigana()

    def shown():
        return {lab.cget("text"): lab for lab in app._furi_labels if lab.winfo_manager()}
    assert _furi_pump(app, lambda: len(shown()) == 2)
    labs = shown()
    assert str(labs["イ"].cget("cursor")) == "hand2"
    labs["きょう"].event_generate("<Button-1>", x=1, y=1)
    assert opened == []                    # 辞書と同じ読み → カーソルを置くだけ
    labs["イ"].event_generate("<Button-1>", x=1, y=1)
    assert opened == ["行"]
    assert "イ" in app.status_var.get()
    app.furigana_var.set(False)
    app._toggle_furigana()


def test_only_suspicious_readings(app, monkeypatch):
    """「誤読候補だけ」: 辞書と同じ読みのふりがなは出さず、色つきだけを出す。
    ふりがなを消しているあいだは押せない。"""
    import core
    import main
    line = "今日は東京へ行った。"
    _furi_ready(app, monkeypatch, {line: [(0, 2, "きょう", core.FURI_OK),
                                          (3, 5, "とうきょう", core.FURI_OK),
                                          (6, 7, "イ", core.FURI_DIFF)]})
    app.geometry(f"{main._px(1100)}x{main._px(760)}")
    app.text.delete("1.0", "end")
    app.text.insert("1.0", line)
    app.furigana_var.set(True)
    app._toggle_furigana()

    def shown():
        return sorted(lab.cget("text") for lab in app._furi_labels if lab.winfo_manager())
    assert _furi_pump(app, lambda: len(shown()) == 3)
    app.furigana_only_var.set(True)
    app._furi_schedule(0)
    assert _furi_pump(app, lambda: shown() == ["イ"])
    assert app._settings_dict()["furigana_only"] is True
    app.furigana_var.set(False)
    app._toggle_furigana()
    app._furi_refresh()
    assert app.furigana_only_chk.instate(["disabled"])
    app.furigana_only_var.set(False)


# ---------------- エンジンを一緒に起動・使い方・起動の速さ・タイトルバー ----------------

def test_options_panel_is_built_on_first_open(app):
    """① の取り出し方の設定は、初めて開いたときに作る（起動を速くするため）。
    開く前に覚えた「詳細設定」の開閉も、作ったときに見た目へ当てる。"""
    assert app._opt_panel is None and app._adv_btn is None
    app.mode_var.set("keep")              # 値（変数）は最初からある
    app._set_advanced(True)               # 作る前でも開閉は覚える
    app._set_opt_rows(True)
    assert app._opt_panel.winfo_manager() and app._adv_frame.winfo_manager()
    assert "▽" in str(app._adv_btn["text"])
    app._set_opt_rows(False)
    app._set_opt_rows(True)               # 2回目は作り直さない
    assert app._opt_panel.winfo_manager()


def test_engine_starts_with_the_app_without_asking(app, monkeypatch):
    """起動してすぐ VOICEVOX が見つからないとき: エンジンが入っていれば、聞かずに起動する。
    窓は出さない。チェックを切ってあれば起動しない。エンジンが無ければ何もしない。"""
    import core
    launched = []
    found = ["run.exe"]
    monkeypatch.setattr(core, "find_voicevox_engine", lambda *a, **k: found[0])
    monkeypatch.setattr(app, "launch_engine_only", lambda quiet=False: launched.append(quiet))
    windows = sum(1 for w in app.winfo_children() if isinstance(w, tk.Toplevel))
    app._engine_autostart = True
    app._engine_autostart_check()
    assert launched == [True], "窓を出さずに（quiet で）起動していません"
    assert sum(1 for w in app.winfo_children() if isinstance(w, tk.Toplevel)) == windows
    app._engine_autostart = False
    app._engine_autostart_check()
    assert launched == [True]
    app._engine_autostart = True
    found[0] = None
    app._engine_autostart_check()
    assert launched == [True]


def _settings_with(tmp_path, monkeypatch, app, body):
    import main as main_mod
    path = tmp_path / "settings.json"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(main_mod, "SETTINGS_PATH", str(path))
    app._load_settings()


def test_engine_autostart_is_on_from_the_start(app, tmp_path, monkeypatch):
    """設定が無ければオン。前に切った人の答えは、そのまま守る。"""
    assert app._engine_autostart is True and app.engine_autostart_var.get() is True
    _settings_with(tmp_path, monkeypatch, app, '{"engine_autostart": false}')
    assert app._engine_autostart is False and app.engine_autostart_var.get() is False
    _settings_with(tmp_path, monkeypatch, app, "{}")
    assert app._engine_autostart is True and app.engine_autostart_var.get() is True
    _settings_with(tmp_path, monkeypatch, app, '{"engine_autostart": null}')
    assert app._engine_autostart is True and app.engine_autostart_var.get() is True


def test_engine_autostart_checkbox_saves_choice(app, monkeypatch):
    """「アプリと一緒に VOICEVOX を起動」のチェックで、いつでも切り替えられる。"""
    saved = []
    monkeypatch.setattr(app, "_save_settings", lambda *a, **k: saved.append(True))
    box = next(w for w in _descendants(app._conn_detail)
               if w.winfo_class() == "TCheckbutton"
               and "一緒に VOICEVOX" in str(w.cget("text")))
    box.invoke()
    assert app._engine_autostart is False and saved
    assert app._settings_dict()["engine_autostart"] is False
    box.invoke()
    assert app._engine_autostart is True
    assert app._settings_dict()["engine_autostart"] is True


def _no_engine_running(monkeypatch):
    import core
    monkeypatch.setattr(core, "vv_probe", lambda *a, **k: {"ok": False, "status": "refused"})


def test_quiet_engine_start_never_opens_a_window(app, monkeypatch):
    """開くたびに自動で起動するので、失敗しても窓で止めない。左下から開けると伝える。"""
    import core
    import main as main_mod
    _no_engine_running(monkeypatch)
    monkeypatch.setattr(main_mod.messagebox, "showerror", lambda *a, **k: pytest.fail("窓を出した"))
    monkeypatch.setattr(app, "_ask_voicevox_path", lambda *a, **k: pytest.fail("窓を出した"))

    def broken(**k):
        raise OSError("起動できない")
    monkeypatch.setattr(core, "launch_voicevox_engine", broken)
    app.launch_engine_only(quiet=True)
    assert "自動で起動できませんでした" in app.status_var.get()

    def missing(**k):
        raise FileNotFoundError("見つからない")
    monkeypatch.setattr(core, "launch_voicevox_engine", missing)
    app.status_var.set("そのまま")
    app.launch_engine_only(quiet=True)
    assert app.status_var.get() == "そのまま"


def test_quiet_engine_start_keeps_the_greeting(app, monkeypatch):
    """自動で起動しただけなら、最初の案内の文を消さない（待ちは左下に出る）。"""
    import core
    _no_engine_running(monkeypatch)
    monkeypatch.setattr(core, "launch_voicevox_engine",
                        lambda **k: (None, "http://127.0.0.1:50021"))
    monkeypatch.setattr(app, "_start_connect_retry", lambda *a, **k: None)
    app.status_var.set("はじめまして")
    app.launch_engine_only(quiet=True)
    assert app.status_var.get() == "はじめまして"
    app.launch_engine_only()   # ボタンを押したときは、そう伝える
    assert "画面なしで起動したよ" in app.status_var.get()


def test_pressing_the_engine_button_still_explains_failures(app, monkeypatch):
    import core
    import main as main_mod
    _no_engine_running(monkeypatch)
    shown = []
    monkeypatch.setattr(main_mod.messagebox, "showerror", lambda *a, **k: shown.append(a))

    def broken(**k):
        raise OSError("起動できない")
    monkeypatch.setattr(core, "launch_voicevox_engine", broken)
    app.launch_engine_only()
    assert shown and "起動できない" in shown[0][1]


def test_guide_language_follows_os(app):
    """使い方の案内は、パソコンの表示言語の版を開く（分からなければ日本語）。"""
    code = app._guide_code
    assert code("ja_JP") == "ja" and code("") == "ja" and code("C") == "ja"
    assert code("zh_TW") == "zh-TW" and code("zh-Hant-HK") == "zh-TW"
    assert code("zh_CN") == "zh-CN" and code("zh-Hans") == "zh-CN"
    assert code("en_US") == "en" and code("fr-FR") == "en"
    assert app._guide_lang() in ("ja", "en", "zh-TW", "zh-CN")


def test_open_guide_picks_language_then_falls_back(app, monkeypatch, tmp_path):
    """その言語の案内が無ければ日本語、それも無ければ GitHub の説明を開く。"""
    import webbrowser
    import main as main_mod
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url))
    monkeypatch.setattr(main_mod, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(app, "_guide_lang", lambda: "en")
    app._open_guide()
    assert opened[-1] == app._GUIDE_FALLBACK
    (tmp_path / "How to use (日本語).html").write_text("ja", encoding="utf-8")
    app._open_guide()
    assert opened[-1].startswith("file:") and "%E6%97%A5%E6%9C%AC%E8%AA%9E" in opened[-1]
    (tmp_path / "How to use (English).html").write_text("en", encoding="utf-8")
    app._open_guide()
    assert "English" in opened[-1]


def test_open_guide_prefers_the_manual_in_japanese(app, monkeypatch, tmp_path):
    """日本語のパソコンでは、画像入りの説明書（説明書.html）を案内より先に開く。
    英語のパソコンでは、英語の案内があればそちら（日本語の説明書は開かない）。"""
    import webbrowser
    import main as main_mod
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url))
    monkeypatch.setattr(main_mod, "APP_DIR", str(tmp_path))
    for name in ("How to use (日本語).html", "How to use (English).html", "説明書.html"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(app, "_guide_lang", lambda: "ja")
    app._open_guide()
    assert opened[-1].startswith("file:") and "%E8%AA%AC%E6%98%8E%E6%9B%B8" in opened[-1]   # 説明書
    assert "説明書" in app.status_var.get()
    monkeypatch.setattr(app, "_guide_lang", lambda: "en")
    app._open_guide()
    assert "English" in opened[-1]
    assert "案内" in app.status_var.get()


def test_open_guide_prefers_the_english_manual_in_english(app, monkeypatch, tmp_path):
    """英語のパソコンでは、英語の説明書（Manual (English).html）を英語の案内より先に開く。
    日本語のパソコンでは、英語の説明書があっても日本語の説明書。"""
    import webbrowser
    import main as main_mod
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url))
    monkeypatch.setattr(main_mod, "APP_DIR", str(tmp_path))
    for name in ("How to use (日本語).html", "How to use (English).html", "説明書.html",
                 "Manual (English).html"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(app, "_guide_lang", lambda: "en")
    app._open_guide()
    assert opened[-1].startswith("file:") and opened[-1].endswith("Manual%20%28English%29.html")
    assert "説明書" in app.status_var.get()
    monkeypatch.setattr(app, "_guide_lang", lambda: "ja")
    app._open_guide()
    assert "%E8%AA%AC%E6%98%8E%E6%9B%B8" in opened[-1]   # 説明書


def test_open_guide_finds_it_above_the_app_folder(app, monkeypatch, tmp_path):
    """Windows の zip では、案内は 起動.pyw の隣（app フォルダの1つ上）にある。
    エラーの記録などの言い方も「app の中の」になる。"""
    import webbrowser
    import main as main_mod
    opened = []
    top = tmp_path / "TextToVoicevox"
    (top / "app").mkdir(parents=True)
    (top / "起動.pyw").write_text("", encoding="utf-8")
    (top / "How to use (English).html").write_text("en", encoding="utf-8")
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url))
    monkeypatch.setattr(main_mod, "APP_DIR", str(top / "app"))
    monkeypatch.setattr(app, "_guide_lang", lambda: "en")
    app._open_guide()
    assert opened[-1].startswith("file:") and "English" in opened[-1]
    assert main_mod._in_app_folder("エラー.log") == "アプリのフォルダの「app」の中の「エラー.log」"
    monkeypatch.setattr(main_mod, "APP_DIR", str(tmp_path))
    assert main_mod._in_app_folder("エラー.log") == "アプリのフォルダの「エラー.log」"


def test_dark_title_bar_is_safe(app):
    """タイトルバーの色の切り替えは、どの OS でも例外を出さない（Windows 以外は何もしない）。"""
    import main
    for key in ("dark", "light"):
        app.theme_var.set(key)
        app.apply_theme()
    assert main._dark_title_bar(app, False) in (True, False)


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows のタイトルバー")
def test_dark_title_bar_waits_for_the_frame_window(app):
    """作った直後の小窓には、タイトルバーの付いた外枠の窓がまだない。外枠が
    できてから塗る（以前は中の窓に設定していて、小窓のタイトルバーが白いままだった）。"""
    import ctypes
    import time
    import main
    win = tk.Toplevel(app)
    win.withdraw()
    try:
        assert main._dark_title_bar(win, True) is False     # まだ外枠がない
        end = time.monotonic() + 3
        value = ctypes.c_int(-1)
        while time.monotonic() < end:
            app.update()
            frame = int(win.wm_frame(), 16)
            if frame != win.winfo_id() and ctypes.windll.dwmapi.DwmGetWindowAttribute(
                    frame, 20, ctypes.byref(value), 4) == 0 and value.value == 1:
                break
            time.sleep(0.02)
        if value.value == -1:
            pytest.skip("この Windows ではタイトルバーの色を読めない")
        assert value.value == 1
    finally:
        win.destroy()


def test_conn_text_wraps_as_if_side_scrollbar_were_hidden(app, monkeypatch):
    """接続の状態の文は、左の列のスクロールバーの分を引いた幅で折り返す。スクロール
    バーで列が広がった分だけ折り返しが減ると、接続欄が低くなってスクロールバーが消え、
    また出る…を繰り返し、窓の高さによっては画面が並び直し続けて固まった。
    スクロールバーはアプリ自身が窓の高さに合わせて出し入れする（Mac では、隠した直後に
    並び直しが走って、また出ることがあった）ので、出ている・いないはここで決めて確かめる。"""
    import types
    sb, lbl = app._steps_sb, app.engine_lbl
    monkeypatch.setattr(sb, "winfo_manager", lambda: "")
    app._wrap_engine_lbl(types.SimpleNamespace(width=300))
    alone = lbl.winfo_pixels(lbl.cget("wraplength"))
    assert alone == 300
    monkeypatch.setattr(sb, "winfo_manager", lambda: "pack")
    app._wrap_engine_lbl(types.SimpleNamespace(width=300 + sb.winfo_reqwidth()))
    assert lbl.winfo_pixels(lbl.cget("wraplength")) == alone


def test_drop_targets_are_set_up_after_the_window_shows(app, monkeypatch):
    """D&D の部品の読み込みと登録は、窓が出たあとにする（起動を速くするため）。
    読み込めない環境では D&D 無しで動き、一覧の案内も「ファイル追加」に替える。"""
    import main
    if not main._HAS_DND:
        pytest.skip("tkinterdnd2 が無い")
    assert app._dnd_ready is False and app._drop_after is not None
    app.after_cancel(app._drop_after)     # 予約の時刻を待たずに、ここで動かす
    app._register_drop_tree_later()
    assert app._dnd_ready is True
    assert app.listbox.bind("<<Drop>>") and app.text.bind("<<Drop>>")
    app._set_opt_rows(True)               # あとから作る ① の設定にも落とせる
    assert app._opt_panel.bind("<<Drop>>")
    # 部品を読み込めない環境
    app._dnd_ready = False

    def broken(_root):
        raise RuntimeError("Unable to load tkdnd library.")
    monkeypatch.setattr(main.TkinterDnD, "require", broken, raising=False)
    monkeypatch.setattr(main.TkinterDnD, "_require", broken)
    app._register_drop_tree_later()
    assert app._dnd_ready is False
    assert "ファイル追加" in app._list_hint.cget("text")


def test_guides_ship_with_the_app_and_stay_offline():
    """使い方の案内（4つの言語）がアプリと一緒にあり、ネットから何も読み込まない
    （このアプリは、入れたあとはネットにつながない）。"""
    import main
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in main.App._GUIDES.values():
        path = os.path.join(root, name)
        assert os.path.exists(path), name
        html = open(path, encoding="utf-8").read()
        assert "googleapis" not in html and "gstatic" not in html, name
        assert "<script src=" not in html, name


def test_docs_say_it_is_unofficial():
    """名前に VOICEVOX が入っているので、公式のソフトではない（非公式ツール）ことを、
    README（4つの言語）・使い方の案内（4つ）・はじめにお読みください.txt と、アプリ名の
    説明に書いておく。"""
    import main
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    marks = {"README.md": "非公式", "README.en.md": "unofficial",
             "README.zh-TW.md": "非官方", "README.zh-CN.md": "非官方",
             "はじめにお読みください.txt": "非公式"}
    words = {"ja": "非公式", "en": "unofficial", "zh-TW": "非官方", "zh-CN": "非官方"}
    marks.update({name: words[lang] for lang, name in main.App._GUIDES.items()})
    for name, word in marks.items():
        text = open(os.path.join(root, name), encoding="utf-8-sig").read()
        assert word in text.lower(), name
    assert "非公式" in main.App.UNOFFICIAL_NOTE


# ---------------- 誤読候補を順に見て回る（次の候補 ▷・F8） ----------------

def _seek_text(app, monkeypatch, lines, rubies_by_text):
    """本文を入れ、ふりがなの読みを（エンジンを使わずに）用意する。"""
    _furi_ready(app, monkeypatch, rubies_by_text)
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "\n".join(lines))
    app.text.mark_set("insert", "1.0")
    app.text.tag_remove("sel", "1.0", "end")
    app.furigana_var.set(True)


def _seek_wait(app, timeout=5):
    """探し終わるまで待つ（エンジンに聞いた読みは、裏の処理からキュー経由で届く）。"""
    import time
    end = time.monotonic() + timeout
    while app._seek is not None and time.monotonic() < end:
        app.update()
        time.sleep(0.01)
    return app._seek is None


def _selected(app):
    if not app.text.tag_ranges("sel"):
        return None
    return app.text.index("sel.first"), app.text.get("sel.first", "sel.last")


def test_seek_misreading_goes_forward_back_and_wraps(app, monkeypatch):
    """「次の候補 ▷」（F8）: 色つき（辞書と違う読み）の語を順に選ぶ。辞書と同じ
    読み・メモ行・空行は飛ばす。最後まで行ったら最初に戻る。Shift+F8 で前へ。"""
    import core
    lines = ["今日は晴れ。", "星が瞬き、鳩が鳴く。", "", "# メモ行", "東京へ行った。"]
    _seek_text(app, monkeypatch, lines, {
        "今日は晴れ。": [(0, 2, "きょう", core.FURI_OK)],
        "星が瞬き、鳩が鳴く。": [(0, 1, "ほし", core.FURI_OK), (2, 4, "シバタ", core.FURI_DIFF),
                             (5, 6, "バト", core.FURI_DIFF)],
        "東京へ行った。": [(0, 2, "とうきょう", core.FURI_OK), (3, 4, "イ", core.FURI_DIFF)],
    })
    assert app.bind_all("<F8>") and app.bind_all("<Shift-F8>")
    app.seek_misreading()
    assert _seek_wait(app) and _selected(app) == ("2.2", "瞬き")
    assert "瞬き" in app.status_var.get() and "シバタ" in app.status_var.get()
    app.seek_misreading()
    assert _seek_wait(app) and _selected(app) == ("2.5", "鳩")
    app.seek_misreading()
    assert _seek_wait(app) and _selected(app) == ("5.3", "行")
    app.seek_misreading()                 # 最後まで行ったので、最初から
    assert _seek_wait(app) and _selected(app) == ("2.2", "瞬き")
    assert "最初から" in app.status_var.get()
    app.seek_misreading(back=True)        # 前へ（最初まで行ったので、最後から）
    assert _seek_wait(app) and _selected(app) == ("5.3", "行")
    app.seek_misreading(back=True)
    assert _seek_wait(app) and _selected(app) == ("2.5", "鳩")


def test_seek_misreading_asks_engine_only_until_found(app, monkeypatch):
    """まだ読みを知らない行は、エンジンに少しずつ聞きながら先へ進む。見つかったら
    そこで止め、本の残りまでは聞かない。見つけた行は、本文欄の中に出す。"""
    import core
    import main
    app.geometry(f"{main._px(1100)}x{main._px(760)}")
    lines = [f"{i}行目の文です。" for i in range(1, 401)]
    lines[149] = "鳩が鳴く。"   # 150行目にだけ誤読候補
    asked = []

    def fake(base, text, sid, use_dictionary=True, timeout=15, read_fix=None):
        asked.append(text)
        return [(0, 1, "バト", core.FURI_DIFF)] if text == "鳩が鳴く。" else []
    _seek_text(app, monkeypatch, lines, {})
    monkeypatch.setattr(core, "vv_furigana", fake)
    app.seek_misreading()
    assert _seek_wait(app, 10) and _selected(app) == ("150.0", "鳩")
    assert "鳩が鳴く。" in asked
    assert "300行目の文です。" not in asked and "400行目の文です。" not in asked
    app.update()
    first = int(app.text.index("@0,0").split(".")[0])
    last = int(app.text.index(f"@0,{app.text.winfo_height()}").split(".")[0])
    assert first < 150 < last


def test_seek_misreading_explains_and_can_be_stopped(app, monkeypatch):
    """探せないときは理由を出す。探している途中にもう一度押すか Esc で、やめる
    （遅れて届いた読みでは動かない）。どこにも無ければ、そう出す。"""
    import time
    _seek_text(app, monkeypatch, ["星が瞬く。", "雨が降る。"], {})
    app.furigana_var.set(False)
    app.seek_misreading()
    assert app._seek is None and "ふりがな" in app.status_var.get()
    app.furigana_var.set(True)
    monkeypatch.setattr(app, "_furi_dict_installed", lambda: False)
    app.seek_misreading()
    assert app._seek is None and "Janome" in app.status_var.get()
    monkeypatch.setattr(app, "_furi_dict_installed", lambda: True)
    monkeypatch.setattr(app, "_engine_ready", lambda: False)
    app.seek_misreading()
    assert app._seek is None and "VOICEVOX" in app.status_var.get()
    monkeypatch.setattr(app, "_engine_ready", lambda: True)
    for stop in (app.seek_misreading, app._kb_escape):
        app._furi_cache.clear()           # 届いた読みは覚えるので、毎回まっさらにする
        app.seek_misreading()             # 知らない行なので、エンジンの答え待ちになる
        assert app._seek is not None
        stop()
        assert app._seek is None and "やめました" in app.status_var.get()
        for _ in range(30):               # 遅れて届いた答えは捨てる
            app.update()
            time.sleep(0.01)
        assert _selected(app) is None
    app.seek_misreading()                 # どちらの行も、辞書と違う読みが無い
    assert _seek_wait(app) and "見つかりませんでした" in app.status_var.get()


def test_next_misreading_button_sits_with_furigana_tools(app, monkeypatch):
    """「次の候補 ▷」は「誤読候補だけ」と「読み方辞書」のあいだ。狭い窓では、
    ふりがなの道具と一緒に本文の下の行へ移る。ふりがなを消しているあいだは押せない。"""
    import main
    for width in (1500, 860):
        app.geometry(f"{main._px(width)}x{main._px(720)}")
        _settle(app)
        group = (app.furigana_chk, app.furigana_only_chk, app.furi_next_btn, app.dict_btn)
        xs = [w.winfo_rootx() for w in group]
        assert xs == sorted(xs), (width, xs)
        assert len({str(w.pack_info()["in"]) for w in group}) == 1
        assert _not_clipped(app.furi_next_btn)
    monkeypatch.setattr(app, "_engine_ready", lambda: False)
    app.furigana_var.set(False)
    app._furi_refresh()
    assert app.furi_next_btn.instate(["disabled"])
    app.furigana_var.set(True)
    app._furi_refresh()
    assert app.furi_next_btn.instate(["!disabled"])


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="字の幅は Windows の字体で測る")
def test_furigana_tools_stay_above_text_at_default_width(app, monkeypatch):
    """いちばんよくある幅（既定の窓）では、VOICEVOX につながっていれば、ふりがなの道具は
    本文の上の行に並ぶ（ボタンを足したときに下の行へ押し出され、「ふりがなが消えた」と
    見えないように）。"""
    import main
    _layout_report(app)
    _furi_ready(app, monkeypatch, {})
    app.furigana_var.set(True)
    app._furi_refresh()                   # つながっているので「ふりがな」（短い方）
    assert app.furigana_chk.cget("text") == "ふりがな"
    app.geometry(f"{main._px(980)}x{main._px(760)}")
    _settle(app)
    assert app._rbar_level <= 1, _layout_widths(app)
    assert str(app.furi_next_btn.pack_info()["in"]) == str(app._rbar[1])


# ---------------- 上の帯のひとこと・② の声のキャラの顔 ----------------

def test_header_shows_motto_under_the_name(app):
    """上の帯のアプリ名の下に「非公式だけど無料、勉強頑張ってね」と出す。"""
    texts = [str(w.cget("text")) for w in _descendants(app._header)
             if w.winfo_class() == "TLabel"]
    assert "非公式だけど無料、勉強頑張ってね" in texts


def _png(color, size=256):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


def _until(app, cond, timeout=5):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def test_face_shows_the_selected_voice(app, monkeypatch):
    """② の顔は、いま選んでいる声のキャラ（スタイル）の公式イラスト。つながる前は
    人の形。スタイルを替えると顔も替わり、一度取った顔は覚えておく（聞き直さない）。
    切れたら人の形に戻る。顔を押すと声のサンプルを聴く（つながっているときだけ）。"""
    import core
    pytest.importorskip("PIL")
    asked = []

    def fake(base, uuid, style_id, timeout=10):
        asked.append(style_id)
        return _png((255, 0, 0, 255) if style_id == 3 else (0, 0, 255, 255))
    monkeypatch.setattr(core, "vv_style_icon", fake)
    monkeypatch.setattr(core, "vv_furigana", lambda *a, **k: [])

    def face():
        return str(app._face_lbl.cget("image"))
    blank = str(app._face_blank)
    assert face() == blank
    app.speakers = [("ずんだもん（ノーマル）", 3, "u"), ("ずんだもん（あまあま）", 1, "u")]
    app._build_char_map()
    app.char_cb.set("ずんだもん")
    app._char_selected()
    app._set_conn_state("ok", "0.26.0")
    assert _until(app, lambda: face() != blank)
    normal = face()
    assert app._face_cache[3].width() == app._face_px
    app.speaker_cb.current(1)
    app._update_portrait()
    assert _until(app, lambda: face() not in (blank, normal))
    app.speaker_cb.current(0)
    app._update_portrait()
    assert face() == normal and asked == [3, 1]
    played = []
    monkeypatch.setattr(app, "play_speaker_sample", lambda: played.append(True))
    app._face_clicked()
    assert played == [True]
    app._set_conn_state("lost")
    assert face() == blank
    app._face_clicked()
    assert played == [True]


def _fit_pump(app, sec=0.6):
    """窓の大きさを変えたあと、文字の大きさの合わせ直し（少し待ってから動く）まで回す。
    忙しいときに時間切れで先へ進まないよう、予約が残っていれば終わるまで待つ（5秒まで）。"""
    import time
    start = time.monotonic()
    while True:
        app.update()
        now = time.monotonic()
        if now - start >= 5.0 or (now - start >= sec and "fit_text" not in app._ticks):
            break
        time.sleep(0.02)


def _text_inner(app):
    """本文欄の、文字を並べられる幅と高さ（ピクセル）。"""
    t = app.text
    edge = (t.winfo_pixels(t.cget("borderwidth"))
            + t.winfo_pixels(t.cget("highlightthickness")))
    return (t.winfo_width() - 2 * (edge + t.winfo_pixels(t.cget("padx"))),
            t.winfo_height() - 2 * (edge + t.winfo_pixels(t.cget("pady"))))


def test_text_font_follows_window_width(app):
    """本文の文字は、窓を広げると大きくなり（1行がだいたい _FIT_CHARS 字）、窓を狭く
    戻すと元に戻る。狭い窓でも、選んだ大きさより小さくはしない。"""
    import main
    app.furigana_var.set(False)
    base = app._font_size0
    adv, _line = app._fit_metrics()

    def check():
        size = int(app.text_font.cget("size"))
        per_line = _text_inner(app)[0] / (adv * size)
        assert base <= size <= base * 2
        if base < size < base * 2:   # 上下の限りに当たっていなければ、1行はだいたい _FIT_CHARS 字
            assert abs(per_line - app._FIT_CHARS) < 3, per_line
        elif size == base:
            assert per_line <= app._FIT_CHARS + 3, per_line
        return size
    app.geometry(f"{main._px(860)}x{main._px(1000)}")
    _fit_pump(app)
    narrow = check()
    app.geometry(f"{main._px(1600)}x{main._px(1000)}")
    _fit_pump(app)                         # 窓を変えただけで（何も押さずに）合わせ直す
    wide = check()
    assert wide > narrow
    app.geometry(f"{main._px(860)}x{main._px(1000)}")
    _fit_pump(app)
    assert int(app.text_font.cget("size")) == narrow


def test_text_font_keeps_enough_lines_visible(app):
    """横に広くて低い窓では、本文欄に _FIT_MIN_LINES 行（ふりがなの場所も入れて）が
    見える大きさで止める。"""
    import main
    app.furigana_var.set(True)
    app.geometry(f"{main._px(1500)}x{main._px(600)}")
    _fit_pump(app)
    app._fit_text_font(force=True)
    size = int(app.text_font.cget("size"))
    _adv, line = app._fit_metrics()
    _w, h = _text_inner(app)
    pitch = line * size + line * max(7, round(size * 0.6)) + 1
    assert size == app._font_pref or h >= app._FIT_MIN_LINES * pitch


def test_text_font_manual_steps_and_auto_switch(app):
    """Ctrl+＋／－／0 は「選んだ大きさ」を変え、窓に合わせて足す分はその上に乗る。
    右クリックの「窓の幅に合わせて大きくする」を外すと、選んだ大きさのまま。"""
    import main
    app.furigana_var.set(False)
    base = app._font_size0
    app.geometry(f"{main._px(1500)}x{main._px(1000)}")
    _fit_pump(app)
    auto = int(app.text_font.cget("size"))
    assert auto > base
    app.change_font(+1)
    assert app._font_pref == base + 1
    assert int(app.text_font.cget("size")) == auto + 1
    assert "窓の幅に合わせて +" in app.status_var.get()
    app.change_font(0)
    assert app._font_pref == base and int(app.text_font.cget("size")) == auto
    labels = [app._font_menu.entrycget(i, "label")
              for i in range(app._font_menu.index("end") + 1)
              if app._font_menu.type(i) != "separator"]
    assert labels == ["大きくする", "小さくする", "元の大きさに戻す", "窓の幅に合わせて大きくする"]
    app._font_menu.invoke(len(labels))     # 「窓の幅に合わせて大きくする」を外す
    assert app.font_auto_var.get() is False
    assert int(app.text_font.cget("size")) == base
    app.change_font(+2)
    assert int(app.text_font.cget("size")) == base + 2
    d = app._settings_dict()
    assert d["font_size"] == base + 2 and d["font_auto"] is False
    app.font_auto_var.set(True)
    app._on_font_auto()
    assert int(app.text_font.cget("size")) == auto + 2


def test_text_font_ctrl_wheel(app):
    """本文の上で Ctrl を押しながらホイールを回すと、文字が大きく・小さくなる
    （本文はスクロールしない）。Tk 9 のホイールはマウスの下の部品に届くので、
    ここでは結び付けと、受け取ったときの動きを確かめる。"""
    class _Ev:
        delta = 120
    base = app._font_pref
    assert app.text.bind("<Control-MouseWheel>")
    assert app._on_ctrl_wheel(_Ev()) == "break"
    assert app._font_pref == base + 1
    _Ev.delta = -120
    app._on_ctrl_wheel(_Ev())
    app._on_ctrl_wheel(_Ev())
    assert app._font_pref == base - 1
    app.change_font(0)


def test_text_font_settings_load(app, tmp_path, monkeypatch):
    """選んだ大きさと「窓に合わせる」を覚えている。前の版の設定（font_auto なし）は
    窓に合わせる（最初からオン）。"""
    import json
    import main as main_mod
    p = tmp_path / "settings.json"
    monkeypatch.setattr(main_mod, "SETTINGS_PATH", str(p))
    p.write_text(json.dumps({"font_size": 14, "font_auto": False}), encoding="utf-8")
    app._load_settings()
    assert app._font_pref == 14 and app.font_auto_var.get() is False
    assert int(app.text_font.cget("size")) == 14
    p.write_text(json.dumps({"font_size": 12}), encoding="utf-8")
    app._load_settings()
    assert app._font_pref == 12 and app.font_auto_var.get() is True


def test_text_font_hint_once_after_user_resize(app):
    """窓の大きさで文字を変えたときは、変え方を1回だけ知らせる（起動の途中は黙って）。"""
    import main
    app.furigana_var.set(False)
    app.geometry(f"{main._px(860)}x{main._px(800)}")
    _fit_pump(app)
    app._fit_quiet_until = 0.0
    app._fit_hint_shown = False
    app.status_var.set("前の知らせ")
    app.geometry(f"{main._px(1500)}x{main._px(1000)}")
    _fit_pump(app)
    assert "右クリックの「文字の大きさ」" in app.status_var.get()
    app.status_var.set("前の知らせ")
    app.geometry(f"{main._px(860)}x{main._px(800)}")
    _fit_pump(app)
    assert app.status_var.get() == "前の知らせ"   # 2回目からは知らせない


def test_text_menu_has_font_size(app, monkeypatch):
    """本文の右クリックに「文字の大きさ」がある。"""
    import tkinter as tk

    class _Ev:
        x = y = 5
        x_root = y_root = 50
    monkeypatch.setattr(tk.Menu, "tk_popup", lambda self, *a: None)
    app._show_text_menu(_Ev())
    m = app._text_menu
    cascades = [m.entrycget(i, "label") for i in range(m.index("end") + 1)
                if m.type(i) == "cascade"]
    assert "文字の大きさ" in cascades


def _reading_text(app):
    """折り返す長い文と短い文がまざった、スクロールが要る本文を入れる。"""
    long_line = "遠くの山がかすんで見えた、" * 8 + "。"
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "\n".join(long_line if i % 3 == 0 else f"短い文の{i}行目です。"
                                     for i in range(200)))
    app.update()


def test_reading_line_stays_in_upper_third(app):
    """読んでいる行は、本文欄の上から3分の1あたりに置く。下に続きが見えて、折り返す文の
    終わりも窓の中に入る（前は see だけで、いちばん下に張り付いていた）。"""
    import main
    app.geometry(f"{main._px(860)}x{main._px(800)}")
    _fit_pump(app)
    _reading_text(app)
    t = app.text
    height = t.winfo_height()
    for ln in range(60, 90):
        app._follow_reading(ln, force=True)
        app.update_idletasks()
        y = t.dlineinfo(f"{ln}.0")[1]
        assert 0.15 * height <= y <= 0.45 * height, (ln, y, height)
        assert t.bbox(f"{ln}.end -1c") is not None, ln     # 文の終わりまで見える
        assert int(t.index(f"@0,{height - 2}").split(".")[0]) > ln   # 次の文も見える
    app._follow_reading(1, force=True)                     # 文書の頭では端で止まる
    app.update_idletasks()
    assert t.index("@0,0") == "1.0"
    app._follow_reading(200, force=True)                   # 文書の終わりも見える
    app.update_idletasks()
    assert t.bbox("200.end -1c") is not None


def test_reading_line_longer_than_window_starts_at_top(app):
    """窓に入りきらないほど長い文は、できるだけ多く見えるよう、頭を本文欄の上端に置く。"""
    import main
    app.geometry(f"{main._px(860)}x{main._px(800)}")
    _fit_pump(app)
    app.text.delete("1.0", "end")
    lines = [f"短い文の{i}行目です。" for i in range(60)]
    lines[30] = "とても長い文です、" * 150 + "。"
    app.text.insert("1.0", "\n".join(lines))
    app.update()
    app._follow_reading(31, force=True)
    app.update_idletasks()
    assert app.text.index("@0,0").split(".")[0] == "31"


def test_reading_follow_waits_after_user_scroll(app):
    """自分でスクロールした直後は、読んでいる行へ引き戻さない（少し待つと、また追う）。
    連続再生の知らせから動くこと、再生を押すと待たずに連れていくことも確かめる。"""
    import main
    app.geometry(f"{main._px(860)}x{main._px(800)}")
    _fit_pump(app)
    _reading_text(app)
    t = app.text
    assert t.bind("<MouseWheel>") and t.bind("<Next>")     # ホイールと PageDown を見ている
    app._follow_reading(100, force=True)
    app.update_idletasks()

    def top_line():
        return int(t.index("@0,0").split(".")[0])
    top = top_line()
    app._note_user_scroll()
    app._previewing = True
    app.q.put(("playall_line", 130, "短い文", 3, 100, 200, app._play_gen))
    app._poll_queue()
    app.update_idletasks()
    assert abs(top_line() - top) <= 1                      # 見ている所を奪わない
    app._user_scrolled_at -= app._FOLLOW_PAUSE_SEC + 1
    app.q.put(("playall_line", 131, "短い文", 3, 101, 200, app._play_gen))
    app._poll_queue()
    app.update_idletasks()
    assert top_line() > top + 10
    assert t.dlineinfo("131.0") is not None
    app._note_user_scroll()
    app._previewing = False
    app._play_jump(50)                                     # 自分で移ったときは待たない
    app.update_idletasks()
    assert t.dlineinfo("50.0") is not None


def test_reading_mode_folds_left_column(app, monkeypatch):
    """「◁ 広げる」（F11）で左の列を畳み、本文を広げて文字も大きくする。もう一度押すと
    元の場所に戻る。何も鳴っていないときは Esc でも戻り、再生中の Esc は止めるほうが先。"""
    import main
    app.furigana_var.set(False)
    app.geometry(f"{main._px(860)}x{main._px(1000)}")
    _fit_pump(app)
    col = app._steps_col
    before = {k: str(v) for k, v in col.pack_info().items()}
    width0, size0 = app.text.winfo_width(), int(app.text_font.cget("size"))
    assert app.bind_all("<F11>")
    # 左の列の欄に入力の焦点があれば、本文へ移す（隠れた欄に文字が入り続けないように）
    moved = []
    monkeypatch.setattr(app, "focus_get", lambda: app.gap_sb)
    monkeypatch.setattr(app.text, "focus_set", lambda: moved.append(True))
    app.read_btn.invoke()
    monkeypatch.undo()
    assert moved == [True]
    _fit_pump(app)
    assert app._reading and not col.winfo_manager()
    assert app.text.winfo_width() > width0
    assert int(app.text_font.cget("size")) >= size0
    assert app.read_btn.cget("text") == "▷ 戻す"
    app.read_btn.invoke()
    _fit_pump(app)
    assert col.winfo_manager() and {k: str(v) for k, v in col.pack_info().items()} == before
    slaves = col.master.pack_slaves()
    assert slaves.index(col) < slaves.index(app._text_col)   # 左の列のまま
    assert app.read_btn.cget("text") == "◁ 広げる"
    assert app.text.winfo_width() == width0
    app._set_reading(True)
    app._kb_escape()
    assert not app._reading                                  # 鳴っていなければ Esc で戻る
    app._set_reading(True)
    app._previewing = True
    stopped = []
    monkeypatch.setattr(app, "_kb_invoke", lambda btn: stopped.append(btn))
    app._kb_escape()
    assert app._reading and stopped == [app.stop_btn]        # 再生中は止めるのが先
    app._previewing = False
    app._set_reading(False)


def test_checkbox_marks_are_drawn_in_theme_colors(app):
    """チェックとラジオの印は自分で描く（× は日本語では「だめ」に見える。Tk 9 では色の
    指定も効かず、ダークでも白い四角だった）。テーマを変えると印の色も変わる。"""
    from tkinter import ttk

    def rgb(color):
        return tuple(v // 257 for v in app.winfo_rgb(color))

    def pixel(img, x, y):
        return tuple(int(v) for v in app.tk.splitlist(img.get(x, y)))

    def near(a, b):
        return all(abs(p - q) <= 2 for p, q in zip(a, b))

    style = ttk.Style(app)
    assert "Mark.check" in str(style.layout("TCheckbutton"))
    assert "Mark.radio" in str(style.layout("TRadiobutton"))
    size = app._mark_imgs["check", "off"].width()
    c = size // 2
    for key, _label, pal in app.THEMES:
        app.theme_var.set(key)
        app.apply_theme()
        imgs = app._mark_imgs
        assert near(pixel(imgs["check", "off"], c, c), rgb(pal["field"])), key
        # 付いているときの箱はテーマの主役の色で、✓ の折れ目は文字の色
        assert near(pixel(imgs["check", "on"], c, size // 5), rgb(pal["accent"])), key
        assert near(pixel(imgs["check", "on"], int(size * 0.43), int(size * 0.71)),
                    rgb(pal["accent_fg"])), key
        assert near(pixel(imgs["radio", "on"], c, c), rgb(pal["accent_fg"])), key
        assert near(pixel(imgs["radio", "off"], c, c), rgb(pal["field"])), key
    app.theme_var.set("light")
    app.apply_theme()


def test_stats_are_shortened_to_fit_and_full_in_tooltip(app):
    """行数・文字数・めやすは、置かれた場所に入る長さで出す。いちばん狭い窓でも、長い本の
    めやすの時間が切れない（全部は stats_var に残り、吹き出しで読める）。"""
    import main
    full = "12,345行・456,789字・めやす約25時間12分"
    app.geometry(f"{main._px(860)}x{main._px(800)}")
    _fit_pump(app)
    app.stats_var.set(full)
    # この試験の字は小さいので、狭い場所を作って確かめる
    app._fit_stats(app._text_width("12,345行・約25時間12分") + 1)
    assert app._stats_show.get() == "12,345行・約25時間12分"
    app._fit_stats(app._text_width("約25時間12分") + 1)
    assert app._stats_show.get() == "約25時間12分"
    app._fit_stats(1)
    assert app._stats_show.get() == "約25時間12分"        # 入らなくても時間は残す
    app._fit_stats(None)
    assert app._stats_show.get() == full
    assert app.stats_var.get() == full
    app.stats_var.set("")
    _fit_pump(app, 0.2)
    assert app._stats_show.get() == ""


def test_player_shows_time_left(app, monkeypatch):
    """再生バーに「あと◯分」を出す。読む行が進むと減り、段落の切れ目の長い間も数える。"""
    import core
    import main
    app.geometry(f"{main._px(1100)}x{main._px(800)}")
    _fit_pump(app)
    app.gap_var.set(0.4)
    app.para_pause_var.set(True)
    app.speed_var.set(1.0)
    line = "あ" * 3200                                     # 話速1.0で10分のめやす
    app.text.delete("1.0", "end")
    app.text.insert("1.0", f"{line}\n{line}\n\n{line}")
    numbered = [(1, line, 3), (2, line, 3), (4, line, 3)]
    app._index_play_lines(numbered)
    ten = core.estimate_read_seconds(line, 1.0)
    assert app._play_left == pytest.approx([3 * ten + 0.4 + 1.0, 2 * ten + 1.0, ten])
    app._set_position(1)
    assert app._pos_var.get() == "1 / 3行・あと約30分"
    app._set_position(4)
    assert app._pos_var.get() == "3 / 3行・あと約10分"
    # 入らないときは、あと◯分だけにする（全部は _pos_var に残る）
    monkeypatch.setattr(app, "_text_width", lambda text: 10 ** 6)
    app._fit_position()
    assert app._pos_show.get() == "あと約10分"
    monkeypatch.undo()
    app._fit_position()
    assert app._pos_show.get() == "3 / 3行・あと約10分"
    app.para_pause_var.set(False)
    app._index_play_lines(numbered)
    assert app._play_left[0] == pytest.approx(3 * ten + 0.8)
    app.para_pause_var.set(True)


def _contrast(a, b):
    """色の読みやすさの比（WCAG。文字は 4.5 以上がめやす）。"""
    def lum(c):
        r, g, b_ = (int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))

        def f(v):
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b_)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_default_theme_is_lively_but_readable(app):
    """既定の「空と落ち葉」は、色を付けても文字が読める（読むのが苦手な人のアプリなので）。"""
    p = app.LIGHT
    for fg, bg in ((p["fg"], p["textbg"]), (p["fg"], p["bg"]), (p["fg"], p["act"]),
                   (p["head_fg"], p["bg"]), (p["subtle"], p["bg"]), (p["tool"], p["bg"]),
                   (p["accent_fg"], p["accent"]), (p["ground_fg"], p["ground"]),
                   (p["ground_title"], p["ground"]), (p["ground_sub"], p["ground"]),
                   (p["ground_link"], p["ground"]), (p["caption_fg"], p["caption"])):
        assert _contrast(fg, bg) >= 4.5, (fg, bg)
    for color in p["steps"]:
        assert _contrast(color, p["bg"]) >= 4.5, color
    assert app._FURI_COLORS_DEFAULT[0] and _contrast(app._FURI_COLORS_DEFAULT[0], p["textbg"]) >= 4.5


def test_step_headings_have_their_own_colors(app):
    """① ② ③ の見出しは、テーマの決めた色で、それぞれ違う色にする。無いテーマは見出しの色。"""
    from tkinter import ttk
    style = ttk.Style(app)
    heads = {str(w.cget("text"))[0]: w for w in _descendants(app._steps)
             if w.winfo_class() == "TLabel" and str(w.cget("text"))[:1] in "①②③"}
    assert set(heads) == {"①", "②", "③"}
    for key, _label, pal in app.THEMES:
        app.theme_var.set(key)
        app.apply_theme()
        for i, mark in enumerate("①②③"):
            want = pal["steps"][i] if "steps" in pal else pal["head_fg"]
            assert str(style.lookup(str(heads[mark].cget("style")), "foreground")) == want
    app.theme_var.set("light")
    app.apply_theme()
    assert len({str(style.lookup(str(w.cget("style")), "foreground")) for w in heads.values()}) == 3


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows のタイトルバー")
def test_title_bar_follows_theme_colors(app, monkeypatch):
    """タイトルバーは、色を決めたテーマではその色に、ほかのテーマでは OS の色に戻す。"""
    import ctypes
    import main
    _settle(app)
    calls = {}

    class _Dwm:
        @staticmethod
        def DwmSetWindowAttribute(hwnd, attr, ref, size):
            calls[attr] = ref._obj.value
            return 0
    monkeypatch.setattr(ctypes.windll, "dwmapi", _Dwm)
    app.theme_var.set("light")
    app.apply_theme()
    app.update()
    assert main._dark_title_bar(app, False) is True
    assert calls[35] == main._colorref(app.LIGHT["caption"])
    assert calls[36] == main._colorref(app.LIGHT["caption_fg"])
    assert main._colorref("#123456") == 0x563412
    # ずんだ・くっきりも、帯と同じ色にする（前は OS の白のままで、帯の上で浮いて見えた）
    for key, pal in (("zunda", app.ZUNDA), ("hc", app.HC)):
        app.theme_var.set(key)
        app.apply_theme()
        assert main._dark_title_bar(app, False) is True
        assert calls[35] == main._colorref(pal["caption"])
        assert calls[36] == main._colorref(pal["caption_fg"])
    # 色を決めていないテーマ（ダーク）は OS の色に戻す（暗さは別の設定で伝える）
    app.theme_var.set("dark")
    app.apply_theme()
    assert main._dark_title_bar(app, True) is True
    assert calls[35] == 0xFFFFFFFF and calls[36] == 0xFFFFFFFF
    for _key, _label, pal in app.THEMES:
        if "caption" in pal:
            assert pal["caption"] == pal["ground"]   # 帯と同じ色
            assert _contrast(pal["caption_fg"], pal["caption"]) >= 4.5
    assert _contrast(app._FURI_COLORS["dark"][0], app.DARK["textbg"]) >= 7   # 小さい字なので高めに
    app.theme_var.set("light")
    app.apply_theme()


# ---- タイトル・思いがけないエラー・段落の字下げ・新しい版のお知らせ（2026-11-06） ----

def test_title_is_the_app_name_without_offline(app):
    """窓の名前はアイコン・見出しと同じ TextToVoicevox。前は「テキスト抽出 → VOICEVOX
    vX.Y.Z（オフライン）」で、つながっていても「オフライン」と出ていた。"""
    import core
    assert app.title() == f"TextToVoicevox v{core.APP_VERSION}"


def test_unexpected_error_in_a_button_is_logged_and_shown_once(app, monkeypatch):
    """pythonw には黒い画面が無いので、ボタンの中のエラーは見えも残りもしなかった。
    エラー.log に残して知らせる。続けて起きても、窓は30秒に1回だけ（記録は毎回）。"""
    shown, logged = [], []
    monkeypatch.setattr(app, "_show_friendly_error",
                        lambda tb, where="", lead="": shown.append((tb, where)))
    monkeypatch.setattr(app, "_log_error", lambda tb, where="": logged.append((tb, where)))

    def boom():
        raise ZeroDivisionError("テスト")
    for _ in range(2):
        try:
            boom()
        except ZeroDivisionError:
            app.report_callback_exception(*sys.exc_info())
    assert len(shown) == 1 and "ZeroDivisionError" in shown[0][0] and "boom" in shown[0][1]
    assert len(logged) == 1, "2回目は窓を出さず、記録だけ"


def test_unexpected_error_in_a_thread_is_shown_from_the_window(app, monkeypatch):
    """裏の処理のエラーは、画面のスレッド（キュー）を通して知らせる（Tk は別スレッドから触れない）。"""
    import threading
    shown = []
    monkeypatch.setattr(app, "_show_friendly_error",
                        lambda tb, where="", lead="": shown.append(where))

    def boom():
        raise RuntimeError("裏で")
    monkeypatch.setattr(threading, "excepthook", app._thread_exception)
    t = threading.Thread(target=boom, name="テスト用の処理")
    t.start()
    t.join()
    app._poll_queue()
    assert shown and "テスト用の処理" in shown[0]


def test_playback_pauses_longer_at_indented_paragraphs(app):
    """行頭の字下げ（段落の頭）でも、連続再生の段落の間を長めにする。読む文には空白を入れない。"""
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "　吾輩は猫である。\n名前はまだ無い。\n　どこで生れたか。")
    numbered = app._speakable_lines()
    assert [t[1] for t in numbered] == ["吾輩は猫である。", "名前はまだ無い。", "どこで生れたか。"]
    assert app._play_kinds(numbered) == ["", "", "para"]


def test_voicevox_text_drops_paragraph_indents(app, monkeypatch, tmp_path):
    """VOICEVOX 用の .txt では、段落の頭の全角スペースを外す（ブロックの頭の余計な空白になる）。"""
    from tkinter import filedialog, messagebox
    out = tmp_path / "v.txt"
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **k: str(out))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "第一章\n　吾輩は猫である。\n名前はまだ無い。")
    app.save_txt()
    assert out.read_text(encoding="utf-8") == "第一章\n吾輩は猫である。\n名前はまだ無い。"


def test_newer_release_shows_the_notice(app):
    """見つけた版が今より新しいときだけ、上の帯に「新しい版があります」を出し、設定に覚える。"""
    assert not app._update_lbl.winfo_manager()
    app._dispatch_msg(("update", "v1.0.0", "https://example.invalid/old"))
    assert not app._update_lbl.winfo_manager(), "古い版では出さない"
    app._dispatch_msg(("update", "v99.0.0", "https://example.invalid/new"))
    assert app._update_lbl.winfo_manager() == "pack"
    s = app._settings_dict()
    assert s["update_latest"] == "v99.0.0" and s["update_url"] == "https://example.invalid/new"
    assert s["update_check"] is True and s["update_checked_at"] > 0


def test_update_is_asked_in_the_background_at_most_daily(app, monkeypatch):
    import time
    import core
    calls = []
    monkeypatch.setattr(core, "install_kind", lambda d: "zip")
    monkeypatch.setattr(core, "fetch_latest_release",
                        lambda: calls.append(1) or ("v99.0.0", "https://example.invalid/r"))
    app._update_checked_at = 0
    app._check_for_update(in_tests=False)
    for _ in range(100):
        if not app.q.empty():
            break
        time.sleep(0.02)
    app._poll_queue()
    assert calls == [1] and app._update_lbl.winfo_manager() == "pack"
    app._check_for_update(in_tests=False)   # 1日たっていないので聞かない
    time.sleep(0.1)
    assert calls == [1]
    app._update_check_on = False            # 「接続設定…」で切ったら聞かない
    app._update_checked_at = 0
    app._check_for_update(in_tests=False)
    time.sleep(0.1)
    assert calls == [1]


def test_no_update_notice_for_a_git_checkout_or_in_tests(app, monkeypatch):
    import core
    monkeypatch.setattr(core, "fetch_latest_release", lambda: pytest.fail("聞いた"))
    app._check_for_update()                 # テスト中は聞かない
    monkeypatch.setattr(core, "install_kind", lambda d: "dev")
    app._check_for_update(in_tests=False)   # GitHub から取ってきた中身には知らせない


def _texts(w):
    out = []
    for c in w.winfo_children():
        try:
            out.append(c.cget("text"))
        except tk.TclError:
            pass
        out += _texts(c)
    return out


@pytest.mark.parametrize("kind, buttons", [
    ("oneline", {"更新する", "変更点を見る", "あとで"}),
    ("zip", {"ダウンロードのページを開く", "あとで"}),
    ("mac", {"ダウンロードのページを開く", "1行をコピー", "あとで"})])
def test_update_dialog_offers_the_way_that_fits(app, monkeypatch, kind, buttons):
    """1行導入で入れた人は押すだけ。zip の人は新しい zip を上書き。Mac はターミナルの1行。"""
    import core
    monkeypatch.setattr(core, "install_kind", lambda d: kind)
    app._update_latest = "v99.0.0"
    before = set(app.winfo_children())
    app._open_update()
    win = [w for w in app.winfo_children() if w not in before][-1]
    got = _texts(win)
    assert buttons <= set(got)
    assert any("v99.0.0" in t for t in got)
    win.destroy()


def test_update_button_reruns_the_installer_and_closes(app, monkeypatch):
    """「更新する」は1行導入を黒い窓で動かして、このアプリを閉じる（開いたままでは入れ替えられない）。"""
    import subprocess
    import main
    started, closed = [], []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: started.append((args, kw)))
    monkeypatch.setattr(app, "_on_close", lambda: closed.append(1))
    app._run_update()
    args, kw = started[0]
    assert args[0] == "powershell.exe" and args[-1] == main.core.INSTALL_ONE_LINER
    assert kw["env"]["T2V_DIR"] == main.APP_DIR and kw["env"]["T2V_FROM_APP"] == "1"
    assert closed == [1]


def test_update_button_waits_while_busy(app, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("始めた"))
    app._set_busy(True)
    try:
        app._run_update()
        assert "処理・再生" in app.status_var.get()
    finally:
        app._set_busy(False)


def test_update_setting_is_remembered(app, monkeypatch, tmp_path):
    import json
    import main as main_mod
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"update_check": False, "update_checked_at": 123.0,
                             "update_latest": "v9.9.9"}), encoding="utf-8")
    monkeypatch.setattr(main_mod, "SETTINGS_PATH", str(p))
    app._load_settings()
    assert app._update_check_on is False and app.update_check_var.get() is False
    assert app._update_checked_at == 123.0 and app._update_latest == "v9.9.9"


def test_guides_are_found_after_the_launcher_is_tucked(app, monkeypatch, tmp_path):
    """起動.pyw を app の中にしまったあとも、いちばん上（案内のある所）をショートカットで見分ける。"""
    import main as main_mod
    top = tmp_path / "TextToVoicevox"
    (top / "app").mkdir(parents=True)
    (top / "TextToVoicevox.lnk").write_bytes(b"")
    monkeypatch.setattr(main_mod, "APP_DIR", str(top / "app"))
    assert main_mod._top_dir() == str(top)


def test_save_starts_as_one_file(app):
    """保存のまとめ方は、最初は「全部を1つに」（本を聴くならこれ）。"""
    assert app._unit() == "combine"
    assert app.unit_cb.get() == "全部を1つに"
    assert list(app._UNITS.values()) == ["全部を1つに", "1行=1ファイル", "行数ごと", "段落ごと"]


def test_many_files_are_asked_before_saving(app, monkeypatch, tmp_path):
    """ファイルが300個を超えるときは、作る前に聞く。「1つにまとめる」なら全部を1つにし、
    「やめる」なら何もしない。「このまま」ならそのまま作る。少ないときは聞かない。"""
    from tkinter import filedialog
    spawned, asked = [], []
    monkeypatch.setattr(app, "_current_speaker", lambda: ("ずんだもん", 3, "u"))
    monkeypatch.setattr(app, "_confirm_speaker_tags", lambda: True)
    monkeypatch.setattr(app, "_spawn", lambda target, args=(): spawned.append(args))
    monkeypatch.setattr(filedialog, "asksaveasfilename",
                        lambda **kw: str(tmp_path / "o.wav"))
    monkeypatch.setattr(filedialog, "askdirectory", lambda **kw: str(tmp_path))
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "\n".join(f"{i}番目の文です。" for i in range(1, 402)))
    each = list(app._UNITS).index("each")

    def answer(v):
        def ask(n):
            asked.append(n)
            return v
        return ask

    def done():
        app._set_busy(False)
        app._synth_restore_button()

    app.unit_cb.current(each)
    app._on_unit_selected()
    monkeypatch.setattr(app, "_ask_many_files", answer(None))
    app.start_synth()
    assert asked == [401] and spawned == [] and not app.busy
    monkeypatch.setattr(app, "_ask_many_files", answer("combine"))
    app.start_synth()
    assert spawned[-1][4] == "combine" and len(spawned[-1][1]) == 1
    assert app._unit() == "combine"          # まとめ方の欄も「全部を1つに」になる
    done()
    app.unit_cb.current(each)
    app._on_unit_selected()
    monkeypatch.setattr(app, "_ask_many_files", answer("keep"))
    app.start_synth()
    assert spawned[-1][4] == "each" and len(spawned[-1][1]) == 401
    done()
    asked.clear()
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "一文目。\n二文目。")
    app.start_synth()
    assert asked == [] and spawned[-1][4] == "each"
    done()


def test_many_files_dialog_buttons(app):
    """聞く窓のボタン: 「1つにまとめる」「このまま ◯個 作る」「やめる」。"""
    def press(part):
        def go():
            for w in app.winfo_children():
                if isinstance(w, tk.Toplevel) and w.title() == "ファイルの数":
                    for b in _descendants(w):
                        if b.winfo_class() == "TButton" and part in str(b.cget("text")):
                            b.invoke()
                            return
            app.after(20, go)
        app.after(20, go)
    press("1つにまとめる")
    assert app._ask_many_files(20874) == "combine"
    press("このまま 20,874個 作る")
    assert app._ask_many_files(20874) == "keep"
    press("やめる")
    assert app._ask_many_files(20874) is None


def test_read_fix_is_on_and_saved(app):
    """数字・日付・記号の読み方の直しは、最初からオン。切り替えると core に伝わり、
    設定に残り、ふりがなも読み直す（覚えた読みの名札に入っている）。"""
    import core
    assert app.read_fix_var.get() is True and core.read_fix_on() is True
    try:
        app.read_fix_var.set(False)
        assert core.read_fix_on() is False
        assert app._settings_dict()["read_fix"] is False
        assert app._furi_keyinfo()[-1] is False
    finally:
        app.read_fix_var.set(True)
    assert core.read_fix_on() is True and app._furi_keyinfo()[-1] is True


def test_dictionary_window_words(app, monkeypatch):
    """読み方辞書の窓: いちばん上に読み方の直しのオン・オフ。項目はふだんの言葉で
    （下がる所・種類・優先）、半角カナは使わない。"""
    monkeypatch.setattr(app, "_dict_refresh", lambda: None)
    app.open_dict_dialog()
    try:
        win = app._dict_win
        texts = [str(w.cget("text")) for w in _descendants(win)
                 if w.winfo_class() in ("TLabel", "TButton", "TCheckbutton")]
        assert "数字・日付・記号の読み方を直す（おすすめ）" in texts
        assert {"下がる所:", "種類:", "優先:", "登録する", "読み直す"} <= set(texts)
        heads = [app._dict_tree.heading(c)["text"] for c in ("accent", "wtype", "prio")]
        assert heads == ["下がる所", "種類", "優先"]
        for t in texts + heads:
            assert not any(w in t for w in ("ｱｸｾﾝﾄ", "アクセント核", "品詞", "優先度", "話者")), t
    finally:
        app._dict_win.destroy()


def test_voicevox_txt_gets_fixed_readings(app, monkeypatch, tmp_path):
    """VOICEVOX 用の .txt には、読み方を直した文を書く（VOICEVOX はそのまま読むので）。
    本文の字は変えない。直しを切ると、本文のまま書く。"""
    from tkinter import filedialog, messagebox
    out = tmp_path / "v.txt"
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "　気温は25℃。\n10:30に集合。")
    app.save_txt()
    assert out.read_text(encoding="utf-8") == "気温は25度。\n10時30分に集合。"
    assert app.text.get("1.0", "end-1c") == "　気温は25℃。\n10:30に集合。"
    try:
        app.read_fix_var.set(False)
        app.save_txt()
        assert out.read_text(encoding="utf-8") == "気温は25℃。\n10:30に集合。"
    finally:
        app.read_fix_var.set(True)


def test_preview_uses_fixed_text(app, monkeypatch):
    """▶試聴も、読み方を直した文で合成する。しまう声の名札もその文で作る
    （直しを切り替えたとき、前の読みの声が出てこない。連続再生・保存と同じ名札）。"""
    import threading
    import core
    seen = {}
    monkeypatch.setattr(core, "vv_dict_hash", lambda base: "h")
    monkeypatch.setattr(core, "synth_cache_get", lambda key: None)
    monkeypatch.setattr(core, "synth_cache_put", lambda key, wav: seen.setdefault("put", key))

    def synth(base, text, sid, read_fix=None, **voice):
        seen["text"], seen["fix"] = text, read_fix
        return b"wav", "キオンワニジュウゴド"
    monkeypatch.setattr(core, "vv_synthesize_with_kana", synth)
    monkeypatch.setattr(core, "play_wav_blocking", lambda wb, stop_event=None: None)
    app._engine_ver = "0.26.1"
    voice = {"speed": 1.0, "pitch": 0.0, "intonation": 1.0, "volume": 1.0}
    app._preview_worker("気温は25℃。", 3, voice, 1, threading.Event(), app._play_gen)
    assert seen["text"] == "気温は25度。" and seen["fix"] is False
    assert seen["put"] == core.synth_cache_key("気温は25度。", 3, engine_ver="0.26.1",
                                               dict_hash="h", **voice)


def test_long_number_reading_gives_way_to_kanji(app, monkeypatch):
    """日付や時刻の読み（数字の上のカタカナ）は長いので、となりの漢字のふりがなと重なる
    ときは出さない（重なると両方とも読めない）。重ならないときは出す。"""
    import core
    import main
    lines = ["2026/11/7に出す。", "10:30から、ゆっくり待つ"]
    _furi_ready(app, monkeypatch, {
        lines[0]: [(0, 9, "ニセンニジュウロクネンジュウイチガツナノカ", core.FURI_RAW),
                   (10, 11, "だ", core.FURI_OK)],
        lines[1]: [(0, 5, "ジュウジサンジュップン", core.FURI_RAW),
                   (12, 13, "ま", core.FURI_OK)]})
    app.geometry(f"{main._px(1100)}x{main._px(760)}")
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "\n".join(lines))
    app.furigana_var.set(True)
    app._toggle_furigana()
    shown = lambda: {lab.cget("text") for lab in app._furi_labels if lab.winfo_manager()}
    assert _furi_pump(app, lambda: {"だ", "ま"} <= shown()), "ふりがなが出ない"
    app.update()
    texts = shown()
    assert "ニセンニジュウロクネンジュウイチガツナノカ" not in texts   # 「だ」と重なる
    assert "ジュウジサンジュップン" in texts                           # 「ま」までは届かない
    app.furigana_var.set(False)
    app._toggle_furigana()


class _FakeEngine:
    """GPU で起こしたことにする、にせの VOICEVOX（すぐ止まる・動き続ける、を選べる）。"""

    def __init__(self, uses_gpu, alive=True, run_path="C:/vv/run.exe"):
        self.uses_gpu, self.run_path, self._alive = uses_gpu, run_path, alive

    def poll(self):
        return None if self._alive else 1


def test_gpu_used_when_available_and_falls_back(app, monkeypatch):
    """「GPU があれば使う」（最初からオン）なら、GPU を使える版と強い GPU があるときに
    GPU で起こす。GPU で起こしたらすぐ止まったときは、CPU で起こし直し、次からも CPU。"""
    import core
    assert app.engine_gpu_var.get() is True and app._engine_gpu is True
    monkeypatch.setattr(core, "find_voicevox_engine", lambda user_path=None: "C:/vv/run.exe")
    monkeypatch.setattr(core, "want_gpu", lambda run, sizes=None: True)
    monkeypatch.setattr(core, "vv_probe", lambda *a, **k: {"ok": False, "status": "refused"})
    monkeypatch.setattr(core, "stop_process", lambda proc, timeout=5: None)
    monkeypatch.setattr(app, "_save_settings", lambda *a, **k: None)
    monkeypatch.setattr(app, "_start_connect_retry", lambda *a, **k: None)
    asked = []

    def launch(port=50021, use_gpu=False, user_path=None, host="127.0.0.1"):
        asked.append(use_gpu)
        return _FakeEngine(bool(use_gpu), alive=not use_gpu), f"http://127.0.0.1:{port}"
    monkeypatch.setattr(core, "launch_voicevox_engine", launch)
    app._engine_proc = None
    app.launch_engine_only(quiet=True)
    assert asked == [True]
    app._connect_retry_tick()            # GPU のほうは、つながる前に止まった
    assert asked == [True, False]
    assert app._engine_gpu_failed == "C:/vv/run.exe" and app._engine_proc.uses_gpu is False
    assert app._settings_dict()["engine_gpu_failed"] == "C:/vv/run.exe"
    app._engine_proc = None
    app.launch_engine_only(quiet=True)   # 次からは CPU
    assert asked[-1] is False
    app._set_engine_gpu(False)           # 切ったら CPU。付け直したら、もう一度 GPU を試す
    assert app._gpu_choice() is False and app._settings_dict()["engine_gpu"] is False
    app._set_engine_gpu(True)
    assert app._gpu_choice() is True and app._engine_gpu_failed == ""
    app._engine_proc = None


def test_connected_label_shows_gpu(app, monkeypatch):
    """GPU で起こした VOICEVOX につながっているときは、左下に「GPU」と出す。"""
    app._engine_proc = _FakeEngine(True)
    try:
        app._set_conn_state("ok", "0.26.1")
        assert app.engine_var.get() == "● 接続OK (v0.26.1・GPU)"
        app._engine_proc = None
        app._set_conn_state("ok", "0.26.1")
        assert app.engine_var.get() == "● 接続OK (v0.26.1)"
    finally:
        app._engine_proc = None
        app._set_conn_state("refused")


def test_pdf_heads_option_and_report(app, monkeypatch):
    """① の「PDF のページの見出しと番号を読まない」（最初からオン）を取り出しに渡し、
    外した行は「直した所」で見られる。"""
    import core
    assert app.heads_var.get() is True and app._settings_dict()["strip_heads"] is True
    seen = {}

    def fake(paths, **kw):
        seen.update(kw)
        kw["report"]["heads"] = ["吾輩は猫である", "12"]
        return "吾輩は猫である。", []
    monkeypatch.setattr(core, "extract_files", fake)
    app.files[:] = ["book.pdf"]
    app._extract_worker(dict(paths=["book.pdf"], strip_heads=app.heads_var.get()), {})
    msg = app.q.get_nowait()
    while msg[0] != "extract_done":
        msg = app.q.get_nowait()
    app._dispatch_msg(msg)
    assert seen["strip_heads"] is True
    assert "ページの見出し・番号2行" in app.status_var.get()
    assert str(app.report_btn["state"]) == "normal"
    app.show_shape_report()
    body = [w for w in _descendants(app._report_win) if w.winfo_class() == "Text"][0]
    assert "ページの見出し（柱）・ページ番号として外した行（2行）" in body.get("1.0", "end")
    app._report_win.destroy()
    app.files[:] = []


class _DeadEngine:
    def __init__(self, uses_gpu=False, run_path="C:/vv/run.exe", alive=False):
        self.uses_gpu, self.run_path, self._alive = uses_gpu, run_path, alive

    def poll(self):
        return None if self._alive else 1


def test_engine_restarts_when_it_stops(app, monkeypatch):
    """自分で起こした VOICEVOX が使っている途中で止まったら、自動で起こし直す。
    GPU のものが続けて止まったら、閉じるまでは CPU で（設定には書かない）。
    10分に3回を超えたら、起こし直すのをやめて知らせる。"""
    import core
    monkeypatch.setattr(core, "find_voicevox_engine", lambda user_path=None: "C:/vv/run.exe")
    monkeypatch.setattr(core, "want_gpu", lambda run, sizes=None: True)
    monkeypatch.setattr(core, "vv_probe", lambda *a, **k: {"ok": False, "status": "refused"})
    monkeypatch.setattr(app, "_save_settings", lambda *a, **k: None)
    monkeypatch.setattr(app, "_start_connect_retry", lambda *a, **k: None)
    started = []

    def launch(port=50021, use_gpu=False, user_path=None, host="127.0.0.1"):
        started.append(bool(use_gpu))
        return _DeadEngine(uses_gpu=bool(use_gpu), alive=True), f"http://127.0.0.1:{port}"
    monkeypatch.setattr(core, "launch_voicevox_engine", launch)
    app._engine_deaths = []
    app._engine_gpu_failed = ""
    app._engine_gpu_session_off = ""
    app._engine_proc = _DeadEngine(uses_gpu=True)            # 1回目: GPU のまま起こし直す
    assert app._check_engine_alive() is True
    assert started == [True] and "起動し直しています" in app.status_var.get()
    assert app._check_engine_alive() is False                # 動いている間は何もしない
    app._engine_proc = _DeadEngine(uses_gpu=True)            # 2回目: 閉じるまで CPU
    app._check_engine_alive()
    assert started == [True, False] and app._engine_gpu_session_off == "C:/vv/run.exe"
    assert app._engine_gpu_failed == "" and app._settings_dict()["engine_gpu_failed"] == ""
    app._engine_proc = _DeadEngine()                          # 3回目: まだ起こし直す
    app._check_engine_alive()
    assert started == [True, False, False]
    app._engine_proc = _DeadEngine()                          # 4回目: やめて知らせる
    app._check_engine_alive()
    assert started == [True, False, False] and app._engine_proc is None
    assert "起動し直すのをやめました" in app.status_var.get()
    app._engine_deaths = []
    app._engine_gpu_failed = ""
    app._engine_gpu_session_off = ""


def test_health_check_notices_dead_engine_even_when_not_connected(app, monkeypatch):
    """接続が切れたあとでも、自分で起こした VOICEVOX が止まっていれば起こし直す。"""
    seen = []
    monkeypatch.setattr(app, "_engine_died", lambda: seen.append(True))
    app._engine_proc = _DeadEngine()
    app._set_conn_state("lost")
    app._health_tick()
    assert seen == [True]
    app._engine_proc = None


def test_health_check_restarts_dead_engine_even_while_playing(app, monkeypatch):
    """再生中でも、自分で起こした VOICEVOX が止まっていれば、すぐ起こし直す。"""
    seen = []
    monkeypatch.setattr(app, "_engine_died", lambda: seen.append(True))
    app._engine_proc = _DeadEngine()
    app._previewing = True
    try:
        app._health_tick()
    finally:
        app._previewing = False
        app._engine_proc = None
    assert seen == [True]


def test_play_synth_waits_for_restarted_engine(app, monkeypatch):
    """連続再生の1行が「つながらない」で失敗したら、VOICEVOX が戻るのを待って、もう一度だけ頼む。
    つながらない以外の失敗（文がおかしい等）は、待たずに飛ばす。"""
    import threading
    import requests
    import core
    calls, probes = [], []

    def synth(base, text, sid, **kw):
        calls.append(text)
        if len(calls) == 1:
            raise requests.ConnectionError("refused")
        return b"wav"
    monkeypatch.setattr(core, "vv_synthesize_cached", synth)

    def probe(url, timeout=1):
        probes.append(url)
        return {"ok": len(probes) >= 2}
    monkeypatch.setattr(core, "vv_probe", probe)
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda s: None)   # 待つ1秒を待たない
    voice = {"speed": 1.0, "pitch": 0.0, "intonation": 1.0, "volume": 1.0}
    tb = [None]
    stop = threading.Event()
    assert app._play_synth("文。", 3, voice, lambda: "h", stop, tb) == b"wav"
    assert calls == ["文。", "文。"] and len(probes) == 2

    def bad(base, text, sid, **kw):
        raise ValueError("422")
    monkeypatch.setattr(core, "vv_synthesize_cached", bad)
    probes.clear()
    assert app._play_synth("文。", 3, voice, lambda: "h", stop, tb) is None and probes == []
    stop.set()                                    # 止められたら待たない
    monkeypatch.setattr(core, "vv_synthesize_cached", synth)
    calls.clear()
    assert app._play_synth("文。", 3, voice, lambda: "h", stop, tb) is None


def test_close_stops_the_reading_helper(app, monkeypatch):
    """閉じるとき、文字を読む裏方も止める。"""
    import core
    seen = []
    monkeypatch.setattr(core, "strong_ocr_shutdown", lambda: seen.append(True))
    monkeypatch.setattr(app, "_save_settings", lambda *a, **k: None)
    monkeypatch.setattr(app, "destroy", lambda: None)
    app._on_close()
    assert seen == [True]


def test_connect_retry_waits_while_playing(app, monkeypatch):
    """再生中は、つなぎ直しの待ち時間を数えない（長い再生のあいだに切れないように）。"""
    import time as _time
    checks = []
    monkeypatch.setattr(app, "check_engine", lambda quiet=False: checks.append(quiet))
    app._conn_retry_until = _time.monotonic() - 1      # もう切れているはずの時刻
    app._conn_retry_interval = 2000
    app._conn_retry_running = True
    app.conn_state = "lost"
    app._previewing = True
    try:
        app._connect_retry_tick()
        assert app._conn_retry_running is True and app._conn_retry_until > _time.monotonic()
    finally:
        app._previewing = False
        app._stop_ticks()
        app._conn_retry_running = False


def test_health_check_reconnects_to_own_running_engine(app, monkeypatch):
    """自分で起こした VOICEVOX が動いているのに、つながっていなければ、見張りがつなぎ直す。"""
    checks = []
    monkeypatch.setattr(app, "check_engine", lambda quiet=False: checks.append(quiet))
    app._engine_proc = _DeadEngine(alive=True)
    app._set_conn_state("lost")
    app._conn_checking = False
    try:
        app._health_tick()
    finally:
        app._engine_proc = None
    assert checks == [True]


def test_synth_waits_for_restarted_engine(app, monkeypatch, tmp_path):
    """音声の生成中に VOICEVOX につながらなくなったら、起こし直すのを待ってから頼み直す
    （2秒・4秒で諦めて、生成を止めない）。"""
    import requests
    import main as main_mod
    core = main_mod.core
    wav = _pcm_wav(0.05, 1000)
    calls, waits = [], []

    def synth(base, text, sid, **kw):
        calls.append(text)
        if len(calls) == 1:
            raise requests.ConnectionError("refused")
        return wav
    monkeypatch.setattr(core, "vv_synthesize_cached", synth)
    monkeypatch.setattr(core, "synth_cache_protect", lambda since: None)
    monkeypatch.setattr(app, "_wait_engine", lambda stop, sec: waits.append(sec) or True)
    app._engine_ver = "x"
    app._dict_hash_tracker = lambda: (lambda: "h")
    app._set_busy(True)
    app._synth_cancel = None
    target = tmp_path / "out.wav"
    app._synth_worker([("一文目。", 3, 0)], [[0]], {"speed": 1.0, "pitch": 0.0,
                      "intonation": 1.0, "volume": 1.0}, str(target), "combine",
                      [0.0], "wav", False)
    assert target.exists() and calls == ["一文目。", "一文目。"] and waits == [app._ENGINE_WAIT_SEC]
    app._set_busy(False)
