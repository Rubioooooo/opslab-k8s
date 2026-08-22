from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from opslab_aiops.diagnosis.models import DiagnosisResult
from opslab_aiops.execution.models import ExecutionResult
from opslab_aiops.safety.models import SafetyDecision
from opslab_aiops.verification.models import VerificationResult


EXPERIENCE_SCHEMA_VERSION = "v1alpha1"

ACTION_NO_ACTION = "NO_ACTION"
ACTION_EVICT_POD = "EVICT_POD"
ACTION_ESCALATE = "ESCALATE"

ALLOWED_ACTIONS = frozenset(
    {
        ACTION_NO_ACTION,
        ACTION_EVICT_POD,
        ACTION_ESCALATE,
    }
)

ALLOWED_CONFIDENCE = frozenset(
    {
        "LOW",
        "MEDIUM",
        "HIGH",
    }
)

SAFETY_DENY = "DENY"
SAFETY_REQUIRE_HUMAN = "REQUIRE_HUMAN"
SAFETY_ALLOW = "ALLOW"

ALLOWED_SAFETY_OUTCOMES = frozenset(
    {
        SAFETY_DENY,
        SAFETY_REQUIRE_HUMAN,
        SAFETY_ALLOW,
    }
)

EXECUTION_ACCEPTED = "ACCEPTED"
EXECUTION_REFUSED = "REFUSED"

ALLOWED_EXECUTION_STATUSES = frozenset(
    {
        EXECUTION_ACCEPTED,
        EXECUTION_REFUSED,
    }
)

FINAL_VERIFIED = "VERIFIED"
FINAL_NOT_RECOVERED = "NOT_RECOVERED"
FINAL_INCONCLUSIVE = "INCONCLUSIVE"

# NO_EXECUTION means:
# no controlled Kubernetes mutation was ACCEPTED.
#
# The executor itself may still have been invoked and returned REFUSED.
FINAL_NO_EXECUTION = "NO_EXECUTION"

VERIFICATION_OUTCOMES = frozenset(
    {
        FINAL_VERIFIED,
        FINAL_NOT_RECOVERED,
        FINAL_INCONCLUSIVE,
    }
)


class ExperienceValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExperienceRecord:
    experience_id: str
    incident_id: str

    diagnosis_id: str
    diagnosis_action: str
    diagnosis_confidence: str

    safety_decision_id: str | None
    safety_outcome: str | None

    execution_id: str | None
    execution_status: str | None

    verification_id: str | None
    verification_outcome: str | None

    target_kind: str
    target_name: str
    target_uid: str

    replacement_uid: str | None

    final_outcome: str

    evidence_refs: tuple[str, ...]

    created_at: str

    schema_version: str = EXPERIENCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _fail(message: str) -> None:
    raise ExperienceValidationError(message)


def _non_empty(
    value: object,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        _fail(
            f"{field_name} must be a non-empty string"
        )

    normalized = value.strip()

    if not normalized:
        _fail(
            f"{field_name} must be a non-empty string"
        )

    return normalized


def _timestamp(
    value: object,
    field_name: str,
) -> str:
    raw = _non_empty(
        value,
        field_name,
    )

    normalized = raw

    if normalized.endswith("Z"):
        normalized = (
            f"{normalized[:-1]}+00:00"
        )

    try:
        parsed = datetime.fromisoformat(
            normalized
        )
    except ValueError as exc:
        raise ExperienceValidationError(
            f"{field_name} must be an ISO-8601 timestamp"
        ) from exc

    if parsed.tzinfo is None:
        _fail(
            f"{field_name} must include timezone information"
        )

    return raw


def _reason_codes(
    value: object,
    field_name: str,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        _fail(
            f"{field_name} must be a non-empty tuple"
        )

    if not value:
        _fail(
            f"{field_name} must be a non-empty tuple"
        )

    for index, item in enumerate(value):
        _non_empty(
            item,
            f"{field_name}[{index}]",
        )

    return value


def _artifact_refs(
    value: object,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        _fail(
            "evidence_refs must be a non-empty tuple"
        )

    if not value:
        _fail(
            "evidence_refs must be a non-empty tuple"
        )

    refs: list[str] = []

    for index, raw in enumerate(value):
        ref = _non_empty(
            raw,
            f"evidence_refs[{index}]",
        )

        if "\\" in ref:
            _fail(
                "evidence_refs must use POSIX-style "
                "relative artifact paths"
            )

        if "://" in ref:
            _fail(
                "evidence_refs must not be URLs"
            )

        path = PurePosixPath(ref)

        if path.is_absolute():
            _fail(
                "evidence_refs must be relative "
                "artifact paths"
            )

        # This intentionally rejects both:
        #
        #   EV-0001
        #   09-execution-result.json
        #
        # Experience references artifacts, not an
        # IncidentContext-local Evidence ID namespace
        # and not an ambiguous bare filename.
        if len(path.parts) < 2:
            _fail(
                "evidence_refs must be normalized "
                "relative artifact paths; bare Evidence "
                "IDs and bare filenames are not allowed"
            )

        if any(
            part in {".", ".."}
            for part in path.parts
        ):
            _fail(
                "evidence_refs must not contain "
                "dot path segments"
            )

        if ":" in path.parts[0]:
            _fail(
                "evidence_refs must not use "
                "drive-qualified paths"
            )

        if str(path) != ref:
            _fail(
                "evidence_refs must use normalized "
                "relative artifact paths"
            )

        refs.append(ref)

    if len(refs) != len(set(refs)):
        _fail(
            "evidence_refs contains duplicate values"
        )

    return tuple(refs)


def _validate_diagnosis(
    diagnosis: DiagnosisResult,
) -> None:
    if diagnosis.schema_version != "v1alpha1":
        _fail(
            "unsupported DiagnosisResult schema_version"
        )

    _non_empty(
        diagnosis.diagnosis_id,
        "diagnosis.diagnosis_id",
    )

    _non_empty(
        diagnosis.incident_id,
        "diagnosis.incident_id",
    )

    _non_empty(
        diagnosis.affected_resource.kind,
        "diagnosis.affected_resource.kind",
    )

    _non_empty(
        diagnosis.affected_resource.name,
        "diagnosis.affected_resource.name",
    )

    _non_empty(
        diagnosis.affected_resource.uid,
        "diagnosis.affected_resource.uid",
    )

    if (
        diagnosis.recommended_action
        not in ALLOWED_ACTIONS
    ):
        _fail(
            "unsupported diagnosis "
            "recommended_action: "
            f"{diagnosis.recommended_action}"
        )

    if (
        diagnosis.confidence
        not in ALLOWED_CONFIDENCE
    ):
        _fail(
            "unsupported diagnosis confidence: "
            f"{diagnosis.confidence}"
        )

    if (
        diagnosis.recommended_action
        in {
            ACTION_EVICT_POD,
            ACTION_ESCALATE,
        }
        and diagnosis.requires_human_approval
        is not True
    ):
        _fail(
            "EVICT_POD and ESCALATE must "
            "require human approval"
        )

    if (
        diagnosis.recommended_action
        == ACTION_NO_ACTION
        and diagnosis.requires_human_approval
        is not False
    ):
        _fail(
            "NO_ACTION must not require "
            "human approval"
        )

    if (
        diagnosis.recommended_action
        == ACTION_EVICT_POD
        and diagnosis.affected_resource.kind
        != "Pod"
    ):
        _fail(
            "EVICT_POD target_kind must be Pod"
        )


def _validate_safety_binding(
    diagnosis: DiagnosisResult,
    safety: SafetyDecision,
) -> None:
    if safety.schema_version != "v1alpha1":
        _fail(
            "unsupported SafetyDecision schema_version"
        )

    if (
        safety.decision
        not in ALLOWED_SAFETY_OUTCOMES
    ):
        _fail(
            "unsupported SafetyDecision decision: "
            f"{safety.decision}"
        )

    _non_empty(
        safety.decision_id,
        "safety.decision_id",
    )

    _reason_codes(
        safety.reason_codes,
        "safety.reason_codes",
    )

    target = diagnosis.affected_resource

    bindings = (
        (
            "incident_id",
            safety.incident_id,
            diagnosis.incident_id,
        ),
        (
            "action",
            safety.action,
            diagnosis.recommended_action,
        ),
        (
            "target_kind",
            safety.target_kind,
            target.kind,
        ),
        (
            "target_name",
            safety.target_name,
            target.name,
        ),
        (
            "target_uid",
            safety.target_uid,
            target.uid,
        ),
    )

    for field_name, actual, expected in bindings:
        if actual != expected:
            _fail(
                "SafetyDecision "
                f"{field_name} binding mismatch"
            )

    if (
        safety.decision == SAFETY_ALLOW
        and safety.human_approved is not True
    ):
        _fail(
            "SafetyDecision ALLOW requires "
            "human_approved=true"
        )

    if (
        safety.decision == SAFETY_REQUIRE_HUMAN
        and safety.human_approved is not False
    ):
        _fail(
            "REQUIRE_HUMAN requires "
            "human_approved=false"
        )


def _validate_execution_binding(
    diagnosis: DiagnosisResult,
    safety: SafetyDecision,
    execution: ExecutionResult,
) -> None:
    if execution.schema_version != "v1alpha1":
        _fail(
            "unsupported ExecutionResult schema_version"
        )

    if (
        execution.status
        not in ALLOWED_EXECUTION_STATUSES
    ):
        _fail(
            "unsupported ExecutionResult status: "
            f"{execution.status}"
        )

    _non_empty(
        execution.execution_id,
        "execution.execution_id",
    )

    _non_empty(
        execution.namespace,
        "execution.namespace",
    )

    _reason_codes(
        execution.reason_codes,
        "execution.reason_codes",
    )

    target = diagnosis.affected_resource

    bindings = (
        (
            "decision_id",
            execution.decision_id,
            safety.decision_id,
        ),
        (
            "incident_id",
            execution.incident_id,
            diagnosis.incident_id,
        ),
        (
            "action",
            execution.action,
            diagnosis.recommended_action,
        ),
        (
            "target_name",
            execution.target_name,
            target.name,
        ),
        (
            "target_uid",
            execution.target_uid,
            target.uid,
        ),
    )

    for field_name, actual, expected in bindings:
        if actual != expected:
            _fail(
                "ExecutionResult "
                f"{field_name} binding mismatch"
            )

    if execution.status == EXECUTION_ACCEPTED:
        if (
            safety.decision != SAFETY_ALLOW
            or safety.human_approved is not True
        ):
            _fail(
                "ACCEPTED execution requires "
                "approved SafetyDecision ALLOW"
            )

        if (
            "EVICTION_ACCEPTED"
            not in execution.reason_codes
        ):
            _fail(
                "ACCEPTED execution is missing "
                "EVICTION_ACCEPTED"
            )

        if (
            execution.api_status_code
            not in (200, 201, 202)
        ):
            _fail(
                "ACCEPTED execution has "
                "inconsistent api_status_code"
            )


def _validate_verification_binding(
    diagnosis: DiagnosisResult,
    execution: ExecutionResult,
    verification: VerificationResult,
) -> None:
    if verification.schema_version != "v1alpha1":
        _fail(
            "unsupported VerificationResult "
            "schema_version"
        )

    if (
        verification.outcome
        not in VERIFICATION_OUTCOMES
    ):
        _fail(
            "unsupported VerificationResult outcome: "
            f"{verification.outcome}"
        )

    _non_empty(
        verification.verification_id,
        "verification.verification_id",
    )

    _reason_codes(
        verification.reason_codes,
        "verification.reason_codes",
    )

    if (
        verification.incident_id
        != diagnosis.incident_id
    ):
        _fail(
            "VerificationResult incident_id "
            "binding mismatch"
        )

    if (
        verification.execution_id
        != execution.execution_id
    ):
        _fail(
            "VerificationResult execution_id "
            "binding mismatch"
        )

    if (
        verification.target_uid
        != diagnosis.affected_resource.uid
    ):
        _fail(
            "VerificationResult target_uid "
            "binding mismatch"
        )

    if verification.replacement_uid is not None:
        _non_empty(
            verification.replacement_uid,
            "verification.replacement_uid",
        )

    if (
        verification.outcome == FINAL_VERIFIED
        and verification.replacement_uid is None
    ):
        _fail(
            "VERIFIED VerificationResult "
            "requires replacement_uid"
        )


def build_experience_record(
    *,
    diagnosis: DiagnosisResult,
    safety_decision: SafetyDecision | None = None,
    execution_result: ExecutionResult | None = None,
    verification_result: VerificationResult | None = None,
    evidence_refs: tuple[str, ...],
    experience_id: str | None = None,
    created_at: str | None = None,
) -> ExperienceRecord:
    _validate_diagnosis(
        diagnosis
    )

    refs = _artifact_refs(
        evidence_refs
    )

    if (
        diagnosis.recommended_action
        == ACTION_EVICT_POD
        and safety_decision is None
    ):
        _fail(
            "EVICT_POD experience requires "
            "SafetyDecision"
        )

    if safety_decision is not None:
        _validate_safety_binding(
            diagnosis,
            safety_decision,
        )

    if execution_result is not None:
        if safety_decision is None:
            _fail(
                "ExecutionResult requires "
                "SafetyDecision"
            )

        if (
            diagnosis.recommended_action
            != ACTION_EVICT_POD
        ):
            _fail(
                "only EVICT_POD may have "
                "an ExecutionResult"
            )

        _validate_execution_binding(
            diagnosis,
            safety_decision,
            execution_result,
        )

    if execution_result is None:
        if verification_result is not None:
            _fail(
                "VerificationResult requires "
                "ExecutionResult"
            )

        if (
            safety_decision is not None
            and safety_decision.decision
            == SAFETY_ALLOW
        ):
            _fail(
                "SafetyDecision ALLOW without "
                "ExecutionResult is incomplete "
                "and must fail closed"
            )

    if verification_result is not None:
        if execution_result is None:
            _fail(
                "VerificationResult requires "
                "ExecutionResult"
            )

        if (
            execution_result.status
            != EXECUTION_ACCEPTED
        ):
            _fail(
                "VerificationResult requires "
                "ACCEPTED execution"
            )

        _validate_verification_binding(
            diagnosis,
            execution_result,
            verification_result,
        )

    if (
        execution_result is not None
        and execution_result.status
        == EXECUTION_ACCEPTED
    ):
        if verification_result is None:
            _fail(
                "ACCEPTED execution requires "
                "VerificationResult"
            )

        final_outcome = (
            verification_result.outcome
        )

    else:
        if verification_result is not None:
            _fail(
                "non-ACCEPTED path must not "
                "contain VerificationResult"
            )

        final_outcome = FINAL_NO_EXECUTION

    record_id = (
        _non_empty(
            experience_id,
            "experience_id",
        )
        if experience_id is not None
        else f"exp-{uuid4()}"
    )

    record_created_at = _timestamp(
        created_at or _utc_now(),
        "created_at",
    )

    return ExperienceRecord(
        experience_id=record_id,
        incident_id=diagnosis.incident_id,

        diagnosis_id=diagnosis.diagnosis_id,
        diagnosis_action=(
            diagnosis.recommended_action
        ),
        diagnosis_confidence=(
            diagnosis.confidence
        ),

        safety_decision_id=(
            safety_decision.decision_id
            if safety_decision is not None
            else None
        ),
        safety_outcome=(
            safety_decision.decision
            if safety_decision is not None
            else None
        ),

        execution_id=(
            execution_result.execution_id
            if execution_result is not None
            else None
        ),
        execution_status=(
            execution_result.status
            if execution_result is not None
            else None
        ),

        verification_id=(
            verification_result.verification_id
            if verification_result is not None
            else None
        ),
        verification_outcome=(
            verification_result.outcome
            if verification_result is not None
            else None
        ),

        target_kind=(
            diagnosis.affected_resource.kind
        ),
        target_name=(
            diagnosis.affected_resource.name
        ),
        target_uid=(
            diagnosis.affected_resource.uid
        ),

        replacement_uid=(
            verification_result.replacement_uid
            if verification_result is not None
            else None
        ),

        final_outcome=final_outcome,

        evidence_refs=refs,

        created_at=record_created_at,
    )
