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
    "urlskip_var",
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
                "voice_detail_open", "remove_urls"):
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
    assert str(app.synth_btn["text"]) == "🔊 音声を生成"


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
    if "M4B" in app._format_choices():
        app.fmt_cb.set("M4B")
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
    """キャッシュ管理ダイアログ: 開閉と多重表示防止。"""
    app.open_cache_dialog()
    assert app._cache_win.winfo_exists()
    first = app._cache_win
    app.open_cache_dialog()   # 2回目は既存を前面に出すだけ
    assert app._cache_win is first
    app._cache_win.destroy()


def test_playall_done_5tuple_dispatch(app):
    """playall_doneの5要素化: skipped込みの完走メッセージが出る。"""
    app._previewing = True
    app.q.put(("playall_done", True, False, 12, 2))
    app._poll_queue()
    assert "12行・2行スキップ" in app.status_var.get()
    assert app._previewing is False


def test_playall_skip_dispatch(app):
    app.q.put(("playall_skip", 7))
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
                      0.4, "wav", False)
    kinds = []
    try:
        while True:
            kinds.append(app.q.get_nowait()[0])
    except Exception:
        pass
    assert "error" in kinds


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
    app._previewing = True          # ワーカーが応答せず残った状態を模擬
    app.preview_btn.config(state="disabled")
    app._recover_if_stuck()
    assert app._previewing is False
    assert str(app.preview_btn["state"]) == "normal"


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
