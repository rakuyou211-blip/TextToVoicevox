# Code signing policy / コード署名の方針

**Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).**

## What is signed / 署名するもの

Only `TextToVoicevox.exe`, the small Windows launcher built from [`launcher/TextToVoicevox.cs`](../launcher/TextToVoicevox.cs).
It removes the "downloaded from the internet" mark (`Zone.Identifier`) from the files in its own folder and then opens `起動.pyw`. (`起動.pyw` already does this today, without a certificate; the signed `.exe` is the tidier version of the same idea.)
The rest of the app is Python source code that anyone can read in this repository.
The file's properties name the product (`TextToVoicevox`) and carry the app's version (`APP_VERSION` in [`core.py`](../core.py)).

署名するのは、Windows 用の小さな起動用 `TextToVoicevox.exe` だけです（ソースは [`launcher/TextToVoicevox.cs`](../launcher/TextToVoicevox.cs)）。
自分のフォルダのファイルから「インターネットから来た」印を外し、`起動.pyw` を開きます。
アプリの本体は Python のソースで、このリポジトリで誰でも読めます。
ファイルのプロパティには、製品名（`TextToVoicevox`）とアプリの版（[`core.py`](../core.py) の `APP_VERSION`）が入ります。

## How it is built / 作り方

- The launcher is built from this repository's source by GitHub Actions ([`.github/workflows/release.yml`](../.github/workflows/release.yml)) on GitHub-hosted runners, on every release. Nothing built on a personal computer is signed.
- Once signing is switched on, the signed launcher is added to `TextToVoicevox_Windows.zip` on the GitHub Release page. **The SignPath application is still pending, so no release ships that `.exe` yet.** Until it does, the zip ships [`起動.pyw`](../起動.pyw) instead: a small Python entry point that does the same job — strip the mark, then set up and start the app (since v1.24.0 the zip has no `.bat` at all) — and that Smart App Control does not block, because `.py` / `.pyw` are outside the extensions it stops. The `.exe` is not urgent because of it.
- 起動用 .exe は、Release のたびに GitHub Actions（GitHub が用意するマシン）がこのリポジトリのソースから作ります。個人のパソコンで作ったものは署名しません。
- **SignPath はまだ申請中で、署名も同梱も始まっていません。** そのあいだは、同じ仕事をする [`起動.pyw`](../起動.pyw) を zip に入れています（印を外してから、セットアップと起動を Python だけでやる。v1.24.0 から zip に `.bat` はありません）。スマート アプリ コントロールは `.py` / `.pyw` を止めないので、署名がなくても行き止まりになりません。

## Changes to the system and uninstalling / システムへの変更とアンインストール

- The launcher changes nothing outside its own folder. To remove the app installed from the zip, delete the extracted folder.
- The one-line installer ([`install.ps1`](../install.ps1); not signed, read by PowerShell from this repository) puts the app in `%LOCALAPPDATA%\TextToVoicevox`, creates a TextToVoicevox icon on the desktop and in the Start menu, and lists the app in Windows **Settings → Apps**. It uses no administrator rights. Uninstall it from **Settings → Apps**, or with the line below ([`uninstall.ps1`](../uninstall.ps1)); either way the folder, the two icons and the Settings entry are removed.
- 起動用 .exe は、自分のフォルダの外を何も変えません。zip から入れたアプリは、展開したフォルダを消せば消えます。
- 1行導入（[`install.ps1`](../install.ps1)。署名はなく、PowerShell がこのリポジトリから読みます）は、アプリを `%LOCALAPPDATA%\TextToVoicevox` に置き、デスクトップとスタートメニューにアイコンを作り、Windows の「**設定 → アプリ**」に載せます。管理者権限は使いません。消すときは「設定 → アプリ」から、または次の1行で（[`uninstall.ps1`](../uninstall.ps1)）。どちらも、フォルダと2つのアイコンと「設定 → アプリ」の登録を消します。

```powershell
irm https://raw.githubusercontent.com/rakuyou211-blip/TextToVoicevox/main/uninstall.ps1 | iex
```

## Team roles / 役割

| Role | Members |
|---|---|
| Committers and reviewers | [rakuyou211-blip](https://github.com/rakuyou211-blip) |
| Approvers | [rakuyou211-blip](https://github.com/rakuyou211-blip) |

Every signing request is approved manually by an approver.
署名の依頼は、毎回 approver が手で承認します。

## Privacy / プライバシー

This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it.
There is no telemetry, and the text you read never leaves your computer. The connections it makes are:

- **The VOICEVOX engine on the same computer** (`127.0.0.1`), to make the voices.
- **A check for a new version**, at most once a day when the app opens: it asks GitHub (`api.github.com`) for the latest release, sending only the app's name and version. The one-line installers say so when they finish, and so does the app next to the switch: turn it off with 新しい版が出たら知らせる (tell me when a new version is out) under 接続設定… (connection settings).
- **Installing and the first-time setup**: the app, its Python libraries and its text-recognition parts are downloaded from GitHub, PyPI and python.org.
- **Only when you click them**: 感想を送る (send feedback) opens a GitHub issue or a Google Forms page in your browser; 更新する (update) downloads the new version; links to the VOICEVOX site open in your browser.

このアプリは、利用者が求めたとき以外、ほかのコンピュータに情報を送りません。
使用状況の収集（テレメトリ）はなく、読み上げる文章がパソコンの外に出ることもありません。通信するのは次のときだけです。

- **同じパソコンの VOICEVOX エンジン**（`127.0.0.1`）。声を作るため。
- **新しい版の確認**：アプリを開いたとき、1日1回まで GitHub（`api.github.com`）に最新の版を聞きます。送るのはアプリの名前と版だけです。1行導入の終わりと、アプリの切り替えの横で知らせ、「接続設定…」の「新しい版が出たら知らせる」で切れます。
- **インストールと初回のセットアップ**：アプリと Python の部品と文字を読む部品を、GitHub・PyPI・python.org からダウンロードします。
- **押したときだけ**：「感想を送る」はブラウザで GitHub の Issue か Google フォームを開きます。「更新する」は新しい版をダウンロードします。VOICEVOX のサイトへのリンクはブラウザで開きます。
