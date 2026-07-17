from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping


ALL_BO3 = "__ALL_BO3__"

def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = re.sub(r"[-_\u2010-\u2015\u2212]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().casefold()
    value = re.sub(r"\bsemi\s*finals?\b", "semifinal", value)
    value = re.sub(r"\bquarter\s*finals?\b", "quarterfinal", value)
    value = re.sub(r"\bfinals\b", "final", value)
    return value


def _side_and_rank(round_text: str) -> tuple[str, int]:
    text = _normalize(round_text)
    if "grand final" in text or "grand finals" in text:
        return "grand", 1000

    if "winner" in text or "upper" in text:
        side = "winners"
    elif "loser" in text or "lower" in text:
        side = "losers"
    else:
        return "unknown", -1

    if "final" in text and "semi" not in text and "quarter" not in text:
        return side, 100
    if "semi" in text:
        return side, 90
    if "quarter" in text:
        return side, 80
    if "eighth" in text:
        return side, 70

    match = re.search(r"(?:round|r)\s*(\d+)", text)
    if match:
        return side, int(match.group(1))
    return side, 0


def is_bo5(round_text: str, winners_from: str, losers_from: str) -> bool:
    if winners_from == ALL_BO3 and losers_from == ALL_BO3:
        return False
    side, rank = _side_and_rank(round_text)
    if side == "grand":
        return True
    if side == "winners":
        if winners_from == ALL_BO3:
            return False
        threshold_side, threshold = _side_and_rank(winners_from)
        return threshold_side == "winners" and rank >= threshold
    if side == "losers":
        if losers_from == ALL_BO3:
            return False
        threshold_side, threshold = _side_and_rank(losers_from)
        return threshold_side == "losers" and rank >= threshold
    return False


def best_of(round_text: str, winners_from: str, losers_from: str) -> int:
    return 5 if is_bo5(round_text, winners_from, losers_from) else 3


def round_side_and_rank(round_text: str) -> tuple[str, int]:
    return _side_and_rank(round_text)


def display_round(round_text: str, mappings: Mapping[str, str]) -> str:
    normalized = _normalize(round_text)
    exact = {_normalize(source): target for source, target in mappings.items() if source.strip()}
    if normalized in exact:
        return exact[normalized]

    # Permit broad rules such as "Grand Final=GF", while preferring the longest key.
    for source in sorted(exact, key=len, reverse=True):
        if source and source in normalized:
            return exact[source]

    winners_round = re.fullmatch(r"(?:winners?|upper) round\s*(\d+)", normalized)
    if winners_round:
        number = winners_round.group(1)
        template = mappings.get("Winners Round {n}", "WR{n}")
        return str(template).replace("{n}", number)
    losers_round = re.fullmatch(r"(?:losers?|lower) round\s*(\d+)", normalized)
    if losers_round:
        number = losers_round.group(1)
        template = mappings.get("Losers Round {n}", "LR{n}")
        return str(template).replace("{n}", number)
    return (round_text or "").strip()
