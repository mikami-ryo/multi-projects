# AutoScoreDetector

This folder contains a prototype for detecting the Smash Ultimate results screen and deciding whether the winner is on the left (`P1`) or right (`P2`).

## Current prototype

- Input: one image file
- Output: `left`, `right`, or `unknown`
- Optional: increment `pScore1` or `pScore2` in `../streamcontrol.json`

The current heuristic looks only for the gold `1` near the top-right of each player box. That makes it lightweight and a better fit than full OCR for the first prototype.

## Install

```powershell
python -m pip install -r requirements.txt
```

## Example

Analyze one image without changing scores:

```powershell
python detect_result.py --image "C:\Users\ryo03\OneDrive\ドキュメント\リザルト画面例.jpg"
```

Analyze one image and save a debug overlay:

```powershell
python detect_result.py --image "C:\Users\ryo03\OneDrive\ドキュメント\リザルト画面例.jpg" --debug-output debug\result_debug.jpg
```

Analyze one image and update `streamcontrol.json`:

```powershell
python detect_result.py --image "C:\Users\ryo03\OneDrive\ドキュメント\リザルト画面例.jpg" --streamcontrol ..\streamcontrol.json --write
```

## OBS capture options

Recommended order:

1. Best: use OBS WebSocket and request screenshots from the gameplay source with `GetSourceScreenshot`.
2. Good: use a dedicated game-only OBS scene or source screenshot, not the final composited stream.
3. Fallback: capture a fixed screen region with `mss` or a similar desktop-capture library.

Why this order:

- Reading a single gameplay source is more stable than reading the final stream output with overlays, alerts, or camera frames on top.
- OBS already knows exactly which source is the game feed, so a WebSocket screenshot avoids window-detection problems.
- A fixed region capture is still workable, but tends to break more easily when layouts move.

## Next step after this prototype

To make this live-safe, the next iteration should add:

- a frame provider for OBS or desktop capture
- a cooldown / re-arm state so the same results screen increments only once
- optional logging of recognized wins and score changes
