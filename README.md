# Tournament Control

大会配信用の以下の機能を、1つのWindowsアプリへ統合します。

- start.gg管理（埋め込みWeb画面＋公式APIの試合一覧）
- StreamControl互換のScoreboard管理
- OBS WebSocketを使ったAuto Score Detector
- Xの無料投稿画面を使う試合開始告知

## Windows版をすぐ使う

[GitHub Releases](https://github.com/hororo223606/multi-projects/releases)から
`TournamentControl-windows-v8.zip`をダウンロードして展開し、フォルダ内の
`TournamentControl.exe`を起動します。`_internal`フォルダも実行に必要なため、
EXEだけを別の場所へ移動しないでください。

## 開発環境での起動

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_tournament_control.py
```

設定画面でstart.gg APIトークンを登録します。トークンはWindows DPAPIで現在のWindowsユーザーに紐づけて暗号化されます。

## OBS Browser Source

アプリ起動中は次のURLで既存Scoreboardを配信します。

```text
http://127.0.0.1:8765/scoreboard.html
```

ポートは設定画面から変更できます。

## OBS画面トランジション

VSまたはGAME SETの検知後に、同梱の`Aya_transition.mp4`をOBSで再生し、
動画が画面を覆っている間にScoreboardデザインとOBS配置を切り替えられます。

```text
http://127.0.0.1:8765/transition.html
```

1. OBSへ上記URLのBrowser Sourceを追加します。
2. ソース名を初期値の`Aya Transition`にするか、アプリ側の設定と一致させます。
3. Browser Sourceを配信キャンバスと同じサイズ・全画面・最前面に配置します。
4. Tournament Controlの「OBS・自動化」→「画面トランジション」で有効にします。
5. 「Browser Sourceで再生テスト」でOBS上の表示を確認してから映像監視を開始します。

初期設定では再生開始から0.8秒後に配置とデザインを切り替え、1.6秒後に
動画を透明に戻します。待機中のページは常に透明で、Browser Sourceのサイズに
動画が自動追従します。VSとGAME SETは個別に有効・無効を選択できます。
Browser Sourceから応答がない場合も、従来の配置・デザイン切り替えは継続します。

Scoreboardの編集可能なファイルは初回起動時に
`%APPDATA%\TournamentControl\scoreboard`へ作成されます。将来のテーマや
Auto Monitor連携に使う拡張境界については[拡張ガイド](docs/EXTENSIONS.md)を参照してください。

## Windowsビルド

```powershell
python -m pip install -r requirements-dev.txt
.\build.ps1
```

成果物は `dist/TournamentControl/TournamentControl.exe` です。OpenCVとQt WebEngineを含むため、単一ファイルではなく安定性の高いフォルダ形式で配布します。
