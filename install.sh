#!/bin/bash
# TextToVoicevox かんたん導入（Mac）
#
# 使い方: 「ターミナル」を開いて、次の1行を貼り付けて return。
#   curl -fsSL https://raw.githubusercontent.com/rakuyou211-blip/TextToVoicevox/main/install.sh | bash
#
# なぜこの方法か:
#   ブラウザで落とした zip には「インターネットから来た」という印（検疫フラグ）が付き、
#   そこから出した 起動.command を開くと Mac が「開けません」で止める。
#   この1行は curl がファイルを取ってくるので印が付かず、
#   できた「TextToVoicevox」アプリは、警告なしで開ける。
#
# やること（ぜんぶ自分のユーザーの中だけ。管理者権限・パスワードは使わない）:
#   1. Python を探す（無ければ、許可をもらって自分用の Python を入れる）
#   2. 最新版を ~/Library/Application Support/TextToVoicevox に置く
#      （上書き更新。設定・辞書・本文の自動保存・立ち絵はそのまま残る）
#   3. 必要な部品を入れる（初回だけネット接続が必要。以降はオフラインで動く）
#   4. ~/Applications に「TextToVoicevox」アプリを作って、起動する
#      （Launchpad と Spotlight から開ける）
#
# やめたいとき（アンインストール）:
#   ~/Library/Application Support/TextToVoicevox フォルダと、
#   ~/Applications/TextToVoicevox.app をゴミ箱へ。
#   ここで Python を入れた場合は ~/.local/python3.12 も消してかまわない。
#
# 上級者・テスト用の環境変数:
#   T2V_REF=ブランチ名   … 最新版の代わりに、そのブランチを入れる
#   T2V_SRC=フォルダ     … ネットから取らず、手元のフォルダを入れる（CI用）
#   T2V_DIR=フォルダ     … 入れる場所を変える
#   T2V_APPS=フォルダ    … アプリを作る場所を変える（既定 ~/Applications）
#   T2V_PY_DIR=フォルダ  … 自分用 Python を入れる場所（既定 ~/.local/python3.12）
#   T2V_OWN_PYTHON=1     … 見つかった Python を使わず、自分用 Python を入れる（CI用）
#   T2V_NO_LAUNCH=1      … 入れ終わっても起動しない
#   T2V_YES=1            … 確認を全部「はい」で進める
#
# ※ curl ... | bash で使う前提のファイル。全体を関数に包んで最後の行で呼ぶので、
#    ダウンロードが途中で切れても、書きかけの行が実行されることはない。

t2v_install() {
    set -u
    local REPO='rakuyou211-blip/TextToVoicevox'
    local APP_NAME='TextToVoicevox'
    local DEST="${T2V_DIR:-$HOME/Library/Application Support/$APP_NAME}"
    local APPS="${T2V_APPS:-$HOME/Applications}"
    local APP="$APPS/$APP_NAME.app"
    local PY_HOME="${T2V_PY_DIR:-$HOME/.local/python3.12}"
    # 自分用 Python（python-build-standalone。uv などが使っている、そのまま動く公式ビルドの詰め合わせ）
    local PBS_TAG='20241016'
    local PBS_VER='3.12.7'

    say() { printf '%s\n' "$*"; }
    step() { printf '\n\033[36m== %s\033[0m\n' "$*"; }
    fail() {
        printf '\n\033[33m  [!] うまくいきませんでした: %s\033[0m\n' "$*"
        say '      ネット接続を確かめて、もう一度この1行を貼り付けてみてください。'
        say '      直らないときは、この画面の文字をそのまま作者に送ってください:'
        say "      https://github.com/$REPO/issues"
        return 1
    }
    ask() {
        [ -n "${T2V_YES:-}" ] && return 0
        local a=''
        # 本文は curl から流れてくるので、答えは画面（/dev/tty）から読む
        printf '%s [Y/n] ' "$1"
        read -r a 2>/dev/null </dev/tty || a='n'
        case "$a" in ''|y|Y|yes|YES|はい|ｙ) return 0 ;; *) return 1 ;; esac
    }
    # Python 3.9 以降で、画面表示の部品（Tk 8.6 以降）が入っているか。
    # Apple の開発ツール付属の Python は Tk 8.5 で、画面が真っ白になるので弾く
    py_ok() {
        [ -x "$1" ] || return 1
        "$1" -c 'import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 9) and tkinter.TkVersion >= 8.6 else 1)' \
            >/dev/null 2>&1
    }
    find_python() {
        local c
        [ -n "${T2V_OWN_PYTHON:-}" ] && { py_ok "$PY_HOME/bin/python3" && echo "$PY_HOME/bin/python3"; return; }
        for c in "$PY_HOME/bin/python3" \
                 /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
                 /Library/Frameworks/Python.framework/Versions/3.9/bin/python3 \
                 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
            py_ok "$c" && { echo "$c"; return; }
        done
        # PATH 上の python3。/usr/bin/python3 は開発ツールが無いと「インストールしますか」の
        # 窓を出すだけなので、開発ツールがあるときだけ試す
        c="$(command -v python3 2>/dev/null || true)"
        if [ -n "$c" ] && { [ "$c" != /usr/bin/python3 ] || xcode-select -p >/dev/null 2>&1; }; then
            py_ok "$c" && { echo "$c"; return; }
        fi
    }
    install_own_python() {
        local arch url tmp
        case "$(uname -m)" in
            arm64) arch='aarch64' ;;
            x86_64) arch='x86_64' ;;
            *) say "  （この Mac の種類 $(uname -m) には対応していません）"; return 1 ;;
        esac
        url="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/cpython-$PBS_VER+$PBS_TAG-$arch-apple-darwin-install_only.tar.gz"
        tmp="$(mktemp -d)" || return 1
        say "  ダウンロード中… $url"
        if ! curl -fsSL --retry 3 "$url" -o "$tmp/py.tar.gz" || ! tar -xzf "$tmp/py.tar.gz" -C "$tmp"; then
            rm -rf "$tmp"; return 1
        fi
        mkdir -p "$(dirname "$PY_HOME")" && rm -rf "$PY_HOME" && mv "$tmp/python" "$PY_HOME"
        rm -rf "$tmp"
        py_ok "$PY_HOME/bin/python3"
    }
    running_app() {
        # この場所に入れた TextToVoicevox が起動中か（更新中に部品を入れ替えると失敗するため）
        ps -axo command= 2>/dev/null | grep -F "$DEST/main.py" | grep -v grep >/dev/null
    }
    source_zip_url() {
        if [ -n "${T2V_REF:-}" ]; then
            echo "https://github.com/$REPO/archive/refs/heads/$T2V_REF.zip"; return
        fi
        local u
        u="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null | "$PY" -c '
import json, sys
rel = json.load(sys.stdin)
for a in rel.get("assets", []):
    if a.get("name", "").endswith(".zip"):
        print(a["browser_download_url"])
        break
' 2>/dev/null || true)"
        if [ -n "$u" ]; then echo "$u"; return; fi
        say '  （最新版の情報を取れなかったので、main ブランチを使います）' >&2
        echo "https://github.com/$REPO/archive/refs/heads/main.zip"
    }

    if [ "$(uname -s)" != Darwin ]; then
        say '  これは Mac 用です。Windows では PowerShell で install.ps1 の1行を使ってください。'
        return 1
    fi

    printf '\n\033[32m  TextToVoicevox をこの Mac に入れます\033[0m\n'
    say "  入れる場所: $DEST"

    # ---- 1. Python ----
    step '1/4 Python を探しています'
    local PY
    PY="$(find_python)"
    if [ -z "$PY" ]; then
        say '  使える Python（3.9以降・画面表示つき）が見つかりませんでした。'
        if ask '  このアプリ専用の Python 3.12 を入れますか？（無料・約20MB・パスワード不要）'; then
            if install_own_python; then PY="$PY_HOME/bin/python3"; fi
        fi
        if [ -z "$PY" ]; then
            say ''
            say '  [!] Python を入れてから、もう一度この1行を貼り付けてください。'
            say '      https://www.python.org/downloads/ （3.9以降・macOS 64-bit universal2 installer）'
            [ -z "${T2V_NO_LAUNCH:-}" ] && open 'https://www.python.org/downloads/' 2>/dev/null
            return 1
        fi
    fi
    say "  使う Python: $PY"

    # ---- 2. 本体を置く ----
    step '2/4 アプリ本体を置いています'
    if running_app; then
        # 開いたまま部品を入れ替えると失敗したり、書きかけの本文を失ったりする
        say '  TextToVoicevox が起動中です。新しい版に入れ替えるので、アプリを閉じてください。'
        if [ -z "${T2V_YES:-}" ]; then
            printf '  閉じたら return を押してください'
            read -r _ 2>/dev/null </dev/tty || true
        fi
        if running_app; then
            say ''
            say '  [!] まだ起動中なので、入れ替えをやめました（何も変えていません）。'
            say '      アプリを閉じてから、もう一度この1行を貼り付けてください。'
            return 1
        fi
    fi
    local tmp src url
    tmp="$(mktemp -d)" || { fail '作業用フォルダを作れませんでした。'; return 1; }
    if [ -n "${T2V_SRC:-}" ]; then
        src="$T2V_SRC"
        say "  手元のフォルダから: $src"
    else
        url="$(source_zip_url)"
        say "  ダウンロード中… $url"
        if ! curl -fsSL --retry 3 "$url" -o "$tmp/app.zip"; then
            rm -rf "$tmp"; fail 'ダウンロードできませんでした。'; return 1
        fi
        # ditto は Finder と同じ展開をする（日本語のファイル名も崩れない）
        if ! ditto -x -k "$tmp/app.zip" "$tmp/x"; then
            rm -rf "$tmp"; fail 'ダウンロードした zip を開けませんでした。'; return 1
        fi
        src="$(find "$tmp/x" -maxdepth 3 -name main.py -print -quit)"
        src="${src%/main.py}"
        if [ -z "$src" ]; then rm -rf "$tmp"; fail 'ダウンロードした中に main.py がありません。'; return 1; fi
    fi
    mkdir -p "$DEST" || { rm -rf "$tmp"; fail "$DEST を作れませんでした。"; return 1; }
    # 上書きコピー（消しはしない）。設定・辞書・自動保存・立ち絵・venv_mac は残る
    if ! rsync -a --exclude .git --exclude venv --exclude venv_mac --exclude __pycache__ \
            --exclude .pytest_cache "$src/" "$DEST/"; then
        rm -rf "$tmp"; fail 'ファイルのコピーに失敗しました。'; return 1
    fi
    rm -rf "$tmp"
    [ -f "$DEST/main.py" ] || { fail '本体を置けませんでした。'; return 1; }
    # 念のため、検疫フラグと実行権限を整える（zip から入れた人と同じ状態にしておく）
    xattr -dr com.apple.quarantine "$DEST" 2>/dev/null
    chmod +x "$DEST"/*.command "$DEST"/*.sh 2>/dev/null

    # ---- 3. 部品 ----
    step '3/4 必要な部品を入れています（初回は数分かかります）'
    # 名前は zip 版の 起動.command と同じ venv_mac（どちらから起動しても同じ部品を使う）
    local VENV="$DEST/venv_mac"
    local VPY="$VENV/bin/python"
    if ! py_ok "$VPY"; then
        # 無い・壊れている・別の Mac から持ってきた venv は作り直す
        rm -rf "$VENV"
        "$PY" -m venv "$VENV" || { fail '仮想環境（venv）の作成に失敗しました。'; return 1; }
    fi
    "$VPY" -m pip install --disable-pip-version-check --progress-bar on -r "$DEST/requirements.txt" \
        || { fail '部品のインストールに失敗しました。'; return 1; }

    # ---- 4. アプリ ----
    step '4/4 アプリ（TextToVoicevox.app）を作っています'
    # Mac 標準の AppleScript でアプリを作り、その中から Python を起動する。
    # 起動スクリプトから Python に切り替える作りだと、Mac は「python3.12 が画面を撮っている」
    # と見なし、「画面収録」の許可の一覧に python3.12 と出てしまう。この作りなら
    # Python はアプリの子として動くので、許可の一覧にも「TextToVoicevox」と出る。
    local tmpdir="$APPS/.t2v_app_new"
    local tmpapp="$tmpdir/$APP_NAME.app"
    local esc_dest
    esc_dest="$(printf '%s' "$DEST" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    rm -rf "$tmpdir"
    if mkdir -p "$tmpdir" && osacompile -o "$tmpapp" >/dev/null 2>&1 <<APPLESCRIPT
-- TextToVoicevox の起動（install.sh が作成）
on run
	set appDir to "$esc_dest"
	set py to appDir & "/venv_mac/bin/python"
	try
		do shell script "test -x " & quoted form of py
	on error
		activate
		display alert "TextToVoicevox を起動できませんでした" message "部品が見つかりません。ターミナルで、入れたときの1行をもう一度貼り付けてください。" as critical
		return
	end try
	try
		do shell script "cd " & quoted form of appDir & " && " & quoted form of py & " " & quoted form of (appDir & "/main.py") & " >/dev/null 2>&1"
	on error
		activate
		display alert "TextToVoicevox が途中で終了しました" message "直らないときは、ターミナルで、入れたときの1行をもう一度貼り付けてください。くわしい記録はアプリのフォルダの「起動エラー.log」「エラー.log」にあります。" as warning
	end try
end run
APPLESCRIPT
    then
        local plist="$tmpapp/Contents/Info.plist"
        plutil -replace CFBundleIdentifier -string 'io.github.rakuyou211-blip.texttovoicevox' "$plist"
        # 起動役のアプリは Dock に出さない（Dock には本体の窓のアイコンだけが出る）
        plutil -replace LSUIElement -bool YES "$plist"
        plutil -replace NSHighResolutionCapable -bool YES "$plist"
        plutil -replace NSScreenCaptureUsageDescription -string '「画面から読む」で、囲んだ所の文字を読み取るために使います。' "$plist"
        # アイコン（作れなくても起動はできる）。sips と iconutil は Mac に最初から入っている
        local iconset="$tmpdir/AppIcon.iconset" s
        if mkdir -p "$iconset" 2>/dev/null; then
            for s in 16 32 128 256; do
                sips -z "$s" "$s" "$DEST/assets/app-icon.png" --out "$iconset/icon_${s}x${s}.png" >/dev/null 2>&1
                sips -z $((s * 2)) $((s * 2)) "$DEST/assets/app-icon.png" --out "$iconset/icon_${s}x${s}@2x.png" >/dev/null 2>&1
            done
            if iconutil -c icns "$iconset" -o "$tmpapp/Contents/Resources/applet.icns" >/dev/null 2>&1; then
                # 新しい macOS の osacompile は Assets.car の絵を優先するので、そちらを外す
                rm -f "$tmpapp/Contents/Resources/Assets.car"
                plutil -remove CFBundleIconName "$plist" >/dev/null 2>&1
            fi
        fi
        # 中身を書き換えたので、手元で署名し直す（アドホック署名）
        codesign --force --deep -s - "$tmpapp" >/dev/null 2>&1
        rm -rf "$APP" && mv "$tmpapp" "$APP"
        rm -rf "$tmpdir"
        touch "$APP"   # Finder・Launchpad にアイコンを読み直させる
        say "  作成: $APP"
    else
        rm -rf "$tmpdir"
        say '  （アプリを作れませんでした。本体は入っています）'
        say "  起動するときは、次のフォルダの「起動.command」をダブルクリックしてください:"
        say "    $DEST"
        APP=''
    fi

    # ---- VOICEVOX（声を作る無料ソフト）が入っているか ----
    # アプリと同じ探し方（core.find_voicevox）で確かめる
    local vv
    vv="$("$VPY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import core; print(core.find_voicevox() or "")' \
        "$DEST" 2>/dev/null | tail -n 1)"

    printf '\n\033[32m  できました！\033[0m\n'
    say '  これからは、Launchpad か Spotlight（⌘+スペース）で「TextToVoicevox」と打てば起動できます。'
    say '  Dock に置くには、起動中の Dock のアイコンを右クリック →「オプション」→「Dock に追加」。'
    say '  新しい版にしたいときも、同じ1行をもう一度貼り付けるだけです（設定や辞書は残ります）。'
    say '  「📷 画面から読む」を初めて使うときだけ、Mac が「画面収録」の許可を聞いてきます。'
    if [ -n "$vv" ]; then
        say "  VOICEVOX: 見つかりました（${vv}）。アプリの「VOICEVOX起動」からつなげます。"
    else
        printf '\n\033[33m  ※ VOICEVOX（読み上げの声を作る無料ソフト）が、この Mac に見つかりませんでした。\033[0m\n'
        say '     文字を取り出すことはできますが、声にするには VOICEVOX が必要です。'
        say '     https://voicevox.hiroshiba.jp/ から入れてください（無料）。'
        if [ -z "${T2V_NO_LAUNCH:-}" ] && ask '  VOICEVOX のダウンロードページを開きますか？'; then
            open 'https://voicevox.hiroshiba.jp/' 2>/dev/null
        fi
    fi

    if [ -z "${T2V_NO_LAUNCH:-}" ]; then
        if [ -n "$APP" ]; then
            open "$APP"
        else
            (cd "$DEST" && nohup "$VPY" main.py >/dev/null 2>&1 &)
        fi
    fi
    return 0
}

t2v_install "$@"
