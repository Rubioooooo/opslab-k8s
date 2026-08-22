from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from opslab_aiops.safety.models import SafetyDecision


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    namespace: str
    safety_decision: SafetyDecision

    schema_version: str = "v1alpha1"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    execution_id: str

    decision_id: str
    incident_id: str

    action: str
    namespace: str

    target_name: str
    target_uid: str

    status: str
    reason_codes: tuple[str, ...]

    attempted_at: str
    api_status_code: int | None = None

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
