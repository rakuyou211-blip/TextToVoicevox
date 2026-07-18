# -*- coding: utf-8 -*-
"""
mp4chapters.py のテスト。

afconvert（macOS標準）で実際のm4aフィクスチャを生成してチャプターを注入し、
ボックス構造・音声データ不変・AVFoundationでの読み戻しを検証する。
afconvertが無い環境（Windows/Linux CI）ではフィクスチャ依存のテストをskipする。
"""
import os
import shutil
import struct
import subprocess
import sys
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mp4chapters

AFCONVERT = shutil.which("afconvert")
SWIFT = "/usr/bin/swift" if os.path.exists("/usr/bin/swift") else shutil.which("swift")
AFPLAY = shutil.which("afplay")

# 日本語タイトル必須（encdによるUTF-8指定が効いていることの確認を兼ねる）
CHAPTERS = [("第一章", 0.0), ("第二章", 3.0), ("第三章", 7.0)]


# ============================================================
#  テスト用の独立した簡易MP4パーサ
#  （被テストモジュールの解析コードを信用しないよう別実装しておく）
# ============================================================
def iter_boxes(data, start, end):
    off = start
    while off < end:
        size, btype = struct.unpack_from(">I4s", data, off)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, off + 8)[0]
            header = 16
        elif size == 0:
            size = end - off
        assert size >= header and off + size <= end, "壊れたボックス: %r" % btype
        yield btype, off, header, size
        off += size


def top_level_types(data):
    return [t for t, _, _, _ in iter_boxes(data, 0, len(data))]


def find_box(data, path, start=0, end=None):
    """b"moov/trak[1]/mdia" のようなパスで(payload開始, 終了)を探す。"""
    if end is None:
        end = len(data)
    name = path[0]
    index = 0
    if b"[" in name:
        name, rest = name.split(b"[")
        index = int(rest[:-1])
    found = 0
    for btype, off, header, size in iter_boxes(data, start, end):
        if btype == name:
            if found == index:
                if len(path) == 1:
                    return off + header, off + size
                return find_box(data, path[1:], off + header, off + size)
            found += 1
    return None


def full_box_payload(data, span):
    """フルボックスのversion/flagsを飛ばした本体を返す。"""
    return data[span[0] + 4:span[1]]


def read_chapter_stco(data):
    """チャプターtrak（hdlr=text）のstcoオフセット一覧を返す。"""
    moov = find_box(data, [b"moov"])
    assert moov is not None
    trak_index = 0
    while True:
        trak = find_box(data, [b"moov", b"trak[%d]" % trak_index])
        if trak is None:
            return None
        hdlr = find_box(data, [b"moov", b"trak[%d]" % trak_index, b"mdia", b"hdlr"])
        if hdlr and data[hdlr[0] + 8:hdlr[0] + 12] == b"text":
            stco = find_box(
                data,
                [b"moov", b"trak[%d]" % trak_index, b"mdia", b"minf", b"stbl", b"stco"])
            body = full_box_payload(data, stco)
            n = struct.unpack_from(">I", body, 0)[0]
            return struct.unpack_from(">%dI" % n, body, 4)
        trak_index += 1


# ============================================================
#  フィクスチャ
# ============================================================
@pytest.fixture(scope="module")
def pristine_m4a(tmp_path_factory):
    """afconvertで10秒無音のm4aを生成し、その生バイト列を返す。"""
    if not AFCONVERT:
        pytest.skip("afconvertが無い環境（macOS以外）")
    d = tmp_path_factory.mktemp("m4a_fixture")
    wav_path = d / "in.wav"
    m4a_path = d / "out.m4a"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 22050 * 10)
    subprocess.run(
        [AFCONVERT, "-f", "m4af", "-d", "aac", str(wav_path), str(m4a_path)],
        check=True, capture_output=True)
    return m4a_path.read_bytes()


@pytest.fixture
def chaptered_m4b(pristine_m4a, tmp_path):
    """チャプター注入済みm4bのパスと、元のバイト列を返す。"""
    path = tmp_path / "book.m4b"
    path.write_bytes(pristine_m4a)
    mp4chapters.add_chapters(str(path), CHAPTERS)
    return path, pristine_m4a


# ============================================================
#  (a) ボックス構造の検証
# ============================================================
class TestBoxStructure:
    def test_trak_count_is_two(self, chaptered_m4b):
        path, _ = chaptered_m4b
        data = path.read_bytes()
        moov = find_box(data, [b"moov"])
        traks = [t for t, _, _, _ in iter_boxes(data, moov[0], moov[1]) if t == b"trak"]
        assert len(traks) == 2

    def test_old_moov_replaced_by_free_and_new_moov_last(self, chaptered_m4b):
        # afconvert出力はmoovが先頭側 → 旧moov領域はfree化され、新moovは末尾のはず
        path, original = chaptered_m4b
        data = path.read_bytes()
        types = top_level_types(data)
        assert types[-1] == b"moov"
        assert types.count(b"moov") == 1
        # 元のmoov位置がfreeになっている（サイズも同一＝後続オフセット不変）
        orig_moov = [(off, size) for t, off, _, size in iter_boxes(original, 0, len(original))
                     if t == b"moov"]
        off, size = orig_moov[0]
        assert data[off + 4:off + 8] == b"free"
        assert struct.unpack_from(">I", data, off)[0] == size

    def test_audio_trak_has_tref_chap(self, chaptered_m4b):
        path, _ = chaptered_m4b
        data = path.read_bytes()
        # trak[0]=音声（元からある方）にtref/chapが入り、chapのIDがチャプターtrakのIDと一致
        chap = find_box(data, [b"moov", b"trak[0]", b"tref", b"chap"])
        assert chap is not None
        chap_id = struct.unpack_from(">I", data, chap[0])[0]
        # チャプターtrak（trak[1]）のtkhdからtrack_idを読む（v0: payload先頭から12バイト目）
        tkhd = find_box(data, [b"moov", b"trak[1]", b"tkhd"])
        assert data[tkhd[0]] == 0  # version 0
        assert struct.unpack_from(">I", data, tkhd[0] + 12)[0] == chap_id

    def test_stco_points_at_text_samples(self, chaptered_m4b):
        # stcoの絶対オフセット位置に「2バイトBE長+UTF-8タイトル」が実在すること
        path, _ = chaptered_m4b
        data = path.read_bytes()
        offsets = read_chapter_stco(data)
        assert offsets is not None and len(offsets) == len(CHAPTERS)
        for off, (title, _) in zip(offsets, CHAPTERS):
            body = title.encode("utf-8")
            length = struct.unpack_from(">H", data, off)[0]
            assert length == len(body)
            assert data[off + 2:off + 2 + length] == body
            # 直後にUTF-8指定のencd拡張ボックスが付いていること
            encd = data[off + 2 + length:off + 2 + length + 12]
            assert encd == struct.pack(">I4sI", 12, b"encd", 0x00000100)

    def test_mvhd_next_track_id_incremented(self, chaptered_m4b):
        path, original = chaptered_m4b
        data = path.read_bytes()
        def next_track_id(buf):
            mvhd = find_box(buf, [b"moov", b"mvhd"])
            offset = 96 if buf[mvhd[0]] == 0 else 108  # v0/v1でフィールド位置が違う
            return struct.unpack_from(">I", buf, mvhd[0] + offset)[0]
        assert next_track_id(data) == next_track_id(original) + 1


# ============================================================
#  (b) タイトルのバイト列がファイルに存在
# ============================================================
class TestTitleBytes:
    def test_japanese_titles_present_as_utf8(self, chaptered_m4b):
        path, _ = chaptered_m4b
        data = path.read_bytes()
        for title, _ in CHAPTERS:
            assert title.encode("utf-8") in data


# ============================================================
#  (c) 元の音声mdat領域が不変
# ============================================================
class TestAudioIntact:
    def test_original_mdat_bytes_unchanged(self, chaptered_m4b):
        path, original = chaptered_m4b
        data = path.read_bytes()
        # 元ファイルのmdatと同じオフセット・同じ内容のままであること
        # （旧moovのfree化で後続オフセットが動かないことの確認）
        mdats = [(off, size) for t, off, _, size in iter_boxes(original, 0, len(original))
                 if t == b"mdat"]
        assert mdats, "フィクスチャにmdatが無い"
        for off, size in mdats:
            assert data[off:off + size] == original[off:off + size]

    def test_ftyp_unchanged(self, chaptered_m4b):
        path, original = chaptered_m4b
        data = path.read_bytes()
        ftyp = [(off, size) for t, off, _, size in iter_boxes(original, 0, len(original))
                if t == b"ftyp"][0]
        assert data[ftyp[0]:ftyp[0] + ftyp[1]] == original[ftyp[0]:ftyp[0] + ftyp[1]]


# ============================================================
#  moovが末尾にあるファイル（切り詰め分岐）
# ============================================================
class TestMoovAtEnd:
    def _make_moov_at_end(self, original):
        """ftyp/moov/free/mdat → ftyp/free/free/mdat/moov に組み替える。

        mdatの位置は動かないので音声trakのstcoは有効なまま。"""
        moov = [(off, size) for t, off, _, size in iter_boxes(original, 0, len(original))
                if t == b"moov"][0]
        off, size = moov
        rebuilt = bytearray(original)
        rebuilt[off:off + 8] = struct.pack(">I4s", size, b"free")
        return bytes(rebuilt) + original[off:off + size]

    def test_truncate_branch(self, pristine_m4a, tmp_path):
        path = tmp_path / "moov_last.m4b"
        moved = self._make_moov_at_end(pristine_m4a)
        assert top_level_types(moved)[-1] == b"moov"
        path.write_bytes(moved)
        mp4chapters.add_chapters(str(path), CHAPTERS)
        data = path.read_bytes()
        # 旧moov（末尾）は切り詰められ、mdat(章テキスト)+新moovに置き換わっている
        types = top_level_types(data)
        assert types[-1] == b"moov"
        assert types.count(b"moov") == 1
        # 音声mdatは不変
        mdats = [(o, s) for t, o, _, s in iter_boxes(pristine_m4a, 0, len(pristine_m4a))
                 if t == b"mdat"]
        for o, s in mdats:
            assert data[o:o + s] == pristine_m4a[o:o + s]
        # チャプターも正しく入っている
        offsets = read_chapter_stco(data)
        assert offsets is not None and len(offsets) == len(CHAPTERS)
        for off, (title, _) in zip(offsets, CHAPTERS):
            body = title.encode("utf-8")
            assert data[off + 2:off + 2 + len(body)] == body


# ============================================================
#  (d) 壊れた入力・不正な引数
# ============================================================
class TestErrors:
    def test_non_mp4_raises_and_file_untouched(self, tmp_path):
        path = tmp_path / "not_mp4.m4a"
        junk = "これはMP4ではないテキストファイルです。".encode("utf-8") * 100
        path.write_bytes(junk)
        with pytest.raises(mp4chapters.Mp4ChapterError):
            mp4chapters.add_chapters(str(path), CHAPTERS)
        assert path.read_bytes() == junk  # 失敗時はファイル無変更

    def test_valid_boxes_but_no_moov_raises(self, tmp_path):
        path = tmp_path / "no_moov.m4a"
        content = struct.pack(">I4s", 16, b"ftyp") + b"M4A \x00\x00\x00\x00" \
            + struct.pack(">I4s", 16, b"free") + b"\x00" * 8
        path.write_bytes(content)
        with pytest.raises(mp4chapters.Mp4ChapterError):
            mp4chapters.add_chapters(str(path), CHAPTERS)
        assert path.read_bytes() == content

    def test_empty_chapters_rejected_before_file_access(self, tmp_path):
        # 入力検証はファイルを開く前に行われる＝存在しないパスでも検証エラーになる
        with pytest.raises(ValueError):
            mp4chapters.add_chapters(str(tmp_path / "nonexistent.m4a"), [])

    def test_non_ascending_starts_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            mp4chapters.add_chapters(
                str(tmp_path / "nonexistent.m4a"), [("A", 0.0), ("B", 5.0), ("C", 3.0)])

    def test_second_application_rejected_and_file_intact(self, chaptered_m4b):
        # 2回目の適用はchap参照の追記による章リスト破損（時刻は1回目・タイトルは
        # 2回目が混ざる）を防ぐため、明示的に例外にする。
        path, _ = chaptered_m4b
        first = path.read_bytes()
        with pytest.raises(mp4chapters.Mp4ChapterError):
            mp4chapters.add_chapters(
                str(path), [("二回目A", 0.0), ("二回目B", 5.0)])
        data = path.read_bytes()
        assert data == first  # 1回目適用直後のままバイト単位で無変更
        # 1回目のチャプターがボックス検証で読めること
        offsets = read_chapter_stco(data)
        assert offsets is not None and len(offsets) == len(CHAPTERS)
        for off, (title, _) in zip(offsets, CHAPTERS):
            body = title.encode("utf-8")
            assert struct.unpack_from(">H", data, off)[0] == len(body)
            assert data[off + 2:off + 2 + len(body)] == body

    def test_start_beyond_duration_rejected(self, pristine_m4a, tmp_path):
        path = tmp_path / "beyond.m4b"
        path.write_bytes(pristine_m4a)
        with pytest.raises(ValueError):
            mp4chapters.add_chapters(str(path), [("A", 0.0), ("B", 9999.0)])
        assert path.read_bytes() == pristine_m4a


# ============================================================
#  統合テスト: AVFoundation実読・afplay再生
# ============================================================
SWIFT_READER = """
import AVFoundation
import Foundation

let path = CommandLine.arguments[1]
let asset = AVURLAsset(url: URL(fileURLWithPath: path))
let groups = asset.chapterMetadataGroups(bestMatchingPreferredLanguages: ["ja", "und", "en"])
for g in groups {
    let start = CMTimeGetSeconds(g.timeRange.start)
    var title = "?"
    for item in g.items {
        if item.commonKey == .commonKeyTitle, let v = item.stringValue { title = v }
    }
    print("\\(start)|\\(title)")
}
"""


@pytest.mark.skipif(not SWIFT, reason="swiftが無い環境")
def test_avfoundation_reads_chapters(chaptered_m4b, tmp_path):
    """AVFoundation（=Apple Books等と同じ読み口）で章タイトル・開始時刻を読み戻す。"""
    path, _ = chaptered_m4b
    script = tmp_path / "readchap.swift"
    script.write_text(SWIFT_READER, encoding="utf-8")
    result = subprocess.run(
        [SWIFT, str(script), str(path)],
        capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == len(CHAPTERS), result.stdout
    for line, (title, start) in zip(lines, CHAPTERS):
        got_start, got_title = line.split("|", 1)
        assert got_title == title
        assert abs(float(got_start) - start) < 0.01


@pytest.mark.skipif(not AFPLAY, reason="afplayが無い環境")
def test_afplay_can_play_result(chaptered_m4b):
    """音声トラックが無傷で再生可能なこと（フィクスチャは無音なので音は出ない）。"""
    path, _ = chaptered_m4b
    result = subprocess.run(
        [AFPLAY, "-t", "1", str(path)], capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
