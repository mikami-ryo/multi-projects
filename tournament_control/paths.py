from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


def resource_root() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parents[1]


def app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = base / "TournamentControl"
    path.mkdir(parents=True, exist_ok=True)
    return path


MANAGED_SCOREBOARD_FILES = (
    "tournament-scoreboard.html",
    "tournament-scoreboard.css",
    "tournament-scoreboard.js",
    "transition.html",
    "transition.css",
    "transition.js",
)


def sync_managed_scoreboard_files(source: Path, target: Path) -> None:
    """Update app-owned files while leaving the editable legacy theme untouched."""
    for relative in MANAGED_SCOREBOARD_FILES:
        source_file = source / relative
        if source_file.exists():
            target_file = target / relative
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)
    source_themes = source / "themes"
    if source_themes.exists():
        shutil.copytree(source_themes, target / "themes", dirs_exist_ok=True)


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    resource_dir: Path
    settings_file: Path
    streamcontrol_file: Path
    scoreboard_assets: Path
    detector_dir: Path
    detector_runtime_config: Path
    automation_rules_file: Path
    scoreboard_state_file: Path
    transition_video_file: Path

    @classmethod
    def create(cls) -> "AppPaths":
        data = app_data_dir()
        resources = resource_root()
        source_streamcontrol = resources / "score_board" / "streamcontrol.json"
        target_streamcontrol = data / "streamcontrol.json"
        if not target_streamcontrol.exists() and source_streamcontrol.exists():
            shutil.copy2(source_streamcontrol, target_streamcontrol)

        source_detector_config = resources / "score_board" / "AutoScoreDetector" / "obs_monitor_config.json"
        detector_runtime_config = data / "obs_monitor_config.json"
        if not detector_runtime_config.exists() and source_detector_config.exists():
            shutil.copy2(source_detector_config, detector_runtime_config)

        # The bundled files remain an immutable factory copy. The server uses
        # this per-user workspace so future design changes do not require an
        # executable rebuild. Existing customizations are never overwritten.
        source_scoreboard = resources / "score_board"
        scoreboard_workspace = data / "scoreboard"
        if not scoreboard_workspace.exists() and source_scoreboard.exists():
            ignored = shutil.ignore_patterns(
                "AutoScoreDetector",
                "StreamControl",
                "TweetHelper",
                "__pycache__",
                "__init__.py",
                ".gitignore",
            )
            shutil.copytree(source_scoreboard, scoreboard_workspace, ignore=ignored)
        elif source_scoreboard.exists():
            sync_managed_scoreboard_files(source_scoreboard, scoreboard_workspace)

        automation_rules_file = data / "automation_rules.json"
        if not automation_rules_file.exists():
            automation_rules_file.write_text(
                '{\n  "version": 1,\n  "rules": []\n}\n', encoding="utf-8"
            )

        scoreboard_state_file = data / "scoreboard_state.json"
        if not scoreboard_state_file.exists():
            scoreboard_state_file.write_text(
                '{\n  "version": 2,\n  "activeTheme": "legacy",\n'
                '  "design": "legacy",\n  "playerCamera": false,\n'
                '  "scene": "in_game",\n  "mode": "automatic",\n'
                '  "lastEvent": null\n}\n',
                encoding="utf-8",
            )

        source_transition_video = resources / "media" / "Aya_transition.mp4"
        transition_video_file = scoreboard_workspace / "media" / "Aya_transition.mp4"
        if not transition_video_file.exists() and source_transition_video.exists():
            transition_video_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_transition_video, transition_video_file)

        return cls(
            data_dir=data,
            resource_dir=resources,
            settings_file=data / "settings.json",
            streamcontrol_file=target_streamcontrol,
            scoreboard_assets=scoreboard_workspace,
            detector_dir=resources / "score_board" / "AutoScoreDetector",
            detector_runtime_config=detector_runtime_config,
            automation_rules_file=automation_rules_file,
            scoreboard_state_file=scoreboard_state_file,
            transition_video_file=transition_video_file,
        )
