# -*- coding: utf-8 -*-
"""第2段の3点の再現テスト。
 (1) %TEMP% の置き土産を起動時に掃除する
 (2) OCR の進捗がページ数の目盛りになる（PDF1冊で 0% のままにならない）
 (3) 保存フェーズが喋り続ける（進捗バーが100%のまま無言にならない）
ユーザーの settings.json / last_text.txt は退避して必ず戻す。"""
import io
import os
import shutil
import tempfile
import threading
import time
import wave

# 私物の絶対パス（本人のPCのユーザー名まで見えてしまう）は、公開のときに困るので外した。
# この台本は tests/_legacy/ にあるので、3つ上がアプリのフォルダ。
APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
keep = {}
for name in ("settings.json", "last_text.txt"):
    p = os.path.join(APP, name)
    if os.path.exists(p):
        keep[p] = p + ".s2bak"
        shutil.copy2(p, keep[p])

ok = []


def check(name, cond):
    if cond:
        ok.append(name)
        print("  OK  " + name)
    else:
        raise AssertionError("NG: " + name)


def tiny_wav(sec=0.05):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * int(24000 * sec))
    return b.getvalue()


try:
    import core
    import main

    # ================= (1) %TEMP% の掃除 =================
    print("=== (1) 異常終了の置き土産を掃除する ===")
    tmp = tempfile.gettempdir()
    old_dir = tempfile.mkdtemp(prefix="t2v_spool_", dir=tmp)
    with io.open(os.path.join(old_dir, "a.wav"), "wb") as f:
        f.write(b"x" * 5000)
    new_dir = tempfile.mkdtemp(prefix="t2v_spool_", dir=tmp)
    fd, old_file = tempfile.mkstemp(prefix="t2v_play_", suffix=".wav", dir=tmp)
    os.close(fd)
    fd, other = tempfile.mkstemp(prefix="zzz_other_", suffix=".wav", dir=tmp)
    os.close(fd)
    two_days = time.time() - 2 * 86400
    for p in (old_dir, old_file, other):
        os.utime(p, (two_days, two_days))

    count, freed = core.sweep_stale_tmp()
    check("1日以上前のフォルダを消す", not os.path.exists(old_dir))
    check("1日以上前のファイルも消す", not os.path.exists(old_file))
    check("**今できたばかりのものは消さない**（動作中の別インスタンス保護）",
          os.path.exists(new_dir))
    check("よそのファイルには触らない", os.path.exists(other))
    check("回収量を数えている（%d件 %dB）" % (count, freed),
          count == 2 and freed >= 5000)
    shutil.rmtree(new_dir, ignore_errors=True)
    os.remove(other)

    # ================= (2) OCR の進捗 =================
    print("\n=== (2) OCR の進捗がページ数になる ===")
    try:
        from PIL import Image
    except ImportError:
        print("  -- Pillow が無いので飛ばす")
        Image = None
    if Image is not None:
        work = tempfile.mkdtemp(prefix="t2v_test_")
        png = os.path.join(work, "page.png")
        Image.new("RGB", (200, 80), "white").save(png)

        marks = []
        orig_run = core.run_ocr

        def fake_run_ocr(paths, lang="ja", strip_labels=True, errors=None,
                         progress_cb=None, cancel_event=None, notices=None):
            # notices: 英語OCRの知らせ用に run_ocr へ足した引数（2026-09-15）
            # 20ページぶんの進捗を演じる（1ファイルでも分母が1にならないことを見る）
            for i in range(1, 21):
                if progress_cb:
                    progress_cb(i, 20)
                time.sleep(0.01)
            return {p: "これはテストのページです。" for p in paths}

        core.run_ocr = fake_run_ocr
        # 残り時間は「5秒を超えるときだけ」出す作り。0.2秒で終わる偽OCRでは
        # 出ないので、1ページ10秒かかったことにして時計を進める
        real_mono = core.time.monotonic
        clock = [real_mono()]

        def fake_mono():
            clock[0] += 10.0
            return clock[0]
        core.time.monotonic = fake_mono
        try:
            core.extract_files(
                [png], pdf_mode="auto", dpi=300,
                progress_cb=lambda d, t, m: marks.append((d, t, m)))
        finally:
            core.run_ocr = orig_run
            core.time.monotonic = real_mono
            shutil.rmtree(work, ignore_errors=True)

        ocr_marks = [m for m in marks if "OCR実行中" in m[2]]
        print("     OCR中の通知 %d件。最後= %r" % (len(ocr_marks),
                                             ocr_marks[-1][2] if ocr_marks else None))
        check("OCR中の分母がページ数（1ではない）",
              bool(ocr_marks) and ocr_marks[-1][1] == 20)
        check("**分子が動く**（0%のまま止まらない）",
              bool(ocr_marks) and ocr_marks[-1][0] == 20)
        check("残り時間の見当が出る",
              any("残り" in m[2] for m in ocr_marks))

    # ================= (3) 保存フェーズ =================
    print("\n=== (3) 保存フェーズが喋り続ける ===")
    app = main.App()
    app.withdraw()
    outdir = tempfile.mkdtemp(prefix="t2v_test_")
    try:
        N = 40
        core.vv_synthesize_cached = lambda base, text, spk, **kw: tiny_wav()
        app._dict_hash_tracker = lambda: (lambda: "x")
        jobs = [("%d行目の文です。" % i, 1, None) for i in range(1, N + 1)]

        seen = []
        orig_put = app.q.put

        def spy(msg):
            if msg and msg[0] in ("progress", "synth_saving", "synth_done"):
                seen.append((time.perf_counter(), msg))
            return orig_put(msg)
        app.q.put = spy
        app._synth_worker(jobs, [list(range(N))], {},
                          os.path.join(outdir, "out.wav"),
                          "combine", 0.4, "wav", True)
        app.q.put = orig_put

        idx_saving = next(i for i, (_t, m) in enumerate(seen)
                          if m[0] == "synth_saving")
        after = [m for _t, m in seen[idx_saving:] if m[0] == "progress"]
        print("     保存フェーズ中の進捗通知: %d件" % len(after))
        for m in after[:6]:
            print("       %.2f / %d  %s" % (m[1], m[2], m[3]))
        check("保存フェーズでも喋る（従来は0件）", len(after) >= 2)
        vals = [m[1] for m in after]
        check("進捗が逆戻りしない", all(b >= a for a, b in zip(vals, vals[1:])))
        check("結合の最中も動く", any("つないで" in m[3] for m in after))
        check("変換中だと分かる", any("変換中" in m[3] for m in after))
        check("字幕の書き出しも伝える", any("字幕" in m[3] for m in after))
        check("最後まで完走してファイルができる",
              os.path.exists(os.path.join(outdir, "out.wav")))
        check("字幕も出ている",
              os.path.exists(os.path.join(outdir, "out.srt")))
    finally:
        shutil.rmtree(outdir, ignore_errors=True)
        app._stop_ticks()
        app.destroy()

    print("\n*** 第2段 %d項目すべて通過 ***" % len(ok))
finally:
    for orig, bak in keep.items():
        shutil.copy2(bak, orig)
        os.remove(bak)
    print("設定と本文を戻した")
