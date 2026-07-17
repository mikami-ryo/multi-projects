from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .models import Match
from .round_rules import best_of, display_round


DEFAULT_PAYLOAD = {
    "bestOf": "bo3",
    "l3rd1a": "",
    "l3rd1b": "",
    "l3rd2a": "",
    "l3rd2b": "",
    "pCountry1": "",
    "pCountry2": "",
    "pName1": "",
    "pName2": "",
    "pScore1": "0",
    "pScore2": "0",
    "pTeam1": "",
    "pTeam2": "",
    "pTwitter1": "",
    "pTwitter2": "",
    "sponsorts": "0",
    "stage": "",
    "streamUrl": "",
    "timestamp": "0",
}


def next_trigger_value(payload: dict) -> str:
    now = int(time.time() * 1000)
    existing = []
    for key in ("timestamp", "sponsorts"):
        value = str(payload.get(key, "")).strip()
        if value.isdigit():
            existing.append(int(value))
    return str(max(now, (max(existing) + 1) if existing else 0))


class StreamControlStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def read(self) -> dict:
        payload = dict(DEFAULT_PAYLOAD)
        if self.path.exists():
            with self.path.open(encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                payload.update(loaded)
        return payload

    def write(self, payload: dict, *, trigger: bool = True) -> dict:
        merged = dict(DEFAULT_PAYLOAD)
        merged.update(payload)
        if trigger:
            merged["timestamp"] = next_trigger_value(merged)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix="streamcontrol-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(merged, handle, ensure_ascii=False, indent=4)
                handle.write("\n")
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        return merged

    def write_with_sponsors(self, payload: dict) -> dict:
        """Save and fire the same timestamp trigger as the legacy Sponsors button."""
        trigger_value = next_trigger_value(payload)
        payload["timestamp"] = trigger_value
        payload["sponsorts"] = trigger_value
        return self.write(payload, trigger=False)

    def apply_match(
        self,
        match: Match,
        *,
        winners_bo5_from: str,
        losers_bo5_from: str,
        round_mappings: dict[str, str],
        stream_url: str = "",
    ) -> dict:
        if not match.ready:
            raise ValueError("対戦者が2名確定していない試合は反映できません。")
        payload = self.read()
        p1, p2 = match.players[:2]
        payload.update(
            {
                "pTeam1": p1.prefix,
                "pName1": p1.gamer_tag,
                "pTwitter1": p1.twitter,
                "pCountry1": p1.country,
                "pScore1": "0",
                "pTeam2": p2.prefix,
                "pName2": p2.gamer_tag,
                "pTwitter2": p2.twitter,
                "pCountry2": p2.country,
                "pScore2": "0",
                "stage": display_round(match.round_text, round_mappings),
                "bestOf": f"bo{best_of(match.round_text, winners_bo5_from, losers_bo5_from)}",
                "streamUrl": stream_url or payload.get("streamUrl", ""),
                "startggSetId": match.set_id,
                "startggRound": match.round_text,
            }
        )
        return self.write_with_sponsors(payload)

    def increment_score(self, side: int) -> dict:
        if side not in (1, 2):
            raise ValueError("side must be 1 or 2")
        payload = self.read()
        key = f"pScore{side}"
        try:
            current = int(str(payload.get(key, "0")))
        except ValueError:
            current = 0
        payload[key] = str(current + 1)
        return self.write(payload)
