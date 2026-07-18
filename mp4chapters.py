# -*- coding: utf-8 -*-
"""
mp4chapters.py - M4A/M4B ファイルへ QuickTime チャプタートラックを追加する（純Python標準ライブラリのみ）

afconvert / ffmpeg が出力した AAC の .m4a に、Apple Books・AVFoundation 系アプリが
読めるチャプター（QuickTime テキストトラック + tref/chap 参照）を埋め込み、
.m4b オーディオブック化するためのモジュール。オフライン動作・依存追加不可という
アプリの制約上、mutagen 等の外部ライブラリは使わず struct だけでボックスを組み立てる。

方式（Apple の「チャプタートラック」方式）:
  - moov に text ハンドラの trak を1本追加し、各チャプターのタイトルを
    テキストサンプルとして持たせる。
  - 音声 trak の tkhd 直後に tref{chap} を挿入してチャプタートラックを参照させる。
  - 音声データ (mdat) の stco オフセットを壊さないため、旧 moov 領域は同サイズの
    free ボックスで潰し、チャプター用 mdat と新 moov はファイル末尾に追記する。
    （moov が既に末尾にある場合だけは moov 位置で切り詰めて追記する。）
"""
import os
import struct
import shutil
import tempfile


class Mp4ChapterError(ValueError):
    """MP4の解析・チャプター追加に失敗したときの例外。

    add_chapters() はこの例外（または入力検証の ValueError/TypeError）を投げるが、
    いずれの場合も元ファイルは書き換えずに残す。"""


# 中身を再帰的にパースする必要があるコンテナボックス。
# チャプター追加に必要なのは moov→trak→mdia→hdlr の経路だけなので、
# minf/stbl 等の深部はバイト列（blob）のまま保持して再構築時にそのまま書き戻す。
_CONTAINER_TYPES = {b"moov", b"trak", b"mdia"}

# QuickTime の単位行列（tkhd / gmhd内textボックスで使う36バイト）。
# 16.16固定小数の1.0=0x00010000、右下だけ2.30固定小数の1.0=0x40000000 と定められている。
_MATRIX_IDENTITY = struct.pack(
    ">9I", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000
)

# ISO 639-2 "und"（言語未指定）を mdhd の5ビットパック形式にしたもの。
# (u-0x60)<<10 | (n-0x60)<<5 | (d-0x60) = 0x55C4
_LANG_UND = 0x55C4


# ============================================================
#  ボックスの生成・走査ヘルパー
# ============================================================
def _box(btype: bytes, payload: bytes) -> bytes:
    """size(32bit)+type+payload の基本ボックスを作る。"""
    size = 8 + len(payload)
    if size > 0xFFFFFFFF:
        # チャプタートラック程度で4GBを超えることはあり得ないが、念のため。
        raise Mp4ChapterError("ボックスが4GBを超えるため32bitサイズで表現できません")
    return struct.pack(">I4s", size, btype) + payload


def _full_box(btype: bytes, version: int, flags: int, payload: bytes) -> bytes:
    """version(8bit)+flags(24bit) 付きのフルボックスを作る。"""
    return _box(btype, struct.pack(">I", (version << 24) | flags) + payload)


def _iter_boxes(data: bytes, start: int, end: int):
    """data[start:end] を子ボックス列として走査し (type, offset, header長, size) を返す。

    size==1（64bit拡張サイズ）と size==0（末尾まで）にも対応。範囲からはみ出す
    ボックスは破損とみなして例外にする（非MP4入力の検出を兼ねる）。"""
    off = start
    while off < end:
        if off + 8 > end:
            raise Mp4ChapterError("MP4ボックスヘッダが途中で切れています")
        size, btype = struct.unpack_from(">I4s", data, off)
        header = 8
        if size == 1:
            if off + 16 > end:
                raise Mp4ChapterError("64bitサイズのボックスヘッダが途中で切れています")
            size = struct.unpack_from(">Q", data, off + 8)[0]
            header = 16
        elif size == 0:
            size = end - off  # 「ファイル末尾まで」の意味
        if size < header or off + size > end:
            raise Mp4ChapterError("MP4ボックスのサイズが不正です（MP4ファイルではない可能性）")
        yield btype, off, header, size
        off += size


def _scan_top_level(f, file_size: int):
    """ファイル先頭からトップレベルボックスを走査し [(type, offset, size), ...] を返す。

    mdat が巨大でもヘッダだけ読んでシークするのでメモリを食わない。"""
    boxes = []
    off = 0
    while off < file_size:
        f.seek(off)
        hdr = f.read(16)
        if len(hdr) < 8:
            raise Mp4ChapterError("MP4ボックスヘッダが途中で切れています")
        size, btype = struct.unpack_from(">I4s", hdr, 0)
        header = 8
        if size == 1:
            if len(hdr) < 16:
                raise Mp4ChapterError("64bitサイズのボックスヘッダが途中で切れています")
            size = struct.unpack_from(">Q", hdr, 8)[0]
            header = 16
        elif size == 0:
            size = file_size - off
        if size < header or off + size > file_size:
            raise Mp4ChapterError("MP4ボックスのサイズが不正です（MP4ファイルではない可能性）")
        boxes.append((btype, off, size))
        off += size
    return boxes


class _Box:
    """パース済みボックスの木ノード。

    コンテナ（_CONTAINER_TYPES）は children を持ち、それ以外は payload の
    バイト列をそのまま保持する。serialize() で常に32bitサイズヘッダに正規化して
    書き戻す（moovが4GBを超えることは実質ないため）。"""

    __slots__ = ("type", "payload", "children")

    def __init__(self, btype, payload=None, children=None):
        self.type = btype
        self.payload = payload
        self.children = children

    def serialize(self) -> bytes:
        if self.children is not None:
            payload = b"".join(c.serialize() for c in self.children)
        else:
            payload = self.payload
        return _box(self.type, payload)


def _parse_tree(btype: bytes, data: bytes, start: int, end: int) -> "_Box":
    """data[start:end]（ボックスのpayload部分）を木にパースする。"""
    if btype in _CONTAINER_TYPES:
        children = []
        for t, off, header, size in _iter_boxes(data, start, end):
            children.append(_parse_tree(t, data, off + header, off + size))
        return _Box(btype, children=children)
    return _Box(btype, payload=bytes(data[start:end]))


def _find_child(box: "_Box", btype: bytes):
    """boxの直下の子から最初のbtypeを探す（無ければNone）。"""
    for c in box.children or ():
        if c.type == btype:
            return c
    return None


# ============================================================
#  既存 moov の読み取り・パッチ
# ============================================================
def _parse_mvhd(payload: bytes):
    """mvhdから (timescale, duration, next_track_id) を取り出す。"""
    if len(payload) < 4:
        raise Mp4ChapterError("mvhdが短すぎます")
    version = payload[0]
    # フィールドオフセットは QuickTime File Format 仕様の固定配置:
    #   v0: ctime4+mtime4 → timescale@12 duration@16(32bit) ... next_track_id@96
    #   v1: ctime8+mtime8 → timescale@20 duration@24(64bit) ... next_track_id@108
    if version == 0:
        if len(payload) < 100:
            raise Mp4ChapterError("mvhd(v0)が短すぎます")
        timescale, duration = struct.unpack_from(">II", payload, 12)
        next_track_id = struct.unpack_from(">I", payload, 96)[0]
    elif version == 1:
        if len(payload) < 112:
            raise Mp4ChapterError("mvhd(v1)が短すぎます")
        timescale = struct.unpack_from(">I", payload, 20)[0]
        duration = struct.unpack_from(">Q", payload, 24)[0]
        next_track_id = struct.unpack_from(">I", payload, 108)[0]
    else:
        raise Mp4ChapterError("未知のmvhdバージョン: %d" % version)
    return timescale, duration, next_track_id


def _patch_mvhd_next_track_id(payload: bytes, new_id: int) -> bytes:
    """mvhdのnext_track_idを書き換えたpayloadを返す。"""
    buf = bytearray(payload)
    offset = 96 if payload[0] == 0 else 108
    struct.pack_into(">I", buf, offset, new_id)
    return bytes(buf)


def _tkhd_info(trak: "_Box"):
    """trakから (tkhdのflags, track_id) を取り出す（tkhdが無ければ(None, None)）。"""
    tkhd = _find_child(trak, b"tkhd")
    if tkhd is None or len(tkhd.payload) < 24:
        return None, None
    flags = int.from_bytes(tkhd.payload[1:4], "big")
    # track_idの位置もバージョン依存: v0はctime4+mtime4の後の@12、v1は8+8の後の@20。
    track_id_offset = 12 if tkhd.payload[0] == 0 else 20
    track_id = struct.unpack_from(">I", tkhd.payload, track_id_offset)[0]
    return flags, track_id


def _handler_type(trak: "_Box"):
    """trakのメディアハンドラ種別（b'soun'等）を返す（見つからなければNone）。"""
    mdia = _find_child(trak, b"mdia")
    if mdia is None:
        return None
    hdlr = _find_child(mdia, b"hdlr")
    if hdlr is None or len(hdlr.payload) < 12:
        return None
    # hdlr payload: version/flags(4) + component_type(4) + component_subtype(4)
    return hdlr.payload[8:12]


def _select_audio_trak(moov: "_Box") -> "_Box":
    """tref{chap}を付ける対象の音声trakを選ぶ。

    「最初のenabledな音声trak」を基本とし、enabledフラグが立っていない
    ファイル（一部のツールはflags=0で書く）でも動くよう音声trakのみでも探す。"""
    traks = [c for c in moov.children if c.type == b"trak"]
    candidates = []
    for trak in traks:
        flags, track_id = _tkhd_info(trak)
        if track_id is None or _handler_type(trak) != b"soun":
            continue
        candidates.append((flags, trak))
    for flags, trak in candidates:
        if flags is not None and (flags & 0x1):  # tkhd flags bit0 = トラック有効
            return trak
    if candidates:
        return candidates[0][1]
    raise Mp4ChapterError("音声トラック(soun)が見つかりません")


def _insert_tref_chap(trak: "_Box", chapter_track_id: int) -> None:
    """音声trakに tref{chap: chapter_track_id} を挿入する（tkhd直後）。

    既にchap参照がある場合は例外にする。chapへIDを追記すると音声trakが
    新旧2本のチャプタートラックを同時参照し、AVFoundationが両者を混ぜて
    「時刻は旧・タイトルは新」の壊れた章リストを返す（サイレント破損）ため、
    再適用は明示的に拒否する。他種の参照（sync等）だけのtrefにはchapを追加する。"""
    chap_entry = struct.pack(">I", chapter_track_id)
    existing = _find_child(trak, b"tref")
    if existing is not None:
        parts = []
        for t, off, header, size in _iter_boxes(existing.payload, 0, len(existing.payload)):
            if t == b"chap":
                raise Mp4ChapterError(
                    "チャプター追加済みのファイルには再適用できません"
                    "（元の音声から作り直してください）")
            parts.append(_box(t, existing.payload[off + header:off + size]))
        parts.append(_box(b"chap", chap_entry))
        existing.payload = b"".join(parts)
        return
    tref = _Box(b"tref", payload=_box(b"chap", chap_entry))
    # tkhdの直後に入れる（QuickTime Playerがこの位置を期待する既存実装が多い）。
    for i, c in enumerate(trak.children):
        if c.type == b"tkhd":
            trak.children.insert(i + 1, tref)
            return
    trak.children.insert(0, tref)


# ============================================================
#  チャプタートラックの構築
# ============================================================
def _text_sample(title: str) -> bytes:
    """チャプター1個分のテキストサンプルを作る。

    形式: 2バイトBE長 + UTF-8本文 + 'encd'拡張ボックス。
    'encd'の値 0x00000100 は「本文はUTF-8」の指定で、これが無いと
    日本語タイトルがMacRomanとして化けるプレイヤーがある。"""
    body = title.encode("utf-8")
    if len(body) > 0xFFFF:
        raise Mp4ChapterError("チャプタータイトルが長すぎます（UTF-8で64KB超）: %r" % title[:50])
    return (
        struct.pack(">H", len(body))
        + body
        + struct.pack(">I4sI", 12, b"encd", 0x00000100)
    )


def _build_text_sample_description() -> bytes:
    """QuickTime 'text' サンプル記述（stsdエントリ）を作る。

    値はffmpeg等の実績あるチャプター実装と同じ「全部デフォルト」:
    表示フラグ0・背景黒・テキストボックス0・フォント情報なし。
    チャプタートラックは無効トラックで描画されないため見た目の値は使われない。"""
    body = (
        struct.pack(">I", 0)            # displayFlags = 0
        + struct.pack(">I", 0)          # textJustification = 0（左寄せ）
        + struct.pack(">3H", 0, 0, 0)   # bgColor RGB = 黒
        + struct.pack(">4H", 0, 0, 0, 0)  # defaultTextBox (top,left,bottom,right) = 0
        + b"\x00" * 8                   # 予約（QT仕様のreserved 64bit）
        + struct.pack(">H", 0)          # fontNumber = 0
        + struct.pack(">H", 0)          # fontFace = 0（標準）
        + b"\x00"                       # 予約
        + b"\x00\x00"                   # 予約
        + struct.pack(">3H", 0, 0, 0)   # foreColor RGB = 黒
        + b"\x00"                       # textName（空のPascal文字列）
    )
    # サンプル記述ヘッダ: size(4)+format(4)+reserved(6)+data_reference_index(2)=16バイト
    return struct.pack(">I4s6xH", 16 + len(body), b"text", 1) + body


def _build_chapter_trak(track_id: int, movie_duration: int, movie_timescale: int,
                        durations_ms, sample_sizes, sample_offsets) -> bytes:
    """チャプター用trakボックス全体のバイト列を組み立てる。"""
    # --- tkhd: flags=0（無効トラック）。チャプタートラックは再生対象ではなく
    #     参照専用なので無効にしておくのが慣例（有効だとテキストが描画されうる）。
    tkhd = _full_box(b"tkhd", 0, 0,
                     struct.pack(">II", 0, 0)          # creation/modification time
                     + struct.pack(">I", track_id)
                     + b"\x00" * 4                     # 予約
                     + struct.pack(">I", movie_duration)  # ムービータイムスケールでの長さ
                     + b"\x00" * 8                     # 予約
                     + struct.pack(">hhh", 0, 0, 0)    # layer, alternate_group, volume
                     + b"\x00" * 2                     # 予約
                     + _MATRIX_IDENTITY
                     + struct.pack(">II", 0, 0))       # width, height（テキスト非表示なので0）

    # --- mdhd: timescale=1000（ミリ秒単位。チャプター開始時刻の分解能として十分）
    total_ms = _movie_ms(movie_duration, movie_timescale)
    if total_ms > 0xFFFFFFFF:
        # 32bit ms = 約49日。オーディオブックでは到達しないため v1 対応はしない。
        raise Mp4ChapterError("総再生時間が長すぎます（mdhd v0で表現できません）")
    mdhd = _full_box(b"mdhd", 0, 0,
                     struct.pack(">IIIIHH", 0, 0, 1000, total_ms, _LANG_UND, 0))

    # --- hdlr: component subtype 'text'（これでチャプター用テキストトラックだと分かる）
    hdlr = _full_box(b"hdlr", 0, 0,
                     struct.pack(">I4s", 0, b"text")   # pre_defined, handler_type
                     + b"\x00" * 12                    # 予約
                     + b"\x00")                        # 名前（空。C文字列/Pascal両解釈で安全）

    # --- minf: テキストトラックのメディア情報ヘッダはQuickTime独自の gmhd。
    #     gmin の graphicsmode=0x40(ditherCopy)・opcolor=0x8000 はQT仕様の標準値。
    gmin = _full_box(b"gmin", 0, 0,
                     struct.pack(">H3HHH", 0x40, 0x8000, 0x8000, 0x8000, 0, 0))
    gmhd = _box(b"gmhd", gmin + _box(b"text", _MATRIX_IDENTITY))

    # --- dinf/dref: 「データは同一ファイル内」を示すurlエントリ（flags=1=self-contained）
    url_entry = _full_box(b"url ", 0, 1, b"")
    dref = _full_box(b"dref", 0, 0, struct.pack(">I", 1) + url_entry)
    dinf = _box(b"dinf", dref)

    # --- stbl: サンプルテーブル一式
    stsd = _full_box(b"stsd", 0, 0,
                     struct.pack(">I", 1) + _build_text_sample_description())
    stts = _full_box(b"stts", 0, 0,
                     struct.pack(">I", len(durations_ms))
                     + b"".join(struct.pack(">II", 1, d) for d in durations_ms))
    # 1チャンク=1サンプルなので stsc は「チャンク1以降すべて1サンプル」の1エントリで足りる。
    stsc = _full_box(b"stsc", 0, 0, struct.pack(">IIII", 1, 1, 1, 1))
    stsz = _full_box(b"stsz", 0, 0,
                     struct.pack(">II", 0, len(sample_sizes))
                     + b"".join(struct.pack(">I", s) for s in sample_sizes))
    stco = _full_box(b"stco", 0, 0,
                     struct.pack(">I", len(sample_offsets))
                     + b"".join(struct.pack(">I", o) for o in sample_offsets))
    stbl = _box(b"stbl", stsd + stts + stsc + stsz + stco)

    minf = _box(b"minf", gmhd + dinf + stbl)
    mdia = _box(b"mdia", mdhd + hdlr + minf)
    return _box(b"trak", tkhd + mdia)


def _movie_ms(duration: int, timescale: int) -> int:
    """ムービータイムスケールの長さをミリ秒に換算する。"""
    if timescale <= 0:
        raise Mp4ChapterError("mvhdのtimescaleが不正です")
    return round(duration * 1000 / timescale)


# ============================================================
#  公開API
# ============================================================
def add_chapters(path: str, chapters: list) -> None:
    """chapters: [(title: str, start_sec: float), ...]（start昇順・先頭は0推奨）。

    path のMP4ファイル（afconvert/ffmpeg出力のm4a等）を書き換えて
    QuickTimeチャプタートラックを追加する。
    失敗時は例外を投げ、ファイルは元のまま残す（一時ファイルに書いてから置換）。

    注意: QuickTimeチャプターはメディア先頭(0秒)から始まるため、先頭チャプターの
    startが0でない場合は0として扱う（先頭に「章なし区間」は表現できない）。
    """
    # ---- 入力検証（ファイルに触る前に済ませる）----
    if not chapters:
        raise ValueError("chaptersが空です")
    titles = []
    starts_ms = []
    for item in chapters:
        try:
            title, start = item
        except (TypeError, ValueError):
            raise ValueError("chaptersは [(タイトル, 開始秒), ...] の形式で指定してください")
        if not isinstance(title, str):
            raise TypeError("チャプタータイトルはstrで指定してください: %r" % (title,))
        if not isinstance(start, (int, float)) or isinstance(start, bool):
            raise TypeError("チャプター開始時刻は数値(秒)で指定してください: %r" % (start,))
        if start < 0:
            raise ValueError("チャプター開始時刻が負です: %r" % (start,))
        titles.append(title)
        starts_ms.append(round(start * 1000))
    # 先頭は0扱い（docstring参照）。丸め後に同時刻となる章は区別できないため昇順を強制。
    starts_ms[0] = 0
    for i in range(1, len(starts_ms)):
        if starts_ms[i] <= starts_ms[i - 1]:
            raise ValueError("チャプター開始時刻は昇順（1ms以上間隔）で指定してください")

    # ---- 元ファイルの解析（読み取りのみ）----
    file_size = os.path.getsize(path)
    with open(path, "rb") as f:
        top_boxes = _scan_top_level(f, file_size)
        moov_index = None
        for i, (btype, off, size) in enumerate(top_boxes):
            if btype == b"moov":
                moov_index = i
                break
        if moov_index is None:
            raise Mp4ChapterError("moovボックスが見つかりません（MP4ファイルではない可能性）")
        _, moov_offset, moov_size = top_boxes[moov_index]
        f.seek(moov_offset)
        moov_raw = f.read(moov_size)
        if len(moov_raw) != moov_size:
            raise Mp4ChapterError("moovの読み取りに失敗しました")

    # moov自身のヘッダ長を求めてpayload部分を木にパース
    _, _, moov_header, _ = next(_iter_boxes(moov_raw, 0, len(moov_raw)))
    moov = _parse_tree(b"moov", moov_raw, moov_header, moov_size)

    mvhd = _find_child(moov, b"mvhd")
    if mvhd is None:
        raise Mp4ChapterError("mvhdボックスが見つかりません")
    movie_timescale, movie_duration, next_track_id = _parse_mvhd(mvhd.payload)
    total_ms = _movie_ms(movie_duration, movie_timescale)
    if total_ms <= 0:
        raise Mp4ChapterError("ムービー長が0です（fragmented MP4は未対応）")
    if starts_ms[-1] >= total_ms:
        raise ValueError(
            "チャプター開始時刻(%.3fs)が総再生時間(%.3fs)以上です"
            % (starts_ms[-1] / 1000, total_ms / 1000))

    # 各章の長さ(ms) = 次章の開始まで。最終章はムービー末尾まで。
    durations_ms = [starts_ms[i + 1] - starts_ms[i] for i in range(len(starts_ms) - 1)]
    durations_ms.append(total_ms - starts_ms[-1])

    # チャプタートラックのID。next_track_idが規約通りならそれを使い、
    # 0/0xFFFFFFFF等の変則値なら既存trakの最大ID+1でフォールバック。
    existing_ids = []
    for trak in moov.children:
        if trak.type == b"trak":
            _, tid = _tkhd_info(trak)
            if tid:
                existing_ids.append(tid)
    if 0 < next_track_id < 0xFFFFFFFF and next_track_id not in existing_ids:
        chapter_track_id = next_track_id
    else:
        chapter_track_id = max(existing_ids, default=0) + 1

    # ---- 追記位置の決定 ----
    # moovが末尾なら「moov位置で切り詰めて追記」、そうでなければ
    # 「旧moovを同サイズfreeで潰して末尾に追記」（mdatのstcoを不変に保つため）。
    moov_is_last = (moov_index == len(top_boxes) - 1)
    append_pos = moov_offset if moov_is_last else file_size

    # ---- チャプターテキスト用mdatとstcoオフセットの計算 ----
    samples = [_text_sample(t) for t in titles]
    sample_sizes = [len(s) for s in samples]
    sample_offsets = []
    cursor = append_pos + 8  # 追記するmdatのヘッダ(8バイト)直後が最初のサンプル
    for size in sample_sizes:
        if cursor > 0xFFFFFFFF:
            # stco(32bit)で指せない位置。co64対応を入れるより先にあり得ない規模なので例外。
            raise Mp4ChapterError("追記位置が4GBを超えるためstcoで表現できません")
        sample_offsets.append(cursor)
        cursor += size
    chapter_mdat = _box(b"mdat", b"".join(samples))

    # ---- moovの再構築 ----
    mvhd.payload = _patch_mvhd_next_track_id(mvhd.payload, chapter_track_id + 1)
    audio_trak = _select_audio_trak(moov)
    _insert_tref_chap(audio_trak, chapter_track_id)
    chapter_trak = _build_chapter_trak(
        chapter_track_id, movie_duration, movie_timescale,
        durations_ms, sample_sizes, sample_offsets)
    # 新trakはmoov末尾に追加（既存trak・udta等の順序は保持）
    moov.children.append(_Box(b"trak", payload=chapter_trak[8:]))
    new_moov = moov.serialize()

    if not moov_is_last and moov_size > 0xFFFFFFFF:
        raise Mp4ChapterError("旧moovが4GB超のためfreeボックスで置換できません")

    # ---- 一時ファイルに書いてから置換（失敗時に元ファイルを守る）----
    abs_path = os.path.abspath(path)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".mp4chapters_", suffix=".tmp", dir=os.path.dirname(abs_path))
    os.close(fd)
    try:
        shutil.copyfile(abs_path, tmp_path)
        with open(tmp_path, "r+b") as f:
            if moov_is_last:
                f.truncate(moov_offset)
            else:
                # 旧moov領域をfreeボックス化。ヘッダ8バイトだけ書き換えれば
                # 残りはfreeのpayload（無視される領域）になる。
                f.seek(moov_offset)
                f.write(struct.pack(">I4s", moov_size, b"free"))
            f.seek(0, os.SEEK_END)
            assert f.tell() == append_pos  # 追記位置の計算とファイル実体の整合性確認
            f.write(chapter_mdat)
            f.write(new_moov)
        os.replace(tmp_path, abs_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
