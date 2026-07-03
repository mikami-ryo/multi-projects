# AutoScoreDetector

This folder contains an experimental results-screen detector for Smash Ultimate.

## What it does

- `detect_result.py`
  Analyzes one image and returns `left`, `right`, or `unknown`.
- `obs_monitor.py`
  Connects to OBS through `obs-websocket`, pulls screenshots from a configured source, detects the winner side, and can increment `streamcontrol.json`.

The current heuristic looks for the gold `1` near the top-right of each player box. That is much simpler and more stable for this use case than trying to OCR the whole screen.

## Install

```powershell
python -m pip install -r requirements.txt
```

## One-image test

Analyze one image without changing scores:

```powershell
python detect_result.py --image ".\sample_result.jpg"
```

Analyze one image and save a debug overlay:

```powershell
python detect_result.py --image ".\sample_result.jpg" --debug-output debug\result_debug.jpg
```

Analyze one image and update `streamcontrol.json`:

```powershell
python detect_result.py --image ".\sample_result.jpg" --streamcontrol ..\streamcontrol.json --write
```

## OBS monitor

1. Enable `obs-websocket` in OBS.
2. Open `obs_monitor_config.json`.
3. Set `obs.source_name` to the gameplay source you want to read.
4. For the safest first run, set `monitor.write_scores` to `false`.
5. Start the monitor:

```powershell
python obs_monitor.py
```

When running:

- it requests screenshots from OBS with `GetSourceScreenshot`
- it analyzes the current frame
- it requires the same winner to appear for multiple frames before acting
- it rearms only after the result screen disappears for several frames

Useful outputs while testing:

- `debug/latest_obs_debug.jpg`
- `state.json`

## OBS capture recommendation

Recommended order:

1. Best: use `obs-websocket` and read the gameplay source directly with `GetSourceScreenshot`.
2. Good: read a dedicated game-only scene or source, not the final composited stream.
3. Fallback: capture a fixed desktop region with a screen-capture library.

Why:

- A single gameplay source is more stable than the final stream layout.
- Alerts, cameras, borders, and overlays on the final stream can break detection.
- OBS already knows which source is the game feed, so using OBS avoids window-finding hacks.

## Notes

- OBS Studio 28+ includes `obs-websocket` by default.
- The current detector still uses a simple gold-`1` heuristic, so `config.json` may need ROI tuning if your crop or layout changes.
- Manual score adjustment remains possible because this tool only updates the same `streamcontrol.json` fields that the existing scoreboard already reads.
