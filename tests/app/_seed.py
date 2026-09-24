# -*- coding: utf-8 -*-
"""テスト用の種データを作る。

本人の私物（スクリーンショットなど）に頼らないための置き換え。
どのPCでも同じものが作れるので、他の人が clone しても同じテストが回ります。
"""
import io
import os
import wave

_FONTS = (r"C:\Windows\Fonts\meiryo.ttc",
          r"C:\Windows\Fonts\YuGothM.ttc",
          r"C:\Windows\Fonts\msgothic.ttc",
          "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
          "/System/Library/Fonts/Hiragino Sans GB.ttc")

JP_LINES = ["吾輩は猫である。名前はまだ無い。",
            "どこで生れたかとんと見当がつかぬ。"]
MIX_LINES = ["iPhoneの設定からWi-Fiをオンにしてください。",
             "Windows 11ではSettingsアプリで言語を追加できます。"]
TECH_LINES = ["GitHubでPull Requestを作り、CIのtestがpassしたらmergeします。",
              "READMEにinstall手順とlicenseを書きます。",
              "PythonのrequestsでJSONをGETします。"]
EN_LINES = ["Your decoy files are NOT being watched right now.",
            "Restart it with the launcher script.",
            "Check the log files for details."]


def tiny_wav(seconds=0.3, rate=24000):
    """無音のWAVのバイト列（再生や結合のテスト用）。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


class Seed:
    """必要になったときに作って、同じものは使い回す。"""

    def __init__(self, directory):
        self.dir = directory
        self._made = {}
        self._font = None

    # ------------------------------------------------ 画像
    def font(self, size=34):
        from PIL import ImageFont
        if self._font is None:
            for path in _FONTS:
                if os.path.exists(path):
                    self._font = path
                    break
            else:                      # 日本語フォントが無い環境（CI等）
                return ImageFont.load_default()
        return ImageFont.truetype(self._font, size)

    def image(self, name, lines, size=34, width=1400, mode="RGB"):
        """文字を描いたPNGを作って、その場所を返す。"""
        from PIL import Image, ImageDraw
        key = (name, mode)
        if key in self._made:
            return self._made[key]
        height = 80 + (size + 26) * len(lines)
        bg = "white" if mode != "RGBA" else (255, 255, 255, 0)
        img = Image.new(mode, (width, height), bg)
        draw = ImageDraw.Draw(img)
        fill = "black" if mode not in ("RGBA",) else (0, 0, 0, 255)
        font = self.font(size)
        for i, line in enumerate(lines):
            draw.text((30, 30 + (size + 26) * i), line, fill=fill, font=font)
        path = os.path.join(self.dir, name)
        img.save(path)
        self._made[key] = path
        return path

    # よく使う4種類（英語の読み取りの判定に使うしきい値は、この4つで測った）
    def japanese(self):
        return self.image("jp.png", JP_LINES)

    def mixed(self):
        return self.image("mix.png", MIX_LINES)

    def technical(self):
        return self.image("tech.png", TECH_LINES)

    def english(self, name="en.png", mode="RGB"):
        return self.image(name, EN_LINES, mode=mode)

    def blank(self):
        from PIL import Image
        path = os.path.join(self.dir, "blank.png")
        if not os.path.exists(path):
            Image.new("RGB", (1200, 400), "white").save(path)
        return path

    def corrupt(self):
        path = os.path.join(self.dir, "corrupt.png")
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + os.urandom(3000))
        return path

    def missing(self):
        return os.path.join(self.dir, "この名前のファイルは無い.png")

    # ------------------------------------------------ 音
    def wav(self, seconds=0.3):
        return tiny_wav(seconds)
