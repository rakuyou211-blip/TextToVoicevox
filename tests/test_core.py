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
