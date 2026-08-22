from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from opslab_aiops.experience.models import (
    ExperienceRecord,
)
from opslab_aiops.experience.store import (
    DuplicateExperienceError,
    ExperienceStoreCorruptionError,
    ExperienceStoreError,
    JsonlExperienceStore,
)


def make_record(
    *,
    experience_id: str = "exp-test-001",
    incident_id: str = "inc-test-001",
    final_outcome: str = "VERIFIED",
) -> ExperienceRecord:
    if final_outcome == "NO_EXECUTION":
        return ExperienceRecord(
            experience_id=experience_id,
            incident_id=incident_id,

            diagnosis_id="diag-test-001",
            diagnosis_action="ESCALATE",
            diagnosis_confidence="MEDIUM",

            safety_decision_id=None,
            safety_outcome=None,

            execution_id=None,
            execution_status=None,

            verification_id=None,
            verification_outcome=None,

            target_kind="Pod",
            target_name="opslab-api-test",
            target_uid="target-uid-001",

            replacement_uid=None,

            final_outcome="NO_EXECUTION",

            evidence_refs=(
                "docs/hermes/evidence/phase6/"
                "test/diagnosis.json",
            ),

            created_at=(
                "2026-08-22T16:00:00Z"
            ),
        )

    verification_outcome = final_outcome

    replacement_uid = (
        "replacement-uid-001"
        if final_outcome
        in {
            "VERIFIED",
            "INCONCLUSIVE",
        }
        else None
    )

    return ExperienceRecord(
        experience_id=experience_id,
        incident_id=incident_id,

        diagnosis_id="diag-test-001",
        diagnosis_action="EVICT_POD",
        diagnosis_confidence="HIGH",

        safety_decision_id="safety-test-001",
        safety_outcome="ALLOW",

        execution_id="exec-test-001",
        execution_status="ACCEPTED",

        verification_id="verify-test-001",
        verification_outcome=(
            verification_outcome
        ),

        target_kind="Pod",
        target_name="opslab-api-test",
        target_uid="target-uid-001",

        replacement_uid=replacement_uid,

        final_outcome=final_outcome,

        evidence_refs=(
            "docs/hermes/evidence/phase6/"
            "test/verification.json",
        ),

        created_at=(
            "2026-08-22T16:00:00Z"
        ),
    )


class ExperienceStoreTests(
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

    def test_missing_store_loads_empty(
        self,
    ) -> None:
        self.assertEqual(
            self.store.load_all(),
            (),
        )

    def test_append_and_reload_verified_record(
        self,
    ) -> None:
        record = make_record()

        self.store.append(record)

        loaded = self.store.load_all()

        self.assertEqual(
            loaded,
            (record,),
        )

        raw = self.path.read_bytes()

        self.assertTrue(
            raw.endswith(b"\n")
        )

        self.assertEqual(
            len(raw.splitlines()),
            1,
        )

    def test_append_and_reload_no_execution_record(
        self,
    ) -> None:
        record = make_record(
            final_outcome="NO_EXECUTION",
        )

        self.store.append(record)

        self.assertEqual(
            self.store.load_all(),
            (record,),
        )

    def test_multiple_records_preserve_order(
        self,
    ) -> None:
        first = make_record(
            experience_id="exp-001",
            incident_id="inc-001",
        )

        second = make_record(
            experience_id="exp-002",
            incident_id="inc-002",
            final_outcome="NO_EXECUTION",
        )

        self.store.append(first)
        self.store.append(second)

        self.assertEqual(
            self.store.load_all(),
            (first, second),
        )

    def test_old_record_bytes_are_not_changed(
        self,
    ) -> None:
        first = make_record(
            experience_id="exp-001",
        )

        second = make_record(
            experience_id="exp-002",
            incident_id="inc-002",
            final_outcome="NO_EXECUTION",
        )

        self.store.append(first)

        before = self.path.read_bytes()

        self.store.append(second)

        after = self.path.read_bytes()

        self.assertTrue(
            after.startswith(before)
        )

        self.assertEqual(
            len(after.splitlines()),
            2,
        )

    def test_get_by_experience_id(
        self,
    ) -> None:
        record = make_record()

        self.store.append(record)

        self.assertEqual(
            self.store.get(
                record.experience_id
            ),
            record,
        )

        self.assertIsNone(
            self.store.get("exp-missing")
        )

    def test_find_by_incident_id(
        self,
    ) -> None:
        first = make_record(
            experience_id="exp-001",
            incident_id="inc-shared",
        )

        second = make_record(
            experience_id="exp-002",
            incident_id="inc-other",
            final_outcome="NO_EXECUTION",
        )

        third = replace(
            make_record(
                experience_id="exp-003",
                incident_id="inc-shared",
            ),
            execution_id="exec-test-003",
            verification_id="verify-test-003",
        )

        self.store.append(first)
        self.store.append(second)
        self.store.append(third)

        self.assertEqual(
            self.store.find_by_incident_id(
                "inc-shared"
            ),
            (first, third),
        )

    def test_duplicate_experience_is_rejected(
        self,
    ) -> None:
        record = make_record()

        self.store.append(record)

        before = self.path.read_bytes()

        with self.assertRaises(
            DuplicateExperienceError
        ):
            self.store.append(record)

        self.assertEqual(
            self.path.read_bytes(),
            before,
        )

    def test_existing_duplicate_ids_fail_closed(
        self,
    ) -> None:
        record = make_record()

        line = (
            json.dumps(
                record.to_dict(),
                sort_keys=True,
            )
            + "\n"
        )

        self.path.write_text(
            line + line,
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ExperienceStoreCorruptionError,
            "duplicate experience_id",
        ):
            self.store.load_all()

    def test_malformed_json_fails_closed(
        self,
    ) -> None:
        self.path.write_text(
            '{"experience_id":',
            encoding="utf-8",
        )

        with self.assertRaises(
            ExperienceStoreCorruptionError
        ):
            self.store.load_all()

    def test_truncated_valid_json_fails_closed(
        self,
    ) -> None:
        record = make_record()

        # Valid JSON, but deliberately no final
        # newline. Store-produced records always end
        # in newline, so this is treated as truncation.
        self.path.write_text(
            json.dumps(
                record.to_dict(),
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ExperienceStoreCorruptionError,
            "truncated",
        ):
            self.store.load_all()

    def test_blank_line_fails_closed(
        self,
    ) -> None:
        record = make_record()

        self.path.write_text(
            json.dumps(
                record.to_dict(),
                sort_keys=True,
            )
            + "\n\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ExperienceStoreCorruptionError,
            "blank line",
        ):
            self.store.load_all()

    def test_unknown_field_fails_closed(
        self,
    ) -> None:
        payload = make_record().to_dict()

        payload["token"] = "must-not-exist"

        self.path.write_text(
            json.dumps(
                payload,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ExperienceStoreCorruptionError,
            "unknown fields",
        ):
            self.store.load_all()

    def test_missing_field_fails_closed(
        self,
    ) -> None:
        payload = make_record().to_dict()

        del payload["incident_id"]

        self.path.write_text(
            json.dumps(
                payload,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ExperienceStoreCorruptionError,
            "missing fields",
        ):
            self.store.load_all()

    def test_schema_mismatch_fails_closed(
        self,
    ) -> None:
        payload = make_record().to_dict()

        payload["schema_version"] = "v9"

        self.path.write_text(
            json.dumps(
                payload,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ExperienceStoreCorruptionError,
            "schema_version",
        ):
            self.store.load_all()

    def test_incomplete_execution_fields_fail_closed(
        self,
    ) -> None:
        payload = make_record().to_dict()

        payload["execution_id"] = None

        self.path.write_text(
            json.dumps(
                payload,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ExperienceStoreCorruptionError,
            "execution fields",
        ):
            self.store.load_all()

    def test_accepted_without_verification_fails_closed(
        self,
    ) -> None:
        payload = make_record().to_dict()

        payload["verification_id"] = None
        payload["verification_outcome"] = None
        payload["replacement_uid"] = None
        payload["final_outcome"] = (
            "NO_EXECUTION"
        )

        self.path.write_text(
            json.dumps(
                payload,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ExperienceStoreCorruptionError,
            "ACCEPTED execution requires",
        ):
            self.store.load_all()

    def test_final_outcome_must_match_verification(
        self,
    ) -> None:
        payload = make_record().to_dict()

        payload["final_outcome"] = (
            "NOT_RECOVERED"
        )

        self.path.write_text(
            json.dumps(
                payload,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ExperienceStoreCorruptionError,
            "final_outcome",
        ):
            self.store.load_all()

    def test_corrupt_existing_store_blocks_append(
        self,
    ) -> None:
        self.path.write_text(
            '{"broken":true}\n',
            encoding="utf-8",
        )

        before = self.path.read_bytes()

        with self.assertRaises(
            ExperienceStoreCorruptionError
        ):
            self.store.append(
                make_record()
            )

        self.assertEqual(
            self.path.read_bytes(),
            before,
        )

    def test_failed_atomic_replace_preserves_old_store(
        self,
    ) -> None:
        first = make_record(
            experience_id="exp-001",
        )

        second = make_record(
            experience_id="exp-002",
            incident_id="inc-002",
            final_outcome="NO_EXECUTION",
        )

        self.store.append(first)

        before = self.path.read_bytes()

        with mock.patch(
            "opslab_aiops.experience.store."
            "os.replace",
            side_effect=OSError(
                "simulated replace failure"
            ),
        ):
            with self.assertRaises(
                ExperienceStoreError
            ):
                self.store.append(second)

        self.assertEqual(
            self.path.read_bytes(),
            before,
        )

        temp_files = tuple(
            self.path.parent.glob(
                f".{self.path.name}.*.tmp"
            )
        )

        self.assertEqual(
            temp_files,
            (),
        )

    def test_store_creates_parent_directory(
        self,
    ) -> None:
        nested_path = (
            Path(self.tmp.name)
            / "nested"
            / "runtime"
            / "experiences.jsonl"
        )

        store = JsonlExperienceStore(
            nested_path
        )

        store.append(
            make_record()
        )

        self.assertTrue(
            nested_path.exists()
        )

    def test_get_rejects_empty_id(
        self,
    ) -> None:
        with self.assertRaises(
            ValueError
        ):
            self.store.get("")

    def test_find_rejects_empty_incident_id(
        self,
    ) -> None:
        with self.assertRaises(
            ValueError
        ):
            self.store.find_by_incident_id(
                ""
            )


if __name__ == "__main__":
    unittest.main()
