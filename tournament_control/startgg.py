from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .models import Match, Player


API_ENDPOINT = "https://api.start.gg/gql/alpha"
EVENT_URL_PATTERN = re.compile(r"/tournament/([^/?#]+)/event/([^/?#]+)", re.IGNORECASE)


EVENT_PHASES_QUERY = """
query TournamentControlEventPhases($eventSlug: String!) {
  event(slug: $eventSlug) {
    id
    name
    phases { id name }
  }
}
"""


PHASE_SETS_QUERY = """
query TournamentControlPhaseSets($phaseId: ID!, $page: Int!, $perPage: Int!) {
  phase(id: $phaseId) {
    id
    name
    sets(page: $page, perPage: $perPage, sortType: STANDARD) {
      pageInfo { total totalPages }
      nodes {
        id
        identifier
        state
        fullRoundText
        totalGames
        slots {
          entrant {
            id
            name
            participants {
              gamerTag
              prefix
              connectedAccounts
            }
          }
        }
      }
    }
  }
}
"""


EVENT_SETS_QUERY = """
query TournamentControlEventSets($eventSlug: String!, $page: Int!, $perPage: Int!) {
  event(slug: $eventSlug) {
    id
    name
    sets(page: $page, perPage: $perPage, sortType: STANDARD) {
      pageInfo { total totalPages }
      nodes {
        id
        identifier
        state
        fullRoundText
        totalGames
        slots {
          entrant {
            id
            name
            participants {
              gamerTag
              prefix
              connectedAccounts
            }
          }
        }
      }
    }
  }
}
"""


class StartGGError(RuntimeError):
    pass


@dataclass(slots=True)
class EventMatches:
    event_id: str
    event_name: str
    matches: list[Match]


def event_slug_from_url(url: str) -> str | None:
    match = EVENT_URL_PATTERN.search(url or "")
    if not match:
        return None
    return f"tournament/{match.group(1)}/event/{match.group(2)}"


def _twitter_from_accounts(accounts: Any) -> str:
    if not isinstance(accounts, dict):
        return ""
    for key in ("twitter", "x", "TWITTER"):
        value = accounts.get(key)
        if isinstance(value, str):
            return value.lstrip("@")
        if isinstance(value, dict):
            for nested in ("value", "username", "displayName"):
                if value.get(nested):
                    return str(value[nested]).lstrip("@")
    return ""


def _player_from_entrant(entrant: dict | None) -> Player:
    if not entrant:
        return Player()
    participants = entrant.get("participants") or []
    participant = participants[0] if participants else {}
    gamer_tag = str(participant.get("gamerTag") or "").strip()
    prefix = str(participant.get("prefix") or "").strip()

    if not gamer_tag:
        entrant_name = str(entrant.get("name") or "").strip()
        if " | " in entrant_name:
            prefix, gamer_tag = [part.strip() for part in entrant_name.rsplit(" | ", 1)]
        else:
            gamer_tag = entrant_name
    return Player(
        entrant_id=str(entrant.get("id") or ""),
        prefix=prefix,
        gamer_tag=gamer_tag,
        twitter=_twitter_from_accounts(participant.get("connectedAccounts")),
    )


def parse_event_matches(data: dict) -> EventMatches:
    event = data.get("event")
    if not event:
        raise StartGGError("イベントが見つかりません。URLとAPIトークンを確認してください。")
    connection = event.get("sets") or {}
    matches = []
    for node in connection.get("nodes") or []:
        players = []
        for slot in node.get("slots") or []:
            players.append(_player_from_entrant(slot.get("entrant")))
        matches.append(
            Match(
                set_id=str(node.get("id") or ""),
                identifier=str(node.get("identifier") or ""),
                round_text=str(node.get("fullRoundText") or node.get("identifier") or ""),
                state=int(node.get("state") or 0),
                total_games=int(node.get("totalGames") or 0),
                players=players,
                phase_id=str(node.get("_phaseId") or ""),
                phase_name=str(node.get("_phaseName") or ""),
            )
        )
    return EventMatches(str(event.get("id") or ""), str(event.get("name") or ""), matches)


class StartGGClient:
    def __init__(self, token: str, timeout: float = 15.0):
        self.token = token.strip()
        self.timeout = timeout

    def execute(self, query: str, variables: dict) -> dict:
        if not self.token:
            raise StartGGError("設定画面でstart.gg APIトークンを登録してください。")
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = urllib.request.Request(
            API_ENDPOINT,
            data=body,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise StartGGError(f"start.gg APIエラー ({exc.code}): {detail[:300]}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise StartGGError(f"start.gg APIへ接続できません: {exc}") from exc
        if payload.get("errors"):
            messages = "; ".join(str(error.get("message", error)) for error in payload["errors"])
            raise StartGGError(messages)
        return payload.get("data") or {}

    def event_matches(self, event_slug: str) -> EventMatches:
        metadata = self.execute(EVENT_PHASES_QUERY, {"eventSlug": event_slug})
        event_metadata = metadata.get("event") or {}
        if not event_metadata:
            raise StartGGError("イベントが見つかりません。URLとAPIトークンを確認してください。")

        phases = event_metadata.get("phases") or []
        if phases:
            all_nodes: list[dict] = []
            for phase_summary in phases:
                phase_id = str(phase_summary.get("id") or "")
                phase_name = str(phase_summary.get("name") or "")
                page = 1
                total_pages = 1
                while page <= total_pages:
                    phase_data = self.execute(
                        PHASE_SETS_QUERY,
                        {"phaseId": phase_id, "page": page, "perPage": 100},
                    )
                    phase = phase_data.get("phase") or {}
                    connection = phase.get("sets") or {}
                    page_info = connection.get("pageInfo") or {}
                    total_pages = int(page_info.get("totalPages") or 1)
                    for original in connection.get("nodes") or []:
                        node = dict(original)
                        node["_phaseId"] = phase_id
                        node["_phaseName"] = phase_name or str(phase.get("name") or "")
                        all_nodes.append(node)
                    page += 1
            return parse_event_matches(
                {
                    "event": {
                        "id": event_metadata.get("id"),
                        "name": event_metadata.get("name"),
                        "sets": {"nodes": all_nodes},
                    }
                }
            )

        # Fallback for events whose API response does not expose phase metadata.
        data = self.execute(EVENT_SETS_QUERY, {"eventSlug": event_slug, "page": 1, "perPage": 100})
        event = data.get("event") or {}
        connection = event.get("sets") or {}
        page_info = connection.get("pageInfo") or {}
        total_pages = int(page_info.get("totalPages") or 1)
        all_nodes = list(connection.get("nodes") or [])

        for page in range(2, total_pages + 1):
            next_data = self.execute(
                EVENT_SETS_QUERY,
                {"eventSlug": event_slug, "page": page, "perPage": 100},
            )
            next_event = next_data.get("event") or {}
            next_connection = next_event.get("sets") or {}
            all_nodes.extend(next_connection.get("nodes") or [])

        connection["nodes"] = all_nodes
        event["sets"] = connection
        data["event"] = event
        return parse_event_matches(data)
