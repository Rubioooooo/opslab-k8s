from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from opslab_aiops.execution.models import ExecutionResult
from opslab_aiops.observation.models import ObservationSnapshot
from opslab_aiops.safety.models import SafetyDecision


@dataclass(frozen=True, slots=True)
class BusinessProbeResult:
    url: str
    success: bool
    status_code: int | None
    checked_at: str
    error: str | None = None

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


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    safety_decision: SafetyDecision
    execution_result: ExecutionResult
    baseline_snapshot: ObservationSnapshot
    post_snapshot: ObservationSnapshot
    business_probe: BusinessProbeResult

    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    schema_version: str = "v1alpha1"


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verification_id: str

    incident_id: str
    execution_id: str

    target_uid: str
    replacement_uid: str | None

    outcome: str
    reason_codes: tuple[str, ...]

    verified_at: str

    baseline_snapshot_id: str
    fresh_snapshot_id: str

    evidence_refs: tuple[str, ...]

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
