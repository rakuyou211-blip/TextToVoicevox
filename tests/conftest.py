# -*- coding: utf-8 -*-
"""テスト全体の下ごしらえ。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core

# 開発者の機械に強い文字読み取りが入っていても、テストはそれを使わない
# （Windows の読み取りの流れを確かめるテストが、知らないうちに別の道を通らないように）
core.OCR_ENGINE_DIR = os.path.join(core.APP_DIR, "ocr_engine_is_not_used_in_tests")
