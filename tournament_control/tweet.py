from __future__ import annotations

import webbrowser
from urllib.parse import quote


def _twitter_suffix(handle: str) -> str:
    handle = (handle or "").strip()
    if not handle:
        return ""
    if not handle.startswith("@"):
        handle = "@" + handle
    return f" ({handle})"


def format_player(team: str, name: str, twitter: str = "") -> str:
    team = (team or "").strip()
    name = (name or "").strip()
    separator = "|" if team else ""
    return f"{team}{separator}{name}{_twitter_suffix(twitter)}"


def build_match_announcement(payload: dict) -> str:
    stage = str(payload.get("stage", "")).strip()
    player1 = format_player(payload.get("pTeam1", ""), payload.get("pName1", ""), payload.get("pTwitter1", ""))
    player2 = format_player(payload.get("pTeam2", ""), payload.get("pName2", ""), payload.get("pTwitter2", ""))
    stream_url = str(payload.get("streamUrl", "")).strip()

    lines: list[str] = []
    if stage:
        lines.append(f"🍀{stage}🍀")
    lines.append(f"{player1} vs {player2}")
    if stream_url:
        lines.append(f"({stream_url})")
    return "\n".join(lines)


def x_intent_url(text: str) -> str:
    return "https://x.com/intent/tweet?text=" + quote(text)


def open_x_intent(text: str) -> bool:
    """Open X using the same browser path as the original Tweet Helper."""
    return webbrowser.open(x_intent_url(text))
