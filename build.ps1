$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }

& $python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name TournamentControl `
  --add-data "score_board\fonts;score_board\fonts" `
  --add-data "score_board\GoSquared;score_board\GoSquared" `
  --add-data "score_board\imgs;score_board\imgs" `
  --add-data "score_board\js;score_board\js" `
  --add-data "score_board\themes;score_board\themes" `
  --add-data "score_board\AutoScoreDetector\templates;score_board\AutoScoreDetector\templates" `
  --add-data "score_board\AutoScoreDetector\config.json;score_board\AutoScoreDetector" `
  --add-data "score_board\AutoScoreDetector\obs_monitor_config.json;score_board\AutoScoreDetector" `
  --add-data "score_board\l3rd1.html;score_board" `
  --add-data "score_board\l3rd1.xml;score_board" `
  --add-data "score_board\l3rd2.html;score_board" `
  --add-data "score_board\l3rd2.xml;score_board" `
  --add-data "score_board\scoreboard.css;score_board" `
  --add-data "score_board\scoreboard.html;score_board" `
  --add-data "score_board\scoreboard.js;score_board" `
  --add-data "score_board\tournament-scoreboard.css;score_board" `
  --add-data "score_board\tournament-scoreboard.html;score_board" `
  --add-data "score_board\tournament-scoreboard.js;score_board" `
  --add-data "score_board\scoreboard.xml;score_board" `
  --add-data "score_board\streamcontrol.json;score_board" `
  --add-data "score_board\streamcontrol.xml;score_board" `
  run_tournament_control.py

Write-Host "Built: $projectRoot\dist\TournamentControl\TournamentControl.exe"
