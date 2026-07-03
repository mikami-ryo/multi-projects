# StreamControl-for-UMBR

## LISCENSE

### StreamControl.exe

Original work Copyright (c) 2012-2015, Tan Yu Sheng
Modified work Copyright (c) 2019, Miguel Müller
All rights reserved.

<https://github.com/farpenoodle/StreamControl>

## AutoScoreDetector

`AutoScoreDetector/` contains an experimental result-screen detector for Smash Ultimate.

- The current prototype analyzes a still image and decides whether the winner is on the left (P1) or right (P2).
- It can optionally increment `streamcontrol.json` by one win for the detected side.
- Manual score adjustment in StreamControl remains available; this tool only updates the same JSON file when explicitly asked to.
