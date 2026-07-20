from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_ROUND_MAPPINGS = {
    "Grand Final Reset": "GFR",
    "Grand Final": "GF",
    "Winners Final": "WF",
    "Winners Semifinal": "WSF",
    "Winners Quarterfinal": "WQF",
    "Losers Final": "LF",
    "Losers Semifinal": "LSF",
    "Losers Quarterfinal": "LQF",
    "Winners Round {n}": "WR{n}",
    "Losers Round {n}": "LR{n}",
}


@dataclass(slots=True)
class Player:
    entrant_id: str = ""
    prefix: str = ""
    gamer_tag: str = ""
    twitter: str = ""
    country: str = ""


@dataclass(slots=True)
class Match:
    set_id: str
    identifier: str
    round_text: str
    state: int
    total_games: int
    players: list[Player]
    phase_id: str = ""
    phase_name: str = ""

    @property
    def ready(self) -> bool:
        return len(self.players) >= 2 and all(
            player.entrant_id
            and player.gamer_tag.strip()
            and player.gamer_tag.strip().casefold() != "tbd"
            for player in self.players[:2]
        )

    @property
    def selectable(self) -> bool:
        # start.gg uses state=3 for a completed set. States 1 (created) and 2
        # (active) are both useful once the two entrants have been resolved.
        return self.state != 3 and self.ready


@dataclass
class AppSettings:
    startgg_home_url: str = "https://www.start.gg/"
    startgg_token: str = ""
    winners_bo5_from: str = "Winners Final"
    losers_bo5_from: str = "Losers Final"
    round_mappings: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ROUND_MAPPINGS))
    phase_bo5_rules: dict[str, dict[str, str]] = field(default_factory=dict)
    phase_round_options: dict[str, dict] = field(default_factory=dict)
    scoreboard_port: int = 8765
    scoreboard_design: str = "legacy"
    player_camera_enabled: bool = False
    stream_url: str = ""
    obs_host: str = "localhost"
    obs_port: int = 4455
    obs_password: str = ""
    obs_source_name: str = "ゲーム画面"
    detector_write_scores: bool = False
    obs_layout_enabled: bool = False
    obs_layout_scene_name: str = ""
    obs_game_source_name: str = "ゲーム画面"
    obs_player1_source_name: str = ""
    obs_player2_source_name: str = ""
    obs_in_game_layout: dict[str, dict] = field(default_factory=dict)
    obs_between_game_layout: dict[str, dict] = field(default_factory=dict)
    transition_in_game_framework_delay: float = 0.0
    transition_in_game_layout_delay: float = 0.0
    transition_between_game_framework_delay: float = 0.0
    transition_between_game_layout_delay: float = 0.0
