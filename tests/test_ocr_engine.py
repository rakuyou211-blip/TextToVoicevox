# -*- coding: utf-8 -*-
"""強い文字読み取り（ocr_helper.py と core の読み取りの流れ）と、縦書きの読み順のテスト。

本物の部品（ocr_engine）は使わない。裏方は小さな偽物の Python スクリプトで置き換える
（どの OS でも動く）。本物での確かめは手元で行う。
"""
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core
import ocr_helper


def _col(text, x0, x1, y0=0.05, y1=0.9):
    return {"text": text, "x0": x0, "x1": x1, "y0": y0, "y1": y1}


# ---------------- 縦書きの読み順（どの読み手でも共通） ----------------
class TestTateToYoko:
    def test_columns_are_read_from_the_right(self):
        """以前は列を左から並べて、ページの終わりから読み上げていた。"""
        lines = [_col("さんれつめ。", 0.1, 0.15), _col("にれつめ。", 0.4, 0.45), _col("いちれつめ。", 0.7, 0.75)]
        out = core.reflow_ocr_lines(core.tate_to_yoko(lines, aspect=1.0))
        assert out.split("\n") == ["いちれつめ。", "にれつめ。", "さんれつめ。"]

    def test_wrapped_columns_are_joined_like_wrapped_lines(self):
        """列の下まで届いて文が続くなら、横書きの折り返しと同じに1文へつなぐ。"""
        lines = [_col("で終わる。", 0.52, 0.57, y1=0.4), _col("文の続き", 0.6, 0.65)]
        out = core.reflow_ocr_lines(core.tate_to_yoko(lines, aspect=1.0))
        assert out == "文の続きで終わる。"

    def test_yoko_page_is_left_alone(self):
        lines = [_col("よこがきの行です", 0.1, 0.8, 0.1, 0.13), _col("次の行です", 0.1, 0.5, 0.2, 0.23)]
        assert core.tate_to_yoko(lines, aspect=1.0) is lines

    def test_a_word_in_a_wide_crop_is_not_a_column(self):
        """横に細長い切り抜きの「OK」は、座標だけ見ると縦長になる。画像の比で見分ける。"""
        line = {"text": "OK", "x0": 0.45, "x1": 0.55, "y0": 0.1, "y1": 0.9}
        assert core.tate_to_yoko([line], aspect=1.0)[0]["x0"] != 0.45
        assert core.tate_to_yoko([line], aspect=10.0)[0]["x0"] == 0.45

    def test_a_short_mark_does_not_turn_the_page(self):
        """横書きのページに縦長の箱が少しあっても、ページごと回さない。"""
        lines = [_col("縦", 0.9, 0.95), _col("よこがきの長い行がここにあります", 0.1, 0.8, 0.1, 0.13)]
        assert core.tate_to_yoko(lines, aspect=1.0) is lines

    def test_windows_result_uses_the_order(self, tmp_path):
        """Windows の読み取りの結果も、右の列から並ぶ。"""
        from PIL import Image
        png = tmp_path / "tate.png"
        Image.new("RGB", (800, 800), "white").save(png)
        data = [{"path": str(png), "ok": True, "text": "x",
                 "lines": [_col("にれつめ。", 0.2, 0.25), _col("いちれつめ。", 0.6, 0.65)]}]
        got = core._parse_windows_ocr_result(data, strip_labels=False)
        assert got[str(png)].split("\n") == ["いちれつめ。", "にれつめ。"]

    def test_image_aspect(self, tmp_path):
        from PIL import Image
        png = tmp_path / "wide.png"
        Image.new("RGB", (300, 100), "white").save(png)
        assert core.image_aspect(str(png)) == 3.0
        assert core.image_aspect(str(tmp_path / "none.png")) == 1.0


# ---------------- 縦書きの列を、Windows の読みで補う ----------------
class TestMergeTateReading:
    def test_keeps_kanji_and_adds_dropped_small_marks(self):
        base = "間中で一番獰悪な種族であたそうだこの書生"      # 漢字は正しいが、小さい字が抜ける
        other = "間中で一番猙悪な種族であったそうだ。この書生"  # 小さい字は読めるが、漢字を読み違える
        assert core._merge_tate_reading(base, other) == "間中で一番獰悪な種族であったそうだ。この書生"

    def test_adds_the_small_form_even_if_windows_read_it_big(self):
        """土台が落とすのは小さい字だけ。Windows の「よ」は、本当は「ょ」。"""
        assert core._merge_tate_reading("あの時はしうが", "あの時はしようが") == "あの時はしょうが"

    def test_does_not_shrink_a_character_the_base_read_big(self):
        """土台が「つ」と読めているなら、Windows の「っ」は信じない。"""
        assert core._merge_tate_reading("見当がつかぬ", "見当がっかぬ") == "見当がつかぬ"

    def test_long_vowel_from_windows(self):
        assert core._merge_tate_reading("ス一と", "スーと") == "スーと"
        assert core._merge_tate_reading("ニャニ泣", "ニャーニャー泣") == "ニャーニャー泣"

    def test_only_droppable_kinds_are_added(self):
        """Windows が足した漢字やかなは足さない（ゴミのことが多い）。"""
        assert core._merge_tate_reading("言ったって今", "一言ったって今") == "言ったって今"

    def test_spaces_in_windows_text_are_ignored(self):
        assert core._merge_tate_reading("あた", "あ っ た") == "あった"

    def test_garbage_from_windows_cannot_replace_the_column(self):
        """日本語の読み取りが入っていない Windows は、縦書きをゴミとして返す。"""
        assert core._merge_tate_reading("文字", "lllll1l1ll1l1lllmnpqrstuvw") == "文字"

    def test_without_windows_returns_base(self):
        assert core._merge_tate_reading("文字", "") == "文字"

    def test_tidy(self):
        assert core._tidy_tate("ス一と") == "スーと"
        assert core._tidy_tate("ページ一覧") == "ページ一覧"
        assert core._tidy_tate("読んだ°") == "読んだ。"


# ---------------- 文字の箱 → 行 ----------------
def _box(text, x0, y0, x1, y1):
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]], text, 0.99]


class TestStrongOcrLines:
    def _lines(self, items, width=1000, height=1000):
        return core._strong_ocr_lines(core._strong_ocr_boxes(items), width, height)

    def test_near_pieces_become_one_line(self):
        lines = self._lines([_box("後半です", 320, 100, 500, 130), _box("前半と", 100, 102, 300, 132)])
        assert [l["text"] for l in lines] == ["前半と後半です"]

    def test_english_pieces_keep_a_space(self):
        lines = self._lines([_box("Hello", 100, 100, 200, 130), _box("world", 210, 100, 320, 130)])
        assert lines[0]["text"] == "Hello world"

    def test_far_pieces_stay_apart_in_reading_order(self):
        """右の塊がわずかに上でも、左から読む。"""
        lines = self._lines([_box("値", 800, 98, 830, 128), _box("名前", 100, 100, 160, 130)])
        assert [l["text"] for l in lines] == ["名前", "値"]

    def test_two_columns_are_read_column_by_column(self):
        items = []
        for i in range(6):
            y = 100 + i * 40
            items.append(_box(f"左の段の{i}行目です。", 50, y, 450, y + 28))
            items.append(_box(f"右の段の{i}行目です。", 550, y, 950, y + 28))
        text = core.reflow_ocr_lines(self._lines(items))
        rows = text.split("\n")
        assert rows[:6] == [f"左の段の{i}行目です。" for i in range(6)]
        assert rows[6:] == [f"右の段の{i}行目です。" for i in range(6)]

    def test_tilted_lines_do_not_swallow_each_other(self):
        """傾いた写真では外接矩形が太る。高さは字の太さから作るので、行が混ざらない。"""
        items = []
        for i in range(4):
            y = 100 + i * 45
            # 長さ 800 で 40px 右下がり（約3度）の行
            items.append([[[100, y], [900, y + 40], [900, y + 68], [100, y + 28]], f"行{i}", 0.99])
        lines = self._lines(items)
        assert [l["text"] for l in lines] == [f"行{i}" for i in range(4)]
        assert lines[0]["y1"] - lines[0]["y0"] < 0.05

    def test_tate_page_is_turned(self):
        items = [_box("にれつめです", 300, 50, 340, 500), _box("いちれつめです", 600, 50, 640, 500)]
        lines = self._lines(items)
        assert [l["text"] for l in lines] == ["いちれつめです", "にれつめです"]

    def test_broken_items_are_skipped(self):
        assert core._strong_ocr_boxes([["x"], [None, "文字", 1.0], _box("", 0, 0, 9, 9)]) == []


# ---------------- 読み取りの流れ ----------------
class FakeHelper:
    """裏方の偽物。"""

    def __init__(self, replies):
        self.replies = dict(replies)
        self.asked = []

    def read(self, path):
        self.asked.append(path)
        reply = self.replies[path]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def wake(self):
        self.asked.append("wake")


def _reply(*items, width=1000, height=1000):
    return {"ok": True, "width": width, "height": height, "items": list(items)}


@pytest.fixture
def engine_dir(tmp_path, monkeypatch):
    """部品が入っている状態を作る。"""
    d = tmp_path / "ocr_engine"
    (d / "python").mkdir(parents=True)
    (d / "python" / "python.exe").write_text("", encoding="utf-8")
    (d / "ok.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(core, "OCR_ENGINE_DIR", str(d))
    monkeypatch.setattr(core, "IS_WIN", True)
    monkeypatch.setattr(core, "IS_MAC", False)
    monkeypatch.setitem(core._STRONG_BROKEN, "mark_time", None)
    return d


class TestStrongOcrAvailable:
    def test_needs_the_mark_and_the_python(self, engine_dir):
        assert core.strong_ocr_available() is True
        (engine_dir / "ok.txt").unlink()
        assert core.strong_ocr_available() is False

    def test_half_installed_is_not_available(self, engine_dir):
        (engine_dir / "python" / "python.exe").unlink()
        assert core.strong_ocr_available() is False

    def test_windows_only(self, engine_dir, monkeypatch):
        monkeypatch.setattr(core, "IS_WIN", False)
        assert core.strong_ocr_available() is False

    def test_broken_until_installed_again(self, engine_dir):
        core._note_strong_broken()
        assert core.strong_ocr_available() is False
        os.utime(engine_dir / "ok.txt", (1000000000, 1000000000))   # 入れ直すと印の時刻が変わる
        assert core.strong_ocr_available() is True


class TestPrepareForOcr:
    def _img(self):
        from PIL import Image
        return Image.new("RGB", (400, 300), (200, 30, 30))

    def test_strong_gets_the_color_image(self, engine_dir):
        out = core.prepare_for_ocr(self._img(), enable=True)
        assert out.mode == "RGB" and out.size == (400, 300)

    def test_large_pages_are_not_cut_to_the_windows_limit(self, engine_dir):
        from PIL import Image
        out = core.prepare_for_ocr(Image.new("RGB", (3500, 2480), "white"), enable=True)
        assert max(out.size) == 3500

    def test_without_it_keeps_the_windows_recipe(self, monkeypatch, tmp_path):
        monkeypatch.setattr(core, "OCR_ENGINE_DIR", str(tmp_path / "none"))
        monkeypatch.setattr(core, "IS_WIN", True)
        monkeypatch.setattr(core, "IS_MAC", False)
        out = core.prepare_for_ocr(self._img(), enable=True)
        assert out.mode == "L" and out.size == (800, 600)


class TestRunOcrChoosesTheReader:
    def test_strong_is_used_when_installed(self, engine_dir, monkeypatch):
        fake = FakeHelper({"a.png": _reply(_box("読めました。", 10, 10, 300, 40))})
        monkeypatch.setattr(core, "_STRONG_HELPER", fake)
        monkeypatch.setattr(core, "run_windows_ocr", lambda *a, **k: pytest.fail("Windows で読んだ"))
        assert core.run_ocr(["a.png"], strip_labels=False) == {"a.png": "読めました。"}

    def test_windows_is_used_when_not_installed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(core, "OCR_ENGINE_DIR", str(tmp_path / "none"))
        monkeypatch.setattr(core, "IS_WIN", True)
        monkeypatch.setattr(core, "IS_MAC", False)
        monkeypatch.setattr(core, "run_windows_ocr", lambda paths, **k: {p: "Windows" for p in paths})
        assert core.run_ocr(["a.png"]) == {"a.png": "Windows"}

    def test_failure_falls_back_and_keeps_what_was_read(self, engine_dir, monkeypatch):
        fake = FakeHelper({"a.png": _reply(_box("読めた。", 10, 10, 200, 40)),
                           "b.png": core.StrongOcrFailed("止まった")})
        monkeypatch.setattr(core, "_STRONG_HELPER", fake)
        asked = []

        def windows(paths, **k):
            asked.extend(paths)
            return {p: "Windows" for p in paths}
        monkeypatch.setattr(core, "run_windows_ocr", windows)
        notices = []
        got = core.run_ocr(["a.png", "b.png", "c.png"], strip_labels=False, notices=notices)
        assert got == {"a.png": "読めた。", "b.png": "Windows", "c.png": "Windows"}
        assert asked == ["b.png", "c.png"]
        assert notices == ["strong_ocr_failed"]
        assert core.strong_ocr_available() is False, "動かなかった部品を、また試しています"

    def test_one_bad_image_does_not_stop_the_rest(self, engine_dir, monkeypatch):
        fake = FakeHelper({"a.png": {"ok": False, "error": "壊れた画像"},
                           "b.png": _reply(_box("読めた。", 10, 10, 200, 40))})
        monkeypatch.setattr(core, "_STRONG_HELPER", fake)
        errors = []
        got = core.run_ocr(["a.png", "b.png"], strip_labels=False, errors=errors)
        assert got == {"a.png": "", "b.png": "読めた。"}
        assert errors == ["a.png: 壊れた画像"]

    def test_cancel_stops_between_images(self, engine_dir, monkeypatch):
        fake = FakeHelper({"a.png": _reply(_box("読めた。", 10, 10, 200, 40)), "b.png": _reply()})
        monkeypatch.setattr(core, "_STRONG_HELPER", fake)
        cancel = threading.Event()
        fake.read_real = fake.read

        def read_then_cancel(path):
            cancel.set()   # 1枚読んでいるあいだに止められた
            return fake.read_real(path)
        fake.read = read_then_cancel
        got = core.run_strong_ocr(["a.png", "b.png"], strip_labels=False, cancel_event=cancel)
        assert list(got) == ["a.png"] and fake.asked == ["a.png"]

    def test_progress_reaches_the_end(self, engine_dir, monkeypatch):
        fake = FakeHelper({"a.png": _reply(), "b.png": _reply()})
        monkeypatch.setattr(core, "_STRONG_HELPER", fake)
        marks = []
        core.run_strong_ocr(["a.png", "b.png"], progress_cb=lambda d, t: marks.append((d, t)))
        assert marks == [(0, 2), (1, 2), (2, 2)]

    def test_english_notice_is_not_raised(self, engine_dir, monkeypatch):
        """強い読み取りは英語も一緒に読むので、英語の案内は出ない。"""
        fake = FakeHelper({"a.png": _reply(_box("The quick brown fox jumps over the lazy dog", 10, 10, 900, 40))})
        monkeypatch.setattr(core, "_STRONG_HELPER", fake)
        notices = []
        got = core.run_ocr(["a.png"], strip_labels=False, notices=notices)
        assert got["a.png"].startswith("The quick")
        assert notices == []

    def test_prewarm_wakes_only_when_installed(self, engine_dir, monkeypatch):
        fake = FakeHelper({})
        monkeypatch.setattr(core, "_STRONG_HELPER", fake)
        core.strong_ocr_prewarm()
        for _ in range(100):
            if fake.asked:
                break
            threading.Event().wait(0.01)
        assert fake.asked == ["wake"]
        (engine_dir / "ok.txt").unlink()
        fake.asked.clear()
        core.strong_ocr_prewarm()
        threading.Event().wait(0.05)
        assert fake.asked == []


class TestTatePageFlow:
    def _page(self, tmp_path):
        from PIL import Image
        png = tmp_path / "tate.png"
        Image.new("RGB", (1000, 1000), "white").save(png)
        return str(png)

    def _items(self):
        return [_box("あたか", 300, 50, 340, 900), _box("見た°", 600, 50, 640, 900)]

    def test_columns_are_filled_from_windows_and_read_from_the_right(self, engine_dir, monkeypatch, tmp_path):
        png = self._page(tmp_path)
        monkeypatch.setattr(core, "_STRONG_HELPER", FakeHelper({png: _reply(*self._items())}))
        monkeypatch.setattr(core, "_windows_ocr_json", lambda paths, lang="ja": [{
            "lines": [_col("あ っ た か 。", 0.30, 0.34), _col("見 た 。", 0.60, 0.64)]}])
        got = core.run_ocr([png], strip_labels=False)
        assert got[png].split("\n") == ["見た。", "あったか。"]

    def test_still_reads_when_windows_cannot_help(self, engine_dir, monkeypatch, tmp_path):
        png = self._page(tmp_path)
        monkeypatch.setattr(core, "_STRONG_HELPER", FakeHelper({png: _reply(*self._items())}))

        def broken(paths, lang="ja"):
            raise RuntimeError("PowerShell が動かない")
        monkeypatch.setattr(core, "_windows_ocr_json", broken)
        got = core.run_ocr([png], strip_labels=False)
        assert got[png].split("\n") == ["見た。", "あたか"]

    def test_turned_image_does_not_ask_windows(self, engine_dir, monkeypatch, tmp_path):
        """回して読んだ画像は、Windows の読みと座標が合わない。"""
        png = self._page(tmp_path)
        reply = dict(_reply(*self._items()), turned=True)
        monkeypatch.setattr(core, "_STRONG_HELPER", FakeHelper({png: reply}))
        monkeypatch.setattr(core, "_windows_ocr_json", lambda *a, **k: pytest.fail("Windows を呼んだ"))
        assert core.run_ocr([png], strip_labels=False)[png].split("\n") == ["見た°", "あたか"]

    def test_yoko_page_does_not_call_windows(self, engine_dir, monkeypatch, tmp_path):
        png = self._page(tmp_path)
        monkeypatch.setattr(core, "_STRONG_HELPER", FakeHelper({png: _reply(_box("横書きです。", 50, 50, 500, 80))}))
        monkeypatch.setattr(core, "_windows_ocr_json", lambda *a, **k: pytest.fail("Windows を呼んだ"))
        assert core.run_ocr([png], strip_labels=False)[png] == "横書きです。"

    def test_windows_lines_accepts_a_single_line_object(self, engine_dir, monkeypatch, tmp_path):
        """PS5.1 の ConvertTo-Json は1要素の配列を畳む。"""
        png = self._page(tmp_path)
        monkeypatch.setattr(core, "_windows_ocr_json", lambda paths, lang="ja": {"lines": _col("文字", 0.1, 0.2)})
        assert core._windows_lines(png)[0]["text"] == "文字"


# ---------------- 裏方とのやりとり（偽物の Python スクリプトで） ----------------
FAKE_SERVER = r"""
import json, sys
out = sys.stdout.buffer
def send(o):
    out.write(json.dumps(o, ensure_ascii=True).encode("ascii") + b"\n"); out.flush()
MODE = sys.argv[1]
if MODE == "fatal":
    send({"fatal": "DLL load failed"}); sys.exit(1)
if MODE == "die_at_start":
    sys.exit(3)
if MODE == "garbage":
    out.write(b"not json\n"); out.flush(); sys.stdin.buffer.readline(); sys.exit(0)
send({"ready": True})
n = 0
for line in sys.stdin.buffer:
    n += 1
    path = json.loads(line)["path"]
    if MODE == "hang":
        import time; time.sleep(60)
    if MODE == "die_on_read":
        sys.exit(4)
    send({"ok": True, "path": path, "n": n})
    if MODE == "one_each":
        sys.exit(0)   # 休みに入った裏方の代わり
"""


@pytest.fixture
def helper(tmp_path, request):
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    made = []

    def make(mode):
        h = core._StrongOcrHelper()
        h._command = lambda: [sys.executable, str(script), mode]
        made.append(h)
        return h
    yield make
    for h in made:
        h._stop()


class TestStrongOcrHelperProcess:
    def test_stays_running_between_images(self, helper):
        h = helper("normal")
        assert h.read("a.png")["n"] == 1
        assert h.read("b.png") == {"ok": True, "path": "b.png", "n": 2}

    def test_japanese_paths_survive(self, helper):
        assert helper("normal").read("C:\\ユーザー\\画像.png")["path"] == "C:\\ユーザー\\画像.png"

    def test_wakes_again_after_it_went_to_rest(self, helper):
        h = helper("one_each")
        assert h.read("a.png")["path"] == "a.png"
        assert h._proc.wait(10) == 0
        assert h.read("b.png")["path"] == "b.png"

    def test_fatal_reason_is_reported(self, helper):
        with pytest.raises(core.StrongOcrFailed, match="DLL load failed"):
            helper("fatal").read("a.png")

    def test_failing_to_start_is_reported(self, helper):
        with pytest.raises(core.StrongOcrFailed):
            helper("die_at_start").read("a.png")

    def test_garbage_is_reported(self, helper):
        with pytest.raises(core.StrongOcrFailed):
            helper("garbage").read("a.png")

    def test_dying_on_every_read_gives_up_after_one_retry(self, helper):
        with pytest.raises(core.StrongOcrFailed):
            helper("die_on_read").read("a.png")

    def test_a_stuck_helper_is_stopped(self, helper):
        h = helper("hang")
        h.READ_TIMEOUT = 0.5
        h.wake()
        proc = h._proc
        with pytest.raises(core.StrongOcrFailed):
            h.read("a.png")
        assert proc.wait(10) is not None
        assert h._proc is None

    def test_missing_python_is_reported(self, helper, tmp_path):
        h = helper("normal")
        h._command = lambda: [str(tmp_path / "nothing.exe")]
        with pytest.raises(core.StrongOcrFailed):
            h.wake()


# ---------------- 裏方そのもの（部品は偽物） ----------------
class TestOcrHelper:
    def test_small_images_are_enlarged_big_pages_are_not(self):
        assert ocr_helper.scale_for(800, 600) == 2
        assert ocr_helper.scale_for(1599, 300) == 2
        assert ocr_helper.scale_for(1600, 900) == 1
        assert ocr_helper.scale_for(2480, 3508) == 1

    def test_coordinates_return_to_the_original_image(self):
        box = [[36, 56], [236, 56], [236, 96], [36, 96]]
        assert ocr_helper.to_original(box, 2, 16) == [[10, 20], [110, 20], [110, 40], [10, 40]]

    def test_recognize_reports_boxes_in_original_coordinates(self):
        class Boxes(list):
            def tolist(self):
                return list(self)

        class Result:
            boxes = [Boxes([[16, 16], [116, 16], [116, 56], [16, 56]])]
            txts = ("文字",)
            scores = (0.9,)
        reply = ocr_helper.recognize(lambda img, use_cls=None: Result(), ("絵", 50, 20, 2))
        assert reply == {"ok": True, "width": 50, "height": 20,
                         "items": [[[[0, 0], [50, 0], [50, 20], [0, 20]], "文字", 0.9]]}

    def test_nothing_found_is_an_empty_list(self):
        class Empty:
            pass
        reply = ocr_helper.recognize(lambda img, use_cls=None: Empty(), ("絵", 50, 20, 1))
        assert reply["ok"] is True and reply["items"] == []

    def _engine(self, answers):
        """絵の名前ごとに、決めた読み（文字, 確からしさ）を返す偽物。"""
        class Boxes(list):
            def tolist(self):
                return list(self)

        def engine(img, use_cls=None):
            asked.append(img)

            class Result:
                boxes = [Boxes([[0, 0], [9, 0], [9, 9], [0, 9]])] if answers.get(img) else None
                txts = (answers[img][0],) if answers.get(img) else None
                scores = (answers[img][1],) if answers.get(img) else None
            return Result()
        asked = []
        engine.asked = asked
        return engine

    def _turns(self):
        return [(lambda i: i + "180", False), (lambda i: i + "R", True), (lambda i: i + "L", True)]

    def test_upright_page_is_read_once(self):
        engine = self._engine({"絵": ("正しく読めた文字", 0.98)})
        reply = ocr_helper.recognize_upright(engine, ("絵", 50, 20, 1), self._turns())
        assert engine.asked == ["絵"] and "turned" not in reply

    def test_upside_down_page_is_turned_and_stops_when_read(self):
        engine = self._engine({"絵": ("ゴミ", 0.6), "絵180": ("正しく読めた文字", 0.99)})
        reply = ocr_helper.recognize_upright(engine, ("絵", 50, 20, 1), self._turns())
        assert engine.asked == ["絵", "絵180"]
        assert reply["turned"] is True and reply["items"][0][1] == "正しく読めた文字"
        assert (reply["width"], reply["height"]) == (50, 20)

    def test_sideways_page_swaps_width_and_height(self):
        engine = self._engine({"絵": ("ゴミ", 0.6), "絵180": ("ゴミ", 0.6), "絵R": ("正しく読めた文字", 0.99)})
        reply = ocr_helper.recognize_upright(engine, ("絵", 50, 20, 1), self._turns())
        assert (reply["width"], reply["height"]) == (20, 50)

    def test_keeps_the_first_reading_if_turning_is_worse(self):
        """写真などで元々の確からしさが低いときは、回しても良くならないので元のまま。"""
        engine = self._engine({"絵": ("そこそこ読めた文字", 0.7), "絵180": ("ゴ", 0.6)})
        reply = ocr_helper.recognize_upright(engine, ("絵", 50, 20, 1), self._turns())
        assert engine.asked == ["絵", "絵180", "絵R", "絵L"]
        assert "turned" not in reply and reply["items"][0][1] == "そこそこ読めた文字"

    def test_blank_image_stays_blank(self):
        engine = self._engine({})
        reply = ocr_helper.recognize_upright(engine, ("絵", 50, 20, 1), self._turns())
        assert reply["items"] == [] and "turned" not in reply

    def test_one_bad_image_does_not_stop_the_rest(self):
        def read(path):
            if path == "bad":
                raise ValueError("画像を開けませんでした")
            return {"ok": True, "items": []}
        sent = []
        lines = [json.dumps({"path": "bad"}), json.dumps({"path": "good"}), "not json"]
        ocr_helper.serve(read, lines, sent.append)
        assert [r["ok"] for r in sent] == [False, True, False]
        assert "画像を開けませんでした" in sent[0]["error"]

    def test_network_is_blocked(self):
        """読み取りはネットを使わない。部品が勝手につなぎに行けないように塞ぐ。"""
        import socket
        keep = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection,
                socket.getaddrinfo)
        try:
            ocr_helper._forbid_network()
            with pytest.raises(OSError):
                socket.create_connection(("example.com", 80), timeout=1)
            with pytest.raises(OSError):
                socket.getaddrinfo("example.com", 80)
            s = socket.socket()
            try:
                with pytest.raises(OSError):
                    s.connect(("127.0.0.1", 9))
            finally:
                s.close()
        finally:
            (socket.socket.connect, socket.socket.connect_ex, socket.create_connection,
             socket.getaddrinfo) = keep
