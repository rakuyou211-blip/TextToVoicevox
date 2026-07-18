# -*- coding: utf-8 -*-
"""ドキュメント整合テスト。

APP_VERSION を上げたのに README / CHANGELOG を更新し忘れたままコミットする事故
（v1.11.0表記が4リリース分取り残された実績あり）を、コードと同じテストゲートで塞ぐ。
stdlibのみでCIでもそのまま動く。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def test_changelog_top_matches_app_version():
    m = re.search(r"^## v(\d+\.\d+\.\d+)", _read("CHANGELOG.md"), re.MULTILINE)
    assert m, "CHANGELOG.md に ## vX.Y.Z 見出しがありません"
    assert m.group(1) == core.APP_VERSION, (
        f"CHANGELOG先頭は v{m.group(1)} だが APP_VERSION は {core.APP_VERSION}")


def test_readme_mentions_current_version():
    for name in ("README.md", "README.en.md"):
        assert f"v{core.APP_VERSION}" in _read(name), (
            f"{name} に v{core.APP_VERSION} の記載がありません（版数の更新漏れ）")


def test_no_stale_zip_names():
    pat = re.compile(r"TextToVoicevox_v([\d.]+)\.zip")
    for name in ("README.md", "README.en.md"):
        for m in pat.finditer(_read(name)):
            assert m.group(1) == core.APP_VERSION, (
                f"{name} の zip 名が古い版です: {m.group(0)}")


def test_python_version_requirement_consistent():
    """最低Pythonバージョンの記載が README(日英)・setup.bat で一致している。"""
    versions = {}
    for name, pat in (("README.md", r"Python\s*([\d.]+)\s*以降"),
                      ("README.en.md",
                       r"Python\s*([\d.]+)\s*or\s*(?:later|newer)"),
                      ("setup.bat", r"(3\.\d+)\s*以降")):
        m = re.search(pat, _read(name))
        assert m, f"{name} に最低Pythonバージョンの記載が見つかりません"
        versions[name] = m.group(1)
    assert len(set(versions.values())) == 1, \
        f"最低Pythonバージョンの記載が不一致: {versions}"
