from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    decision_id: str
    incident_id: str
    action: str

    target_kind: str
    target_name: str
    target_uid: str

    decision: str
    reason_codes: tuple[str, ...]

    evaluated_at: str
    fresh_snapshot_id: str
    human_approved: bool

    schema_version: str = "v1alpha1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )
