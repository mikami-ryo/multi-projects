param(
  [string]$DistPath = ""
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
$distributionRoot = if ($DistPath) {
  [System.IO.Path]::GetFullPath((Join-Path $projectRoot $DistPath))
} else {
  Join-Path $projectRoot "dist"
}

# Build a minimal runtime asset tree. The source score_board directory also
# contains legacy tools, alternate flag sets, unused GSAP plugins, and design
# drafts that are useful for development but not for the packaged app.
$scoreboardStage = Join-Path $projectRoot "work\package-scoreboard"
if (Test-Path -LiteralPath $scoreboardStage) {
  Remove-Item -LiteralPath $scoreboardStage -Recurse -Force
}
New-Item -ItemType Directory -Path $scoreboardStage -Force | Out-Null

function Copy-ScoreboardFile {
  param([string]$RelativePath)
  $sourcePath = Join-Path (Join-Path $projectRoot "score_board") $RelativePath
  if (-not (Test-Path -LiteralPath $sourcePath)) {
    throw "Required scoreboard asset is missing: $RelativePath"
  }
  $targetPath = Join-Path $scoreboardStage $RelativePath
  $targetParent = Split-Path -Parent $targetPath
  New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
  Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
}

$rootAssets = @(
  "l3rd1.html", "l3rd1.xml", "l3rd2.html", "l3rd2.xml",
  "scoreboard.css", "scoreboard.html", "scoreboard.js", "scoreboard.xml",
  "streamcontrol.json", "streamcontrol.xml",
  "tournament-scoreboard.css", "tournament-scoreboard.html", "tournament-scoreboard.js",
  "transition.css", "transition.html", "transition.js"
)
$fontAssets = @(
  "fonts\fonts.css", "fonts\Gadugi.woff", "fonts\Gadugi-Bold.woff",
  "fonts\mona.woff", "fonts\knockout-htf68.woff",
  "fonts\OpenSans-Bold.ttf", "fonts\OpenSans-BoldItalic.ttf",
  "fonts\Futehodo-MaruGothic.ttf", "fonts\nicomoji-plus_v2-5.ttf"
)
$scriptAssets = @(
  "js\countries.js", "js\xjs.js", "js\textFit.min.js",
  "js\gsap\TweenMax.js", "js\gsap\plugins\ScrollToPlugin.js"
)
$imageAssets = @(
  "imgs\aya_scoreboard_0_.png", "imgs\aya_scoreboard_1__.png",
  "imgs\aya_scoreboard_2__.png", "imgs\aya_scoreboard_3_.png",
  "imgs\l3rd_01.png", "imgs\l3rd_02.png", "imgs\l3rd_03.png", "imgs\l3rd_04.png"
)
foreach ($score in 0..9) {
  $imageAssets += "imgs\${score}b.png"
  $imageAssets += "imgs\${score}r.png"
}
foreach ($asset in ($rootAssets + $fontAssets + $scriptAssets + $imageAssets)) {
  Copy-ScoreboardFile $asset
}
Copy-ScoreboardFile "GoSquared\LICENSE.txt"
Copy-Item -LiteralPath "score_board\GoSquared\expanded" -Destination (Join-Path $scoreboardStage "GoSquared") -Recurse
Copy-Item -LiteralPath "score_board\themes" -Destination $scoreboardStage -Recurse
New-Item -ItemType Directory -Path (Join-Path $scoreboardStage "AutoScoreDetector") -Force | Out-Null
Copy-Item -LiteralPath "score_board\AutoScoreDetector\templates" -Destination (Join-Path $scoreboardStage "AutoScoreDetector") -Recurse
Copy-ScoreboardFile "AutoScoreDetector\config.json"
Copy-ScoreboardFile "AutoScoreDetector\obs_monitor_config.json"

& $python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name TournamentControl `
  --distpath $distributionRoot `
  --additional-hooks-dir "packaging-hooks" `
  --add-data "$scoreboardStage;score_board" `
  --add-data "media\Aya_transition.mp4;media" `
  run_tournament_control.py

# Python and recent Qt wheels can be compiled with different Visual C++
# toolset revisions. PyInstaller places Python's runtime in _internal while
# PySide6 keeps its matching, newer runtime in _internal\PySide6. Windows can
# then load the older copy first and QtWidgets fails with "procedure not
# found". Promote Qt's matching runtime to _internal so every packaged Qt DLL
# resolves against the same toolset revision.
$pysideDir = & $python -c "from pathlib import Path; import PySide6; print(Path(PySide6.__file__).resolve().parent)"
$runtimeTarget = Join-Path $distributionRoot "TournamentControl\_internal"
$runtimeNames = @(
  "concrt140.dll",
  "msvcp140.dll",
  "msvcp140_1.dll",
  "msvcp140_2.dll",
  "msvcp140_codecvt_ids.dll",
  "vcruntime140.dll",
  "vcruntime140_1.dll"
)
foreach ($runtimeName in $runtimeNames) {
  $runtimeSource = Join-Path $pysideDir $runtimeName
  if (Test-Path -LiteralPath $runtimeSource) {
    Copy-Item -LiteralPath $runtimeSource -Destination $runtimeTarget -Force
  }
}

# The Codex workspace may prepend Poppler's native directory to PATH. Its ICU
# DLL has the same filename as the Windows system ICU shim but exports a
# different API. PyInstaller can therefore collect the Poppler DLL for
# Qt6Core, causing QtCore/QtWidgets imports to fail in the packaged app.
# Qt for Windows expects the system ICU shim, so remove only these mistakenly
# collected files from the verified application _internal directory.
foreach ($foreignIcuName in @("icuuc.dll", "icudt78.dll")) {
  $foreignIcuPath = Join-Path $runtimeTarget $foreignIcuName
  if (Test-Path -LiteralPath $foreignIcuPath) {
    Remove-Item -LiteralPath $foreignIcuPath -Force
  }
}

# Tournament Control is a Japanese application. Keep Japanese translations
# and the English fallback while excluding the other bundled Qt/WebEngine
# locales from the distribution.
$translationsPath = Join-Path $runtimeTarget "PySide6\translations"
if (Test-Path -LiteralPath $translationsPath) {
  Get-ChildItem -LiteralPath $translationsPath -File | Where-Object {
    $_.Name -notmatch "_(ja|en)\.qm$"
  } | Remove-Item -Force
  $webEngineLocalesPath = Join-Path $translationsPath "qtwebengine_locales"
  if (Test-Path -LiteralPath $webEngineLocalesPath) {
    Get-ChildItem -LiteralPath $webEngineLocalesPath -File | Where-Object {
      $_.Name -notin @("ja.pak", "en-US.pak")
    } | Remove-Item -Force
  }
}

# The bundled PySide6 wheel contains both release and debug Chromium resource
# packs. TournamentControl ships release Qt libraries, so the *.debug files
# are never selected at runtime and add roughly 77 MB to every distribution.
$webEngineResourcesPath = Join-Path $runtimeTarget "PySide6\resources"
if (Test-Path -LiteralPath $webEngineResourcesPath) {
  $debugWebEngineResources = @(
    "qtwebengine_devtools_resources.debug.pak",
    "qtwebengine_resources.debug.pak",
    "qtwebengine_resources_100p.debug.pak",
    "qtwebengine_resources_200p.debug.pak",
    "v8_context_snapshot.debug.bin"
  )
  foreach ($resourceName in $debugWebEngineResources) {
    $resourcePath = Join-Path $webEngineResourcesPath $resourceName
    if (Test-Path -LiteralPath $resourcePath) {
      Remove-Item -LiteralPath $resourcePath -Force
    }
  }
}

Write-Host "Built: $distributionRoot\TournamentControl\TournamentControl.exe"
