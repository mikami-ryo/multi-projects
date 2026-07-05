# AutoScoreDetector

スマブラSP配信向けの、自動スコア反映用ツールです。

## できること

- `detect_result.py`
  1枚の画像を見て、次の3つを別々に判定します。
  - `GAME SET` 画面か
  - `results` 画面か
  - 勝者が左側か右側か
- `obs_monitor.py`
  OBS の `obs-websocket` 経由で指定ソースのスクリーンショットを定期取得し、以下の順で確認できたときだけスコアを加算します。
  1. `GAME SET` を検出
  2. `results` を検出
  3. 同じ勝者側を連続で検出

配信中は常に試合画面が流れているため、勝者判定だけで即加算せず、この流れで安全側に寄せています。

## インストール

```powershell
python -m pip install -r requirements.txt
```

## まずは1枚画像で確認

判定だけ行い、スコアは変更しません。

```powershell
python detect_result.py --image ".\sample_result.jpg"
```

デバッグ用の重ね書き画像を保存する場合:

```powershell
python detect_result.py --image ".\sample_result.jpg" --debug-output ".\debug\result_debug.jpg"
```

判定成功時に `streamcontrol.json` を更新する場合:

```powershell
python detect_result.py --image ".\sample_result.jpg" --streamcontrol "..\streamcontrol.json" --write
```

`--write` は、`results` 画面かつ勝者が確定したときだけ実行されます。

## OBS 連携の手順

1. OBS Studio を起動します。
2. `obs-websocket` を有効にします。
3. `obs_monitor_config.json` を開きます。
4. `obs.source_name` を、読み取りたいゲーム映像ソース名に合わせます。
5. 必要なら `obs.password` を OBS 側の設定に合わせます。
6. 初回テストでは `monitor.write_scores` を `false` にして、誤判定がないか先に確認するのがおすすめです。
7. 実行します。

```powershell
python obs_monitor.py
```

## 監視中の状態遷移

監視中は、内部状態が次のように動きます。

1. `in_match`
2. `awaiting_results`
3. `cooldown`
4. 終了画面が消えたら再び `in_match`

この状態遷移により、試合中や画面切り替え中の誤加算を防ぎます。

## 設定ファイル

### `config.json`

画像判定そのものの設定です。

- `GAME SET` の文字判定用 ROI
- `results` の順位数字を見る ROI
- 金色、白色、暗部の閾値

レイアウトやクロップ位置が違う場合は、まずこのファイルの ROI を調整してください。

### `obs_monitor_config.json`

監視動作の設定です。

- OBS 接続先
- 監視対象ソース名
- 取得間隔
- 連続何フレームで確定させるか
- `write_scores` を有効にするか

## テスト時に見る場所

必要に応じて、以下を有効にして確認できます。

- `debug/latest_obs_debug.jpg`
- `state.json`

どちらも `obs_monitor_config.json` の `save_debug_image` / `save_state_json` でオンオフできます。

## OBS 取り込みのおすすめ

おすすめ順は次の通りです。

1. OBS のゲームソースを `obs-websocket` で直接読む
2. 最終配信画面ではなく、ゲームだけの専用シーンや専用ソースを読む
3. どうしても難しい場合のみ、画面全体や固定領域をキャプチャする

理由:

- ゲーム単体ソースの方がレイアウト変化に強いです。
- カメラ、枠、テロップ、通知が少ないほど判定が安定します。
- OBS から直接読むと、別アプリでの画面探索が不要です。

## 注意

- OBS Studio 28 以降では `obs-websocket` が標準搭載です。
- 現在の判定はルールベースなので、配信レイアウトに応じてしきい値調整が必要な場合があります。
- 手動でのスコア調整はこれまで通り可能です。このツールは既存の `streamcontrol.json` を同じ形式で更新しているだけです。
