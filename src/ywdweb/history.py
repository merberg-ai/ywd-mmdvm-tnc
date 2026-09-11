"""Safe read-only access to ywd-packetlog history."""
from __future__ import annotations

from collections import Counter, deque
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def valid_date(value: str) -> str:
    if not _DATE.fullmatch(value):
        raise ValueError("date must be YYYY-MM-DD")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("invalid calendar date") from exc
    return value


class HistoryStore:
    def __init__(self, directory: Path, *, max_history_limit: int = 1000, max_console_lines: int = 2000, max_stats_events: int = 100000) -> None:
        self.directory = Path(directory)
        self.max_history_limit = max_history_limit
        self.max_console_lines = max_console_lines
        self.max_stats_events = max_stats_events

    def dates(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        result = []
        for path in self.directory.glob("????-??-??.jsonl"):
            stem = path.stem
            try:
                result.append(valid_date(stem))
            except ValueError:
                continue
        return sorted(set(result), reverse=True)

    def _json_path(self, date: str) -> Path:
        return self.directory / f"{valid_date(date)}.jsonl"

    def _human_path(self, date: str) -> Path:
        return self.directory / f"{valid_date(date)}.log"

    @staticmethod
    def _matches(event: dict[str, Any], *, kind: str | None, station: str | None, query: str | None) -> bool:
        if kind:
            event_name = str(event.get("event", ""))
            if kind.endswith(".*"):
                if not event_name.startswith(kind[:-1]):
                    return False
            elif event_name != kind:
                return False
        if station:
            needle = station.upper()
            haystack = [event.get("source"), event.get("destination")]
            path = event.get("path")
            if isinstance(path, list):
                haystack.extend(path)
            if not any(needle in str(value).upper() for value in haystack if value is not None):
                return False
        if query:
            needle = query.casefold()
            text = " ".join(
                str(event.get(name, ""))
                for name in ("source", "destination", "frame_type", "info_text", "reason", "error")
            )
            if needle not in text.casefold():
                return False
        return True

    def events(self, date: str, *, limit: int = 250, kind: str | None = None, station: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), self.max_history_limit))
        path = self._json_path(date)
        if not path.is_file():
            return []
        found: deque[dict[str, Any]] = deque(maxlen=limit)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or event.get("schema") != 1:
                    continue
                if self._matches(event, kind=kind, station=station, query=query):
                    found.append(event)
        return list(found)

    def console(self, date: str, *, lines: int = 400) -> list[str]:
        lines = max(1, min(int(lines), self.max_console_lines))
        path = self._human_path(date)
        if not path.is_file():
            return []
        out: deque[str] = deque(maxlen=lines)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                out.append(line.rstrip("\r\n"))
        return list(out)

    def stats(self, date: str) -> dict[str, Any]:
        path = self._json_path(date)
        result: dict[str, Any] = {
            "date": valid_date(date),
            "events": 0,
            "rx_frames": 0,
            "tx_submitted": 0,
            "tx_complete": 0,
            "tx_rejected": 0,
            "tx_timeout": 0,
            "tx_failed": 0,
            "top_sources": [],
            "top_destinations": [],
            "frame_types": {},
            "rssi_samples": 0,
            "rssi_average": None,
            "truncated": False,
        }
        if not path.is_file():
            return result
        sources: Counter[str] = Counter()
        destinations: Counter[str] = Counter()
        frame_types: Counter[str] = Counter()
        rssi_total = 0
        rssi_count = 0
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if result["events"] >= self.max_stats_events:
                    result["truncated"] = True
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or event.get("schema") != 1:
                    continue
                result["events"] += 1
                kind = str(event.get("event", ""))
                if kind == "rx.frame":
                    result["rx_frames"] += 1
                elif kind == "tx.submitted":
                    result["tx_submitted"] += 1
                elif kind == "tx.complete":
                    result["tx_complete"] += 1
                elif kind == "tx.rejected":
                    result["tx_rejected"] += 1
                elif kind == "tx.timeout":
                    result["tx_timeout"] += 1
                elif kind == "tx.failed":
                    result["tx_failed"] += 1
                if event.get("source"):
                    sources[str(event["source"])] += 1
                if event.get("destination"):
                    destinations[str(event["destination"])] += 1
                if event.get("frame_type"):
                    frame_types[str(event["frame_type"])] += 1
                raw_rssi = event.get("raw_rssi")
                if isinstance(raw_rssi, int):
                    rssi_total += raw_rssi
                    rssi_count += 1
        result["top_sources"] = [{"name": name, "count": count} for name, count in sources.most_common(8)]
        result["top_destinations"] = [{"name": name, "count": count} for name, count in destinations.most_common(8)]
        result["frame_types"] = dict(frame_types.most_common())
        result["rssi_samples"] = rssi_count
        result["rssi_average"] = round(rssi_total / rssi_count, 1) if rssi_count else None
        return result
