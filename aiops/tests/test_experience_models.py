from __future__ import annotations

import json
import unittest
from dataclasses import replace

from opslab_aiops.diagnosis.models import (
    AffectedResource,
    DiagnosisResult,
)
from opslab_aiops.execution.models import (
    ExecutionResult,
)
from opslab_aiops.experience.models import (
    FINAL_INCONCLUSIVE,
    FINAL_NOT_RECOVERED,
    FINAL_NO_EXECUTION,
    FINAL_VERIFIED,
    ExperienceValidationError,
    build_experience_record,
)
from opslab_aiops.safety.models import (
    SafetyDecision,
)
from opslab_aiops.verification.models import (
    VerificationResult,
)


INCIDENT_ID = "inc-test-001"

TARGET_NAME = "opslab-api-test"
TARGET_UID = "target-uid-001"

DECISION_ID = "safety-test-001"
EXECUTION_ID = "exec-test-001"
VERIFICATION_ID = "verify-test-001"

REPLACEMENT_UID = "replacement-uid-001"

EVIDENCE_REFS = (
    "docs/hermes/evidence/phase6/test/"
    "incident-context.json",
    "docs/hermes/evidence/phase6/test/"
    "diagnosis.json",
)


def make_diagnosis(
    *,
    action: str = "EVICT_POD",
) -> DiagnosisResult:
    return DiagnosisResult(
        diagnosis_id="diag-test-001",
        incident_id=INCIDENT_ID,

        summary="test summary",
        root_cause="test root cause",
        confidence="HIGH",

        # DiagnosisResult keeps its own IncidentContext-local
        # Evidence IDs. Experience evidence_refs are different:
        # they point to artifacts.
        evidence_refs=("EV-0001",),

        affected_resource=AffectedResource(
            kind="Pod",
            name=TARGET_NAME,
            uid=TARGET_UID,
        ),

        recommended_action=action,
        rationale="test rationale",
        risk_notes=(),

        requires_human_approval=(
            action in {
                "EVICT_POD",
                "ESCALATE",
            }
        ),
    )


def make_safety(
    *,
    decision: str = "ALLOW",
) -> SafetyDecision:
    if decision == "ALLOW":
        reason_codes = (
            "ALL_SAFETY_CHECKS_PASSED",
        )
        human_approved = True

    elif decision == "REQUIRE_HUMAN":
        reason_codes = (
            "HUMAN_APPROVAL_REQUIRED",
        )
        human_approved = False

    else:
        reason_codes = (
            "DENY_TEST",
        )
        human_approved = False

    return SafetyDecision(
        decision_id=DECISION_ID,
        incident_id=INCIDENT_ID,
        action="EVICT_POD",

        target_kind="Pod",
        target_name=TARGET_NAME,
        target_uid=TARGET_UID,

        decision=decision,
        reason_codes=reason_codes,

        evaluated_at=(
            "2026-08-22T15:48:28Z"
        ),
        fresh_snapshot_id="obs-test-001",

        human_approved=human_approved,
    )


def make_execution(
    *,
    status: str = "ACCEPTED",
) -> ExecutionResult:
    if status == "ACCEPTED":
        reason_codes = (
            "EVICTION_ACCEPTED",
        )
        api_status_code = 201

    else:
        reason_codes = (
            "EVICTION_POLICY_BLOCKED",
        )
        api_status_code = 429

    return ExecutionResult(
        execution_id=EXECUTION_ID,

        decision_id=DECISION_ID,
        incident_id=INCIDENT_ID,

        action="EVICT_POD",
        namespace="opslab",

        target_name=TARGET_NAME,
        target_uid=TARGET_UID,

        status=status,
        reason_codes=reason_codes,

        attempted_at=(
            "2026-08-22T15:48:29Z"
        ),
        api_status_code=api_status_code,
    )


def make_verification(
    *,
    outcome: str = "VERIFIED",
) -> VerificationResult:
    if outcome == "VERIFIED":
        replacement_uid = REPLACEMENT_UID
        reason_codes = (
            "ALL_VERIFICATION_CHECKS_PASSED",
        )

    elif outcome == "INCONCLUSIVE":
        replacement_uid = REPLACEMENT_UID
        reason_codes = (
            "TEST_INCONCLUSIVE",
        )

    else:
        replacement_uid = None
        reason_codes = (
            "TEST_NOT_RECOVERED",
        )

    return VerificationResult(
        verification_id=VERIFICATION_ID,

        incident_id=INCIDENT_ID,
        execution_id=EXECUTION_ID,

        target_uid=TARGET_UID,
        replacement_uid=replacement_uid,

        outcome=outcome,
        reason_codes=reason_codes,

        verified_at=(
            "2026-08-22T15:48:39Z"
        ),

        baseline_snapshot_id="obs-test-001",
        fresh_snapshot_id="obs-test-002",

        # This is deliberately the VerificationResult's
        # existing evidence namespace.
        evidence_refs=(
            "baseline.json",
        ),
    )


class ExperienceModelsTests(
    unittest.TestCase
):
    def build(
        self,
        *,
        diagnosis: DiagnosisResult | None = None,
        safety: SafetyDecision | None = None,
        execution: ExecutionResult | None = None,
        verification: VerificationResult | None = None,
        evidence_refs: tuple[str, ...] = EVIDENCE_REFS,
    ):
        return build_experience_record(
            diagnosis=(
                diagnosis
                or make_diagnosis()
            ),
            safety_decision=safety,
            execution_result=execution,
            verification_result=verification,
            evidence_refs=evidence_refs,
            experience_id="exp-test-001",
            created_at=(
                "2026-08-22T16:00:00Z"
            ),
        )

    def test_verified_experience_is_built(
        self,
    ) -> None:
        record = self.build(
            safety=make_safety(),
            execution=make_execution(),
            verification=make_verification(),
        )

        self.assertEqual(
            record.final_outcome,
            FINAL_VERIFIED,
        )
        self.assertEqual(
            record.incident_id,
            INCIDENT_ID,
        )
        self.assertEqual(
            record.execution_id,
            EXECUTION_ID,
        )
        self.assertEqual(
            record.verification_id,
            VERIFICATION_ID,
        )
        self.assertEqual(
            record.replacement_uid,
            REPLACEMENT_UID,
        )

        payload = json.loads(
            record.to_json()
        )

        self.assertEqual(
            payload["schema_version"],
            "v1alpha1",
        )
        self.assertEqual(
            payload["final_outcome"],
            "VERIFIED",
        )

    def test_escalate_without_execution_is_valid(
        self,
    ) -> None:
        record = self.build(
            diagnosis=make_diagnosis(
                action="ESCALATE",
            ),
        )

        self.assertEqual(
            record.final_outcome,
            FINAL_NO_EXECUTION,
        )
        self.assertIsNone(
            record.safety_decision_id
        )
        self.assertIsNone(
            record.execution_id
        )
        self.assertIsNone(
            record.verification_id
        )

    def test_no_action_without_execution_is_valid(
        self,
    ) -> None:
        record = self.build(
            diagnosis=make_diagnosis(
                action="NO_ACTION",
            ),
        )

        self.assertEqual(
            record.final_outcome,
            FINAL_NO_EXECUTION,
        )

    def test_safety_deny_without_execution_is_valid(
        self,
    ) -> None:
        record = self.build(
            safety=make_safety(
                decision="DENY",
            ),
        )

        self.assertEqual(
            record.safety_outcome,
            "DENY",
        )
        self.assertEqual(
            record.final_outcome,
            FINAL_NO_EXECUTION,
        )

    def test_require_human_without_approval_is_valid(
        self,
    ) -> None:
        record = self.build(
            safety=make_safety(
                decision="REQUIRE_HUMAN",
            ),
        )

        self.assertEqual(
            record.safety_outcome,
            "REQUIRE_HUMAN",
        )
        self.assertEqual(
            record.final_outcome,
            FINAL_NO_EXECUTION,
        )

    def test_refused_execution_is_no_execution(
        self,
    ) -> None:
        record = self.build(
            safety=make_safety(),
            execution=make_execution(
                status="REFUSED",
            ),
        )

        self.assertEqual(
            record.execution_status,
            "REFUSED",
        )
        self.assertEqual(
            record.final_outcome,
            FINAL_NO_EXECUTION,
        )
        self.assertIsNone(
            record.verification_id
        )

    def test_not_recovered_is_preserved(
        self,
    ) -> None:
        record = self.build(
            safety=make_safety(),
            execution=make_execution(),
            verification=make_verification(
                outcome="NOT_RECOVERED",
            ),
        )

        self.assertEqual(
            record.final_outcome,
            FINAL_NOT_RECOVERED,
        )

    def test_inconclusive_is_preserved(
        self,
    ) -> None:
        record = self.build(
            safety=make_safety(),
            execution=make_execution(),
            verification=make_verification(
                outcome="INCONCLUSIVE",
            ),
        )

        self.assertEqual(
            record.final_outcome,
            FINAL_INCONCLUSIVE,
        )

    def test_allow_without_execution_fails_closed(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ExperienceValidationError,
            "ALLOW without ExecutionResult",
        ):
            self.build(
                safety=make_safety(),
            )

    def test_accepted_execution_without_verification_fails_closed(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ExperienceValidationError,
            "ACCEPTED execution requires "
            "VerificationResult",
        ):
            self.build(
                safety=make_safety(),
                execution=make_execution(),
            )

    def test_execution_without_safety_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ExperienceValidationError,
            "ExecutionResult requires "
            "SafetyDecision",
        ):
            self.build(
                diagnosis=make_diagnosis(
                    action="NO_ACTION",
                ),
                execution=make_execution(
                    status="REFUSED",
                ),
            )

    def test_safety_incident_mismatch_is_rejected(
        self,
    ) -> None:
        safety = replace(
            make_safety(
                decision="DENY",
            ),
            incident_id="inc-other",
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "SafetyDecision incident_id "
            "binding mismatch",
        ):
            self.build(
                safety=safety,
            )

    def test_execution_target_uid_mismatch_is_rejected(
        self,
    ) -> None:
        execution = replace(
            make_execution(
                status="REFUSED",
            ),
            target_uid="wrong-target-uid",
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "ExecutionResult target_uid "
            "binding mismatch",
        ):
            self.build(
                safety=make_safety(),
                execution=execution,
            )

    def test_verification_execution_id_mismatch_is_rejected(
        self,
    ) -> None:
        verification = replace(
            make_verification(),
            execution_id="exec-other",
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "VerificationResult execution_id "
            "binding mismatch",
        ):
            self.build(
                safety=make_safety(),
                execution=make_execution(),
                verification=verification,
            )

    def test_verified_without_replacement_uid_is_rejected(
        self,
    ) -> None:
        verification = replace(
            make_verification(),
            replacement_uid=None,
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "replacement_uid",
        ):
            self.build(
                safety=make_safety(),
                execution=make_execution(),
                verification=verification,
            )

    def test_bare_incident_evidence_id_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ExperienceValidationError,
            "artifact paths",
        ):
            self.build(
                safety=make_safety(
                    decision="DENY",
                ),
                evidence_refs=(
                    "EV-0001",
                ),
            )

    def test_bare_filename_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ExperienceValidationError,
            "artifact paths",
        ):
            self.build(
                safety=make_safety(
                    decision="DENY",
                ),
                evidence_refs=(
                    "09-execution-result.json",
                ),
            )

    def test_duplicate_evidence_refs_are_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ExperienceValidationError,
            "duplicate",
        ):
            self.build(
                safety=make_safety(
                    decision="DENY",
                ),
                evidence_refs=(
                    EVIDENCE_REFS[0],
                    EVIDENCE_REFS[0],
                ),
            )

    def test_unsupported_diagnosis_schema_is_rejected(
        self,
    ) -> None:
        diagnosis = replace(
            make_diagnosis(),
            schema_version="v9",
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "DiagnosisResult schema_version",
        ):
            self.build(
                diagnosis=diagnosis,
                safety=make_safety(
                    decision="DENY",
                ),
            )

    def test_invalid_created_at_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ExperienceValidationError,
            "ISO-8601",
        ):
            build_experience_record(
                diagnosis=make_diagnosis(),
                safety_decision=make_safety(
                    decision="DENY",
                ),
                evidence_refs=EVIDENCE_REFS,
                experience_id="exp-test-001",
                created_at="not-a-time",
            )


if __name__ == "__main__":
    unittest.main()
