from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence


SESSION_DATE_FORMATS = (
    "%I:%M %p on %d %B, %Y",
    "%I:%M %p on %B %d, %Y",
    "%d %B %Y",
    "%B %d, %Y",
)
WEEKDAYS = {
    name.lower(): index
    for index, name in enumerate(calendar.day_name)
}
MONTHS = {
    name.lower(): index
    for index, name in enumerate(calendar.month_name)
    if name
}


def parse_session_datetime(value: Any) -> Optional[datetime]:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for fmt in SESSION_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_day(value: datetime) -> str:
    return f"{value.day} {value.strftime('%B')} {value.year}"


def resolve_temporal_mentions(text: str, session_date_time: Any) -> List[Dict[str, Any]]:
    anchor = parse_session_datetime(session_date_time)
    if anchor is None:
        return []
    value = str(text or "")
    matches: List[Dict[str, Any]] = []

    def add(raw: str, start: datetime, end: datetime, granularity: str, display: str) -> None:
        matches.append(
            {
                "raw": raw,
                "anchor_date": format_day(anchor),
                "start_date": format_day(start),
                "end_date": format_day(end),
                "granularity": granularity,
                "resolved": display,
                "method": "deterministic_session_relative_time_v1",
            }
        )

    patterns = [
        (r"\byesterday\b", -1),
        (r"\btoday\b", 0),
        (r"\btomorrow\b", 1),
    ]
    for pattern, delta in patterns:
        for match in re.finditer(pattern, value, flags=re.IGNORECASE):
            day = anchor + timedelta(days=delta)
            add(match.group(0), day, day, "day", format_day(day))

    for match in re.finditer(
        r"\blast\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
        value,
        flags=re.IGNORECASE,
    ):
        target = WEEKDAYS[match.group(1).lower()]
        delta = (anchor.weekday() - target) % 7
        delta = 7 if delta == 0 else delta
        day = anchor - timedelta(days=delta)
        add(
            match.group(0),
            day,
            day,
            "day",
            f"The {match.group(1)} before {format_day(anchor)}",
        )

    for match in re.finditer(r"\blast week\b", value, flags=re.IGNORECASE):
        end = anchor - timedelta(days=anchor.weekday() + 1)
        start = end - timedelta(days=6)
        add(
            match.group(0),
            start,
            end,
            "week",
            f"The week before {format_day(anchor)}",
        )

    for direction, delta in (("last", -1), ("next", 1)):
        for match in re.finditer(rf"\b{direction} month\b", value, flags=re.IGNORECASE):
            month_index = anchor.month - 1 + delta
            year = anchor.year + month_index // 12
            month = month_index % 12 + 1
            start = datetime(year, month, 1)
            end = datetime(year, month, calendar.monthrange(year, month)[1])
            add(match.group(0), start, end, "month", f"{calendar.month_name[month]} {year}")

    matches.sort(key=lambda item: value.lower().find(str(item["raw"]).lower()))
    return matches


def temporal_context_lines(metadata: Mapping[str, Any]) -> List[str]:
    lines: List[str] = []
    session_date_time = metadata.get("session_date_time")
    if session_date_time:
        lines.append(f"session datetime: {session_date_time}")
    for mention in metadata.get("temporal_mentions") or []:
        lines.append(
            'deterministic time: "{raw}" = {resolved} '
            "(anchor: {anchor_date})".format(**mention)
        )
    query = metadata.get("image_query")
    caption = metadata.get("blip_caption")
    image_urls = metadata.get("img_url") or []
    if query:
        lines.append(f"image search/query metadata: {query}")
    if caption:
        lines.append(f"image caption: {caption}")
    if image_urls:
        lines.append(f"image URL metadata: {', '.join(str(item) for item in image_urls)}")
    return lines


def canonicalize_relative_answer(
    answer: str,
    memories: Sequence[Any],
) -> str:
    """Replace a reader's relative-only answer with its deterministic value."""
    result = str(answer or "").strip()
    normalized = re.sub(r"[^a-z]+", " ", result.lower()).strip()
    for memory in memories:
        metadata = getattr(memory, "metadata", {}) or {}
        for mention in metadata.get("temporal_mentions") or []:
            raw = re.sub(r"[^a-z]+", " ", str(mention.get("raw") or "").lower()).strip()
            if raw and (normalized == raw or normalized in {f"the {raw}", f"on {raw}"}):
                return str(mention.get("resolved") or result)
    return result
