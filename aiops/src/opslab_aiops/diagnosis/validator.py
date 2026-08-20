from __future__ import annotations

import json
import re
from typing import Any

from opslab_aiops.context.models import IncidentContext

from .models import (
    AffectedResource,
    DiagnosisResult,
)


ALLOWED_CONFIDENCE = frozenset(
    {
        "LOW",
        "MEDIUM",
        "HIGH",
    }
)

ALLOWED_ACTIONS = frozenset(
    {
        "NO_ACTION",
        "EVICT_POD",
        "ESCALATE",
    }
)


class DiagnosisValidationError(ValueError):
    pass


TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "diagnosis_id",
        "incident_id",
        "summary",
        "root_cause",
        "confidence",
        "evidence_refs",
        "affected_resource",
        "recommended_action",
        "rationale",
        "risk_notes",
        "requires_human_approval",
    }
)


AFFECTED_RESOURCE_KEYS = frozenset(
    {
        "kind",
        "name",
        "uid",
    }
)


UNSAFE_TEXT_PATTERNS = (
    re.compile(r"\bkubectl\b", re.IGNORECASE),
    re.compile(r"\bssh\b", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(
        r"\b(?:bash|sh|zsh)\s+-c\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:curl|wget)\b[^\n]*"
        r"(?:-X|--request)\s*"
        r"(?:POST|PUT|PATCH|DELETE)\b",
        re.IGNORECASE,
    ),
)


def _require_object(
    value: Any,
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DiagnosisValidationError(
            f"{field_name} must be an object"
        )

    return value


def _require_string(
    value: Any,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        raise DiagnosisValidationError(
            f"{field_name} must be a string"
        )

    value = value.strip()

    if not value:
        raise DiagnosisValidationError(
            f"{field_name} must not be empty"
        )

    return value


def _require_bool(
    value: Any,
    field_name: str,
) -> bool:
    if not isinstance(value, bool):
        raise DiagnosisValidationError(
            f"{field_name} must be a boolean"
        )

    return value


def _require_string_tuple(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise DiagnosisValidationError(
            f"{field_name} must be a JSON array"
        )

    result = tuple(
        _require_string(
            item,
            f"{field_name}[]",
        )
        for item in value
    )

    if not allow_empty and not result:
        raise DiagnosisValidationError(
            f"{field_name} must not be empty"
        )

    if len(result) != len(set(result)):
        raise DiagnosisValidationError(
            f"{field_name} contains duplicate values"
        )

    return result


def _validate_exact_keys(
    value: dict[str, Any],
    expected: frozenset[str],
    field_name: str,
) -> None:
    actual = frozenset(value)

    missing = expected - actual
    unknown = actual - expected

    if missing:
        raise DiagnosisValidationError(
            f"{field_name} missing fields: "
            + ",".join(sorted(missing))
        )

    if unknown:
        raise DiagnosisValidationError(
            f"{field_name} contains unknown fields: "
            + ",".join(sorted(unknown))
        )


def _validate_safe_free_text(
    values: tuple[str, ...],
) -> None:
    for value in values:
        for pattern in UNSAFE_TEXT_PATTERNS:
            if pattern.search(value):
                raise DiagnosisValidationError(
                    "diagnosis contains prohibited executable content"
                )


def parse_and_validate_diagnosis(
    raw_response: str,
    context: IncidentContext,
) -> DiagnosisResult:
    try:
        decoded = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise DiagnosisValidationError(
            f"invalid diagnosis JSON: {exc.msg}"
        ) from exc

    payload = _require_object(
        decoded,
        "DiagnosisResult",
    )

    _validate_exact_keys(
        payload,
        TOP_LEVEL_KEYS,
        "DiagnosisResult",
    )

    schema_version = _require_string(
        payload["schema_version"],
        "schema_version",
    )

    if schema_version != "v1alpha1":
        raise DiagnosisValidationError(
            "unsupported DiagnosisResult schema_version"
        )

    diagnosis_id = _require_string(
        payload["diagnosis_id"],
        "diagnosis_id",
    )

    incident_id = _require_string(
        payload["incident_id"],
        "incident_id",
    )

    if incident_id != context.incident_id:
        raise DiagnosisValidationError(
            "diagnosis incident_id does not match IncidentContext"
        )

    summary = _require_string(
        payload["summary"],
        "summary",
    )

    root_cause = _require_string(
        payload["root_cause"],
        "root_cause",
    )

    confidence = _require_string(
        payload["confidence"],
        "confidence",
    )

    if confidence not in ALLOWED_CONFIDENCE:
        raise DiagnosisValidationError(
            f"unsupported confidence: {confidence}"
        )

    evidence_refs = _require_string_tuple(
        payload["evidence_refs"],
        "evidence_refs",
        allow_empty=False,
    )

    evidence_by_id = {
        evidence.evidence_id: evidence
        for evidence in context.evidence
    }

    unknown_evidence = (
        set(evidence_refs)
        - set(evidence_by_id)
    )

    if unknown_evidence:
        raise DiagnosisValidationError(
            "diagnosis references unknown Evidence IDs: "
            + ",".join(sorted(unknown_evidence))
        )

    # A diagnosis must contain at least one current-state fact.
    if not any(
        evidence_by_id[evidence_id].category
        == "CURRENT_STATE"
        for evidence_id in evidence_refs
    ):
        raise DiagnosisValidationError(
            "diagnosis must reference CURRENT_STATE evidence"
        )

    affected_payload = _require_object(
        payload["affected_resource"],
        "affected_resource",
    )

    _validate_exact_keys(
        affected_payload,
        AFFECTED_RESOURCE_KEYS,
        "affected_resource",
    )

    affected_resource = AffectedResource(
        kind=_require_string(
            affected_payload["kind"],
            "affected_resource.kind",
        ),
        name=_require_string(
            affected_payload["name"],
            "affected_resource.name",
        ),
        uid=_require_string(
            affected_payload["uid"],
            "affected_resource.uid",
        ),
    )

    # Phase 2 v1 only accepts diagnosis for the detected target.
    if (
        affected_resource.kind
        != context.trigger.target_kind
        or affected_resource.name
        != context.trigger.target_name
        or affected_resource.uid
        != context.trigger.target_uid
    ):
        raise DiagnosisValidationError(
            "affected_resource does not match incident target"
        )

    recommended_action = _require_string(
        payload["recommended_action"],
        "recommended_action",
    )

    if recommended_action not in ALLOWED_ACTIONS:
        raise DiagnosisValidationError(
            f"unsupported recommended_action: "
            f"{recommended_action}"
        )

    rationale = _require_string(
        payload["rationale"],
        "rationale",
    )

    risk_notes = _require_string_tuple(
        payload["risk_notes"],
        "risk_notes",
        allow_empty=True,
    )

    requires_human_approval = _require_bool(
        payload["requires_human_approval"],
        "requires_human_approval",
    )

    if (
        recommended_action
        in {"EVICT_POD", "ESCALATE"}
        and not requires_human_approval
    ):
        raise DiagnosisValidationError(
            f"{recommended_action} requires human approval"
        )

    if (
        recommended_action == "NO_ACTION"
        and requires_human_approval
    ):
        raise DiagnosisValidationError(
            "NO_ACTION must not require human approval"
        )

    _validate_safe_free_text(
        (
            summary,
            root_cause,
            rationale,
            *risk_notes,
        )
    )

    return DiagnosisResult(
        diagnosis_id=diagnosis_id,
        incident_id=incident_id,
        summary=summary,
        root_cause=root_cause,
        confidence=confidence,
        evidence_refs=evidence_refs,
        affected_resource=affected_resource,
        recommended_action=recommended_action,
        rationale=rationale,
        risk_notes=risk_notes,
        requires_human_approval=requires_human_approval,
    )
