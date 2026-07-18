# -*- coding: utf-8 -*-
"""
cli.py - GUIなしの一括変換（自動化・上級者向け）

使い方の例:
  # PDF/画像/テキストからVOICEVOX向けtxtを作る（エンジン不要）
  python cli.py 本.pdf -o 出力フォルダ

  # 音声まで一括生成（VOICEVOXエンジン起動が必要）
  python cli.py 本.pdf -o 出力フォルダ --wav --speaker ずんだもん --combine --srt

`起動.bat` / `起動.command` と同じ venv の python で実行してください。
"""
import os
import sys
import argparse

import core


def build_parser():
    p = argparse.ArgumentParser(
        prog="cli.py",
        description="PDF・画像・テキスト → 整形txt / VOICEVOX音声 の一括変換（GUIなし）")
    p.add_argument("--version", action="version",
                   version=f"TextToVoicevox {core.APP_VERSION}")
    p.add_argument("inputs", nargs="+", help="入力ファイル（PDF/画像/txt/docx/epub）")
    p.add_argument("-o", "--out", required=True, help="出力フォルダ")
    p.add_argument("--wav", action="store_true", help="音声も生成する（要エンジン）")
    p.add_argument("--format", choices=["wav", "m4a", "mp3", "m4b"], default="wav",
                   help="音声の形式（既定: wav。m4b=章付きオーディオブック・全文結合）")
    p.add_argument("--speaker", default="", help="話者名（部分一致可。既定: 最初の話者）")
    p.add_argument("--speed", type=float, default=1.0, help="話速 0.5〜2.0")
    p.add_argument("--pitch", type=float, default=0.0, help="音高 -0.15〜0.15")
    p.add_argument("--intonation", type=float, default=1.0, help="抑揚 0〜2")
    p.add_argument("--volume", type=float, default=1.0, help="音量 0〜2")
    p.add_argument("--combine", action="store_true", help="全文を1つの音声に結合")
    p.add_argument("--gap", type=float, default=0.4, help="結合時の文間無音秒（既定0.4）")
    p.add_argument("--srt", action="store_true", help="結合時にSRT字幕も保存")
    p.add_argument("--mode", choices=["sentence", "keep"], default="sentence",
                   help="整形: sentence=文ごとに改行（既定）/ keep=元の改行維持")
    p.add_argument("--join-wrapped", action="store_true",
                   help="改行で途切れた文を積極的に連結（小説向け）")
    p.add_argument("--smart-join", action=argparse.BooleanOptionalAction, default=False,
                   help="折り返しで途切れた文を連結（1段組みの本文向け。既定OFF）")
    p.add_argument("--paren-ruby", action="store_true",
                   help="「漢字(かんじ)」型ルビを除去（Web小説向け）")
    p.add_argument("--normalize", action="store_true",
                   help="全角英数記号を半角に正規化し、囲み数字・組文字を読みに展開"
                        "（①→1・㈱→株式会社・㎡→平方メートル）")
    p.add_argument("--fix-confusables", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="OCRが取り違えやすい同形文字（力⇄カ・一⇄ー・O⇄0等）を"
                        "前後の文脈で補正（OCR由来テキストのみ。既定ON。"
                        "--no-fix-confusables で無効）")
    p.add_argument("--strip-urls", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="URL・メールアドレスを除去（読み上げると1文字ずつ"
                        "読まれるため。既定ON。--no-strip-urls で無効）")
    p.add_argument("--denoise", action=argparse.BooleanOptionalAction, default=True,
                   help="画面キャプチャの映像内オーバーレイ文字（時刻・局ロゴ・SNSハンドル・"
                        "矢印・英文ブロック、および局ロゴ/番組名/カテゴリ等のラベル）を"
                        "除去（既定ON。--no-denoise で無効）")
    p.add_argument("--pdf-ocr", action="store_true", help="PDFを常にOCRする")
    p.add_argument("--dpi", type=int, default=300, help="OCR解像度（既定300）")
    p.add_argument("--url", default="http://127.0.0.1:50021",
                   help="VOICEVOXエンジンURL（既定 http://127.0.0.1:50021）")
    return p


def pick_speaker(name, speakers):
    if not name:
        return speakers[0]
    sp = core.resolve_speaker(name, speakers)
    if sp is None:
        raise SystemExit(f"話者「{name}」が見つかりません。候補: "
                         + ", ".join(s[0] for s in speakers[:10]) + " ...")
    return sp


def main(argv=None):
    args = build_parser().parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    # M4Bの結合強制は --srt 警告より先に確定させる（m4b+--srtは正しく保存される）
    if args.wav and args.format == "m4b" and not args.combine:
        print("M4B（オーディオブック）は全文を1ファイルに結合します"
              "（--combine を自動適用）。")
        args.combine = True

    if args.srt and not args.combine:
        print("警告: --srt は --combine と併用したときだけ保存されます"
              "（今回は字幕を出力しません）。", file=sys.stderr)

    print(f"[1/3] テキスト抽出中... ({len(args.inputs)}ファイル)")
    raw, warnings = core.extract_files(
        args.inputs, pdf_mode=("ocr" if args.pdf_ocr else "auto"), dpi=args.dpi,
        strip_labels=args.denoise, fix_confusables=args.fix_confusables,
        progress_cb=lambda d, t, m: print(f"  {m}"))
    for w in warnings:
        print(f"  警告: {w}", file=sys.stderr)
    text = core.clean_text(raw, mode=args.mode, join_wrapped=args.join_wrapped,
                           smart_join=args.smart_join,
                           paren_ruby=args.paren_ruby, normalize=args.normalize,
                           denoise=args.denoise, remove_urls=args.strip_urls)
    if not text:
        raise SystemExit("テキストを抽出できませんでした。")

    txt_path = os.path.join(args.out, "voicevox_text.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    lines = [ln for ln in text.split("\n") if ln.strip()]
    print(f"[2/3] 整形txtを保存: {txt_path}（{len(lines)}行）")

    if not args.wav:
        print("[3/3] 完了（--wav 指定なしのため音声は生成しません）")
        return 0

    ver = core.vv_check(args.url)
    if not ver:
        raise SystemExit(f"VOICEVOXエンジンに接続できません: {args.url}\n"
                         "VOICEVOXを起動してから再実行してください。")
    speakers = core.vv_speakers(args.url)
    sp = pick_speaker(args.speaker, speakers)
    print(f"[3/3] 音声生成中... 話者: {sp[0]} / 形式: {args.format.upper()}")

    voice = dict(speed=args.speed, pitch=args.pitch,
                 intonation=args.intonation, volume=args.volume)
    encoders = core.audio_encoders()
    need = "m4a" if args.format in ("m4a", "m4b") else args.format
    if args.format != "wav" and need not in encoders:
        raise SystemExit(f"{args.format.upper()}への変換ツールがありません"
                         "（Mac: afconvert / それ以外: ffmpeg が必要）。")

    # 行頭の「@話者名:」タグをGUIと同様に解釈する（タグ自体は読み上げず、
    # その行だけ指定話者に切り替える）。未解決タグは行全体を既定話者で読む。
    speak_lines, sids = [], []
    for ln in lines:
        if ln.strip().startswith(("#", "＃")):
            continue   # 行頭#はメモ行（GUIと同じく読み上げ対象外）
        name, rest = core.parse_speaker_tag(ln)
        if name is not None:
            m = core.resolve_speaker(name, speakers)
            if m is not None:
                if rest.strip():
                    speak_lines.append(rest)
                    sids.append(m[1])
                continue
        speak_lines.append(ln)
        sids.append(sp[1])

    if not speak_lines:
        raise SystemExit("読み上げ対象の行がありません"
                         "（すべて空行・#メモ行・未解決タグでした）。")

    wavs = []
    for i, (spoken, sid) in enumerate(zip(speak_lines, sids)):
        wavs.append(core.vv_synthesize_one(args.url, spoken, sid, **voice))
        print(f"  {i+1}/{len(speak_lines)}")

    if args.combine:
        out_audio = os.path.join(args.out, f"voicevox_output.{args.format}")
        merged = core.concat_wavs(wavs, gap_sec=args.gap)
        core.encode_audio(merged, out_audio, args.format, encoders)
        print(f"保存: {out_audio}")
        if args.format == "m4b":
            try:
                import mp4chapters
                heads = core.detect_chapters(speak_lines)
                if heads:
                    starts, t = [], 0.0
                    for w in wavs:
                        starts.append(t)
                        t += core.wav_duration(w) + args.gap
                    chs = [(title, starts[i]) for title, i in heads]
                    if chs[0][1] > 0:
                        chs.insert(0, ("冒頭", 0.0))
                    mp4chapters.add_chapters(out_audio, chs)
                    print(f"チャプター{len(chs)}個を埋め込みました")
                else:
                    print("章見出し（第N章等）が無いためチャプターなしで保存しました")
            except Exception as e:
                print(f"警告: チャプター埋め込みに失敗（音声は保存済み）: {e}",
                      file=sys.stderr)
        if args.srt:
            srt_path = os.path.join(args.out, "voicevox_output.srt")
            durations = [core.wav_duration(w) for w in wavs]
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(core.make_srt(speak_lines, durations, gap_sec=args.gap))
            print(f"保存: {srt_path}")
    else:
        for i, wb in enumerate(wavs):
            fn = os.path.join(args.out, f"{i+1:03d}.{args.format}")
            core.encode_audio(wb, fn, args.format, encoders)
        print(f"保存: {args.out} に {len(wavs)}ファイル")
    # 公開時に必要なクレジット表記（VOICEVOX利用規約）
    label_of = {s[1]: s[0] for s in speakers}
    used = []
    for sid in sids:
        lb = label_of.get(sid, "")
        if lb and lb not in used:
            used.append(lb)
    credit = core.voicevox_credit(used)
    if credit:
        print(f"※音声を公開する場合はクレジット表記が必要です: {credit}")
    print("完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
