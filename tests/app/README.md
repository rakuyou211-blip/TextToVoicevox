# テスト

配布物には入りません。開発しているPCで回すためのものです。

## 回し方

```
pytest tests/app -m "not engine" -q -ra        # ふだん（実機なしで通る分）
pytest tests/app -q -ra                        # 実機（VOICEVOXと音が要る）込み
pytest tests/app/test_core_basics.py -q        # 1ファイルだけ
pytest tests/app -k URL -q                     # 名前で選ぶ
```

CI（画面もエンジンも無い）では `-m "not engine and not gui"`。
`-ra` を付けると、飛ばしたテストとその理由が出る（英語OCRが入っていないと
1ファイルまるごと静かに消えるので、それに気づくため）。

**全179本**（うち gui 62・slow 31・engine 8）。開発機での所要はふだんの分で1〜2分、
実機込みで3分ほど。幅があるのは RapidOCR の読み取りが、そのとき動いている他のものに
持っていかれるぶん遅くなるため。

## 何があるか

| ファイル | 中身 |
|---|---|
| `conftest.py` | 共通の土台。見張りと fixture（下記） |
| `_seed.py` | テスト用の画像・音を作る。本人のスクリーンショットには頼らない |
| `test_smoke.py` | 土台そのものの確認（16本）。見張りが本当に落とすかを、入れ子の pytest で確かめる |
| `test_core_basics.py` | URL・VOICEVOX検出・辞書・.vvproj・画面の初期状態 |
| `test_stability.py` | 壊れにくさ（壊れた設定・キャンセル後のボタン・遅れて届く通知・終了時のワーカー・合成失敗の切り上げ） |
| `test_settings.py` | 窓の位置と大きさの記憶（いまはそれだけ。ほかの設定もここへ合流させる） |
| `test_progress.py` | 進捗の見え方と停止、%TEMP% の掃除、保存フェーズ |
| `test_playback.py` | 再生・試聴・先読み（止めた再生は鳴らさない、二重に合成しない） |
| `test_ocr_english.py` | 英語OCR（ひらがな率での判定・読み手の選び方・アプリ内の案内） |
| `test_ocr_cancel.py` | 英語の読み直しの取り消しと進捗、スレッド数の上限 |
| `test_ocr_rapidocr.py` | RapidOCR の際どい入力（壊れたPNG・空白・巨大画像・並行） |
| `test_engine_live.py` | 実機（エンジン＋音が要る）。試聴・連続再生・しおりからの再開・停止直後の再生・再生の一時ファイル |
| `_legacy/` | 移植前の古い台本（**全部移植済み**・拾われません）。消すと戻せないので残してある。`_legacy/README.md` に対応表 |

## 見張り（黙って通らないための仕掛け）

- **本人のファイルを守る** … `settings.json` と `last_text.txt` は毎テスト前に覚え、
  後で必ず元に戻す。意図せず書き換わっていたら、戻したうえで**落とす**。
  わざと書き換えるテストには `@pytest.mark.writes_settings` を付ける。
- **`App._on_close` を呼べなくする** … 設定を保存してしまう唯一の経路。
  画面を閉じるのは `_stop_ticks()` と `destroy()` で足りる。
- **%TEMP% の置き土産を数える** … `t2v_*` が増えていたら落とす。
  再生の一時ファイルは持ち越しが仕様なので、数える前に終了時と同じ片づけを走らせる。
- **本物の署名を見る** … 偽物（stub）が本体の引数追加に追いつかず黙って通る事故が
  3回あったので、テストが差し替えている本体の関数12本の引数を照合している
  （`run_ocr` / `run_rapidocr` / `_run_windows_ocr_chunk` / `_english_second_pass` /
  `_rapidocr_engine` / `rapidocr_available` / `vv_synthesize_cached` /
  `play_wav_blocking` / `synth_cache_get` / `synth_cache_put` / `vv_dict_hash` /
  `vv_reading`）。**偽物を足したら `test_smoke.py` にも1行足すこと。**
- **エラーの記録が生えたら落とす** … `エラー.log` / `起動エラー.log` / `settings.json.bak`。
  本体は例外を握りつぶしてここに書くので、生えたのに気づけないと
  「例外が出ていたのにテストは通った」が成り立ってしまう。
  承知で踏むテストには `@pytest.mark.writes_error_log` を付ける。
- **打ち間違えたマーカーで止める** … `engine` を打ち間違えると、エンジンの要るテストが
  CI で走ってしまう。知らないマーカーがあれば集める段階で止める
  （`--strict-markers` は conftest から入れられないので自前でやっている）。
- **本体のバグを skip の裏に隠さない** … 画面の作り直しは、Tcl の一時的な不調の
  言い回しに一致したときだけ。それ以外の例外はそのまま投げる。

## fixture

| 名前 | 何をくれるか |
|---|---|
| `app` | 画面（`main.App`）。`@pytest.mark.gui` が必須。終わったら安全に閉じる |
| `pump(秒)` | 画面のイベントを回しながら待つ |
| `engine` | VOICEVOXエンジンのURL。動いていなければヘッドレスで起こす（session） |
| `connected_app` | エンジンにつながった状態の画面 |
| `seed` | テスト用の画像・音（session） |

## マーカー

| 名前 | 意味 |
|---|---|
| `engine` | VOICEVOXエンジンが要る |
| `gui` | 画面（Tk）を作る。CIでは外す |
| `slow` | 数十秒かかる |
| `writes_settings` | 設定を意図的に書き換える（後片づけは土台がやる） |
| `writes_error_log` | エラーの記録が生えるのを承知で踏む（後片づけは土台がやる） |

## Tk をくり返し作るときの注意（実測）

1つのプロセスで画面を作り直すと、Tcl が自分の部品を見失って**たまに**倒れる
（`invalid command name "tcl_findLibrary"` / `Can't find a usable tk.tcl` /
`couldn't read file .../ttk/notebook.tcl`。最後のはファイルが在るのに読めない）。
土台で3つ手を打ってある（`conftest.py`）:

1. `TCL_LIBRARY` / `TK_LIBRARY` を `sys.base_prefix` から求めて渡す
   （確かめるために一度 Tk を作って壊す、をやると次が**必ず**倒れる）
2. 空の窓を1つ、テストが終わるまで開いたままにしておく（`tk_keeper`）。
   最後の窓を閉じると Tcl 側の共通の後始末まで走ってしまうため
3. それでも倒れたら、少し待って作り直す（3回まで）

なので、gui のテストを書くときは自前で `Tk()` を作らず `app` fixture を使うこと。
