# 移植前の台本（全部移植済み・pytest には拾われません）

ここにある8本は、pytest に載せる前の古い形のテストです。
**2026-09-23 に全部 `tests/` の pytest 形式へ移しました。** 対応はこう:

| 元 | 移植先 |
|---|---|
| `test_all.py` | `../test_core_basics.py` |
| `test_stage1.py` | `../test_stability.py` |
| `test_stage2.py` | `../test_progress.py` |
| `test_review_abc.py` | `../test_playback.py` |
| `test_geometry.py` | `../test_settings.py` |
| `test_english_ocr.py` + `test_english_help.py` | `../test_ocr_english.py` |
| `test_second_pass_cancel.py` | `../test_ocr_cancel.py` |
| `test_stage1_live.py` | `../test_engine_live.py` |
| `test_rapidocr_edges.py` | `../test_ocr_rapidocr.py` |

（`test_all.py` と `test_stage1.py` は移植のときに消しました。残り8本がここにあります）

移植のあと、別の担当が元と移植先を1本ずつ読み比べて、落ちた確認・弱まった確認を
洗い出しました。その指摘はすべて移植先に反映してあります
（空振りの合格の解消、拡大される枝と透過PNGの確認の復活、実時間の上限の復活など）。

**なぜ消さずに残してあるか**: この計画は git で管理していないので、消すと戻せません。
読み比べの拠り所として残してあります。もう要らないと判断したら、この `_legacy/`
フォルダごと消して構いません（pytest は `collect_ignore_glob` で拾っていません）。
