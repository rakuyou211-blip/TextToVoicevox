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
#  cli.py: --srt 単独指定の警告 / @話者タグの解釈
# ============================================================
class TestCliSrtWarning:
    def test_srt_without_combine_warns(self, tmp_path, capsys):
        import cli
        src = tmp_path / "n.txt"
        src.write_text("一文目。二文目。", encoding="utf-8")
        rc = cli.main([str(src), "-o", str(tmp_path / "o"), "--srt"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "--srt" in err and "--combine" in err


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
