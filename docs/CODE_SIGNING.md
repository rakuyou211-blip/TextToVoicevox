# Code signing policy / コード署名の方針

**Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).**

## What is signed / 署名するもの

Only `TextToVoicevox.exe`, the small Windows launcher built from [`launcher/TextToVoicevox.cs`](../launcher/TextToVoicevox.cs).
It removes the "downloaded from the internet" mark (`Zone.Identifier`) from the files in its own folder and then runs `起動.bat`.
The rest of the app is Python source code that anyone can read in this repository.

署名するのは、Windows 用の小さな起動用 `TextToVoicevox.exe` だけです（ソースは [`launcher/TextToVoicevox.cs`](../launcher/TextToVoicevox.cs)）。
自分のフォルダのファイルから「インターネットから来た」印を外し、`起動.bat` を動かします。
アプリの本体は Python のソースで、このリポジトリで誰でも読めます。

## How it is built / 作り方

- The launcher is built from this repository's source by GitHub Actions ([`.github/workflows/release.yml`](../.github/workflows/release.yml)) on every release. Nothing built on a personal computer is signed.
- The signed launcher is added to `TextToVoicevox_Windows.zip` on the GitHub Release page.
- 起動用 .exe は、Release のたびに GitHub Actions がこのリポジトリのソースから作ります。個人のパソコンで作ったものは署名しません。

## Team roles / 役割

| Role | Members |
|---|---|
| Committers and reviewers | [rakuyou211-blip](https://github.com/rakuyou211-blip) |
| Approvers | [rakuyou211-blip](https://github.com/rakuyou211-blip) |

Every signing request is approved manually by an approver.
署名の依頼は、毎回 approver が手で承認します。

## Privacy / プライバシー

This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it.
It talks only to the VOICEVOX engine running on the same computer (`127.0.0.1`). There is no telemetry.
The first-time setup downloads the Python libraries listed in `requirements.txt` from PyPI.

このアプリは、利用者が求めたとき以外、ほかのコンピュータに情報を送りません。
通信するのは、同じパソコンで動く VOICEVOX エンジン（`127.0.0.1`）だけです。使用状況の収集（テレメトリ）はありません。
初回のセットアップでだけ、`requirements.txt` の部品を PyPI からダウンロードします。
