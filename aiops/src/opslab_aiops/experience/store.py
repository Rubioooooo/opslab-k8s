from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import fields
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .models import (
    ALLOWED_ACTIONS,
    ALLOWED_CONFIDENCE,
    ALLOWED_EXECUTION_STATUSES,
    ALLOWED_SAFETY_OUTCOMES,
    EXPERIENCE_SCHEMA_VERSION,
    EXECUTION_ACCEPTED,
    FINAL_NO_EXECUTION,
    FINAL_VERIFIED,
    VERIFICATION_OUTCOMES,
    ExperienceRecord,
)


EXPERIENCE_RECORD_KEYS = frozenset(
    field.name
    for field in fields(ExperienceRecord)
)


class ExperienceStoreError(RuntimeError):
    pass


class ExperienceStoreCorruptionError(
    ExperienceStoreError
):
    pass


class DuplicateExperienceError(
    ExperienceStoreError
):
    pass


def _corrupt(message: str) -> None:
    raise ExperienceStoreCorruptionError(
        message
    )


def _require_string(
    value: object,
    field_name: str,
) -> str:
    if not isinstance(value, str):
        _corrupt(
            f"{field_name} must be a string"
        )

    normalized = value.strip()

    if not normalized:
        _corrupt(
            f"{field_name} must not be empty"
        )

    return normalized


def _optional_string(
    value: object,
    field_name: str,
) -> str | None:
    if value is None:
        return None

    return _require_string(
        value,
        field_name,
    )


def _require_timestamp(
    value: object,
    field_name: str,
) -> str:
    raw = _require_string(
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
        raise ExperienceStoreCorruptionError(
            f"{field_name} must be "
            "an ISO-8601 timestamp"
        ) from exc

    if parsed.tzinfo is None:
        _corrupt(
            f"{field_name} must include "
            "timezone information"
        )

    return raw


def _require_artifact_refs(
    value: object,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        _corrupt(
            "evidence_refs must be "
            "a JSON array"
        )

    if not value:
        _corrupt(
            "evidence_refs must not be empty"
        )

    refs: list[str] = []

    for index, raw in enumerate(value):
        ref = _require_string(
            raw,
            f"evidence_refs[{index}]",
        )

        if "\\" in ref:
            _corrupt(
                "evidence_refs must use "
                "POSIX-style paths"
            )

        if "://" in ref:
            _corrupt(
                "evidence_refs must not "
                "contain URLs"
            )

        path = PurePosixPath(ref)

        if path.is_absolute():
            _corrupt(
                "evidence_refs must be "
                "relative paths"
            )

        if len(path.parts) < 2:
            _corrupt(
                "evidence_refs must be "
                "artifact paths"
            )

        if any(
            part in {".", ".."}
            for part in path.parts
        ):
            _corrupt(
                "evidence_refs must not "
                "contain dot segments"
            )

        if ":" in path.parts[0]:
            _corrupt(
                "evidence_refs must not "
                "use drive-qualified paths"
            )

        if str(path) != ref:
            _corrupt(
                "evidence_refs must be "
                "normalized paths"
            )

        refs.append(ref)

    if len(refs) != len(set(refs)):
        _corrupt(
            "evidence_refs contains "
            "duplicate values"
        )

    return tuple(refs)


def _validate_exact_keys(
    payload: dict[str, Any],
) -> None:
    actual = frozenset(payload)

    missing = (
        EXPERIENCE_RECORD_KEYS - actual
    )

    unknown = (
        actual - EXPERIENCE_RECORD_KEYS
    )

    if missing:
        _corrupt(
            "ExperienceRecord missing fields: "
            + ",".join(sorted(missing))
        )

    if unknown:
        _corrupt(
            "ExperienceRecord contains "
            "unknown fields: "
            + ",".join(sorted(unknown))
        )


def _parse_record(
    payload: object,
) -> ExperienceRecord:
    if not isinstance(payload, dict):
        _corrupt(
            "ExperienceRecord must be "
            "a JSON object"
        )

    _validate_exact_keys(payload)

    schema_version = _require_string(
        payload["schema_version"],
        "schema_version",
    )

    if (
        schema_version
        != EXPERIENCE_SCHEMA_VERSION
    ):
        _corrupt(
            "unsupported ExperienceRecord "
            "schema_version"
        )

    experience_id = _require_string(
        payload["experience_id"],
        "experience_id",
    )

    incident_id = _require_string(
        payload["incident_id"],
        "incident_id",
    )

    diagnosis_id = _require_string(
        payload["diagnosis_id"],
        "diagnosis_id",
    )

    diagnosis_action = _require_string(
        payload["diagnosis_action"],
        "diagnosis_action",
    )

    if diagnosis_action not in ALLOWED_ACTIONS:
        _corrupt(
            "unsupported diagnosis_action"
        )

    diagnosis_confidence = _require_string(
        payload["diagnosis_confidence"],
        "diagnosis_confidence",
    )

    if (
        diagnosis_confidence
        not in ALLOWED_CONFIDENCE
    ):
        _corrupt(
            "unsupported diagnosis_confidence"
        )

    safety_decision_id = _optional_string(
        payload["safety_decision_id"],
        "safety_decision_id",
    )

    safety_outcome = _optional_string(
        payload["safety_outcome"],
        "safety_outcome",
    )

    if (
        safety_outcome is not None
        and safety_outcome
        not in ALLOWED_SAFETY_OUTCOMES
    ):
        _corrupt(
            "unsupported safety_outcome"
        )

    execution_id = _optional_string(
        payload["execution_id"],
        "execution_id",
    )

    execution_status = _optional_string(
        payload["execution_status"],
        "execution_status",
    )

    if (
        execution_status is not None
        and execution_status
        not in ALLOWED_EXECUTION_STATUSES
    ):
        _corrupt(
            "unsupported execution_status"
        )

    verification_id = _optional_string(
        payload["verification_id"],
        "verification_id",
    )

    verification_outcome = _optional_string(
        payload["verification_outcome"],
        "verification_outcome",
    )

    if (
        verification_outcome is not None
        and verification_outcome
        not in VERIFICATION_OUTCOMES
    ):
        _corrupt(
            "unsupported verification_outcome"
        )

    target_kind = _require_string(
        payload["target_kind"],
        "target_kind",
    )

    target_name = _require_string(
        payload["target_name"],
        "target_name",
    )

    target_uid = _require_string(
        payload["target_uid"],
        "target_uid",
    )

    replacement_uid = _optional_string(
        payload["replacement_uid"],
        "replacement_uid",
    )

    final_outcome = _require_string(
        payload["final_outcome"],
        "final_outcome",
    )

    if final_outcome not in (
        VERIFICATION_OUTCOMES
        | {FINAL_NO_EXECUTION}
    ):
        _corrupt(
            "unsupported final_outcome"
        )

    evidence_refs = _require_artifact_refs(
        payload["evidence_refs"]
    )

    created_at = _require_timestamp(
        payload["created_at"],
        "created_at",
    )

    # Paired optional fields must either both
    # exist or both be absent.
    if (
        (safety_decision_id is None)
        != (safety_outcome is None)
    ):
        _corrupt(
            "safety decision fields are "
            "incomplete"
        )

    if (
        (execution_id is None)
        != (execution_status is None)
    ):
        _corrupt(
            "execution fields are incomplete"
        )

    if (
        (verification_id is None)
        != (verification_outcome is None)
    ):
        _corrupt(
            "verification fields are "
            "incomplete"
        )

    if (
        diagnosis_action == "EVICT_POD"
        and safety_decision_id is None
    ):
        _corrupt(
            "EVICT_POD experience requires "
            "SafetyDecision"
        )

    if (
        safety_outcome == "ALLOW"
        and execution_id is None
    ):
        _corrupt(
            "SafetyDecision ALLOW without "
            "ExecutionResult is incomplete"
        )

    if (
        verification_id is None
        and replacement_uid is not None
    ):
        _corrupt(
            "replacement_uid requires "
            "VerificationResult"
        )

    if (
        execution_status
        == EXECUTION_ACCEPTED
    ):
        if safety_outcome != "ALLOW":
            _corrupt(
                "ACCEPTED execution requires "
                "SafetyDecision ALLOW"
            )

        if verification_id is None:
            _corrupt(
                "ACCEPTED execution requires "
                "VerificationResult"
            )

        if (
            final_outcome
            != verification_outcome
        ):
            _corrupt(
                "final_outcome must match "
                "verification_outcome"
            )

    else:
        if verification_id is not None:
            _corrupt(
                "non-ACCEPTED execution must "
                "not contain VerificationResult"
            )

        if (
            final_outcome
            != FINAL_NO_EXECUTION
        ):
            _corrupt(
                "non-ACCEPTED path must have "
                "final_outcome=NO_EXECUTION"
            )

    if (
        verification_outcome
        == FINAL_VERIFIED
        and replacement_uid is None
    ):
        _corrupt(
            "VERIFIED experience requires "
            "replacement_uid"
        )

    return ExperienceRecord(
        experience_id=experience_id,
        incident_id=incident_id,

        diagnosis_id=diagnosis_id,
        diagnosis_action=diagnosis_action,
        diagnosis_confidence=(
            diagnosis_confidence
        ),

        safety_decision_id=(
            safety_decision_id
        ),
        safety_outcome=safety_outcome,

        execution_id=execution_id,
        execution_status=execution_status,

        verification_id=verification_id,
        verification_outcome=(
            verification_outcome
        ),

        target_kind=target_kind,
        target_name=target_name,
        target_uid=target_uid,

        replacement_uid=replacement_uid,

        final_outcome=final_outcome,

        evidence_refs=evidence_refs,

        created_at=created_at,

        schema_version=schema_version,
    )


def _record_json_line(
    record: ExperienceRecord,
) -> bytes:
    # Round-trip through the strict persisted-record
    # parser before any write.
    payload = record.to_dict()

    validated = _parse_record(
        json.loads(
            json.dumps(
                payload,
                ensure_ascii=False,
            )
        )
    )

    serialized = json.dumps(
        validated.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return (
        serialized.encode("utf-8")
        + b"\n"
    )


class JsonlExperienceStore:
    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self.path = Path(path)

        if not self.path.name:
            raise ValueError(
                "experience store path must "
                "include a filename"
            )

        self.lock_path = (
            self.path.parent
            / f".{self.path.name}.lock"
        )

    def _ensure_parent(self) -> None:
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def _read_raw_unlocked(self) -> bytes:
        if not self.path.exists():
            return b""

        try:
            return self.path.read_bytes()
        except OSError as exc:
            raise ExperienceStoreError(
                "failed to read experience store"
            ) from exc

    def _parse_raw(
        self,
        raw: bytes,
    ) -> tuple[ExperienceRecord, ...]:
        if not raw:
            return ()

        # Every record written by this store ends in
        # newline. Missing newline is treated as a
        # truncated/corrupt final record.
        if not raw.endswith(b"\n"):
            _corrupt(
                "experience store is truncated: "
                "missing final newline"
            )

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExperienceStoreCorruptionError(
                "experience store is not valid UTF-8"
            ) from exc

        records: list[
            ExperienceRecord
        ] = []

        seen_ids: set[str] = set()

        for line_number, line in enumerate(
            text.splitlines(),
            start=1,
        ):
            if not line.strip():
                _corrupt(
                    "experience store contains "
                    f"blank line at {line_number}"
                )

            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ExperienceStoreCorruptionError(
                    "malformed JSONL record at "
                    f"line {line_number}: "
                    f"{exc.msg}"
                ) from exc

            record = _parse_record(payload)

            if record.experience_id in seen_ids:
                _corrupt(
                    "duplicate experience_id "
                    "already exists in store: "
                    f"{record.experience_id}"
                )

            seen_ids.add(
                record.experience_id
            )

            records.append(record)

        return tuple(records)

    def _load_unlocked(
        self,
    ) -> tuple[
        tuple[ExperienceRecord, ...],
        bytes,
    ]:
        raw = self._read_raw_unlocked()

        records = self._parse_raw(raw)

        return records, raw

    def load_all(
        self,
    ) -> tuple[ExperienceRecord, ...]:
        self._ensure_parent()

        try:
            with self.lock_path.open(
                "a+b"
            ) as lock_file:
                fcntl.flock(
                    lock_file.fileno(),
                    fcntl.LOCK_SH,
                )

                try:
                    records, _ = (
                        self._load_unlocked()
                    )
                finally:
                    fcntl.flock(
                        lock_file.fileno(),
                        fcntl.LOCK_UN,
                    )

        except ExperienceStoreError:
            raise

        except OSError as exc:
            raise ExperienceStoreError(
                "failed to load experience store"
            ) from exc

        return records

    def append(
        self,
        record: ExperienceRecord,
    ) -> None:
        self._ensure_parent()

        new_line = _record_json_line(
            record
        )

        temp_path: Path | None = None

        try:
            with self.lock_path.open(
                "a+b"
            ) as lock_file:
                fcntl.flock(
                    lock_file.fileno(),
                    fcntl.LOCK_EX,
                )

                try:
                    existing, raw = (
                        self._load_unlocked()
                    )

                    if any(
                        item.experience_id
                        == record.experience_id
                        for item in existing
                    ):
                        raise DuplicateExperienceError(
                            "experience_id already "
                            "exists: "
                            f"{record.experience_id}"
                        )

                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        dir=self.path.parent,
                        prefix=(
                            f".{self.path.name}."
                        ),
                        suffix=".tmp",
                        delete=False,
                    ) as temp_file:
                        temp_path = Path(
                            temp_file.name
                        )

                        if raw:
                            temp_file.write(raw)

                        temp_file.write(new_line)

                        temp_file.flush()

                        os.fsync(
                            temp_file.fileno()
                        )

                    os.replace(
                        temp_path,
                        self.path,
                    )

                    temp_path = None

                    directory_fd = os.open(
                        self.path.parent,
                        os.O_RDONLY,
                    )

                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)

                finally:
                    fcntl.flock(
                        lock_file.fileno(),
                        fcntl.LOCK_UN,
                    )

        except (
            ExperienceStoreError,
            DuplicateExperienceError,
        ):
            raise

        except OSError as exc:
            raise ExperienceStoreError(
                "failed to append experience record"
            ) from exc

        finally:
            if (
                temp_path is not None
                and temp_path.exists()
            ):
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def get(
        self,
        experience_id: str,
    ) -> ExperienceRecord | None:
        if (
            not isinstance(
                experience_id,
                str,
            )
            or not experience_id.strip()
        ):
            raise ValueError(
                "experience_id must be "
                "a non-empty string"
            )

        for record in self.load_all():
            if (
                record.experience_id
                == experience_id
            ):
                return record

        return None

    def find_by_incident_id(
        self,
        incident_id: str,
    ) -> tuple[ExperienceRecord, ...]:
        if (
            not isinstance(
                incident_id,
                str,
            )
            or not incident_id.strip()
        ):
            raise ValueError(
                "incident_id must be "
                "a non-empty string"
            )

        return tuple(
            record
            for record in self.load_all()
            if record.incident_id
            == incident_id
        )
