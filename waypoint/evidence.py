"""Per-run evidence on disk (PLAN.md 3.5, R-SENS-5).

    evidence/runs/<run_id>/
      artifact.json          the exact artifact that ran - the contract, verbatim
      meta.json              capability, version, content hash, redacted inputs
      events.jsonl           one line per decision: the reviewable record of the run
      result.json            the result, with outputs redacted for this sink
      stop.png / final.png   screenshots, sensitive regions already blacked out
      *_snapshot.json        the sanitized accessibility snapshot at that moment

Everything written passes through the run's redactor at the EVIDENCE sink, as a
second line of defence: callers already hand this module sanitized data. The
artifact copy is exempt - it is the reviewed contract, which can hold no literal
values (R-SENS-9) - and so is meta.json, whose content hash would otherwise be
mangled by the long-digit detector.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from waypoint.policy.redactor import Redactor, Sink


class EvidenceWriter:
    def __init__(self, root: Path, run_id: str, redactor: Redactor) -> None:
        self.dir = Path(root) / run_id
        self.dir.mkdir(parents=True, exist_ok=False)
        self._redactor = redactor
        self._events = (self.dir / "events.jsonl").open("a", encoding="utf-8")

    @staticmethod
    def new_run_id() -> str:
        return f"{datetime.now(UTC):%Y%m%dT%H%M%S}Z-{uuid.uuid4().hex[:6]}"

    def _clean(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redactor.scrub(value, Sink.EVIDENCE)
        if isinstance(value, dict):
            return {k: self._clean(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self._clean(v) for v in value]
        return value

    def event(self, kind: str, **fields: Any) -> None:
        record = {
            "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": kind,
            **self._clean({k: v for k, v in fields.items() if v is not None}),
        }
        self._events.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._events.flush()

    def json(self, name: str, data: Any, *, scrub: bool = True) -> str:
        payload = self._clean(data) if scrub else data
        (self.dir / name).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n"
        )
        return name

    def text(self, name: str, content: str) -> str:
        (self.dir / name).write_text(content)
        return name

    def screenshot(self, name: str, png: bytes) -> str:
        (self.dir / name).write_bytes(png)
        return name

    def close(self) -> None:
        self._events.close()
