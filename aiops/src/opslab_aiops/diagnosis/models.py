from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AffectedResource:
    kind: str
    name: str
    uid: str


@dataclass(frozen=True, slots=True)
class DiagnosisResult:
    diagnosis_id: str
    incident_id: str

    summary: str
    root_cause: str
    confidence: str

    evidence_refs: tuple[str, ...]

    affected_resource: AffectedResource

    recommended_action: str
    rationale: str
    risk_notes: tuple[str, ...]

    requires_human_approval: bool

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
