// TextToVoicevox の起動用 .exe（Windows）
//
// 何をするか:
//   1. このフォルダ（とその下）のファイルから「インターネットから来た」印
//      （Zone.Identifier。ブラウザで落とした zip を展開すると全部に付く）を外す
//   2. いつもの「起動.bat」を動かす（初回は部品のセットアップ、2回目からはすぐ起動）
//
// なぜ .exe にするか:
//   印の付いた .bat は、Windows の SmartScreen やスマート アプリ コントロールに
//   止められる。署名した .exe だけを最初に開いてもらえば、そこで印を外すので、
//   その後に動く .bat や Python のファイルは止められない。
//   この .exe は Release のときに GitHub Actions でソースから作り、
//   SignPath Foundation の証明書で署名する（docs/CODE_SIGNING.md）。
//
// ビルド（.NET Framework 4 に付いてくる C# コンパイラ。追加の道具はいらない）:
//   csc /target:winexe /out:TextToVoicevox.exe /win32icon:app-icon.ico launcher\TextToVoicevox.cs
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

static class Launcher
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool DeleteFileW(string path);

    const string Title = "TextToVoicevox";

    // 印を外さないフォルダ（中身は自分で作ったもの・数が多くて時間がかかる）
    static readonly string[] SkipDirs = { "venv", ".git", "__pycache__" };

    [STAThread]
    static int Main()
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        if (!File.Exists(Path.Combine(dir, "main.py")))
        {
            // zip を開いた中身のまま .exe だけを実行すると、Windows は .exe だけを
            // 一時フォルダへ取り出して動かすので、隣のファイルが見つからない
            MessageBox.Show(
                "同じフォルダにアプリの本体（main.py）が見つかりません。\n\n" +
                "zip を右クリック →「すべて展開」してから、出てきたフォルダの中の\n" +
                "TextToVoicevox.exe を開いてください。",
                Title, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 1;
        }

        Unblock(dir);

        string bat = Path.Combine(dir, "起動.bat");
        if (!File.Exists(bat))
        {
            MessageBox.Show("「起動.bat」が見つかりません。zip をもう一度展開してください。",
                            Title, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 1;
        }
        try
        {
            // 初回はセットアップの進み具合を見せたいので、黒い窓（コンソール）ありで動かす。
            // 2回目からは 起動.bat がすぐ終わるので、窓は一瞬で閉じる
            var psi = new ProcessStartInfo("cmd.exe", "/c \"\"" + bat + "\"\"")
            {
                WorkingDirectory = dir,
                UseShellExecute = false,
            };
            Process.Start(psi);
            return 0;
        }
        catch (Exception e)
        {
            MessageBox.Show("起動できませんでした: " + e.Message, Title,
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    // このフォルダの下のファイルから Zone.Identifier（ダウンロードの印）を外す。
    // 外せなかったファイルがあっても止めない（そのときは .bat 側がいつもどおり案内する）
    static void Unblock(string dir)
    {
        try
        {
            foreach (string f in Directory.GetFiles(dir))
            {
                DeleteFileW(f + ":Zone.Identifier");
            }
            foreach (string d in Directory.GetDirectories(dir))
            {
                string name = Path.GetFileName(d);
                if (Array.IndexOf(SkipDirs, name) >= 0)
                {
                    continue;
                }
                Unblock(d);
            }
        }
        catch (Exception)
        {
            // 読めないフォルダなど。できたところまでで十分
        }
    }
}
