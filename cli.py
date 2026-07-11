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
    p.add_argument("inputs", nargs="+", help="入力ファイル（PDF/画像/txt/docx/epub）")
    p.add_argument("-o", "--out", required=True, help="出力フォルダ")
    p.add_argument("--wav", action="store_true", help="音声も生成する（要エンジン）")
    p.add_argument("--format", choices=["wav", "m4a", "mp3"], default="wav",
                   help="音声の形式（既定: wav）")
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
                   help="改行で途切れた文を連結（小説向け）")
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

    print(f"[1/3] テキスト抽出中... ({len(args.inputs)}ファイル)")
    raw, warnings = core.extract_files(
        args.inputs, pdf_mode=("ocr" if args.pdf_ocr else "auto"), dpi=args.dpi,
        progress_cb=lambda d, t, m: print(f"  {m}"))
    for w in warnings:
        print(f"  警告: {w}", file=sys.stderr)
    text = core.clean_text(raw, mode=args.mode, join_wrapped=args.join_wrapped)
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
    if args.format != "wav" and args.format not in encoders:
        raise SystemExit(f"{args.format.upper()}への変換ツールがありません"
                         "（Mac: afconvert / それ以外: ffmpeg が必要）。")

    wavs = []
    for i, ln in enumerate(lines):
        wavs.append(core.vv_synthesize_one(args.url, ln, sp[1], **voice))
        print(f"  {i+1}/{len(lines)}")

    if args.combine:
        out_audio = os.path.join(args.out, f"voicevox_output.{args.format}")
        merged = core.concat_wavs(wavs, gap_sec=args.gap)
        core.encode_audio(merged, out_audio, args.format, encoders)
        print(f"保存: {out_audio}")
        if args.srt:
            srt_path = os.path.join(args.out, "voicevox_output.srt")
            durations = [core.wav_duration(w) for w in wavs]
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(core.make_srt(lines, durations, gap_sec=args.gap))
            print(f"保存: {srt_path}")
    else:
        for i, wb in enumerate(wavs):
            fn = os.path.join(args.out, f"{i+1:03d}.{args.format}")
            core.encode_audio(wb, fn, args.format, encoders)
        print(f"保存: {args.out} に {len(wavs)}ファイル")
    print("完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
