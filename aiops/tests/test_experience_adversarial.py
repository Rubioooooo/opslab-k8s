from __future__ import annotations

import json
import multiprocessing
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from opslab_aiops.diagnosis.models import (
    AffectedResource,
    DiagnosisResult,
)
from opslab_aiops.execution.models import (
    ExecutionResult,
)
from opslab_aiops.experience.models import (
    ExperienceRecord,
    ExperienceValidationError,
    build_experience_record,
)
from opslab_aiops.experience.store import (
    ExperienceStoreCorruptionError,
    JsonlExperienceStore,
)
from opslab_aiops.safety.models import (
    SafetyDecision,
)
from opslab_aiops.verification.models import (
    VerificationResult,
)


INCIDENT_ID = "inc-adv-001"
DIAGNOSIS_ID = "diag-adv-001"

TARGET_NAME = "opslab-api-adv"
TARGET_UID = "target-adv-uid"

DECISION_ID = "safety-adv-001"
EXECUTION_ID = "exec-adv-001"
VERIFICATION_ID = "verify-adv-001"

REPLACEMENT_UID = "replacement-adv-uid"

ARTIFACT_REFS = (
    "docs/hermes/evidence/phase6/"
    "adversarial/source.json",
)


def make_diagnosis() -> DiagnosisResult:
    return DiagnosisResult(
        diagnosis_id=DIAGNOSIS_ID,
        incident_id=INCIDENT_ID,

        summary="adversarial test",
        root_cause="bounded test hypothesis",
        confidence="HIGH",

        evidence_refs=(
            "EV-0001",
        ),

        affected_resource=AffectedResource(
            kind="Pod",
            name=TARGET_NAME,
            uid=TARGET_UID,
        ),

        recommended_action="EVICT_POD",
        rationale="bounded remediation",
        risk_notes=(),

        requires_human_approval=True,
    )


def make_safety() -> SafetyDecision:
    return SafetyDecision(
        decision_id=DECISION_ID,
        incident_id=INCIDENT_ID,
        action="EVICT_POD",

        target_kind="Pod",
        target_name=TARGET_NAME,
        target_uid=TARGET_UID,

        decision="ALLOW",

        reason_codes=(
            "ALL_SAFETY_CHECKS_PASSED",
        ),

        evaluated_at=(
            "2026-08-22T15:48:28Z"
        ),

        fresh_snapshot_id="obs-adv-001",

        human_approved=True,
    )


def make_execution() -> ExecutionResult:
    return ExecutionResult(
        execution_id=EXECUTION_ID,

        decision_id=DECISION_ID,
        incident_id=INCIDENT_ID,

        action="EVICT_POD",
        namespace="opslab",

        target_name=TARGET_NAME,
        target_uid=TARGET_UID,

        status="ACCEPTED",

        reason_codes=(
            "EVICTION_ACCEPTED",
        ),

        attempted_at=(
            "2026-08-22T15:48:29Z"
        ),

        api_status_code=201,
    )


def make_verification() -> VerificationResult:
    return VerificationResult(
        verification_id=VERIFICATION_ID,

        incident_id=INCIDENT_ID,
        execution_id=EXECUTION_ID,

        target_uid=TARGET_UID,
        replacement_uid=REPLACEMENT_UID,

        outcome="VERIFIED",

        reason_codes=(
            "ALL_VERIFICATION_CHECKS_PASSED",
        ),

        verified_at=(
            "2026-08-22T15:48:39Z"
        ),

        baseline_snapshot_id="obs-adv-001",
        fresh_snapshot_id="obs-adv-002",

        evidence_refs=(
            "baseline.json",
        ),
    )


def build_verified() -> ExperienceRecord:
    return build_experience_record(
        diagnosis=make_diagnosis(),
        safety_decision=make_safety(),
        execution_result=make_execution(),
        verification_result=(
            make_verification()
        ),
        evidence_refs=ARTIFACT_REFS,
        experience_id="exp-adv-001",
        created_at=(
            "2026-08-22T16:00:00Z"
        ),
    )


def make_concurrent_record(
    index: int,
) -> ExperienceRecord:
    suffix = f"{index:03d}"

    return ExperienceRecord(
        experience_id=f"exp-concurrent-{suffix}",
        incident_id=f"inc-concurrent-{suffix}",

        diagnosis_id=f"diag-concurrent-{suffix}",
        diagnosis_action="EVICT_POD",
        diagnosis_confidence="HIGH",

        safety_decision_id=(
            f"safety-concurrent-{suffix}"
        ),
        safety_outcome="ALLOW",

        execution_id=f"exec-concurrent-{suffix}",
        execution_status="ACCEPTED",

        verification_id=(
            f"verify-concurrent-{suffix}"
        ),
        verification_outcome="VERIFIED",

        target_kind="Pod",
        target_name=(
            f"opslab-api-concurrent-{suffix}"
        ),
        target_uid=f"target-concurrent-{suffix}",

        replacement_uid=(
            f"replacement-concurrent-{suffix}"
        ),

        final_outcome="VERIFIED",

        evidence_refs=(
            "docs/hermes/evidence/phase6/"
            f"concurrent/{suffix}.json",
        ),

        created_at=(
            "2026-08-22T16:00:00Z"
        ),
    )


def concurrent_append_worker(
    store_path: str,
    index: int,
) -> None:
    store = JsonlExperienceStore(
        store_path
    )

    store.append(
        make_concurrent_record(index)
    )


class ExperienceBindingAdversarialTests(
    unittest.TestCase
):
    def build(
        self,
        *,
        diagnosis: DiagnosisResult | None = None,
        safety: SafetyDecision | None = None,
        execution: ExecutionResult | None = None,
        verification: VerificationResult | None = None,
    ) -> ExperienceRecord:
        return build_experience_record(
            diagnosis=(
                diagnosis
                or make_diagnosis()
            ),
            safety_decision=(
                safety
                or make_safety()
            ),
            execution_result=(
                execution
                or make_execution()
            ),
            verification_result=(
                verification
                or make_verification()
            ),
            evidence_refs=ARTIFACT_REFS,
            experience_id="exp-adv-001",
            created_at=(
                "2026-08-22T16:00:00Z"
            ),
        )

    def test_safety_target_name_mismatch_is_rejected(
        self,
    ) -> None:
        safety = replace(
            make_safety(),
            target_name="wrong-pod",
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "target_name binding mismatch",
        ):
            self.build(
                safety=safety
            )

    def test_safety_action_mismatch_is_rejected(
        self,
    ) -> None:
        safety = replace(
            make_safety(),
            action="NO_ACTION",
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "action binding mismatch",
        ):
            self.build(
                safety=safety
            )

    def test_execution_decision_id_mismatch_is_rejected(
        self,
    ) -> None:
        execution = replace(
            make_execution(),
            decision_id="safety-forged",
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "decision_id binding mismatch",
        ):
            self.build(
                execution=execution
            )

    def test_execution_incident_mismatch_is_rejected(
        self,
    ) -> None:
        execution = replace(
            make_execution(),
            incident_id="inc-forged",
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "incident_id binding mismatch",
        ):
            self.build(
                execution=execution
            )

    def test_verification_incident_mismatch_is_rejected(
        self,
    ) -> None:
        verification = replace(
            make_verification(),
            incident_id="inc-forged",
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "incident_id binding mismatch",
        ):
            self.build(
                verification=verification
            )

    def test_verification_execution_id_mismatch_is_rejected(
        self,
    ) -> None:
        verification = replace(
            make_verification(),
            execution_id="exec-forged",
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "execution_id binding mismatch",
        ):
            self.build(
                verification=verification
            )

    def test_verification_target_uid_mismatch_is_rejected(
        self,
    ) -> None:
        verification = replace(
            make_verification(),
            target_uid="target-forged",
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "target_uid binding mismatch",
        ):
            self.build(
                verification=verification
            )

    def test_accepted_execution_without_acceptance_reason_is_rejected(
        self,
    ) -> None:
        execution = replace(
            make_execution(),
            reason_codes=(
                "FORGED_ACCEPTANCE",
            ),
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "EVICTION_ACCEPTED",
        ):
            self.build(
                execution=execution
            )

    def test_accepted_execution_with_bad_api_status_is_rejected(
        self,
    ) -> None:
        execution = replace(
            make_execution(),
            api_status_code=500,
        )

        with self.assertRaisesRegex(
            ExperienceValidationError,
            "api_status_code",
        ):
            self.build(
                execution=execution
            )

    def test_verified_without_replacement_identity_is_rejected(
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
                verification=verification
            )


class ExperienceStoreAdversarialTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.tmp = (
            tempfile.TemporaryDirectory()
        )

        self.addCleanup(
            self.tmp.cleanup
        )

        self.path = (
            Path(self.tmp.name)
            / "experiences.jsonl"
        )

        self.store = JsonlExperienceStore(
            self.path
        )

    def write_payload(
        self,
        payload: dict[str, object],
    ) -> None:
        self.path.write_text(
            json.dumps(
                payload,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_forged_verified_with_refused_execution_is_rejected(
        self,
    ) -> None:
        payload = build_verified().to_dict()

        payload["execution_status"] = (
            "REFUSED"
        )

        self.write_payload(payload)

        with self.assertRaises(
            ExperienceStoreCorruptionError
        ):
            self.store.load_all()

    def test_forged_no_execution_with_verification_is_rejected(
        self,
    ) -> None:
        payload = build_verified().to_dict()

        payload["execution_id"] = None
        payload["execution_status"] = None
        payload["final_outcome"] = (
            "NO_EXECUTION"
        )

        self.write_payload(payload)

        with self.assertRaises(
            ExperienceStoreCorruptionError
        ):
            self.store.load_all()

    def test_forged_no_execution_with_replacement_uid_is_rejected(
        self,
    ) -> None:
        record = ExperienceRecord(
            experience_id="exp-no-exec",
            incident_id="inc-no-exec",

            diagnosis_id="diag-no-exec",
            diagnosis_action="ESCALATE",
            diagnosis_confidence="MEDIUM",

            safety_decision_id=None,
            safety_outcome=None,

            execution_id=None,
            execution_status=None,

            verification_id=None,
            verification_outcome=None,

            target_kind="Pod",
            target_name="opslab-api",
            target_uid="target-no-exec",

            replacement_uid="forged-replacement",

            final_outcome="NO_EXECUTION",

            evidence_refs=(
                "docs/hermes/evidence/phase6/"
                "forged/no-execution.json",
            ),

            created_at=(
                "2026-08-22T16:00:00Z"
            ),
        )

        self.write_payload(
            record.to_dict()
        )

        with self.assertRaisesRegex(
            ExperienceStoreCorruptionError,
            "replacement_uid",
        ):
            self.store.load_all()

    def test_url_evidence_reference_is_rejected(
        self,
    ) -> None:
        payload = build_verified().to_dict()

        payload["evidence_refs"] = [
            "https://example.invalid/evidence.json"
        ]

        self.write_payload(payload)

        with self.assertRaisesRegex(
            ExperienceStoreCorruptionError,
            "URLs",
        ):
            self.store.load_all()

    def test_absolute_evidence_reference_is_rejected(
        self,
    ) -> None:
        payload = build_verified().to_dict()

        payload["evidence_refs"] = [
            "/tmp/evidence.json"
        ]

        self.write_payload(payload)

        with self.assertRaisesRegex(
            ExperienceStoreCorruptionError,
            "relative",
        ):
            self.store.load_all()

    def test_malformed_middle_record_blocks_entire_store(
        self,
    ) -> None:
        first = make_concurrent_record(1)
        third = make_concurrent_record(3)

        content = (
            json.dumps(
                first.to_dict(),
                sort_keys=True,
            )
            + "\n"
            + '{"broken":'
            + "\n"
            + json.dumps(
                third.to_dict(),
                sort_keys=True,
            )
            + "\n"
        )

        self.path.write_text(
            content,
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ExperienceStoreCorruptionError,
            "line 2",
        ):
            self.store.load_all()

        with self.assertRaises(
            ExperienceStoreCorruptionError
        ):
            self.store.get(
                first.experience_id
            )

    def test_corrupt_store_blocks_query_by_incident(
        self,
    ) -> None:
        self.path.write_text(
            '{"broken":true}\n',
            encoding="utf-8",
        )

        with self.assertRaises(
            ExperienceStoreCorruptionError
        ):
            self.store.find_by_incident_id(
                "inc-any"
            )

    def test_concurrent_process_writers_preserve_all_records(
        self,
    ) -> None:
        process_count = 12

        context = (
            multiprocessing.get_context(
                "fork"
            )
        )

        processes = tuple(
            context.Process(
                target=concurrent_append_worker,
                args=(
                    str(self.path),
                    index,
                ),
            )
            for index in range(
                process_count
            )
        )

        for process in processes:
            process.start()

        for process in processes:
            process.join(timeout=10)

        for process in processes:
            self.assertFalse(
                process.is_alive(),
                "concurrent writer did not exit",
            )

            self.assertEqual(
                process.exitcode,
                0,
                "concurrent writer failed",
            )

        records = self.store.load_all()

        self.assertEqual(
            len(records),
            process_count,
        )

        ids = {
            record.experience_id
            for record in records
        }

        self.assertEqual(
            len(ids),
            process_count,
        )

        raw = self.path.read_bytes()

        self.assertTrue(
            raw.endswith(b"\n")
        )

        self.assertEqual(
            len(raw.splitlines()),
            process_count,
        )


if __name__ == "__main__":
    unittest.main()
