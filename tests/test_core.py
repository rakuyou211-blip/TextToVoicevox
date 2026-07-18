# -*- coding: utf-8 -*-
"""
core.py の環境非依存な純ロジックのテスト。
OCR・VOICEVOXエンジン・GUIに依存しないため、どのOSのCIでも実行できる。
"""
import io
import sys
import wave
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core


# ============================================================
#  is_cjk
# ============================================================
class TestIsCjk:
    def test_hiragana(self):
        assert core.is_cjk("あ")

    def test_katakana(self):
        assert core.is_cjk("ア")
        assert core.is_cjk("ー")  # 長音符

    def test_kanji(self):
        assert core.is_cjk("漢")
        assert core.is_cjk("語")

    def test_cjk_punctuation(self):
        assert core.is_cjk("。")
        assert core.is_cjk("、")
        assert core.is_cjk("「")

    def test_fullwidth_forms(self):
        assert core.is_cjk("Ａ")  # 全角英字
        assert core.is_cjk("１")  # 全角数字

    def test_ascii_is_not_cjk(self):
        assert not core.is_cjk("A")
        assert not core.is_cjk("1")
        assert not core.is_cjk(" ")
        assert not core.is_cjk(".")

    def test_empty_string(self):
        assert not core.is_cjk("")


# ============================================================
#  remove_cjk_spaces
# ============================================================
class TestRemoveCjkSpaces:
    def test_ocr_letter_spacing_removed(self):
        # OCR特有の「文 字 間 空 白」を除去
        assert core.remove_cjk_spaces("文 字 間 空 白") == "文字間空白"

    def test_fullwidth_spaces_removed(self):
        assert core.remove_cjk_spaces("これ　は　テスト") == "これはテスト"

    def test_ascii_word_space_kept(self):
        # 英単語間のスペースは保持される
        assert core.remove_cjk_spaces("Excel 365") == "Excel 365"

    def test_ascii_word_space_dropped_when_disabled(self):
        assert core.remove_cjk_spaces("Excel 365", keep_ascii_spaces=False) == "Excel365"

    def test_mixed_japanese_and_ascii(self):
        # CJKと隣接する空白は削除、ASCII同士は保持
        assert core.remove_cjk_spaces("私は Excel 365 を使う") == "私はExcel 365を使う"

    def test_consecutive_spaces_collapse(self):
        assert core.remove_cjk_spaces("word    word") == "word word"

    def test_spaces_around_newline_dropped(self):
        assert core.remove_cjk_spaces("abc \ndef") == "abc\ndef"
        assert core.remove_cjk_spaces("abc\n def") == "abc\ndef"

    def test_trailing_space_dropped(self):
        assert core.remove_cjk_spaces("abc ") == "abc"

    def test_empty_string(self):
        assert core.remove_cjk_spaces("") == ""


# ============================================================
#  join_wrapped_lines
# ============================================================
class TestJoinWrappedLines:
    def test_wrapped_sentence_joined(self):
        # 文末記号で終わらない行は次行と連結（CJKは空白なし）
        text = "これはテスト\nです。"
        assert core.join_wrapped_lines(text) == "これはテストです。"

    def test_sentence_end_keeps_newline(self):
        text = "これはテストです。\n続きの文。"
        assert core.join_wrapped_lines(text) == "これはテストです。\n続きの文。"

    def test_ascii_lines_joined_with_space(self):
        text = "hello\nworld"
        assert core.join_wrapped_lines(text) == "hello world"

    def test_max_len_safety_valve(self):
        # 連結後が max_len を超える場合は連結しない（安全弁）
        long_line = "あ" * 100
        text = long_line + "\n続き"
        result = core.join_wrapped_lines(text, max_len=90)
        assert result == long_line + "\n続き"

    def test_short_lines_within_max_len_joined(self):
        text = "みじかい\nぎょう"
        assert core.join_wrapped_lines(text, max_len=90) == "みじかいぎょう"

    def test_blank_line_keeps_paragraphs(self):
        text = "第一段落の文\n\n第二段落の文"
        assert core.join_wrapped_lines(text) == "第一段落の文\n\n第二段落の文"

    def test_empty_string(self):
        assert core.join_wrapped_lines("") == ""


# ============================================================
#  is_jp_script_char
# ============================================================
class TestIsJpScriptChar:
    def test_kana_kanji_true(self):
        assert core.is_jp_script_char("あ")
        assert core.is_jp_script_char("ア")
        assert core.is_jp_script_char("漢")
        assert core.is_jp_script_char("ｱ")  # 半角カナ

    def test_ascii_and_symbols_false(self):
        assert not core.is_jp_script_char("A")
        assert not core.is_jp_script_char("1")
        assert not core.is_jp_script_char("、")   # 全角句読点は“中身”ではない
        assert not core.is_jp_script_char("１")   # 全角数字も対象外
        assert not core.is_jp_script_char("")


# ============================================================
#  smart_join_wrapped（折り返しだけ賢く連結）
# ============================================================
class TestSmartJoinWrapped:
    def test_joins_long_wrapped_cjk_sentence(self):
        raw = ("「貨物の価値の20%の払い戻しを求める」トランプ大統領がホ\n"
               "ルムズ海峡の安全航行の対価を各国に請求")
        assert core.smart_join_wrapped(raw) == \
            "「貨物の価値の20%の払い戻しを求める」トランプ大統領がホルムズ海峡の安全航行の対価を各国に請求"

    def test_joins_body_across_three_lines(self):
        raw = ("アメリカのトランプ大統領は、イランがホルムズ海峡で商船への攻撃を続けている\n"
               "ことを受けて、イランの港湾への船の出入りの封鎖措置を\n"
               "開始すると表明しました。")
        out = core.smart_join_wrapped(raw)
        assert out.count("\n") == 0
        assert out.endswith("表明しました。")

    def test_does_not_join_across_bullet(self):
        raw = ("トランプ大統領がホルムズ海峡の対価を各国に請求\n"
               "■ホルムズ海峡封鎖措置再開")
        assert core.smart_join_wrapped(raw).split("\n") == [
            "トランプ大統領がホルムズ海峡の対価を各国に請求",
            "■ホルムズ海峡封鎖措置再開"]

    def test_does_not_join_across_script_change(self):
        # 日本語行の直後の英字行は別ブロック（字種の切替）
        raw = "米軍がイラン港湾を封鎖する措置を再開すると表明\nTHE"
        assert core.smart_join_wrapped(raw).split("\n") == [
            "米軍がイラン港湾を封鎖する措置を再開すると表明", "THE"]

    def test_keeps_short_list_items(self):
        assert core.smart_join_wrapped("りんご\nみかん\nぶどう") == "りんご\nみかん\nぶどう"

    def test_keeps_short_wrap_below_threshold(self):
        # 16字未満の行は折り返しとみなさない（従来どおり改行を尊重）
        assert core.smart_join_wrapped("これは改行で\n途切れた文です。") == \
            "これは改行で\n途切れた文です。"

    def test_no_join_after_sentence_ender(self):
        raw = "とても長い一文がここで終わりました。\n次のあたらしい文がここから始まります"
        assert core.smart_join_wrapped(raw).split("\n")[0].endswith("。")

    def test_joins_wrapped_ascii_with_space(self):
        raw = "This is a fairly long english line that\nwraps onto the next"
        assert core.smart_join_wrapped(raw) == \
            "This is a fairly long english line that wraps onto the next"

    def test_blank_line_keeps_paragraph(self):
        raw = "とても長い最初の段落がここに書いてある\n\n二つ目の段落がここに書いてある"
        assert "\n\n" in core.smart_join_wrapped(raw)

    def test_whitespace_only_line_breaks_paragraph(self):
        # 空白のみの行（NBSP等）も段落境界として扱い、前後を糊付けしない
        a = "アメリカの大統領が海峡の封鎖措置について表明した"
        b = "日経平均株価は大きく下落し年初来安値を更新した"
        out = core.smart_join_wrapped(a + "\n \n" + b)
        assert a in out.split("\n") and b in out.split("\n")

    def test_cascade_stops_at_short_line(self):
        # 長い行が“リスト全体”を飲み込まない（短い行に達したら連鎖を止める）
        raw = "本日午後に首相官邸で開かれた関係閣僚会議で決まった重点項目は次の通り\nテロ\n対策\n強化"
        lines = core.smart_join_wrapped(raw).split("\n")
        assert "対策" in lines and "強化" in lines  # 短い後続項目は独立のまま

    def test_empty(self):
        assert core.smart_join_wrapped("") == ""


# ============================================================
#  reflow_ocr_lines（座標を使った折り返し連結）
# ============================================================
def _ln(text, x0, x1, y0, y1):
    return {"text": text, "x0": x0, "x1": x1, "y0": y0, "y1": y1}


class TestReflowOcrLines:
    # Apple Vision の実測に近い合成座標（正規化・y0=上端）
    HEAD = _ln("ホルムズ海峡の封鎖措置", 0.033, 0.345, 0.043, 0.094)   # 短い見出し
    B1 = _ln("アメリカの大統領はイランが海峡で攻撃を続けて", 0.029, 0.996, 0.171, 0.228)
    B2 = _ln("いることを受けて港湾の封鎖措置を開始すると表明しま", 0.037, 0.995, 0.237, 0.288)
    LAST = _ln("した。", 0.037, 0.108, 0.303, 0.363)                    # 段落末（短い）
    L1 = _ln("重点項目", 0.033, 0.145, 0.456, 0.510)
    L2 = _ln("テロ対策", 0.033, 0.148, 0.519, 0.575)

    def test_joins_wrapped_full_lines(self):
        out = core.reflow_ocr_lines([self.B1, self.B2, self.LAST])
        assert out == ("アメリカの大統領はイランが海峡で攻撃を続けて"
                       "いることを受けて港湾の封鎖措置を開始すると表明しました。")

    def test_heading_and_list_stay_separate(self):
        out = core.reflow_ocr_lines(
            [self.HEAD, self.B1, self.B2, self.LAST, self.L1, self.L2]).split("\n")
        assert "ホルムズ海峡の封鎖措置" in out          # 見出しは独立
        assert "重点項目" in out and "テロ対策" in out    # 箇条書きは独立
        assert any(l.endswith("表明しました。") for l in out)  # 本文は1文に連結
        assert len(out) == 4

    def test_separate_columns_not_joined(self):
        c1 = _ln("ひだりのれつ", 0.03, 0.45, 0.10, 0.15)
        c2 = _ln("みぎのれつ", 0.55, 0.95, 0.10, 0.15)
        assert len(core.reflow_ocr_lines([c1, c2]).split("\n")) == 2

    def test_short_block_not_treated_as_wrap(self):
        # 右余白まで届かない短い塊は折り返しとみなさない（連結しない）
        a = _ln("みじかい", 0.03, 0.20, 0.10, 0.15)
        b = _ln("つぎのぎょう", 0.03, 0.20, 0.16, 0.21)
        assert len(core.reflow_ocr_lines([a, b]).split("\n")) == 2

    def test_big_gap_breaks_block(self):
        # 行間が大きい（別ブロック）なら、たとえ前行が長くても連結しない
        a = _ln("よこいっぱいにひろがるながいぎょうがここにある", 0.03, 0.98, 0.10, 0.16)
        b = _ln("とおくはなれたべつのぎょう", 0.03, 0.60, 0.60, 0.66)
        assert len(core.reflow_ocr_lines([a, b]).split("\n")) == 2

    def test_joins_wrap_ending_in_bracket(self):
        # 座標で折り返し確定なら、行末が鉤括弧「」でも次行と連結する
        a = _ln("姫路市豊富町にある工場で爆発があったと「日本化薬」", 0.05, 0.97, 0.10, 0.14)
        b = _ln("の担当者から通報があった", 0.05, 0.40, 0.15, 0.19)
        assert core.reflow_ocr_lines([a, b]) == \
            "姫路市豊富町にある工場で爆発があったと「日本化薬」の担当者から通報があった"

    def test_distant_line_does_not_contaminate_margin(self):
        # 右余白はブロック内だけで測る。離れた行（フッター等）が同じx0でも、
        # 別ブロックの短い見出しを“折り返し”に化けさせて誤連結してはいけない。
        a = _ln("本日の予定", 0.10, 0.28, 0.10, 0.14)
        b = _ln("会議あり", 0.10, 0.22, 0.15, 0.19)
        footer = _ln("ページ下部の注記文言", 0.10, 0.31, 0.90, 0.94)
        out = core.reflow_ocr_lines([a, b, footer]).split("\n")
        assert out == ["本日の予定", "会議あり", "ページ下部の注記文言"]

    def test_empty(self):
        assert core.reflow_ocr_lines([]) == ""


# ============================================================
#  strip_overlay_labels（座標で“映像内オーバーレイ・ラベル行”を除去）
# ============================================================
def _texts(lines):
    return {l["text"] for l in lines}


class TestStripOverlayLabels:
    # MBSニュース画像の Apple Vision 実測に近い合成座標（正規化・y0=上端, H≈0.035）。
    LOGO = _ln("MBSニュース", 0.019, 0.188, 0.000, 0.020)          # 小・上部・孤立（0.57H）
    DATE = _ln("2026年7月14日（火）19:48", 0.702, 0.917, 0.000, 0.025)  # 日時・上部（0.72H）
    CAT = _ln("国内", 0.016, 0.054, 0.037, 0.060)                  # カテゴリ 小・上部・孤立（0.66H）
    # 本文＝複数行が縦に連なる段落ブロック（H の基準・保護対象）
    B1a = _ln("14日午後、兵庫県姫路市にある工場の実験室で爆発があり、男性2人が病院に搬送",
              0.015, 0.895, 0.111, 0.146)
    B1b = _ln("されました。このうち30代くらいの男性が意識不明の重体です。",
              0.016, 0.702, 0.161, 0.196)
    B2a = _ln("通報した従業員が爆発音を聞いて実験室に駆けつけたところ、男性が倒れているの",
              0.015, 0.894, 0.456, 0.489)
    B2b = _ln("を発見したということです。", 0.016, 0.314, 0.506, 0.540)

    def _body(self):
        return [self.B1a, self.B1b, self.B2a, self.B2b]

    def test_drops_logo_and_datetime(self):
        # 上部・小さめ・孤立のロゴ行と日時行を落とす。本文は残す。
        kept = _texts(core.strip_overlay_labels([self.LOGO, self.DATE] + self._body()))
        assert "MBSニュース" not in kept
        assert self.DATE["text"] not in kept
        assert self.B1a["text"] in kept and self.B1b["text"] in kept

    def test_drops_short_category(self):
        # 本文と別サイズ・上部・孤立の短いカテゴリ行（「国内」）を落とす。
        kept = _texts(core.strip_overlay_labels([self.CAT] + self._body()))
        assert "国内" not in kept
        for b in self._body():
            assert b["text"] in kept

    def test_keeps_body_period_line(self):
        # 句点を含む短い段落末は本文シグナル。上部・小・孤立でも必ず残す（保護）。
        last = _ln("した。", 0.013, 0.079, 0.030, 0.062)
        kept = _texts(core.strip_overlay_labels([last] + self._body()))
        assert "した。" in kept

    def test_keeps_body_block_member(self):
        # 複数行ブロックの一部（段落末の短い行 B1b/B2b）は残す。
        kept = _texts(core.strip_overlay_labels(self._body()))
        assert self.B1b["text"] in kept and self.B2b["text"] in kept

    def test_keeps_long_line_even_if_small_and_top(self):
        # 小さめ・上部・孤立でも、一定長以上の行は見出し/本文として残す（単一シグナルでは落とさない）。
        head = _ln("兵庫県姫路市豊富町の化学工場で爆発事故が発生", 0.03, 0.55, 0.004, 0.024)
        kept = _texts(core.strip_overlay_labels([head] + self._body()))
        assert head["text"] in kept

    def test_single_signal_isolated_not_dropped(self):
        # 本文と同じ高さ・中段・孤立の短い行（＝孤立シグナル“1つだけ”）は落とさない。
        lonely = _ln("メモ書き", 0.40, 0.55, 0.400, 0.435)
        kept = _texts(core.strip_overlay_labels([self.B1a, self.B1b, lonely,
                                                 self.B2a, self.B2b]))
        assert "メモ書き" in kept

    def test_short_word_midbody_normal_size_kept(self):
        # 本文中に紛れた短語（本文と同サイズ・中段・孤立の「国内」）は孤立1シグナルのみ→残す。
        word = _ln("国内", 0.015, 0.055, 0.400, 0.435)
        kept = _texts(core.strip_overlay_labels([self.B1a, self.B1b, word,
                                                 self.B2a, self.B2b]))
        assert "国内" in kept

    def test_few_lines_unchanged(self):
        # 本文ブロックが立たない少数行では何もしない（安全側）。
        assert core.strip_overlay_labels([]) == []
        two = [self.B1a, self.B1b]
        assert core.strip_overlay_labels(two) == two

    def test_no_body_block_keeps_all(self):
        # 段落（複数行ブロック）が無い＝本文を特定できない。すべて孤立行でも全部残す。
        a = _ln("あいう", 0.03, 0.20, 0.05, 0.09)
        b = _ln("かきくけ", 0.50, 0.72, 0.05, 0.09)
        c = _ln("さしす", 0.03, 0.20, 0.30, 0.34)
        d = _ln("たちつて", 0.50, 0.72, 0.60, 0.64)
        kept = _texts(core.strip_overlay_labels([a, b, c, d]))
        assert kept == {"あいう", "かきくけ", "さしす", "たちつて"}

    def test_full_scene_keeps_all_body(self):
        # ロゴ+日時+カテゴリ+本文の実シーン: ラベル3行だけ消え、本文4行は全て残る。
        scene = [self.LOGO, self.DATE, self.CAT] + self._body()
        kept = _texts(core.strip_overlay_labels(scene))
        assert kept == {b["text"] for b in self._body()}


# ============================================================
#  split_sentences
# ============================================================
class TestSplitSentences:
    def test_basic_japanese(self):
        assert core.split_sentences("これはテストです。続きの文。") == \
            ["これはテストです。", "続きの文。"]

    def test_exclamation_and_question(self):
        assert core.split_sentences("すごい！本当？はい。") == \
            ["すごい！", "本当？", "はい。"]

    def test_newline_is_separator(self):
        assert core.split_sentences("一行目\n二行目") == ["一行目", "二行目"]

    def test_trailing_text_without_ender(self):
        assert core.split_sentences("終わった。まだ途中") == ["終わった。", "まだ途中"]

    def test_ascii_enders(self):
        assert core.split_sentences("Hello! How are you?") == \
            ["Hello!", "How are you?"]

    def test_empty_and_whitespace(self):
        assert core.split_sentences("") == []
        assert core.split_sentences("   \n  ") == []


# ============================================================
#  clean_text
# ============================================================
class TestCleanText:
    def test_sentence_mode_splits_per_line(self):
        raw = "これはテストです。続きの文。"
        assert core.clean_text(raw, mode="sentence") == \
            "これはテストです。\n続きの文。"

    def test_ocr_spaces_removed(self):
        raw = "こ れ は テ ス ト で す 。"
        assert core.clean_text(raw, mode="sentence") == "これはテストです。"

    def test_ascii_spaces_kept(self):
        raw = "Excel 365 を使う。"
        assert core.clean_text(raw, mode="sentence") == "Excel 365を使う。"

    def test_crlf_normalized(self):
        raw = "一行目\r\n二行目\rさんぎょうめ"
        result = core.clean_text(raw, mode="keep")
        assert result == "一行目\n二行目\nさんぎょうめ"

    def test_excess_blank_lines_collapsed(self):
        raw = "一つ目\n\n\n\n二つ目"
        result = core.clean_text(raw, mode="keep", remove_blank=False)
        assert "\n\n\n" not in result

    def test_remove_blank_lines(self):
        raw = "一つ目\n\n二つ目"
        assert core.clean_text(raw, mode="keep", remove_blank=True) == "一つ目\n二つ目"

    def test_keep_mode_preserves_line_structure(self):
        raw = "見出し\n本文はここ。まだ続く。"
        result = core.clean_text(raw, mode="keep")
        assert result == "見出し\n本文はここ。まだ続く。"

    def test_join_wrapped_connects_wrapped_sentence(self):
        raw = "これは改行で\n途切れた文です。"
        assert core.clean_text(raw, mode="sentence", join_wrapped=True) == \
            "これは改行で途切れた文です。"

    def test_no_join_wrapped_respects_newlines(self):
        raw = "これは改行で\n途切れた文です。"
        assert core.clean_text(raw, mode="sentence", join_wrapped=False) == \
            "これは改行で\n途切れた文です。"

    def test_smart_join_defaults_off_in_core(self):
        # core.clean_text の既定は後方互換（smart_join=False＝連結しない）
        raw = ("アメリカのトランプ大統領がホルムズ海峡の封鎖措置について\n"
               "改めて表明した")
        assert core.clean_text(raw, mode="sentence").count("\n") == 1

    def test_smart_join_joins_long_wrap(self):
        raw = ("アメリカのトランプ大統領がホルムズ海峡の封鎖措置について\n"
               "改めて表明した")
        assert core.clean_text(raw, mode="sentence", smart_join=True) == \
            "アメリカのトランプ大統領がホルムズ海峡の封鎖措置について改めて表明した"

    def test_join_wrapped_takes_precedence_over_smart(self):
        # 両方Trueなら積極連結(join_wrapped)が優先され、短い折り返しも連結される
        raw = "これは改行で\n途切れた文です。"
        assert core.clean_text(raw, mode="sentence",
                               join_wrapped=True, smart_join=True) == \
            "これは改行で途切れた文です。"

    def test_empty_input(self):
        assert core.clean_text("") == ""


# ============================================================
#  concat_wavs
# ============================================================
def _make_wav(duration_sec=0.1, framerate=24000, sampwidth=2, nchannels=1,
              value=b"\x01\x00"):
    """テスト用の合成WAVバイト列を作る。"""
    nframes = int(framerate * duration_sec)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(nchannels)
        w.setsampwidth(sampwidth)
        w.setframerate(framerate)
        w.writeframes(value * nframes)
    return buf.getvalue()


class TestConcatWavs:
    def test_empty_list(self):
        assert core.concat_wavs([]) == b""

    def test_single_wav_roundtrip(self):
        wav = _make_wav(duration_sec=0.1)
        out = core.concat_wavs([wav], gap_sec=0.4)
        with wave.open(io.BytesIO(out), "rb") as w:
            assert w.getnframes() == int(24000 * 0.1)  # 単体では無音は挟まれない
            assert w.getframerate() == 24000
            assert w.getsampwidth() == 2
            assert w.getnchannels() == 1

    def test_two_wavs_with_gap(self):
        wav1 = _make_wav(duration_sec=0.1)
        wav2 = _make_wav(duration_sec=0.2)
        gap = 0.4
        out = core.concat_wavs([wav1, wav2], gap_sec=gap)
        with wave.open(io.BytesIO(out), "rb") as w:
            expected = int(24000 * 0.1) + int(24000 * gap) + int(24000 * 0.2)
            assert w.getnframes() == expected

    def test_gap_is_silence(self):
        wav1 = _make_wav(duration_sec=0.1, value=b"\x01\x00")
        wav2 = _make_wav(duration_sec=0.1, value=b"\x02\x00")
        out = core.concat_wavs([wav1, wav2], gap_sec=0.5)
        with wave.open(io.BytesIO(out), "rb") as w:
            frames = w.readframes(w.getnframes())
        n1 = int(24000 * 0.1) * 2  # bytes
        ngap = int(24000 * 0.5) * 2
        gap_bytes = frames[n1:n1 + ngap]
        assert gap_bytes == b"\x00" * ngap
        # 前後のデータは保持されている
        assert frames[:n1] == b"\x01\x00" * int(24000 * 0.1)
        assert frames[n1 + ngap:] == b"\x02\x00" * int(24000 * 0.1)

    def test_zero_gap(self):
        wav = _make_wav(duration_sec=0.1)
        out = core.concat_wavs([wav, wav], gap_sec=0)
        with wave.open(io.BytesIO(out), "rb") as w:
            assert w.getnframes() == int(24000 * 0.1) * 2


# ============================================================
#  プラットフォーム依存関数の Linux 上での挙動
# ============================================================
@pytest.mark.skipif(sys.platform in ("win32", "darwin"),
                    reason="Linux（非対応OS）での挙動のみ検証")
class TestPlatformBehaviorOnLinux:
    def test_find_voicevox_returns_none(self):
        assert core.find_voicevox() is None

    def test_run_ocr_raises_runtime_error(self):
        with pytest.raises(RuntimeError):
            core.run_ocr(["/tmp/dummy.png"])

    def test_can_play_is_false(self):
        assert core.can_play() is False


def test_run_ocr_empty_list_returns_empty_dict():
    # 空リストはOS判定より前に {} を返す（全OS共通）
    assert core.run_ocr([]) == {}


# ============================================================
#  strip_aozora（青空文庫注記の除去）
# ============================================================
class TestStripAozora:
    def test_ruby_removed(self):
        assert core.strip_aozora("吾輩《わがはい》は猫である") == "吾輩は猫である"

    def test_ruby_bar_removed(self):
        assert core.strip_aozora("｜東京《とうきょう》の空") == "東京の空"

    def test_note_removed(self):
        assert core.strip_aozora("本文［＃「本文」に傍点］です") == "本文です"

    def test_plain_text_unchanged(self):
        assert core.strip_aozora("普通のテキストはそのまま。") == "普通のテキストはそのまま。"

    def test_multiple_annotations(self):
        s = "彼女《かのじょ》は｜薔薇《ばら》を見た。［＃改ページ］次の章。"
        assert core.strip_aozora(s) == "彼女は薔薇を見た。次の章。"


# ============================================================
#  read_txt（文字コード自動判定）
# ============================================================
class TestReadTxt:
    def _write(self, tmp_path, data: bytes):
        p = tmp_path / "t.txt"
        p.write_bytes(data)
        return str(p)

    def test_utf8(self, tmp_path):
        p = self._write(tmp_path, "こんにちは。\n".encode("utf-8"))
        assert core.read_txt(p) == "こんにちは。\n"

    def test_utf8_with_bom(self, tmp_path):
        p = self._write(tmp_path, b"\xef\xbb\xbf" + "こんにちは。".encode("utf-8"))
        assert core.read_txt(p) == "こんにちは。"

    def test_cp932(self, tmp_path):
        p = self._write(tmp_path, "日本語のシフトJISテキスト。".encode("cp932"))
        assert core.read_txt(p) == "日本語のシフトJISテキスト。"


# ============================================================
#  extract_docx（最小のdocxを合成して検証）
# ============================================================
def _make_docx(path, paragraphs):
    """テスト用の最小docxを生成する。"""
    import zipfile
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = ""
    for runs in paragraphs:
        rxml = "".join(f"<w:r><w:t>{t}</w:t></w:r>" for t in runs)
        body += f"<w:p>{rxml}</w:p>"
    doc = f'<?xml version="1.0" encoding="UTF-8"?><w:document {ns}><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", doc)


class TestExtractDocx:
    def test_paragraphs_extracted(self, tmp_path):
        p = str(tmp_path / "t.docx")
        _make_docx(p, [["最初の段落。"], ["二つ目の", "段落。"]])
        assert core.extract_docx(p) == "最初の段落。\n二つ目の段落。"

    def test_empty_paragraph_skipped(self, tmp_path):
        p = str(tmp_path / "t.docx")
        _make_docx(p, [["本文。"], [], ["次。"]])
        assert core.extract_docx(p) == "本文。\n次。"


# ============================================================
#  extract_epub（最小のepubを合成して検証）
# ============================================================
def _make_epub(path, chapters):
    """テスト用の最小epubを生成する。chapters: [(filename, html), ...]"""
    import zipfile
    container = ('<?xml version="1.0"?>'
                 '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>')
    items = "".join(f'<item id="c{i}" href="{fn}" media-type="application/xhtml+xml"/>'
                    for i, (fn, _) in enumerate(chapters))
    refs = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    opf = ('<?xml version="1.0"?>'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
           f'<manifest>{items}</manifest><spine>{refs}</spine></package>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        for fn, html in chapters:
            z.writestr(f"OEBPS/{fn}", html)


class TestExtractEpub:
    def test_chapters_in_spine_order(self, tmp_path):
        p = str(tmp_path / "t.epub")
        _make_epub(p, [("a.xhtml", "<html><body><p>第一章。</p></body></html>"),
                       ("b.xhtml", "<html><body><p>第二章。</p></body></html>")])
        assert core.extract_epub(p) == "第一章。\n\n第二章。"

    def test_ruby_rt_dropped(self, tmp_path):
        p = str(tmp_path / "t.epub")
        html = "<html><body><p><ruby>吾輩<rt>わがはい</rt></ruby>は猫である</p></body></html>"
        _make_epub(p, [("a.xhtml", html)])
        assert core.extract_epub(p) == "吾輩は猫である"

    def test_script_style_title_dropped(self, tmp_path):
        p = str(tmp_path / "t.epub")
        html = ("<html><head><title>タイトル</title><style>p{}</style></head>"
                "<body><script>var x=1;</script><p>本文だけ。</p></body></html>")
        _make_epub(p, [("a.xhtml", html)])
        assert core.extract_epub(p) == "本文だけ。"

    def test_block_tags_become_newlines(self, tmp_path):
        p = str(tmp_path / "t.epub")
        html = "<html><body><h1>見出し</h1><p>一行目。<br/>二行目。</p></body></html>"
        _make_epub(p, [("a.xhtml", html)])
        # ブロック要素の境界は段落区切りとして空行1つになる（<br/>は改行のみ）
        assert core.extract_epub(p) == "見出し\n\n一行目。\n二行目。"


# ============================================================
#  hira_to_kata
# ============================================================
class TestHiraToKata:
    def test_basic(self):
        assert core.hira_to_kata("わがはい") == "ワガハイ"

    def test_katakana_unchanged(self):
        assert core.hira_to_kata("ワガハイ") == "ワガハイ"

    def test_mixed_and_choon(self):
        assert core.hira_to_kata("らーめん") == "ラーメン"

    def test_small_kana(self):
        assert core.hira_to_kata("きゃりー") == "キャリー"


# ============================================================
#  make_vvproj
# ============================================================
_SP_UUID = "7ffcb7ce-00ec-4bdc-82cd-45a8889e43ff"  # テスト用の話者UUID


class TestMakeVvproj:
    def test_structure(self):
        import json
        proj = json.loads(core.make_vvproj(["一行目。", "二行目。"], 2, _SP_UUID))
        # 0.22.0形式: これ未満だとqueryなしaudioItemがマイグレーションで落ちる
        assert proj["appVersion"] == "0.22.0"
        talk = proj["talk"]
        assert len(talk["audioKeys"]) == 2
        assert set(talk["audioKeys"]) == set(talk["audioItems"].keys())
        items = [talk["audioItems"][k] for k in talk["audioKeys"]]
        assert items[0]["text"] == "一行目。"
        assert items[1]["text"] == "二行目。"
        for it in items:
            assert it["voice"] == {"engineId": core.VV_ENGINE_ID,
                                   "speakerId": _SP_UUID, "styleId": 2}
        # songセクション: 0.22スキーマの必須項目が揃っていること
        song = proj["song"]
        assert song["tpqn"] == 480
        assert song["trackOrder"] == list(song["tracks"].keys())
        track = song["tracks"][song["trackOrder"][0]]
        for field in ("name", "keyRangeAdjustment", "volumeRangeAdjustment",
                      "notes", "pitchEditData", "solo", "mute", "gain", "pan"):
            assert field in track

    def test_empty_lines_skipped(self):
        import json
        proj = json.loads(core.make_vvproj(["", "  ", "本文。"], 0, _SP_UUID))
        assert len(proj["talk"]["audioKeys"]) == 1

    def test_keys_are_unique_uuids(self):
        import json, uuid as uuid_mod
        proj = json.loads(core.make_vvproj(["あ"] * 5, 1, _SP_UUID))
        keys = proj["talk"]["audioKeys"]
        assert len(set(keys)) == 5
        for k in keys:
            uuid_mod.UUID(k)  # 不正なUUIDなら例外


# ============================================================
#  extract_files のテキスト系ファイル対応
# ============================================================
class TestExtractFilesTextFormats:
    def test_txt_with_aozora(self, tmp_path):
        p = tmp_path / "novel.txt"
        p.write_text("吾輩《わがはい》は猫である。", encoding="utf-8")
        text, warnings = core.extract_files([str(p)])
        assert text == "吾輩は猫である。"
        assert warnings == []

    def test_docx(self, tmp_path):
        p = str(tmp_path / "t.docx")
        _make_docx(p, [["段落テキスト。"]])
        text, warnings = core.extract_files([p])
        assert text == "段落テキスト。"
        assert warnings == []

    def test_mixed_txt_and_epub_order(self, tmp_path):
        t = tmp_path / "a.txt"
        t.write_text("テキスト側。", encoding="utf-8")
        e = str(tmp_path / "b.epub")
        _make_epub(e, [("a.xhtml", "<html><body><p>EPUB側。</p></body></html>")])
        text, _ = core.extract_files([str(t), e])
        assert text == "テキスト側。\n\nEPUB側。"


# ============================================================
#  wav_duration
# ============================================================
def test_wav_duration():
    wav = _make_wav(duration_sec=0.5)
    assert abs(core.wav_duration(wav) - 0.5) < 0.01


# ============================================================
#  parse_speaker_tag / is_dialogue_line / resolve_speaker
# ============================================================
_SPEAKERS = [("ずんだもん（ノーマル）", 3, "uuid-z"),
             ("ずんだもん（あまあま）", 1, "uuid-z"),
             ("四国めたん（ノーマル）", 2, "uuid-m")]


class TestSpeakerTag:
    def test_tag_parsed(self):
        assert core.parse_speaker_tag("@ずんだもん: こんにちは") == ("ずんだもん", "こんにちは")

    def test_fullwidth_colon(self):
        assert core.parse_speaker_tag("@四国めたん：やあ") == ("四国めたん", "やあ")

    def test_no_tag(self):
        assert core.parse_speaker_tag("ただの本文です。") == (None, "ただの本文です。")

    def test_at_without_colon_is_not_tag(self):
        assert core.parse_speaker_tag("@everyone 集合")[0] is None

    def test_resolve_exact(self):
        assert core.resolve_speaker("ずんだもん（あまあま）", _SPEAKERS)[1] == 1

    def test_resolve_prefix_takes_first_style(self):
        # スタイル省略時は最初のスタイル
        assert core.resolve_speaker("ずんだもん", _SPEAKERS)[1] == 3

    def test_resolve_partial(self):
        assert core.resolve_speaker("めたん", _SPEAKERS)[1] == 2

    def test_resolve_unknown(self):
        assert core.resolve_speaker("存在しない話者", _SPEAKERS) is None


class TestIsDialogueLine:
    def test_kagi_bracket(self):
        assert core.is_dialogue_line("「おはよう」と彼は言った")

    def test_double_bracket(self):
        assert core.is_dialogue_line("『本のタイトル』")

    def test_narration(self):
        assert not core.is_dialogue_line("彼は「おはよう」と言った")


# ============================================================
#  make_vvproj の行別話者
# ============================================================
class TestMakeVvprojPerLine:
    def test_tuple_entries_override_default(self):
        import json
        entries = [("地の文。", None, None), ("「セリフ」", 2, "uuid-m")]
        proj = json.loads(core.make_vvproj(entries, 3, "uuid-z"))
        items = [proj["talk"]["audioItems"][k] for k in proj["talk"]["audioKeys"]]
        assert items[0]["voice"]["styleId"] == 3
        assert items[0]["voice"]["speakerId"] == "uuid-z"
        assert items[1]["voice"]["styleId"] == 2
        assert items[1]["voice"]["speakerId"] == "uuid-m"

    def test_str_entries_still_work(self):
        import json
        proj = json.loads(core.make_vvproj(["そのまま。"], 0, "uuid-z"))
        it = list(proj["talk"]["audioItems"].values())[0]
        assert it["voice"]["styleId"] == 0


# ============================================================
#  audio_encoders / encode_audio
# ============================================================
class TestEncodeAudio:
    def test_wav_passthrough(self, tmp_path):
        wav = _make_wav(duration_sec=0.1)
        out = str(tmp_path / "o.wav")
        core.encode_audio(wav, out, "wav")
        assert open(out, "rb").read() == wav

    def test_unknown_format_raises(self, tmp_path):
        with pytest.raises(RuntimeError):
            core.encode_audio(_make_wav(0.1), str(tmp_path / "o.ogg"), "ogg", encoders={})

    @pytest.mark.skipif(not core.IS_MAC, reason="afconvertはmacOSのみ")
    def test_m4a_with_afconvert(self, tmp_path):
        out = str(tmp_path / "o.m4a")
        core.encode_audio(_make_wav(0.2), out, "m4a")
        data = open(out, "rb").read()
        assert len(data) > 100 and b"ftyp" in data[:16]  # MP4コンテナ

    def test_encoders_shape(self):
        enc = core.audio_encoders()
        assert set(enc.keys()) <= {"m4a", "mp3"}


# ============================================================
#  make_srt（字幕タイミング）
# ============================================================
class TestMakeSrt:
    def test_basic_timing(self):
        srt = core.make_srt(["一行目。", "二行目。"], [1.5, 2.0], gap_sec=0.5)
        blocks = srt.strip().split("\n\n")
        assert len(blocks) == 2
        assert blocks[0].split("\n") == [
            "1", "00:00:00,000 --> 00:00:01,500", "一行目。"]
        # 2行目は 1.5 + 0.5(gap) = 2.0秒から
        assert blocks[1].split("\n") == [
            "2", "00:00:02,000 --> 00:00:04,000", "二行目。"]

    def test_no_gap(self):
        srt = core.make_srt(["あ", "い"], [1.0, 1.0])
        assert "00:00:01,000 --> 00:00:02,000" in srt

    def test_hour_rollover(self):
        srt = core.make_srt(["長い本の終わり。"], [10.0], gap_sec=0.0)
        assert srt.startswith("1\n00:00:00,000 --> 00:00:10,000")
        # 3661.5秒 = 1時間1分1.5秒
        assert core._srt_ts(3661.5) == "01:01:01,500"

    def test_millisecond_rounding(self):
        assert core._srt_ts(0.9996) == "00:00:01,000"


# ============================================================
#  cli.py の引数解析
# ============================================================
class TestCli:
    def _parse(self, argv):
        import cli
        return cli.build_parser().parse_args(argv)

    def test_minimal(self):
        a = self._parse(["input.pdf", "-o", "out"])
        assert a.inputs == ["input.pdf"]
        assert a.out == "out"
        assert not a.wav and a.format == "wav" and a.mode == "sentence"

    def test_full_options(self):
        a = self._parse(["a.pdf", "b.txt", "-o", "out", "--wav", "--format", "m4a",
                         "--speaker", "ずんだもん", "--speed", "1.3",
                         "--combine", "--gap", "0.2", "--srt", "--join-wrapped"])
        assert a.inputs == ["a.pdf", "b.txt"]
        assert a.wav and a.format == "m4a" and a.speaker == "ずんだもん"
        assert a.combine and a.srt and abs(a.gap - 0.2) < 1e-9

    def test_pick_speaker_default_and_named(self):
        import cli
        sps = [("四国めたん（ノーマル）", 2, "u1"), ("ずんだもん（ノーマル）", 3, "u2")]
        assert cli.pick_speaker("", sps)[1] == 2
        assert cli.pick_speaker("ずんだもん", sps)[1] == 3

    def test_pick_speaker_unknown_exits(self):
        import cli
        with pytest.raises(SystemExit):
            cli.pick_speaker("いない人", [("四国めたん（ノーマル）", 2, "u1")])

    def test_cli_txt_only_end_to_end(self, tmp_path):
        # エンジン不要のtxt出力パスを実行
        import cli
        src = tmp_path / "novel.txt"
        src.write_text("吾輩《わがはい》は猫である。名前はまだ無い。", encoding="utf-8")
        out = tmp_path / "out"
        rc = cli.main([str(src), "-o", str(out)])
        assert rc == 0
        result = (out / "voicevox_text.txt").read_text(encoding="utf-8")
        assert result == "吾輩は猫である。\n名前はまだ無い。"


# ============================================================
#  strip_paren_ruby / normalize_ascii / fmt_duration
# ============================================================
class TestStripParenRuby:
    def test_hiragana_ruby(self):
        assert core.strip_paren_ruby("吾輩(わがはい)は猫である") == "吾輩は猫である"

    def test_katakana_ruby_fullwidth_parens(self):
        assert core.strip_paren_ruby("竜（ドラゴン）が飛ぶ") == "竜が飛ぶ"

    def test_annotation_kept(self):
        # かな以外が混ざる括弧は注釈なので残す
        assert core.strip_paren_ruby("補足(2023年)を参照") == "補足(2023年)を参照"

    def test_paren_after_kana_kept(self):
        # 直前が漢字でなければルビと見なさない
        assert core.strip_paren_ruby("これ(これ)は残る") == "これ(これ)は残る"

    def test_multiple(self):
        assert core.strip_paren_ruby("竜（ドラゴン）と魔法(まほう)") == "竜と魔法"


class TestNormalizeAscii:
    def test_fullwidth_alnum(self):
        assert core.normalize_ascii("Ｅｘｃｅｌ２０２３") == "Excel2023"

    def test_fullwidth_symbols(self):
        assert core.normalize_ascii("（Ａ＋Ｂ）＝Ｃ！") == "(A+B)=C!"

    def test_japanese_untouched(self):
        assert core.normalize_ascii("日本語はそのまま。") == "日本語はそのまま。"

    def test_fullwidth_space_untouched(self):
        # 全角スペースは remove_cjk_spaces の担当なので変換しない
        assert core.normalize_ascii("あ　い") == "あ　い"


class TestCleanTextNewOptions:
    def test_paren_ruby_option(self):
        out = core.clean_text("吾輩(わがはい)は猫である。", paren_ruby=True)
        assert out == "吾輩は猫である。"

    def test_normalize_option(self):
        out = core.clean_text("Ｅｘｃｅｌ ３６５を使う。", normalize=True)
        assert out == "Excel 365を使う。"

    def test_defaults_off(self):
        out = core.clean_text("吾輩(わがはい)は猫である。")
        assert "(わがはい)" in out


# ============================================================
#  denoise_capture（画面キャプチャのノイズ除去）
# ============================================================
_FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _load_fixture(name):
    with open(os.path.join(_FIXTURE_DIR, name), encoding="utf-8") as f:
        return f.read()


# 日本語の見出し＋本文を含む文脈（doc全体が日本語主体だと認識させる）。
_JP_CONTEXT = ("トランプ大統領がホルムズ海峡封鎖を警告\n"
               "アメリカのトランプ大統領は封鎖を表明した。")


def _denoise_lines(noise_lines):
    """ノイズ行を日本語文脈に混ぜて denoise_capture し、残った行を返す。"""
    raw = _JP_CONTEXT + "\n" + "\n".join(noise_lines)
    return core.denoise_capture(raw).split("\n")


class TestDenoiseCapture:
    def test_drops_short_english_overlay(self):
        # 局ロゴ・英字オーバーレイの短い断片は落ちる（矢印＋短い英字も含む）
        left = _denoise_lines(["NEWS", "THE", "TIME，", "LIVE", "← EXIT"])
        for x in ["NEWS", "THE", "TIME，", "LIVE", "← EXIT"]:
            assert x not in left

    def test_drops_standalone_timestamp(self):
        assert "06:02" not in _denoise_lines(["06:02"])
        assert "6:02" not in _denoise_lines(["6:02"])

    def test_drops_standalone_date(self):
        left = _denoise_lines(["7/14（火）", "2026年7月14日（火）06:02", "7月14日"])
        for x in ["7/14（火）", "2026年7月14日（火）06:02", "7月14日"]:
            assert x not in left

    def test_drops_standalone_number(self):
        left = _denoise_lines(["1.04", "1.2万", "1,234件"])
        for x in ["1.04", "1.2万", "1,234件"]:
            assert x not in left

    def test_drops_sns_handle(self):
        left = _denoise_lines(["@rakuyou211", "＠news_bot"])
        assert "@rakuyou211" not in left
        assert "＠news_bot" not in left

    def test_drops_symbol_only_lines(self):
        # カテゴリE（記号だけの行）＝ _denoise_symbol_only が拾う純粋な記号行のみ。
        assert core._denoise_symbol_only("▶")
        assert core._denoise_symbol_only("■ ■ ■")
        assert not core._denoise_symbol_only("← EXIT")  # 英字混じりはEではない
        left = _denoise_lines(["▶", "■ ■ ■", "→→→", "【】"])
        for x in ["▶", "■ ■ ■", "→→→", "【】"]:
            assert x not in left

    def test_keeps_headline_and_body(self):
        left = _denoise_lines(["NEWS", "06:02"])
        assert "トランプ大統領がホルムズ海峡封鎖を警告" in left
        assert "アメリカのトランプ大統領は封鎖を表明した。" in left

    # ---- 保護ルール（本文の取りこぼし防止） ----
    def test_strong_jp_signal_helper(self):
        # 本文シグナル判定の単体テスト。句読点“または”助詞（ひらがな）単独でも本文と判る。
        # ひらがな枝（句読点なし）を独立して固定する回帰テスト。
        assert core._denoise_has_strong_jp("あ")            # ひらがな単独
        assert core._denoise_has_strong_jp("14日にも会談")  # 助詞のみ・句点なし
        assert core._denoise_has_strong_jp("表明した")      # 送り仮名
        assert core._denoise_has_strong_jp("再開。")        # 句読点のみ
        assert not core._denoise_has_strong_jp("NEWS")
        assert not core._denoise_has_strong_jp("06:02")
        assert not core._denoise_has_strong_jp("ホルムズ")   # カタカナだけは強シグナル扱いしない

    def test_protects_date_inside_sentence_with_punctuation(self):
        # 文中に現れる日付は“文”なので残す（句読点あり）
        out = core.denoise_capture("2026年7月14日に表明した。")
        assert out == "2026年7月14日に表明した。"

    def test_protects_date_inside_sentence_particle_only(self):
        # 句読点が無く助詞（ひらがな）だけで本文と判る行も残す（ひらがな枝を独立検証）
        left = _denoise_lines(["2026年7月14日にも会談"])
        assert "2026年7月14日にも会談" in left

    def test_protects_line_with_brackets(self):
        left = _denoise_lines(["「20%の払い戻しを求める」"])
        assert "「20%の払い戻しを求める」" in left

    def test_keeps_long_katakana_headline(self):
        # ひらがなが無くても、長いカタカナ見出しは（英字断片ではないので）残る
        left = _denoise_lines(["ホルムズカイキョウフウサソチ"])
        assert "ホルムズカイキョウフウサソチ" in left

    def test_protects_short_kanji_line_with_numbers(self):
        # 数字・記号・英字が多くても、漢字/かな（日本語の中身）を含む短い見出しは残す。
        # 英字だけの断片（THE/NEWS）と切り分ける回帰テスト。
        keep = ["20%減", "iOS版", "第3四半期", "3位に転落", "GDP速報値"]
        left = _denoise_lines(keep)
        for x in keep:
            assert x in left, f"body line wrongly dropped: {x!r}"

    def test_drops_long_english_block_in_jp_doc(self):
        # 日本語主体の文書に埋め込まれた英文ブロック（長文）も落とす
        eng = ("The Hormuz Strait is OPEN and will remain OPEN "
               "with or without Iran as we reinstate the blockade")
        left = _denoise_lines([eng])
        assert eng not in left

    def test_drops_mostly_english_line_with_few_kanji(self):
        # 漢字が少し混じっていても、日本語比率がごく僅かなら外国語ブロックとして落とす
        line = "known as THE GUARDIAN OF THE HORMUZ STRAIT such and トランプ大統領"
        assert line not in _denoise_lines([line])

    def test_keeps_mostly_japanese_labels(self):
        keep = ["国際", "ホルムズ海峡", "20%減", "iOS版"]
        left = _denoise_lines(keep)
        for x in keep:
            assert x in left, f"jp label wrongly dropped: {x!r}"

    # ---- 英語主体の入力を誤って全消ししない ----
    def test_english_dominant_input_not_gutted(self):
        raw = "BREAKING NEWS\nTHE TIMES\nHormuz Strait Update"
        out = core.denoise_capture(raw)
        for x in ["BREAKING NEWS", "THE TIMES", "Hormuz Strait Update"]:
            assert x in out.split("\n")

    def test_short_english_kept_without_japanese_context(self):
        # 日本語文脈が無ければ短い英字行も残す（doc門番がOFF）
        assert core.denoise_capture("THE") == "THE"

    def test_doc_gate_ratio_branch(self):
        # 日本語量は20字未満だが比率>=0.5 → 比率ブランチで日本語主体と判定し英字断片を落とす
        raw = "封鎖措置を警告\nNEWS"          # CJK 7字(<20) / 比率 7/11≈0.64(>=0.5)
        left = core.denoise_capture(raw).split("\n")
        assert "NEWS" not in left
        assert "封鎖措置を警告" in left

    def test_date_scan_linear_no_redos(self):
        # スラッシュ数字の長い羅列でも線形時間で終わる（破滅的バックトラック回帰）
        import time
        s = "1/12" * 400 + "a"
        t = time.time()
        core.denoise_capture(s)
        assert time.time() - t < 1.0

    def test_strict_noise_empty_string_safe(self):
        # 空文字でも s[0] で落ちない
        assert core._denoise_is_strict_noise("") is False

    def test_empty_input(self):
        assert core.denoise_capture("") == ""

    def test_blank_lines_only(self):
        assert core.denoise_capture("\n  \n\t\n").strip() == ""

    def test_blank_only_input(self):
        assert core.denoise_capture("\n\n").strip() == ""


class TestDenoiseFixture:
    """今回のニュース（トランプ/ホルムズ海峡）の生OCRをフィクスチャ化した回帰テスト。"""

    def test_hormuz_news_overlay_removed_body_kept(self):
        raw = _load_fixture("news_hormuz_ocr.txt")
        left = core.denoise_capture(raw).split("\n")
        # 映像内オーバーレイ（局ロゴ・時刻・日付・数値・矢印・英字断片）は消える
        for noise in ["THE", "NEWS", "TIME，", "← EXIT", "7/14（火）",
                      "1.04", "2026年7月14日（火）06:02", "Pre：", "sec'"]:
            assert noise not in left, f"noise line should be dropped: {noise!r}"
        # 見出し・本文の日本語行は残る
        assert any("各国に請求" in ln for ln in left)
        assert any("アメリカのトランプ大統領は" in ln for ln in left)
        assert any("封鎖措置について" in ln for ln in left)
        # 実際にノイズが減っている
        assert len(left) < len(raw.split("\n"))


class TestCleanTextDenoise:
    _RAW = ("トランプ大統領がホルムズ海峡の封鎖を警告。\nNEWS\n06:02\n@rakuyou211\n"
            "アメリカのトランプ大統領は封鎖措置を表明した。")

    def test_denoise_defaults_to_false(self):
        import inspect
        assert inspect.signature(core.clean_text).parameters["denoise"].default is False

    def test_denoise_false_matches_golden(self):
        # 後方互換の本丸: denoise=False の出力を固定の期待値で凍結する。
        # denoise=False は denoise 前処理を丸ごとスキップする＝機能追加前と同一経路なので、
        # この期待値は「従来出力」を表す。clean_text 共有ロジックの将来の退行をここで捕まえる。
        raw = "こ れ は\r\nテスト です。 Excel 365\n次の 行。"
        assert core.clean_text(raw, denoise=False) == \
            "これは\nテストです。\nExcel 365\n次の行。"

    def test_denoise_false_byte_identical_to_default_on_fixture(self):
        # フィクスチャ全体でも既定＝denoise=False（引数を付けても従来経路のまま）
        raw = _load_fixture("news_hormuz_ocr.txt")
        for mode in ("sentence", "keep"):
            assert core.clean_text(raw, mode=mode) == \
                core.clean_text(raw, mode=mode, denoise=False)

    def test_denoise_false_keeps_overlay(self):
        out = core.clean_text(self._RAW, denoise=False)
        assert "NEWS" in out and "06:02" in out and "@rakuyou211" in out

    def test_denoise_true_removes_overlay(self):
        out = core.clean_text(self._RAW, denoise=True)
        assert "NEWS" not in out
        assert "06:02" not in out
        assert "@rakuyou211" not in out
        assert "トランプ大統領がホルムズ海峡の封鎖を警告。" in out
        assert "アメリカのトランプ大統領は封鎖措置を表明した。" in out

    def test_denoise_keeps_body_date_drops_standalone_datetime(self):
        raw = ("米、ホルムズ海峡巡り警告\n"
               "2026年7月14日（火）06:02\n"
               "2026年7月14日に表明した。")
        out = core.clean_text(raw, mode="keep", denoise=True)
        assert "2026年7月14日に表明した。" in out.split("\n")
        assert "2026年7月14日（火）06:02" not in out.split("\n")


class TestCliDenoiseOcrOnly:
    """v1.16.0: denoise はOCR由来テキスト限定。テキストファイルは本文100%なので
    英文行・時刻らしき行も本文として残す（小説の英文引用・年号マーカー保護）。"""
    _RAW = ("トランプ大統領がホルムズ海峡封鎖を警告\nNEWS\n06:02\n"
            "アメリカのトランプ大統領は表明した。")

    def test_txt_input_keeps_overlay_like_lines(self, tmp_path):
        import cli
        src = tmp_path / "novel.txt"
        src.write_text(self._RAW, encoding="utf-8")
        out = tmp_path / "o"
        rc = cli.main([str(src), "-o", str(out)])   # denoise 既定ONのまま
        assert rc == 0
        text = (out / "voicevox_text.txt").read_text(encoding="utf-8")
        assert "NEWS" in text and "06:02" in text
        assert "アメリカのトランプ大統領は表明した。" in text

    def test_no_denoise_flag_also_keeps(self, tmp_path):
        import cli
        src = tmp_path / "novel.txt"
        src.write_text(self._RAW, encoding="utf-8")
        out = tmp_path / "o"
        rc = cli.main([str(src), "-o", str(out), "--no-denoise"])
        assert rc == 0
        text = (out / "voicevox_text.txt").read_text(encoding="utf-8")
        assert "NEWS" in text and "06:02" in text

    def test_extract_files_txt_not_denoised(self, tmp_path):
        # コア関数レベルでも: denoise=True はテキスト系入力に触れない
        src = tmp_path / "novel.txt"
        src.write_text(self._RAW, encoding="utf-8")
        text, warnings = core.extract_files([str(src)], denoise=True)
        assert "NEWS" in text and "06:02" in text


class TestFmtDuration:
    def test_seconds(self):
        assert core.fmt_duration(42) == "約42秒"

    def test_minutes(self):
        assert core.fmt_duration(125) == "約2分05秒"

    def test_hours(self):
        assert core.fmt_duration(3720) == "約1時間02分"

    def test_negative_clamped(self):
        assert core.fmt_duration(-3) == "約0秒"


# ============================================================
#  extract_epub のhref解決（パーセントエンコード / 相対パス / 全欠落）
# ============================================================
def _make_epub_split_href(path, entries):
    """zip実体名とOPF hrefを別々に指定できるテスト用epub。
    entries: [(zip_name, href, html), ...]"""
    import zipfile
    container = ('<?xml version="1.0"?>'
                 '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>')
    items = "".join(f'<item id="c{i}" href="{href}" media-type="application/xhtml+xml"/>'
                    for i, (_zn, href, _h) in enumerate(entries))
    refs = "".join(f'<itemref idref="c{i}"/>' for i in range(len(entries)))
    opf = ('<?xml version="1.0"?>'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
           f'<manifest>{items}</manifest><spine>{refs}</spine></package>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        for zn, _href, html in entries:
            z.writestr(f"OEBPS/{zn}", html)


class TestExtractEpubHrefResolution:
    def test_percent_encoded_japanese_href(self, tmp_path):
        from urllib.parse import quote
        p = str(tmp_path / "enc.epub")
        zn = "第1章.xhtml"
        _make_epub_split_href(p, [
            (zn, quote(zn), "<html><body><p>符号化された章。</p></body></html>")])
        assert core.extract_epub(p) == "符号化された章。"

    def test_space_in_filename_href(self, tmp_path):
        p = str(tmp_path / "sp.epub")
        _make_epub_split_href(p, [
            ("Chapter 1.xhtml", "Chapter%201.xhtml",
             "<html><body><p>空白入りの章。</p></body></html>")])
        assert core.extract_epub(p) == "空白入りの章。"

    def test_all_chapters_missing_raises(self, tmp_path):
        # hrefがどのzip実体にも一致しない → 無言で空を返さず例外にする
        p = str(tmp_path / "bad.epub")
        _make_epub_split_href(p, [
            ("real.xhtml", "does-not-exist.xhtml",
             "<html><body><p>x</p></body></html>")])
        with pytest.raises(RuntimeError):
            core.extract_epub(p)


# ============================================================
#  extract_files が一時ディレクトリを残さないこと
# ============================================================
def test_extract_files_cleans_temp_dir(tmp_path):
    import glob
    import tempfile as _tf
    src = tmp_path / "a.txt"
    src.write_text("本文だけ。", encoding="utf-8")
    pat = os.path.join(_tf.gettempdir(), "t2v_img_*")
    before = set(glob.glob(pat))
    text, warnings = core.extract_files([str(src)])
    after = set(glob.glob(pat))
    assert text == "本文だけ。"
    assert after <= before  # 新規に作られたt2v_img_一時ディレクトリが残っていない


# ============================================================
#  cli.py: 分割出力のSRT / @話者タグの解釈
# ============================================================
class TestCliSrtPerFile:
    def test_srt_saved_for_each_unit(self, tmp_path, monkeypatch):
        """v1.16.0: --srt は結合時だけでなく分割出力でもファイルごとに保存される。"""
        import cli
        src = tmp_path / "n.txt"
        src.write_text("一文目。二文目。", encoding="utf-8")
        speakers = [("ずんだもん（ノーマル）", 3, "uz")]
        monkeypatch.setattr(core, "vv_check", lambda url, timeout=3: "0.0.0")
        monkeypatch.setattr(core, "vv_speakers", lambda url, timeout=10: speakers)
        monkeypatch.setattr(core, "vv_synthesize_one",
                            lambda url, text, sid, **kw: _make_wav(0.05))
        out = tmp_path / "o"
        rc = cli.main([str(src), "-o", str(out), "--wav", "--srt"])
        assert rc == 0
        assert (out / "001.wav").exists() and (out / "001.srt").exists()
        assert (out / "002.wav").exists() and (out / "002.srt").exists()


class TestCliSpeakerTags:
    def test_tags_stripped_and_routed(self, tmp_path, monkeypatch):
        import cli
        src = tmp_path / "s.txt"
        src.write_text("@ずんだもん: こんにちは\n地の文です。", encoding="utf-8")
        speakers = [("ずんだもん（ノーマル）", 3, "uz"),
                    ("四国めたん（ノーマル）", 2, "um")]
        monkeypatch.setattr(core, "vv_check", lambda url, timeout=3: "0.0.0")
        monkeypatch.setattr(core, "vv_speakers", lambda url, timeout=10: speakers)
        calls = []

        def fake_synth(url, text, sid, **kw):
            calls.append((text, sid))
            return _make_wav(0.05)

        monkeypatch.setattr(core, "vv_synthesize_one", fake_synth)
        monkeypatch.setattr(core, "audio_encoders", lambda: {})
        rc = cli.main([str(src), "-o", str(tmp_path / "o"),
                       "--wav", "--speaker", "ずんだもん"])
        assert rc == 0
        # タグは読み上げず、その行は指定話者(3)で。地の文も既定話者(3)。
        assert ("こんにちは", 3) in calls
        assert ("地の文です。", 3) in calls
        assert all("@" not in t for t, _ in calls)


# ============================================================
#  split_sentences（括弧・連続終端記号の対応）
# ============================================================
class TestSplitSentencesBrackets:
    def test_quote_not_split_inside(self):
        assert core.split_sentences("「もう帰る。」と彼は言った。") == \
            ["「もう帰る。」と彼は言った。"]

    def test_consecutive_enders_grouped(self):
        assert core.split_sentences("えっ！？そうなの？") == ["えっ！？", "そうなの？"]

    def test_stray_closer_attached(self):
        # 前行から迷い込んだ閉じ括弧は前の文に取り込む（現行の「」B分断より自然）
        assert core.split_sentences("A。」B") == ["A。」", "B"]

    def test_quote_inside_sentence(self):
        assert core.split_sentences("彼は「危ない！」と叫んだ。") == \
            ["彼は「危ない！」と叫んだ。"]

    def test_halfwidth_parens(self):
        # normalize_ascii 適用後の (…?) でも分割しない
        assert core.split_sentences("(テスト?)です。次の文。") == \
            ["(テスト?)です。", "次の文。"]

    def test_unclosed_bracket_reset_by_newline(self):
        # 開き括弧が閉じないOCRテキストでも改行で必ず切れる（行を跨いで巻き込まない）
        assert core.split_sentences("「壊れた行。\n次の文。") == \
            ["「壊れた行。", "次の文。"]

    def test_orphan_fullwidth_closer_clamped(self):
        # 開きの無い】で深さが負にならず、以降の分割が正常に続く
        assert core.split_sentences("見出し】本文です。次です。") == \
            ["見出し】本文です。", "次です。"]

    def test_multiline_dialogue_single_unit(self):
        assert core.split_sentences("「そうか。それなら行こう。」") == \
            ["「そうか。それなら行こう。」"]


# ============================================================
#  fix_ocr_confusables（同形文字の文脈補正）
# ============================================================
class TestFixOcrConfusables:
    def test_choonpu_from_ichi(self):
        assert core.fix_ocr_confusables("サ一ビスを開始") == "サービスを開始"
        assert core.fix_ocr_confusables("エラ一コ一ド") == "エラーコード"

    def test_ichi_from_choonpu_between_kanji(self):
        assert core.fix_ocr_confusables("第ー章") == "第一章"

    def test_real_ichi_protected(self):
        # 直後がひらがな・漢字なら本物の「一」（助数詞・固有名詞）
        for s in ("メロン一つ", "アメリカ一の", "ケーキ一個", "一ノ瀬さん"):
            assert core.fix_ocr_confusables(s) == s

    def test_kanji_to_katakana(self):
        assert core.fix_ocr_confusables("デジ夕ル") == "デジタル"
        assert core.fix_ocr_confusables("卜ヨタと二ュース") == "トヨタとニュース"
        assert core.fix_ocr_confusables("口ッカーと工アコン") == "ロッカーとエアコン"

    def test_kanji_words_protected(self):
        # 漢字文脈・実在語は変換しない
        for s in ("口コミを見る", "その口コミが", "お口ケアの話", "夕方のこと",
                  "力士の力", "山口ロープウェイ", "工場で働く", "二人の話",
                  # 口・二はカタカナ語との実在複合語が多く、語頭変換は
                  # 小書きカナ・長音が続くときだけ（口ボットは既知の限界として温存）
                  "彼は口パクだった", "入り口ドア", "口ボット",
                  "あと二ヶ月で完成", "まだ二カ月かかる", "二チームに分けた",
                  "残り二コマ", "百カ日の法要"):
            assert core.fix_ocr_confusables(s) == s

    def test_compound_choonpu_misread(self):
        # 「ー→一」誤認の隣のカタカナを漢字と誤判定しない（2パス順序の回帰テスト）
        assert core.fix_ocr_confusables("顧客ニ一ズ") == "顧客ニーズ"
        assert core.fix_ocr_confusables("診察カ一ド") == "診察カード"

    def test_katakana_to_kanji(self):
        assert core.fix_ocr_confusables("入カ完了") == "入力完了"
        assert core.fix_ocr_confusables("人エ知能") == "人工知能"
        assert core.fix_ocr_confusables("第ニ章") == "第二章"

    def test_counter_and_prev_guards(self):
        # 「数カ月」等の助数詞・「誰カ」等は変換しない
        for s in ("数カ月と何カ国", "十数カ所", "誰カが来た", "入カした"):
            assert core.fix_ocr_confusables(s) == s

    def test_digit_tokens(self):
        assert core.fix_ocr_confusables("2O26年1l時30分") == "2026年11時30分"

    def test_alnum_tokens_protected(self):
        for s in ("H2Oの化学式", "500mlのペットボトル", "Illustrator",
                  # 英字がトークン先頭に固まる形は実在の型番・記号名
                  "O157が検出された", "O2センサー", "型番はl0です"):
            assert core.fix_ocr_confusables(s) == s

    def test_context_skips_ocr_spaces(self):
        # OCR特有の文字間空白があっても前後の実文字で判定する
        assert core.fix_ocr_confusables("サ 一 ビス") == "サ ー ビス"

    def test_empty(self):
        assert core.fix_ocr_confusables("") == ""


# ============================================================
#  expand_readable_chars（読み上げ困難な記号の展開）
# ============================================================
class TestExpandReadableChars:
    def test_circled_numbers(self):
        assert core.expand_readable_chars("①と⑳と㉑と㊿") == "1と20と21と50"

    def test_consecutive_numbers_separated(self):
        # 連続する丸数字・隣接する算用数字と連結して別の数を作らない
        assert core.expand_readable_chars("手順は①②③の順") == "手順は1、2、3の順"
        assert core.expand_readable_chars("手順①2番目") == "手順1、2番目"

    def test_roman_numerals(self):
        assert core.expand_readable_chars("Ⅲ章とⅻ") == "3章と12"

    def test_company_and_units(self):
        assert core.expand_readable_chars("㈱山田と50㎡と25℃") == \
            "株式会社山田と50平方メートルと25度"

    def test_plain_text_unchanged(self):
        s = "普通のテキスト ABC 123。"
        assert core.expand_readable_chars(s) == s

    def test_clean_text_normalize_applies(self):
        out = core.clean_text("①これは㈱テストの話。", normalize=True)
        assert out == "1これは株式会社テストの話。"

    def test_clean_text_without_normalize_keeps(self):
        out = core.clean_text("①これは㈱テストの話。", normalize=False)
        assert "①" in out and "㈱" in out


# ============================================================
#  denoise_capture（クレジット行・数値行・英数見出しの改善）
# ============================================================
class TestDenoiseCaptureNew:
    def test_photo_credit_dropped(self):
        raw = "本文の記事です。\n写真：ロイター\n撮影＝共同"
        out = core.denoise_capture(raw)
        assert "写真：ロイター" not in out
        assert "撮影＝共同" not in out
        assert "本文の記事です。" in out

    def test_credit_sentence_with_hiragana_kept(self):
        raw = "本文の記事です。\n写真は田中さんの提供です"
        assert "写真は田中さんの提供です" in core.denoise_capture(raw)

    def test_count_with_double_suffix_dropped(self):
        raw = "本文の記事です。\n1.2万人\n1,234万回"
        out = core.denoise_capture(raw)
        assert "1.2万人" not in out
        assert "1,234万回" not in out

    def test_count_sentence_kept(self):
        raw = "本文の記事です。\n1.2万人が視聴した。"
        assert "1.2万人が視聴した。" in core.denoise_capture(raw)

    def test_single_suffix_counts_still_dropped(self):
        # 従来から落ちていた単独カウント行の回帰ガード
        raw = "本文の記事です。\n1.2K\n3M\n1位\n50%"
        out = core.denoise_capture(raw)
        for noise in ("1.2K", "3M", "1位", "50%"):
            assert noise not in out.split("\n")

    def test_price_not_strict_noise(self):
        # 「円」はカウント接尾辞に含めない（価格はラベル除去・非日本語文書でも消さない）
        assert not core._denoise_is_strict_noise("1,980円")
        assert core._denoise_is_strict_noise("1.2万人")

    def test_product_headline_kept(self):
        raw = "日本語の記事本文です。\niPhone 17 Pro Max\nNintendo Switch 2"
        out = core.denoise_capture(raw)
        assert "iPhone 17 Pro Max" in out
        assert "Nintendo Switch 2" in out

    def test_ascii_fragments_still_dropped(self):
        # 英字だけ・数字だけの断片は従来どおり落ちる（日本語主体の文書で判定が効く）
        raw = ("日本語の記事本文がここにあります。\n"
               "記事の続きも日本語で書かれています。\n"
               "THE\nNEWS\n2026 07 14")
        out = core.denoise_capture(raw)
        for noise in ("THE", "NEWS", "2026 07 14"):
            assert noise not in out.split("\n")

    def test_english_sns_overlays_still_dropped(self):
        # 製品名保護がSNSの視聴数・経過時間・英語日付まで残さない（v1.11相当を維持）
        raw = ("日本語の記事本文がここにあります。\n"
               "記事の続きも日本語で書かれています。\n"
               "1.2K views\n10K views\nposted 3 hours ago\nJul 14, 2026")
        out = core.denoise_capture(raw)
        for noise in ("1.2K views", "10K views", "posted 3 hours ago",
                      "Jul 14, 2026"):
            assert noise not in out.split("\n")


# ============================================================
#  strip_aozora（踊り字・くの字点・底本フッターの拡充）
# ============================================================
class TestStripAozoraExpanded:
    def test_odoriji_expanded(self):
        assert core.strip_aozora("こゝろ") == "こころ"
        assert core.strip_aozora("つゞく") == "つづく"
        assert core.strip_aozora("バナヽ") == "バナナ"

    def test_odoriji_at_head_untouched(self):
        # 直前がかなでない踊り字は展開しない（句読点・文頭の複製防止）
        assert core.strip_aozora("ゝあ") == "ゝあ"
        assert core.strip_aozora("。ゝ") == "。ゝ"

    def test_kunoji_expanded(self):
        assert core.strip_aozora("どき／＼した") == "どきどきした"
        assert core.strip_aozora("しみ／″＼と") == "しみじみと"

    def test_kunoji_after_non_kana_untouched(self):
        # AA的な／＼は展開しない
        assert core.strip_aozora("矢印／＼記号") == "矢印／＼記号"

    def test_kunoji_ambiguous_unit_untouched(self):
        # 直前2文字のさらに前もかな＝繰り返し単位が2文字と確定できない場合は
        # 展開しない（かはる／″＼を「かはるばる」にしない）
        assert core.strip_aozora("かはる／″＼") == "かはる／″＼"
        assert core.strip_aozora("おもひ／＼に") == "おもひ／＼に"

    def test_footer_removed(self):
        body = "本文です。\n" * 10
        raw = body + "底本：「吾輩は猫である」新潮文庫\n1990年発行"
        out = core.strip_aozora(raw)
        assert "底本：" not in out
        assert "本文です。" in out

    def test_footer_like_line_in_first_half_kept(self):
        # テキスト前半の「底本：」は削らない（連結txtの安全弁）
        raw = "底本：メモ\n" + "本文です。\n" * 20
        assert "底本：メモ" in core.strip_aozora(raw)

    def test_concat_works_last_footer_only(self):
        # 複数作品の連結txt: 中間の底本行で後続作品を巻き込まず、末尾の奥付だけ削る
        raw = ("作品Aの本文です。\n" * 30
               + "底本：「作品A」文庫X\n"
               + "作品Bの本文です。\n" * 20
               + "底本：「作品B」文庫Y\n1990年発行")
        out = core.strip_aozora(raw)
        assert "作品Bの本文です。" in out
        assert "底本：「作品B」文庫Y" not in out

    def test_mid_footer_never_deletes_following_work(self):
        # 末尾に底本が無い連結txtでは、中間の底本行の後に続く作品を削らない
        # （底本以降が _AOZORA_FOOTER_MAX_CHARS を超える＝奥付ではなく本文とみなす）
        raw = ("作品Aの本文です。\n" * 30
               + "底本：「作品A」文庫X\n"
               + "作品Bの本文です。\n" * 300)
        out = core.strip_aozora(raw)
        assert "作品Bの本文です。" in out

    def test_symbol_note_block_removed(self):
        raw = ("タイトル\n"
               + "-" * 20 + "\n"
               + "【テキスト中に現れる記号について】\n"
               + "《》：ルビ\n"
               + "-" * 20 + "\n"
               + "本文が始まります。")
        out = core.strip_aozora(raw)
        assert "記号について" not in out
        assert "本文が始まります。" in out

    def test_plain_hr_lines_kept(self):
        # 記号説明の見出しが無い水平線は本文の一部として残す
        raw = "前半\n" + "-" * 20 + "\n中身\n" + "-" * 20 + "\n後半"
        assert core.strip_aozora(raw) == raw


# ============================================================
#  preprocess_image（透過白合成・モード正規化・OS別の上限とグレースケール）
# ============================================================
class TestPreprocessImage:
    def _img(self, mode, size, color=None):
        from PIL import Image
        return Image.new(mode, size, color) if color is not None \
            else Image.new(mode, size)

    def test_transparent_composited_white(self):
        out = core.preprocess_image(
            self._img("RGBA", (100, 100), (0, 0, 0, 0)), enable=False)
        assert out.mode == "RGB"
        assert out.getpixel((50, 50)) == (255, 255, 255)

    def test_cmyk_normalized_for_png(self):
        out = core.preprocess_image(self._img("CMYK", (50, 50)), enable=False)
        assert out.mode in ("RGB", "L")

    def test_disable_keeps_color_and_size_cap_only(self, monkeypatch):
        monkeypatch.setattr(core, "IS_WIN", False)
        monkeypatch.setattr(core, "IS_MAC", True)
        out = core.preprocess_image(
            self._img("RGB", (5000, 100), (10, 20, 30)), enable=False)
        assert out.mode == "RGB"
        assert max(out.size) == 4000

    def test_mac_keeps_color_when_enabled(self, monkeypatch):
        monkeypatch.setattr(core, "IS_WIN", False)
        monkeypatch.setattr(core, "IS_MAC", True)
        out = core.preprocess_image(self._img("RGB", (2000, 1000), (200, 30, 30)))
        assert out.mode == "RGB"

    def test_windows_caps_at_ocr_limit(self, monkeypatch):
        monkeypatch.setattr(core, "IS_WIN", True)
        monkeypatch.setattr(core, "IS_MAC", False)
        # A4×300dpi相当がOcrEngineの上限(2600)以内に収まる
        out = core.preprocess_image(self._img("RGB", (3508, 2480), "white"))
        assert max(out.size) <= core.WIN_OCR_MAX_DIM
        # 2倍拡大後も上限を超えない
        out2 = core.preprocess_image(self._img("RGB", (1400, 1000), "white"))
        assert max(out2.size) <= core.WIN_OCR_MAX_DIM
        assert out2.mode == "L"   # Windowsはグレースケール強調

    def test_non_windows_keeps_4000_cap(self, monkeypatch):
        monkeypatch.setattr(core, "IS_WIN", False)
        monkeypatch.setattr(core, "IS_MAC", True)
        out = core.preprocess_image(self._img("RGB", (5000, 1000), "white"))
        assert max(out.size) == 4000


# ============================================================
#  _parse_windows_ocr_result（Windows OCRの座標パイプライン）
# ============================================================
class TestParseWindowsOcrResult:
    # 折り返された2行（右端まで届く1行目）→ reflow で1文に連結される
    _WRAPPED = [
        {"text": "これは長い本文の一行目でありまして", "x0": 0.05, "x1": 0.95,
         "y0": 0.30, "y1": 0.34},
        {"text": "二行目に続きます。", "x0": 0.05, "x1": 0.60,
         "y0": 0.35, "y1": 0.39},
    ]

    def test_lines_reflowed(self):
        data = [{"path": "a.png", "ok": True,
                 "text": "これは長い本文の一行目でありまして\n二行目に続きます。",
                 "lines": self._WRAPPED}]
        out = core._parse_windows_ocr_result(data)
        assert out["a.png"] == "これは長い本文の一行目でありまして二行目に続きます。"

    def test_old_format_uses_text(self):
        data = [{"path": "b.png", "ok": True, "text": "旧形式テキスト"}]
        assert core._parse_windows_ocr_result(data)["b.png"] == "旧形式テキスト"

    def test_broken_lines_fall_back_to_text(self):
        data = [{"path": "c.png", "ok": True, "text": "fallback",
                 "lines": [{"broken": True}]}]
        assert core._parse_windows_ocr_result(data)["c.png"] == "fallback"

    def test_failed_item_empty(self):
        data = [{"path": "d.png", "ok": False, "text": "", "error": "x"}]
        assert core._parse_windows_ocr_result(data)["d.png"] == ""

    def test_single_dict_lines_wrapped(self):
        # ConvertTo-Json が1要素配列をオブジェクトに畳んだ場合
        data = [{"path": "e.png", "ok": True, "text": "一行だけ",
                 "lines": {"text": "一行だけ", "x0": 0.1, "x1": 0.5,
                           "y0": 0.1, "y1": 0.15}}]
        assert core._parse_windows_ocr_result(data)["e.png"] == "一行だけ"

    def test_strip_labels_toggle(self):
        # 本文ブロック＋最上部の孤立短ラベル。strip_labels=True でのみ除去される
        body = [
            {"text": f"本文の段落{i}行目がここに続いています。", "x0": 0.05,
             "x1": 0.95, "y0": 0.30 + i * 0.05, "y1": 0.33 + i * 0.05}
            for i in range(4)
        ]
        label = {"text": "NEWSロゴ", "x0": 0.02, "x1": 0.18,
                 "y0": 0.02, "y1": 0.05}
        data = [{"path": "f.png", "ok": True, "text": "raw",
                 "lines": [label] + body}]
        with_strip = core._parse_windows_ocr_result(data, strip_labels=True)
        without = core._parse_windows_ocr_result(data, strip_labels=False)
        assert "NEWSロゴ" not in with_strip["f.png"]
        assert "NEWSロゴ" in without["f.png"]


# ============================================================
#  extract_files の fix_confusables（OCR由来テキストのみ補正）
# ============================================================
class TestExtractFilesFixConfusables:
    def _fake_ocr(self, monkeypatch, result_text):
        monkeypatch.setattr(
            core, "run_ocr",
            lambda paths, lang="ja", strip_labels=True, errors=None, **kw:
                {p: result_text for p in paths})

    def _png(self, tmp_path):
        from PIL import Image
        p = tmp_path / "img.png"
        Image.new("RGB", (200, 100), "white").save(p)
        return str(p)

    def test_ocr_text_fixed_when_enabled(self, tmp_path, monkeypatch):
        self._fake_ocr(monkeypatch, "卜ヨタのサ一ビス")
        text, warnings = core.extract_files([self._png(tmp_path)],
                                            fix_confusables=True)
        assert text == "トヨタのサービス"
        assert not warnings

    def test_ocr_text_untouched_when_disabled(self, tmp_path, monkeypatch):
        self._fake_ocr(monkeypatch, "卜ヨタのサ一ビス")
        text, _ = core.extract_files([self._png(tmp_path)],
                                     fix_confusables=False)
        assert text == "卜ヨタのサ一ビス"

    def test_text_layer_never_fixed(self, tmp_path):
        # txt入力（テキスト層）には fix_confusables=True でも適用しない
        src = tmp_path / "novel.txt"
        src.write_text("口コミとサ一ビス", encoding="utf-8")
        text, _ = core.extract_files([str(src)], fix_confusables=True)
        assert text == "口コミとサ一ビス"


# ============================================================
#  段組の列分割（_split_columns / reflow_ocr_lines の読み順）
# ============================================================
class TestSplitColumns:
    def _col(self, x0, x1, n, prefix):
        # 縦に並んだn行の列を合成（右端まで届く行＝折り返し扱いになる長い行）
        return [{"text": f"{prefix}{i}", "x0": x0, "x1": x1,
                 "y0": 0.1 + i * 0.05, "y1": 0.13 + i * 0.05}
                for i in range(n)]

    def test_two_columns_read_in_order(self):
        left = self._col(0.05, 0.45, 4, "左")
        right = self._col(0.55, 0.95, 4, "右")
        # y順で交互に混ぜて入力しても、列単位（左→右）で出力される
        mixed = [l for pair in zip(left, right) for l in pair]
        out = core.reflow_ocr_lines(mixed)
        joined = out.replace("\n", "")
        assert joined.index("左3") < joined.index("右0")

    def test_narrow_label_column_not_split(self):
        # 設定画面型の「狭いラベル列/値列」は段組と誤検出しない（幅ガード）
        labels = self._col(0.05, 0.15, 4, "項目")   # 幅0.10 < 0.25
        values = self._col(0.30, 0.90, 4, "値")
        out = core.reflow_ocr_lines(labels + values)
        # 単一列扱い＝y順（ラベルと値が交互）のまま
        lines = out.split("\n")
        assert lines[0].startswith("項目0")
        assert "値0" in out

    def test_few_lines_not_split(self):
        # 各列1〜2行では列分割しない（行数ガード）
        a = self._col(0.05, 0.45, 2, "A")
        b = self._col(0.55, 0.95, 2, "B")
        out = core.reflow_ocr_lines(a + b)
        assert out  # クラッシュせず出力される（従来動作）


# ============================================================
#  章見出し検出（detect_chapters）
# ============================================================
class TestDetectChapters:
    def test_basic_headings(self):
        lines = ["第一章", "本文です。", "第2章 出会い", "続き。", "エピローグ"]
        assert core.detect_chapters(lines) == \
            [("第一章", 0), ("第2章 出会い", 2), ("エピローグ", 4)]

    def test_sentences_not_detected(self):
        lines = ["第一章では以下を説明します。",
                 "これはプロローグ的な話だが見出しではない文はどうかというと長い。"]
        assert core.detect_chapters(lines) == []

    def test_no_headings(self):
        assert core.detect_chapters(["ただの本文。", "続き。"]) == []


# ============================================================
#  整形レポート用ヘルパー
# ============================================================
class TestDenoiseRemovedLines:
    def test_removed_lines_listed(self):
        raw = "本文の記事です。\nNEWS\n06:02\n続きの本文です。"
        removed = core.denoise_removed_lines(raw)
        assert "NEWS" in removed and "06:02" in removed
        assert "本文の記事です。" not in removed

    def test_no_noise_empty(self):
        assert core.denoise_removed_lines("本文だけです。") == []


class TestVoicevoxCredit:
    def test_dedup_and_format(self):
        labels = ["ずんだもん（ノーマル）", "ずんだもん（あまあま）",
                  "四国めたん（ノーマル）"]
        assert core.voicevox_credit(labels) == \
            "VOICEVOX:ずんだもん、VOICEVOX:四国めたん"

    def test_empty(self):
        assert core.voicevox_credit([]) == ""


# ============================================================
#  低品質OCRの再判定（2パスリトライのしきい値）
# ============================================================
class TestOcrRetryHeuristics:
    def test_needs_retry_on_garbage(self):
        assert core._ocr_needs_retry("")                       # 空
        assert core._ocr_needs_retry("a1b2")                   # 少なすぎ
        assert core._ocr_needs_retry("!@#$%^&*()_+=~~~~|||")   # 日本語比率ゼロ

    def test_good_text_no_retry(self):
        assert not core._ocr_needs_retry("これは正常に読めた日本語の文章です。")

    def test_retry_needs_clear_win(self):
        # 僅差では差し替えない（1.2倍超のヒステリシス）
        assert not core._ocr_retry_better("日本語十文字の結果", "日本語十文字の結果あ")
        assert core._ocr_retry_better("あい", "しっかり読めた日本語の文章です")

    def test_flatten_illumination_shape(self):
        from PIL import Image
        img = Image.new("RGB", (200, 100), (120, 120, 120))
        out = core.flatten_illumination(img)
        assert out.mode == "L" and out.size == (200, 100)


class TestParseWindowsOcrErrors:
    def test_error_collected(self):
        data = [{"path": r"C:\img\a.png", "ok": False, "text": "",
                 "error": "boom"}]
        errors = []
        out = core._parse_windows_ocr_result(data, errors=errors)
        assert out[r"C:\img\a.png"] == ""
        assert errors and "boom" in errors[0]


class TestSplitColumnsChatGuard:
    def test_chat_bubbles_keep_time_order(self):
        # 左右の吹き出しが交互のチャットスクショは段組と誤検出しない
        # （行が同じ高さに並ばない＝対にならないため単一列扱い）
        chat = []
        for i in range(3):
            chat.append({"text": f"受信{i}のメッセージ本文です", "x0": 0.03,
                         "x1": 0.36, "y0": 0.05 + i * 0.20, "y1": 0.08 + i * 0.20})
            chat.append({"text": f"送信{i}のメッセージ本文です", "x0": 0.60,
                         "x1": 0.95, "y0": 0.15 + i * 0.20, "y1": 0.18 + i * 0.20})
        lines = core.reflow_ocr_lines(chat).split("\n")
        assert lines[0].startswith("受信0")
        assert lines[1].startswith("送信0")


class TestDetectChaptersBoundary:
    def test_prefix_sentences_not_detected(self):
        # 見出し語で始まる本文（前方一致）は境界チェックで除外される
        for s in ("その3人が事件の鍵を握る", "はじめに言葉ありき",
                  "終章のない物語だった", "その十年後…", "第一章、それは"):
            assert core.detect_chapters([s]) == [], s

    def test_headings_with_boundary_detected(self):
        assert core.detect_chapters(["第一章", "第2章 出会い", "序章：始まり"]) == \
            [("第一章", 0), ("第2章 出会い", 1), ("序章：始まり", 2)]


# ============================================================
#  v1.14.0: 読み上げ向け正規化・URL除去・章/読み系ヘルパー
# ============================================================
class TestNormalizeReadings:
    def test_thousands_separator_removed(self):
        assert core.normalize_readings("価格は1,234円と12,345,678円") == \
            "価格は1234円と12345678円"

    def test_list_commas_kept(self):
        # 桁区切りでないカンマ（後ろが3桁でない）は残す
        assert core.normalize_readings("1,23と1,2345") == "1,23と1,2345"

    def test_nakaguro_run_to_ellipsis(self):
        assert core.normalize_readings("待って・・・すごい・・・・") == \
            "待って…すごい…"
        assert core.normalize_readings("A・B") == "A・B"   # 区切りの中黒は残す

    def test_clean_text_normalize_applies(self):
        out = core.clean_text("売上は1,234円・・・", normalize=True)
        assert out == "売上は1234円…"


class TestStripUrls:
    def test_urls_and_emails_removed(self):
        raw = "詳細は https://example.com/news?id=1 とinfo@example.comへ。"
        out = core.strip_urls(raw)
        assert "https" not in out and "@" not in out
        assert "詳細は" in out and "へ。" in out

    def test_clean_text_remove_urls_option(self):
        raw = "本文です。\nhttps://example.com/x\n続きです。"
        out = core.clean_text(raw, remove_urls=True)
        assert "example" not in out
        assert "本文です。" in out and "続きです。" in out

    def test_default_off_keeps_urls(self):
        raw = "https://example.com"
        assert "example" in core.clean_text(raw)


class TestConfusablesHa:
    def test_ha_between_kanji_fixed(self):
        assert core.fix_ocr_confusables("十ハ番の演目") == "十八番の演目"

    def test_katakana_ha_words_protected(self):
        for s in ("ハイテクの話", "ハワイへ行く", "彼はハンサム",
                  # 調性のハ（クラシック曲名で頻出）は変換しない
                  "交響曲第5番ハ短調", "前奏曲嬰ハ長調"):
            assert core.fix_ocr_confusables(s) == s


class TestEstimateReadSeconds:
    def test_speed_scales(self):
        base = core.estimate_read_seconds("あ" * 320)
        assert 55 <= base <= 65          # 320字 ≒ 1分
        assert core.estimate_read_seconds("あ" * 320, speed=2.0) < base


class TestOcrRetryRotation:
    def test_rotation_candidates_tried(self, monkeypatch, tmp_path):
        # 1回目が低品質のとき、回転候補が試され最良が採用される
        from PIL import Image
        calls = []

        def fake_run_ocr(paths, lang="ja", strip_labels=True, errors=None):
            calls.append(paths[0])
            # 2番目の候補（rot90）だけ良い結果を返す
            if "rot90" in paths[0]:
                return {paths[0]: "回転したらしっかり読めた日本語の文章です。"}
            return {paths[0]: ""}
        monkeypatch.setattr(core, "run_ocr", fake_run_ocr)
        monkeypatch.setattr(core, "IS_MAC", True)
        img = Image.new("RGB", (200, 100), "white")
        out = core.ocr_retry_if_poor("", img, str(tmp_path))
        assert out == "回転したらしっかり読めた日本語の文章です。"
        assert any("flat" in c for c in calls)
        assert any("rot90" in c for c in calls)
        # 良い結果が出た時点で rot270 は試さない（早期打ち切り）
        assert not any("rot270" in c for c in calls)


class TestStripUrlsSurgical:
    def test_japanese_after_url_preserved(self):
        # URLの直後に空白なしで日本語が続く書き方（日本語では普通）で本文を消さない
        assert core.strip_urls("詳しくはhttps://example.com/infoをご覧ください。") == \
            "詳しくはをご覧ください。"
        assert core.strip_urls("公式サイト（https://example.com）で確認。") == \
            "公式サイト（）で確認。"

    def test_www_slang_not_eaten(self):
        # ネットスラングの w 連打 + 句点は www. と誤マッチしない
        assert core.strip_urls("面白すぎwww.まじで") == "面白すぎwww.まじで"

    def test_trailing_punct_kept(self):
        assert core.strip_urls("文末 https://example.com. 次") == "文末 . 次"

    def test_fullwidth_url_removed_with_normalize(self):
        out = core.clean_text("参考ｈｔｔｐｓ：／／ｅｘａｍｐｌｅ．ｃｏｍです。",
                              normalize=True, remove_urls=True)
        assert out == "参考です。"


class TestConfusablesHaNumberContext:
    def test_bungo_particle_ha_protected(self):
        # 文語カタカナ文の係助詞ハ（漢字+ハ+漢字）は数値文脈でないため変換しない
        for s in ("吾輩ハ猫デアル", "天皇ハ神聖ニシテ", "被告人ハ無罪"):
            assert core.fix_ocr_confusables(s) == s
        # ニ→二（既存仕様）は発火し得るが、ハは八にならない
        assert "八" not in core.fix_ocr_confusables("天ハ人ノ上ニ人ヲ造ラズ")

    def test_number_context_fixed(self):
        assert core.fix_ocr_confusables("二十ハ歳になった") == "二十八歳になった"


class TestChapterHeadingSpacePreserved:
    def test_clean_text_keeps_heading_space(self):
        # 「第N章 タイトル」の区切り空白は clean_text で保護され、章検出が効く
        out = core.clean_text("第1章 はじまり\n本文 です。\n第二章　再会\n続き。")
        lines = out.split("\n")
        assert lines[0] == "第1章 はじまり"
        assert lines[1] == "本文です。"          # 本文の空白は従来どおり除去
        assert core.detect_chapters(lines) == \
            [("第1章 はじまり", 0), ("第二章　再会", 2)]


class TestSpeakableText:
    def test_skips_memo_and_strips_tags(self):
        text = "# 台本メモ\n@ずんだもん: こんにちは\n本文です。\n＃これもメモ\n"
        assert core.speakable_text(text) == "こんにちは\n本文です。"

    def test_plain_text_unchanged(self):
        assert core.speakable_text("一行目。\n二行目。") == "一行目。\n二行目。"

    def test_blank_and_tag_only_lines_dropped(self):
        assert core.speakable_text("\n@ずんだもん:\n\n") == ""


class TestFilenameSnippet:
    def test_basic_japanese(self):
        assert core.filename_snippet("こんにちは、世界。") == "こんにちは、世界。"

    def test_strips_forbidden_chars_and_spaces(self):
        assert core.filename_snippet('a<b>:c"/d\\|?*e f') == "abcdef"

    def test_max_chars(self):
        assert core.filename_snippet("あ" * 30) == "あ" * 12

    def test_empty_when_nothing_usable(self):
        assert core.filename_snippet("  \t　 ") == ""
        assert core.filename_snippet("???") == ""

    def test_no_trailing_ascii_dot(self):
        # 末尾の半角ピリオドはWindowsで不正なファイル名になるため落とす
        assert not core.filename_snippet("End of file.").endswith(".")


# ============================================================
#  v1.16.0: 《》記法・自動チャプター・キャンセル・キャッシュほか
# ============================================================
class TestRubyContextGuard:
    def test_kanji_ruby_removed(self):
        assert core.strip_aozora("北海道《ほっかいどう》へ") == "北海道へ"

    def test_bar_ruby_removed(self):
        assert core.strip_aozora("｜北海道《ほっかいどう》へ") == "北海道へ"

    def test_web_novel_skill_name_kept(self):
        # 漢字直後でない《…》はWeb小説のスキル名・強調＝本文として残す
        assert core.strip_aozora("彼は《ファイアボール》を放った。") == \
            "彼は《ファイアボール》を放った。"

    def test_kakuyomu_emphasis_expanded(self):
        # カクヨム傍点《《…》》は中身を展開（従来は「》」だけ残る破損だった）
        assert core.strip_aozora("これは《《本当に大事》》なことだ。") == \
            "これは本当に大事なことだ。"


class TestHalfwidthKana:
    def test_normalized_and_dakuten_composed(self):
        assert core.normalize_halfwidth_kana("ｶﾞｲﾄﾞ") == "ガイド"
        assert core.normalize_halfwidth_kana("ﾃﾞｰﾀ｡") == "データ。"

    def test_clean_text_normalize_splits_halfwidth_kuten(self):
        # 半角句点｡も全角化され、文分割が効くようになる
        out = core.clean_text("ﾃｽﾄです｡続きです｡", normalize=True)
        assert out.split("\n") == ["テストです。", "続きです。"]


class TestNumericRange:
    def test_range_to_kara(self):
        assert core.normalize_readings("10〜20人") == "10から20人"
        assert core.normalize_readings("10~20人") == "10から20人"

    def test_non_numeric_tilde_kept(self):
        assert core.normalize_readings("よろしく〜") == "よろしく〜"


class TestKumimojiUnits:
    def test_units_expanded(self):
        out = core.expand_readable_chars("時速40㌔で3㌧・50㌫")
        assert out == "時速40キロで3トン・50パーセント"


class TestReadTxtEncoding:
    def test_euc_jp_detected(self, tmp_path):
        p = tmp_path / "euc.txt"
        p.write_bytes("吾輩は猫である。名前はまだ無い。".encode("euc_jp"))
        assert "吾輩は猫である" in core.read_txt(str(p))

    def test_cp932_still_works(self, tmp_path):
        p = tmp_path / "sjis.txt"
        p.write_bytes("こんにちは世界".encode("cp932"))
        assert core.read_txt(str(p)) == "こんにちは世界"

    def test_utf8_fast_path(self, tmp_path):
        p = tmp_path / "u8.txt"
        p.write_text("普通のUTF-8です。", encoding="utf-8")
        assert core.read_txt(str(p)) == "普通のUTF-8です。"


class TestFallbackChapters:
    def test_marks_every_interval(self):
        starts = [0.0, 300.0, 650.0, 900.0, 1300.0]
        lines = ["a", "b", "c行のテキストがとても長い場合は切られる", "d", "e"]
        chs = core.fallback_chapters(starts, lines, interval_sec=600)
        assert chs[0] == ("冒頭", 0.0)
        assert chs[1][1] == 650.0
        assert chs[1][0].startswith("c行のテキストがとても長")
        assert chs[2][1] == 1300.0

    def test_short_book_only_head(self):
        chs = core.fallback_chapters([0.0, 10.0], ["a", "b"], interval_sec=600)
        assert chs == [("冒頭", 0.0)]

    def test_empty(self):
        assert core.fallback_chapters([], []) == []


class TestExtractCancel:
    def test_preset_cancel_returns_partial_with_warning(self, tmp_path):
        import threading
        ev = threading.Event()
        ev.set()   # 最初から中断済み → ファイルを1つも処理せず戻る
        files = []
        for i in range(2):
            p = tmp_path / f"t{i}.txt"
            p.write_text(f"本文{i}です。", encoding="utf-8")
            files.append(str(p))
        text, warnings = core.extract_files(files, cancel_event=ev)
        assert text == ""
        assert any("キャンセル" in w for w in warnings)


class TestSynthCache:
    def test_key_changes_with_inputs(self):
        k = core.synth_cache_key("こんにちは", 3, 1.0, 0.0, 1.0, 1.0, "0.24", "d1")
        assert k == core.synth_cache_key("こんにちは", 3, 1.0, 0.0, 1.0, 1.0,
                                         "0.24", "d1")
        assert k != core.synth_cache_key("こんばんは", 3, 1.0, 0.0, 1.0, 1.0,
                                         "0.24", "d1")
        assert k != core.synth_cache_key("こんにちは", 4, 1.0, 0.0, 1.0, 1.0,
                                         "0.24", "d1")
        assert k != core.synth_cache_key("こんにちは", 3, 1.2, 0.0, 1.0, 1.0,
                                         "0.24", "d1")
        assert k != core.synth_cache_key("こんにちは", 3, 1.0, 0.0, 1.0, 1.0,
                                         "0.25", "d1")
        assert k != core.synth_cache_key("こんにちは", 3, 1.0, 0.0, 1.0, 1.0,
                                         "0.24", "d2")

    def test_put_get_roundtrip_and_evict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "SYNTH_CACHE_DIR", str(tmp_path / "vc"))
        wav1 = b"RIFF" + b"1" * 60      # 64B（RIFFヘッダ風・検証を通る最小形）
        wav2 = b"RIFF" + b"2" * 100     # 104B
        core.synth_cache_put("k1", wav1)
        assert core.synth_cache_get("k1") == wav1
        assert core.synth_cache_get("nokey") is None
        # 上限を極小にして追加すると古い方から消える
        import os as _os
        import time as _time
        p1 = _os.path.join(core.SYNTH_CACHE_DIR, "k1.wav")
        _os.utime(p1, (_time.time() - 100, _time.time() - 100))
        core.synth_cache_put("k2", wav2)
        core._synth_cache_evict(max_bytes=110)   # 合計168B > 110B → 古いk1だけ削除
        assert core.synth_cache_get("k1") is None
        assert core.synth_cache_get("k2") == wav2

    def test_put_rejects_non_wav_and_get_heals_corruption(self, tmp_path,
                                                          monkeypatch):
        monkeypatch.setattr(core, "SYNTH_CACHE_DIR", str(tmp_path / "vc"))
        core.synth_cache_put("bad", b"not-a-wav")   # WAVでない → 保存しない
        assert core.synth_cache_get("bad") is None
        # 電源断などで残った壊れたエントリは get が削除してミス扱い（自己回復）
        import os as _os
        _os.makedirs(core.SYNTH_CACHE_DIR, exist_ok=True)
        p = _os.path.join(core.SYNTH_CACHE_DIR, "corrupt.wav")
        with open(p, "wb") as f:
            f.write(b"RIFF")   # 44バイト以下＝不完全
        assert core.synth_cache_get("corrupt") is None
        assert not _os.path.exists(p)

    def test_cached_passthrough_without_keys(self, monkeypatch):
        # engine_ver / dict_hash が無いときはキャッシュせず素通し
        calls = []
        monkeypatch.setattr(core, "vv_synthesize_one",
                            lambda *a, **k: calls.append(1) or b"w")
        assert core.vv_synthesize_cached("u", "t", 1) == b"w"
        assert core.vv_synthesize_cached("u", "t", 1) == b"w"
        assert len(calls) == 2


class TestDocxNoDuplicates:
    def _make_docx(self, tmp_path, body_xml):
        import zipfile as zf
        p = tmp_path / "t.docx"
        doc = ('<?xml version="1.0"?>'
               '<w:document xmlns:w="http://schemas.openxmlformats.org/'
               'wordprocessingml/2006/main" xmlns:mc="http://schemas.'
               'openxmlformats.org/markup-compatibility/2006">'
               f'<w:body>{body_xml}</w:body></w:document>')
        with zf.ZipFile(p, "w") as z:
            z.writestr("word/document.xml", doc)
        return str(p)

    def test_textbox_extracted_once(self, tmp_path):
        # mc:Choice/mc:Fallback の両分岐＋入れ子w:p で同文が4重になっていた
        inner = '<w:p><w:r><w:t>箱の文言</w:t></w:r></w:p>'
        body = ('<w:p><w:r><w:t>本文。</w:t></w:r></w:p>'
                f'<w:p><w:r><mc:AlternateContent><mc:Choice>{inner}</mc:Choice>'
                f'<mc:Fallback>{inner}</mc:Fallback>'
                '</mc:AlternateContent></w:r></w:p>')
        out = core.extract_docx(self._make_docx(tmp_path, body))
        assert out.count("箱の文言") == 1
        assert "本文。" in out

    def test_plain_paragraphs_unchanged(self, tmp_path):
        body = ('<w:p><w:r><w:t>一段落。</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>二段落。</w:t></w:r></w:p>')
        out = core.extract_docx(self._make_docx(tmp_path, body))
        assert out == "一段落。\n二段落。"


class TestEpubNavSkip:
    def _make_epub(self, tmp_path, extra_item="", extra_ref=""):
        import zipfile as zf
        p = tmp_path / "t.epub"
        container = ('<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:'
                     'opendocument:xmlns:container"><rootfiles><rootfile '
                     'full-path="OEBPS/content.opf"/></rootfiles></container>')
        opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf">'
               '<manifest>'
               '<item id="c1" href="c1.xhtml"/>' + extra_item +
               '</manifest><spine>'
               '<itemref idref="c1"/>' + extra_ref +
               '</spine></package>')
        with zf.ZipFile(p, "w") as z:
            z.writestr("META-INF/container.xml", container)
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/c1.xhtml", "<html><body><p>本文です。</p></body></html>")
            z.writestr("OEBPS/nav.xhtml", "<html><body><p>目次リンク</p></body></html>")
        return str(p)

    def test_nav_and_nonlinear_skipped(self, tmp_path):
        p = self._make_epub(
            tmp_path,
            extra_item='<item id="nav" href="nav.xhtml" properties="nav"/>',
            extra_ref='<itemref idref="nav" linear="no"/>')
        out = core.extract_epub(p)
        assert "本文です。" in out
        assert "目次リンク" not in out

    def test_all_aux_falls_back(self, tmp_path):
        # 全章が linear=no でも本文が空にならない（誤ラベルEPUB対策）
        import zipfile as zf
        p = tmp_path / "t2.epub"
        container = ('<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:'
                     'opendocument:xmlns:container"><rootfiles><rootfile '
                     'full-path="content.opf"/></rootfiles></container>')
        opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf">'
               '<manifest><item id="c1" href="c1.xhtml"/></manifest>'
               '<spine><itemref idref="c1" linear="no"/></spine></package>')
        with zf.ZipFile(p, "w") as z:
            z.writestr("META-INF/container.xml", container)
            z.writestr("content.opf", opf)
            z.writestr("c1.xhtml", "<html><body><p>唯一の本文</p></body></html>")
        assert "唯一の本文" in core.extract_epub(str(p))


class TestIsMemoLine:
    def test_memo_detection(self):
        assert core.is_memo_line("# メモ")
        assert core.is_memo_line("　＃全角も")
        assert not core.is_memo_line("本文 # 中の井桁")
        assert not core.is_memo_line("")


class TestCliUnitAndVvproj:
    def _setup(self, tmp_path, monkeypatch):
        speakers = [("ずんだもん（ノーマル）", 3, "uz")]
        monkeypatch.setattr(core, "vv_check", lambda url, timeout=3: "0.0.0")
        monkeypatch.setattr(core, "vv_speakers", lambda url, timeout=10: speakers)
        monkeypatch.setattr(core, "vv_synthesize_one",
                            lambda url, text, sid, **kw: _make_wav(0.05))
        src = tmp_path / "s.txt"
        src.write_text("一文目。二文目。三文目。", encoding="utf-8")
        return src

    def test_unit_nlines_groups(self, tmp_path, monkeypatch):
        import cli
        src = self._setup(tmp_path, monkeypatch)
        out = tmp_path / "o"
        rc = cli.main([str(src), "-o", str(out), "--wav",
                       "--unit", "nlines", "--split-lines", "2"])
        assert rc == 0
        assert (out / "001.wav").exists() and (out / "002.wav").exists()
        assert not (out / "003.wav").exists()   # 3行→2+1で2ファイル

    def test_vvproj_written_without_wav(self, tmp_path, monkeypatch):
        import cli
        import json as _json
        src = self._setup(tmp_path, monkeypatch)
        out = tmp_path / "o"
        rc = cli.main([str(src), "-o", str(out), "--vvproj"])
        assert rc == 0
        proj = _json.loads((out / "voicevox_project.vvproj"
                            ).read_text(encoding="utf-8"))
        assert len(proj["talk"]["audioKeys"]) == 3
        assert not (out / "001.wav").exists()

    def test_name_snippet_optin(self, tmp_path, monkeypatch):
        import cli
        src = self._setup(tmp_path, monkeypatch)
        out = tmp_path / "o"
        rc = cli.main([str(src), "-o", str(out), "--wav", "--name-snippet"])
        assert rc == 0
        assert (out / "001_一文目。.wav").exists()

    def test_url_trailing_slash_normalized(self, tmp_path, monkeypatch):
        import cli
        seen = []
        monkeypatch.setattr(core, "vv_check",
                            lambda url, timeout=3: seen.append(url) or "0.0.0")
        monkeypatch.setattr(core, "vv_speakers", lambda url, timeout=10:
                            [("ずんだもん（ノーマル）", 3, "uz")])
        monkeypatch.setattr(core, "vv_synthesize_one",
                            lambda url, text, sid, **kw: _make_wav(0.05))
        src = tmp_path / "s.txt"
        src.write_text("一文。", encoding="utf-8")
        rc = cli.main([str(src), "-o", str(tmp_path / "o"), "--wav",
                       "--url", "http://127.0.0.1:50021/"])
        assert rc == 0
        assert seen[0] == "http://127.0.0.1:50021"


class TestReviewFixesV116:
    """v1.16.0レビューで確定した指摘の回帰テスト。"""

    def test_ruby_after_compat_kanji(self):
        # 互換漢字（﨑=U+FA11）・拡張B（𠮟=U+20B9F）直後のルビも削除される
        assert core.strip_aozora("山﨑《やまざき》さん") == "山﨑さん"
        assert core.strip_aozora("𠮟《しか》る") == "𠮟る"

    def test_sentence_mode_keeps_paragraphs_when_blank_kept(self):
        # remove_blank=False の文ごとモードで空行（段落境界）が保持される
        out = core.clean_text("一文目。二文目。\n\n三文目。", remove_blank=False)
        assert out == "一文目。\n二文目。\n\n三文目。"

    def test_sentence_mode_default_unchanged(self):
        out = core.clean_text("一文目。二文目。\n\n三文目。")
        assert out == "一文目。\n二文目。\n三文目。"

    def test_cli_unit_para_splits(self, tmp_path, monkeypatch):
        # --unit para + --keep-blank が既定の文ごとモードでも段落分割できる
        import cli
        speakers = [("ずんだもん（ノーマル）", 3, "uz")]
        monkeypatch.setattr(core, "vv_check", lambda url, timeout=3: "0.0.0")
        monkeypatch.setattr(core, "vv_speakers", lambda url, timeout=10: speakers)
        monkeypatch.setattr(core, "vv_synthesize_one",
                            lambda url, text, sid, **kw: _make_wav(0.05))
        src = tmp_path / "s.txt"
        src.write_text("一段落の一文。一段落の二文。\n\n二段落。", encoding="utf-8")
        out = tmp_path / "o"
        rc = cli.main([str(src), "-o", str(out), "--wav",
                       "--unit", "para", "--keep-blank"])
        assert rc == 0
        assert (out / "001.wav").exists() and (out / "002.wav").exists()
        assert not (out / "003.wav").exists()

    def test_dict_hash_includes_priority(self, monkeypatch):
        # 優先度だけ変えてもハッシュが変わる（キャッシュが正しく無効化される）
        class _R:
            def __init__(self, data):
                self._d = data
            def raise_for_status(self):
                pass
            def json(self):
                return self._d
        base = {"u1": {"surface": "泉", "pronunciation": "イズミ",
                       "accent_type": 0, "word_type": "PROPER_NOUN",
                       "priority": 5}}
        import requests
        monkeypatch.setattr(requests, "get", lambda url, timeout=10: _R(base))
        h1 = core.vv_dict_hash("http://x")
        base["u1"]["priority"] = 9
        h2 = core.vv_dict_hash("http://x")
        assert h1 != h2


# ============================================================
#  v1.17.0: ストリーミング結合・ページ範囲・チャプター/グループ統合ほか
# ============================================================
class TestConcatWavsToFile:
    def test_matches_bytes_version_and_durations(self, tmp_path):
        wavs = [_make_wav(0.10), _make_wav(0.05), _make_wav(0.20)]
        out = tmp_path / "j.wav"
        durs = core.concat_wavs_to_file(wavs, str(out), gap_sec=0.3)
        assert out.read_bytes() == core.concat_wavs(wavs, gap_sec=0.3)
        for d, w in zip(durs, wavs):
            assert abs(d - core.wav_duration(w)) < 1e-6

    def test_accepts_file_paths(self, tmp_path):
        wavs = [_make_wav(0.05), _make_wav(0.05)]
        srcs = []
        for i, w in enumerate(wavs):
            p = tmp_path / f"{i}.wav"
            p.write_bytes(w)
            srcs.append(str(p))
        out = tmp_path / "j.wav"
        core.concat_wavs_to_file(srcs, str(out), gap_sec=0.1)
        assert out.read_bytes() == core.concat_wavs(wavs, gap_sec=0.1)

    def test_4gb_guard(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "_WAV_MAX_DATA", 1000)   # 上限を極小にして発火確認
        with pytest.raises(RuntimeError, match="4GB"):
            core.concat_wavs_to_file([_make_wav(0.5)], str(tmp_path / "x.wav"))


class TestEncodeAudioFile:
    def test_wav_move_and_copy(self, tmp_path):
        src = tmp_path / "in.wav"
        src.write_bytes(_make_wav(0.05))
        out1 = tmp_path / "copy.wav"
        core.encode_audio_file(str(src), str(out1), "wav", keep_input=True)
        assert src.exists() and out1.exists()
        out2 = tmp_path / "moved.wav"
        core.encode_audio_file(str(src), str(out2), "wav")
        assert not src.exists() and out2.exists()


class TestParsePageRanges:
    def test_basic_forms(self):
        assert core.parse_page_ranges("5-320") == [(5, 320)]
        assert core.parse_page_ranges("1-3,7,10-") == [(1, 3), (7, 7), (10, None)]
        assert core.parse_page_ranges("-20") == [(1, 20)]
        assert core.parse_page_ranges("") is None
        assert core.parse_page_ranges(None) is None

    def test_fullwidth_and_reversed(self):
        assert core.parse_page_ranges("５〜１０、２０") == [(5, 10), (20, 20)]
        assert core.parse_page_ranges("20-5") == [(5, 20)]   # 逆順は入れ替え

    def test_invalid_raises(self):
        for bad in ("abc", "1-2-3", "0-5", "3,x"):
            with pytest.raises(ValueError):
                core.parse_page_ranges(bad)

    def test_page_in_ranges(self):
        r = core.parse_page_ranges("2-4,10-")
        assert not core.page_in_ranges(1, r)
        assert core.page_in_ranges(3, r)
        assert not core.page_in_ranges(5, r)
        assert core.page_in_ranges(99, r)
        assert core.page_in_ranges(1, None)


class TestUnresolvedSpeakerTags:
    _SP = [("ずんだもん（ノーマル）", 3, "u")]

    def test_detects_typo_and_skips_memo(self):
        # 「ずんだも」は前方一致で解決される（既存仕様）ので、真に解決不能な
        # タイプミス（すんだもん）だけが検出される
        text = ("@ずんだもん: こんにちは\n@ずんだも: 前方一致で解決\n"
                "@すんだもん: タイプミス\n# @めも: メモ\n地の文")
        assert core.unresolved_speaker_tags(text, self._SP) == [(3, "すんだもん")]

    def test_clean_text_ok(self):
        assert core.unresolved_speaker_tags("地の文だけ。", self._SP) == []


class TestBuildChapters:
    def test_heads_with_intro(self):
        lines = ["まえせつ", "第一章 出発", "本文"]
        chs, kind = core.build_chapters(lines, [10.0, 5.0, 5.0])
        assert kind == "heads"
        assert chs[0] == ("冒頭", 0.0)
        assert chs[1][0].startswith("第一章")

    def test_heads_single_at_start(self):
        chs, kind = core.build_chapters(["第一章", "本文"], [3.0, 5.0])
        assert kind == "heads" and len(chs) == 1   # 冒頭補完なし・1個でも埋め込む

    def test_auto_fallback(self):
        lines = ["a"] * 3
        chs, kind = core.build_chapters(lines, [400.0, 400.0, 400.0])
        assert kind == "auto" and len(chs) >= 2

    def test_none_for_short(self):
        chs, kind = core.build_chapters(["短い"], [10.0])
        assert kind == "none" and chs == []


class TestGroupOutputIndices:
    def test_all_units(self):
        para = [0, 0, 1, 1, 2]
        assert core.group_output_indices("combine", para) == [[0, 1, 2, 3, 4]]
        assert core.group_output_indices("each", para) == [[0], [1], [2], [3], [4]]
        assert core.group_output_indices("nlines", para, 2) == [[0, 1], [2, 3], [4]]
        assert core.group_output_indices("para", para) == [[0, 1], [2, 3], [4]]

    def test_nlines_min_two(self):
        assert core.group_output_indices("nlines", [0, 0, 0], 1) == [[0, 1], [2]]


class TestApplyOcrCorrections:
    def test_confusables_and_denoise(self):
        text = "卜ヨタの発表\nNEWS\nトヨタが発表した。"
        out, conf, removed = core.apply_ocr_corrections(text,
                                                        fix_confusables=True,
                                                        denoise=True)
        assert "トヨタの発表" in out and "NEWS" not in out
        assert conf and conf[0][0] == "卜ヨタの発表"
        assert "NEWS" in removed

    def test_noop_when_disabled(self):
        out, conf, removed = core.apply_ocr_corrections("卜ヨタ\nNEWS")
        assert out == "卜ヨタ\nNEWS" and not conf and not removed


class TestCacheProtectAndLimit:
    def test_protect_skips_recent(self, tmp_path, monkeypatch):
        import os as _os
        import time as _time
        monkeypatch.setattr(core, "SYNTH_CACHE_DIR", str(tmp_path / "vc"))
        monkeypatch.setattr(core, "_synth_cache_protect_since", 0.0)
        old_wav = b"RIFF" + b"o" * 100
        new_wav = b"RIFF" + b"n" * 100
        core.synth_cache_put("old", old_wav)
        p = _os.path.join(core.SYNTH_CACHE_DIR, "old.wav")
        _os.utime(p, (_time.time() - 1000, _time.time() - 1000))
        core.synth_cache_put("new", new_wav)
        # 保護あり: 直近に触れた new は消えず、保護外の old だけ消える
        core.synth_cache_protect(_time.time() - 500)
        try:
            core._synth_cache_evict(max_bytes=1)
            assert core.synth_cache_get("old") is None
            assert core.synth_cache_get("new") == new_wav
        finally:
            core.synth_cache_protect(0.0)

    def test_set_limit_bounds(self, monkeypatch):
        monkeypatch.setattr(core, "_SYNTH_CACHE_MAX_BYTES", 500 * 1024 * 1024)
        core.set_synth_cache_limit(2000)
        assert core._SYNTH_CACHE_MAX_BYTES == 2000 * 1024 * 1024
        core.set_synth_cache_limit(1)        # 範囲外は無視
        assert core._SYNTH_CACHE_MAX_BYTES == 2000 * 1024 * 1024
        core.set_synth_cache_limit("abc")    # 不正も無視
        assert core._SYNTH_CACHE_MAX_BYTES == 2000 * 1024 * 1024
        core.set_synth_cache_limit(500)

    def test_stats_and_clear(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "SYNTH_CACHE_DIR", str(tmp_path / "vc"))
        core.synth_cache_put("a", b"RIFF" + b"1" * 60)
        core.synth_cache_put("b", b"RIFF" + b"2" * 60)
        n, total = core.synth_cache_stats()
        assert n == 2 and total == 128
        assert core.synth_cache_clear() == 2
        assert core.synth_cache_stats() == (0, 0)

    def test_dict_hash_failure_returns_empty(self, monkeypatch):
        import requests
        def _boom(url, timeout=10):
            raise OSError("down")
        monkeypatch.setattr(requests, "get", _boom)
        assert core.vv_dict_hash("http://x") == ""


class TestWindowsOcrChunking:
    def test_chunks_and_cancel(self, monkeypatch):
        calls = []
        monkeypatch.setattr(core, "_run_windows_ocr_chunk",
                            lambda paths, **kw: calls.append(list(paths))
                            or {p: f"t{p}" for p in paths})
        paths = [f"p{i}" for i in range(45)]
        progress = []
        out = core.run_windows_ocr(paths, chunk_size=20,
                                   progress_cb=lambda i, n: progress.append(i))
        assert len(out) == 45 and len(calls) == 3
        assert progress == [20, 40, 45]
        # キャンセル: 最初から中断済みなら1チャンクも実行しない
        import threading
        ev = threading.Event()
        ev.set()
        calls.clear()
        out = core.run_windows_ocr(paths, chunk_size=20, cancel_event=ev)
        assert out == {} and calls == []

    def test_partial_fatal_reported(self, monkeypatch):
        state = {"n": 0}
        def chunk(paths, **kw):
            state["n"] += 1
            if state["n"] == 2:
                raise RuntimeError("boom")
            return {p: "ok" for p in paths}
        monkeypatch.setattr(core, "_run_windows_ocr_chunk", chunk)
        errors = []
        out = core.run_windows_ocr([f"p{i}" for i in range(30)],
                                   chunk_size=10, errors=errors)
        assert len(out) == 20
        assert any("boom" in e for e in errors)


class TestCliV117:
    def _setup(self, tmp_path, monkeypatch, body="@ずんだもん: こん\n@四国めたん: ばん"):
        speakers = [("ずんだもん（ノーマル）", 3, "uz"),
                    ("四国めたん（ノーマル）", 2, "um")]
        monkeypatch.setattr(core, "vv_check", lambda url, timeout=3: "0.0.0")
        monkeypatch.setattr(core, "vv_speakers", lambda url, timeout=10: speakers)
        monkeypatch.setattr(core, "vv_synthesize_one",
                            lambda url, text, sid, **kw: _make_wav(0.05))
        src = tmp_path / "s.txt"
        src.write_text(body, encoding="utf-8")
        return src

    def test_csv_sidecar_and_speaker_names(self, tmp_path, monkeypatch):
        import cli
        src = self._setup(tmp_path, monkeypatch)
        out = tmp_path / "o"
        rc = cli.main([str(src), "-o", str(out), "--wav", "--no-cache",
                       "--name-snippet", "--list-csv"])
        assert rc == 0
        names = sorted(p.name for p in out.glob("*.wav"))
        assert names[0].startswith("001_ずんだもん_")   # 複数話者は話者名入り
        csv_text = (out / "セリフ一覧.csv").read_text(encoding="utf-8-sig")
        assert "ずんだもん" in csv_text and "四国めたん" in csv_text

    def test_invalid_pages_exits(self, tmp_path, monkeypatch):
        import cli
        src = self._setup(tmp_path, monkeypatch)
        with pytest.raises(SystemExit, match="--pages"):
            cli.main([str(src), "-o", str(tmp_path / "o"), "--pages", "abc"])

    def test_cache_hits_on_second_run(self, tmp_path, monkeypatch):
        import cli
        monkeypatch.setattr(core, "SYNTH_CACHE_DIR", str(tmp_path / "vc"))
        monkeypatch.setattr(core, "vv_dict_hash", lambda url, timeout=10: "d1")
        calls = []
        speakers = [("ずんだもん（ノーマル）", 3, "uz")]
        monkeypatch.setattr(core, "vv_check", lambda url, timeout=3: "0.0.0")
        monkeypatch.setattr(core, "vv_speakers", lambda url, timeout=10: speakers)
        monkeypatch.setattr(core, "vv_synthesize_one",
                            lambda url, text, sid, **kw:
                            calls.append(text) or _make_wav(0.05))
        src = tmp_path / "s.txt"
        src.write_text("一文目。二文目。", encoding="utf-8")
        assert cli.main([str(src), "-o", str(tmp_path / "o1"), "--wav"]) == 0
        n_first = len(calls)
        assert n_first == 2
        assert cli.main([str(src), "-o", str(tmp_path / "o2"), "--wav"]) == 0
        assert len(calls) == n_first   # 2回目は全行キャッシュヒット＝合成0回
