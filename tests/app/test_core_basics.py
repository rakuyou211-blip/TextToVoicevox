# -*- coding: utf-8 -*-
"""VOICEVOX連携の土台まわり（エンジンが動いていなくても通るもの）。

もとは _legacy/test_all.py。1本の台本だったものを、落ちた場所が名前で分かる
ように割りました。名前は元のまま残しています。
"""
import json

import pytest

import core


# ------------------------------------------------------------------ URL
def test_URL正規化():
    assert core.vv_normalize_url("127.0.0.1:50021") == "http://127.0.0.1:50021"
    assert core.vv_normalize_url("  http://x:1/ ") == "http://x:1"
    assert core.vv_normalize_url("") == ""


def test_壊れたURLは_bad_url():
    assert core.vv_probe("ttp://こわれた", timeout=1)["status"] == "bad_url"


def test_候補URLの列挙():
    assert isinstance(core.vv_discover_urls(), list)


def test_runtime_infoは例外を投げない():
    assert isinstance(core.vv_runtime_urls(), list)


# ------------------------------------------------------------------ 検出
def test_本体を見つける():
    assert core.find_voicevox()


def test_エンジンを見つける():
    assert core.find_voicevox_engine()


@pytest.mark.skipif(not core.IS_WIN, reason="Windowsだけの経路")
def test_レジストリからも見つく():
    assert core._win_registry_voicevox()


def test_空きポートが取れる():
    assert 1024 < core.free_port() < 65536


# ------------------------------------------------------------------ 辞書
def test_品詞の逆引き():
    assert core.word_type_of({"part_of_speech": "動詞",
                              "part_of_speech_detail_1": "自立"}) == "VERB"


def test_未知の品詞はNone():
    assert core.word_type_of({"part_of_speech": "感動詞",
                              "part_of_speech_detail_1": "*"}) is None


def test_表記ゆれをまとめる():
    assert core.dict_surface_key("ＡＢＣ") == core.dict_surface_key("abc")


# ------------------------------------------------------------------ .vvproj
SID, UID = 2, "uuid-x"

RAW_QUERY = {
    "accent_phrases": [{"moras": [{"text": "ア", "consonant": None,
                                   "consonant_length": None, "vowel": "a",
                                   "vowel_length": 0.1, "pitch": 5.5}],
                        "accent": 1, "pause_mora": None,
                        "is_interrogative": False}],
    "speedScale": 1.0, "pitchScale": 0.0, "intonationScale": 1.0,
    "volumeScale": 1.0, "prePhonemeLength": 0.1, "postPhonemeLength": 0.1,
    "pauseLength": None, "pauseLengthScale": 1.0,
    "outputSamplingRate": 24000, "outputStereo": False, "kana": "ア"}


@pytest.fixture
def plain_vvproj():
    text = core.make_vvproj(["一行目。", ("二行目。", 3, "uuid-y")], SID, UID)
    core.validate_vvproj(text)
    return json.loads(text)


@pytest.fixture
def query():
    return core.normalize_vvproj_query(RAW_QUERY, 1.3, 0.05, 1.4, 0.8, 1.5)


@pytest.fixture
def vvproj_with_query(query):
    text = core.make_vvproj([("行。", SID, UID, query),
                             ("query無しの行。", SID, UID, None)], SID, UID)
    core.validate_vvproj(text)
    return text


def test_appVersionは0_22_0(plain_vvproj):
    assert plain_vvproj["appVersion"] == "0.22.0"


def test_query無しが既定(plain_vvproj):
    assert all("query" not in item
               for item in plain_vvproj["talk"]["audioItems"].values())


def test_キーがcamelCaseになる(query):
    assert "accentPhrases" in query
    assert "isInterrogative" in query["accentPhrases"][0]


def test_中のnullが消える(query):
    assert "consonant" not in query["accentPhrases"][0]["moras"][0]


def test_調整値が入る(query):
    assert query["speedScale"] == 1.3
    assert query["volumeScale"] == 0.8


def test_句読点の間も反映(query):
    assert query["pauseLengthScale"] == 1.5


def test_queryの有り無しが混ざってよい(vvproj_with_query):
    items = list(json.loads(vvproj_with_query)["talk"]["audioItems"].values())
    assert "query" in items[0]
    assert "query" not in items[1]


def test_自己点検が壊れたqueryを止める(vvproj_with_query):
    broken = json.loads(vvproj_with_query)
    key = broken["talk"]["audioKeys"][0]
    broken["talk"]["audioItems"][key]["query"]["speedScale"] = "はやい"
    with pytest.raises(ValueError):
        core.validate_vvproj(json.dumps(broken, ensure_ascii=False))


# ------------------------------------------------------------------ 画面
@pytest.mark.gui
def test_未接続では音声機能が使えない(app, pump):
    pump(1.5)
    assert not app._engine_ready()
    assert str(app.synth_btn["state"]) == "disabled"
    assert str(app.dict_btn["state"]) == "disabled"


@pytest.mark.gui
def test_エンジンのみボタンがある(app):
    assert app.engine_only_btn


@pytest.mark.gui
def test_全テーマ切替(app):
    assert app.THEMES
    for key, _label, _preview in app.THEMES:
        app.theme_var.set(key)
        app.apply_theme()
        app.update()


@pytest.mark.gui
def test_設定キーが揃っている(app):
    need = ["theme", "speaker", "base_url", "engine_url_mode", "voicevox_path",
            "engine_use_gpu", "vvproj_query", "geometry", "synth_cache_mb"]
    have = app._settings_dict()
    assert [k for k in need if k not in have] == []


@pytest.mark.gui
def test_定期ループを管理している(app):
    assert isinstance(app._ticks, dict) and app._ticks


@pytest.mark.gui
def test_終了後にループが残らない(app):
    app._stop_ticks()
    assert not app._ticks
