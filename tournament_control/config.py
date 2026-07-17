from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path

from .models import AppSettings
from .secrets import protect_secret, unprotect_secret


class SettingsStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            with self.path.open(encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return AppSettings()

        allowed = {item.name for item in fields(AppSettings)}
        values = {key: value for key, value in raw.items() if key in allowed}
        protected = str(raw.get("startgg_token_protected", ""))
        try:
            values["startgg_token"] = unprotect_secret(protected)
        except OSError:
            values["startgg_token"] = ""
        return AppSettings(**values)

    def save(self, settings: AppSettings) -> None:
        payload = asdict(settings)
        token = payload.pop("startgg_token", "")
        payload["startgg_token_protected"] = protect_secret(token)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
