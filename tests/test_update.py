# -*- coding: utf-8 -*-
"""新しい版のお知らせ（core.fetch_latest_release ほか）と、字下げで見分ける段落
（core.paragraph_ids・clean_text）のテスト。ネットにはつながない。"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---- 新しい版のお知らせ ----

@pytest.mark.parametrize("text, want", [
    ("v1.26.0", (1, 26, 0)), ("1.26", (1, 26, 0)), ("v2.0.10-beta", (2, 0, 10)),
    ("", None), (None, None), ("latest", None)])
def test_parse_version(text, want):
    assert core.parse_version(text) == want


def test_is_newer_compares_numbers_not_text():
    """1.10 は 1.9 より新しい（文字で比べると逆になる）。読めない版は知らせない。"""
    assert core.is_newer("v1.10.0", "1.9.9")
    assert not core.is_newer("v1.9.9", "1.10.0")
    assert not core.is_newer("v1.25.0", "1.25.0")
    assert not core.is_newer("nightly", "1.25.0")
    assert core.is_newer("v99.0.0")   # 既定は今の版と比べる


def test_update_is_asked_at_most_once_a_day():
    day = core.UPDATE_CHECK_EVERY
    assert core.update_due(0, now=1000)
    assert not core.update_due(1000, now=1000 + day - 1)
    assert core.update_due(1000, now=1000 + day)
    assert core.update_due(5000, now=1000), "時計が戻っていたら聞き直す"
    assert core.update_due("こわれた値", now=1000)


class _Reply:
    def __init__(self, data):
        self._body = json.dumps(data).encode("utf-8")

    def __enter__(self):
        return io.BytesIO(self._body)

    def __exit__(self, *a):
        return False


def test_fetch_latest_release_sends_only_the_app_version():
    """送るのはアプリの名前と版（User-Agent）だけ。返事からタグとページの URL を取る。"""
    seen = []

    def fake(req, timeout):
        seen.append((req.full_url, dict(req.header_items()), timeout))
        return _Reply({"tag_name": "v1.26.0", "html_url": "https://example.invalid/r"})
    assert core.fetch_latest_release(urlopen=fake) == ("v1.26.0", "https://example.invalid/r")
    url, headers, timeout = seen[0]
    assert url == core.RELEASES_API and timeout <= 10
    assert headers["User-agent"] == "TextToVoicevox/" + core.APP_VERSION


@pytest.mark.parametrize("reply", [
    {"tag_name": "v1.26.0", "prerelease": True}, {"tag_name": "v1.26.0", "draft": True},
    {"tag_name": "nightly"}, ["not", "a", "dict"]])
def test_fetch_ignores_trial_and_odd_releases(reply):
    assert core.fetch_latest_release(urlopen=lambda req, timeout: _Reply(reply)) == (None, None)


def test_fetch_never_raises_without_network():
    def offline(req, timeout):
        raise OSError("つながらない")
    assert core.fetch_latest_release(urlopen=offline) == (None, None)


def test_install_kind(tmp_path):
    """押すだけで入れ替えられるのは、Windows の1行導入で入れた所だけ。git の中身には知らせない。"""
    base = tmp_path / "Local"
    one = base / "TextToVoicevox"
    one.mkdir(parents=True)
    assert core.install_kind(str(one), is_win=True, local_appdata=str(base)) == "oneline"
    bundle = tmp_path / "somewhere"
    (bundle / "runtime").mkdir(parents=True)
    assert core.install_kind(str(bundle), is_win=True, local_appdata=str(base)) == "oneline"
    zipped = tmp_path / "Desktop" / "TextToVoicevox" / "app"
    zipped.mkdir(parents=True)
    assert core.install_kind(str(zipped), is_win=True, local_appdata=str(base)) == "zip"
    assert core.install_kind(str(zipped), is_win=False) == "mac"
    (zipped / ".git").mkdir()
    assert core.install_kind(str(zipped), is_win=True, local_appdata=str(base)) == "dev"


def test_update_one_liner_is_the_documented_one():
    """「更新する」が動かす1行は、README に載せている1行導入と同じ。"""
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        readme = f.read()
    assert core.INSTALL_ONE_LINER in readme
    assert core.INSTALL_ONE_LINER_MAC in readme


# ---- 字下げで見分ける段落 ----

def test_paragraph_ids_follow_blank_lines_and_indents():
    lines = ["第一章", "", "　吾輩は猫である。", "名前はまだ無い。", "　どこで生れたか。",
             "「おい」", "", "　しかも。"]
    ids = core.paragraph_ids(lines)
    assert ids[2] != ids[0], "空行のあと"
    assert ids[3] == ids[2], "字下げの無い行は同じ段落"
    assert ids[4] != ids[3], "字下げで次の段落"
    assert ids[5] == ids[4]
    assert ids[7] == ids[6], "空行のあとの字下げで2回は数えない（どちらでも段落は変わる）"
    assert ids[7] != ids[5]
    assert core.paragraph_ids(["　最初の行"]) == [0]


_BOOK = ("第一章　出会い\n\n"
         "　吾輩は猫である。名前はまだ無い。どこで生れたかとんと見当がつかぬ。\n"
         "　何でも薄暗いじめじめした所で泣いていた事だけは記憶している。\n"
         "「おい、お前」\n"
         "　しかもあとで聞くとそれは書生という人間であったそうだ。\n")


@pytest.mark.parametrize("opts", [{}, {"smart_join": True}, {"join_wrapped": True},
                                  {"mode": "keep"}, {"remove_blank": False}])
def test_extracted_book_keeps_paragraph_breaks(opts):
    """取り出すときに「空行を削除」（最初からオン）でも、字下げは段落の頭の全角スペース
    1つとして残る。以前は消えていて、取り出した本では段落の間が長くならなかった。"""
    out = core.clean_text(_BOOK, **opts)
    lines = [l for l in out.split("\n") if l.strip()]
    ids = [i for l, i in zip(out.split("\n"), core.paragraph_ids(out.split("\n"))) if l.strip()]
    kinds = core.line_breaks(lines, ids)
    assert kinds.count("para") == 3, (opts, lines)
    starts = [l for l in lines if l.startswith("　")]
    assert len(starts) == 3 and all(not l.startswith("　　") for l in starts)
    assert core._PARA_MARK not in out


def test_text_without_indents_is_unchanged():
    """字下げの無い文章の取り出し結果は、以前と1文字も変わらない。"""
    raw = "吾輩は猫である。名前はまだ無い。\n\nどこで生れたかとんと見当がつかぬ。\n"
    assert core.clean_text(raw) == "吾輩は猫である。\n名前はまだ無い。\nどこで生れたかとんと見当がつかぬ。"
