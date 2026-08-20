from __future__ import annotations

import json
import unittest

from opslab_aiops.context.models import (
    Evidence,
    IncidentContext,
    IncidentCurrentState,
    IncidentSource,
    IncidentTrigger,
    TopologyObservation,
)
from opslab_aiops.diagnosis import (
    DiagnosisValidationError,
    parse_and_validate_diagnosis,
)
from opslab_aiops.observation.models import (
    DeploymentObservation,
    EventObservation,
    PodObservation,
    ReplicaSetObservation,
)


INCIDENT_ID = "inc-test"
TARGET_UID = "pod-target-uid"


def make_context() -> IncidentContext:
    deployment = DeploymentObservation(
        name="opslab-api",
        uid="deployment-uid",
        generation=31,
        observed_generation=31,
        replicas=2,
        updated_replicas=2,
        ready_replicas=1,
        available_replicas=1,
        unavailable_replicas=1,
    )

    replica_set = ReplicaSetObservation(
        name="opslab-api-current",
        uid="rs-uid",
        owner_name="opslab-api",
        owner_kind="Deployment",
        owner_uid="deployment-uid",
        replicas=2,
        ready_replicas=1,
        available_replicas=1,
    )

    target = PodObservation(
        name="opslab-api-target",
        uid=TARGET_UID,
        owner_name="opslab-api-current",
        owner_kind="ReplicaSet",
        owner_uid="rs-uid",
        node_name="worker1",
        phase="Running",
        pod_ip="10.244.1.10",
        ready=False,
        deletion_timestamp=None,
        start_time="2026-08-20T00:00:00Z",
    )

    sibling = PodObservation(
        name="opslab-api-sibling",
        uid="pod-sibling-uid",
        owner_name="opslab-api-current",
        owner_kind="ReplicaSet",
        owner_uid="rs-uid",
        node_name="worker1",
        phase="Running",
        pod_ip="10.244.1.11",
        ready=True,
        deletion_timestamp=None,
        start_time="2026-08-20T00:00:00Z",
    )

    event = EventObservation(
        uid="event-uid",
        type="Warning",
        reason="Unhealthy",
        message="Readiness probe failed",
        involved_kind="Pod",
        involved_name="opslab-api-target",
        involved_uid=TARGET_UID,
        count=2,
        timestamp="2026-08-20T00:00:30Z",
        reporting_controller="kubelet",
    )

    evidence = (
        Evidence(
            evidence_id="EV-0001",
            category="CURRENT_STATE",
            source_kind="Pod",
            source_name="opslab-api-target",
            source_uid=TARGET_UID,
            fact_type="POD_STATUS",
            fact={
                "phase": "Running",
                "ready": False,
            },
            observed_at="2026-08-20T00:01:00Z",
        ),
        Evidence(
            evidence_id="EV-0002",
            category="CURRENT_STATE",
            source_kind="Deployment",
            source_name="opslab-api",
            source_uid="deployment-uid",
            fact_type="DEPLOYMENT_AVAILABILITY",
            fact={
                "replicas": 2,
                "ready_replicas": 1,
            },
            observed_at="2026-08-20T00:01:00Z",
        ),
        Evidence(
            evidence_id="EV-0003",
            category="HISTORICAL_EVENT",
            source_kind="Event",
            source_name="event-uid",
            source_uid="event-uid",
            fact_type="KUBERNETES_EVENT",
            fact={
                "reason": "Unhealthy",
                "message": "Readiness probe failed",
            },
            observed_at="2026-08-20T00:00:30Z",
        ),
    )

    return IncidentContext(
        incident_id=INCIDENT_ID,
        incident_type="FASTAPI_UNHEALTHY_INSTANCE",
        created_at="2026-08-20T00:01:00Z",
        source=IncidentSource(
            snapshot_id="obs-test",
            collected_at="2026-08-20T00:01:00Z",
            namespace="opslab",
            workload_name="opslab-api",
            service_name="opslab-api",
        ),
        trigger=IncidentTrigger(
            rule_id="fastapi_unhealthy_instance.v1",
            target_kind="Pod",
            target_name="opslab-api-target",
            target_uid=TARGET_UID,
            evidence_refs=("EV-0001", "EV-0002"),
        ),
        current_state=IncidentCurrentState(
            deployment=deployment,
            current_replica_sets=(replica_set,),
            current_pods=(target, sibling),
            endpoint_slices=(),
            pdbs=(),
            hpas=(),
            topology=(
                TopologyObservation(
                    node_name="worker1",
                    pod_names=(
                        "opslab-api-sibling",
                        "opslab-api-target",
                    ),
                ),
            ),
        ),
        historical_evidence=(event,),
        evidence=evidence,
    )


def valid_payload() -> dict:
    return {
        "schema_version": "v1alpha1",
        "diagnosis_id": "diag-test-001",
        "incident_id": INCIDENT_ID,
        "summary": (
            "The target FastAPI Pod is Running but not Ready."
        ),
        "root_cause": (
            "The current Pod readiness failure is consistent "
            "with the observed readiness probe evidence."
        ),
        "confidence": "HIGH",
        "evidence_refs": [
            "EV-0001",
            "EV-0002",
            "EV-0003",
        ],
        "affected_resource": {
            "kind": "Pod",
            "name": "opslab-api-target",
            "uid": TARGET_UID,
        },
        "recommended_action": "EVICT_POD",
        "rationale": (
            "The affected instance is unavailable while a sibling "
            "replica remains available."
        ),
        "risk_notes": [
            "A later Safety Gate must evaluate disruption safety."
        ],
        "requires_human_approval": True,
    }


class DiagnosisValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = make_context()

    def validate(self, payload: dict):
        return parse_and_validate_diagnosis(
            json.dumps(payload),
            self.context,
        )

    def test_valid_diagnosis_is_accepted(self) -> None:
        result = self.validate(valid_payload())

        self.assertEqual(result.incident_id, INCIDENT_ID)
        self.assertEqual(result.confidence, "HIGH")
        self.assertEqual(
            result.recommended_action,
            "EVICT_POD",
        )

    def test_malformed_json_is_rejected(self) -> None:
        with self.assertRaises(DiagnosisValidationError):
            parse_and_validate_diagnosis(
                "{not-json",
                self.context,
            )

    def test_wrong_schema_is_rejected(self) -> None:
        payload = valid_payload()
        payload["schema_version"] = "v9"

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)

    def test_wrong_incident_id_is_rejected(self) -> None:
        payload = valid_payload()
        payload["incident_id"] = "inc-other"

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)

    def test_unknown_evidence_is_rejected(self) -> None:
        payload = valid_payload()
        payload["evidence_refs"].append("EV-9999")

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)

    def test_historical_only_evidence_is_rejected(self) -> None:
        payload = valid_payload()
        payload["evidence_refs"] = ["EV-0003"]

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)

    def test_invalid_confidence_is_rejected(self) -> None:
        payload = valid_payload()
        payload["confidence"] = "VERY_HIGH"

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)

    def test_invalid_action_is_rejected(self) -> None:
        payload = valid_payload()
        payload["recommended_action"] = "DELETE_POD"

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)

    def test_evict_without_human_approval_is_rejected(self) -> None:
        payload = valid_payload()
        payload["requires_human_approval"] = False

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)

    def test_escalate_without_human_approval_is_rejected(self) -> None:
        payload = valid_payload()
        payload["recommended_action"] = "ESCALATE"
        payload["requires_human_approval"] = False

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)

    def test_no_action_with_human_approval_is_rejected(self) -> None:
        payload = valid_payload()
        payload["recommended_action"] = "NO_ACTION"
        payload["requires_human_approval"] = True

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)

    def test_affected_resource_mismatch_is_rejected(self) -> None:
        payload = valid_payload()
        payload["affected_resource"]["uid"] = "wrong-uid"

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)

    def test_kubectl_content_is_rejected(self) -> None:
        payload = valid_payload()
        payload["rationale"] = (
            "Run kubectl delete pod opslab-api-target."
        )

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)

    def test_shell_content_is_rejected(self) -> None:
        payload = valid_payload()
        payload["rationale"] = (
            "Use bash -c to restart the target process."
        )

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)

    def test_unknown_top_level_field_is_rejected(self) -> None:
        payload = valid_payload()
        payload["command"] = "something"

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)

    def test_duplicate_evidence_refs_are_rejected(self) -> None:
        payload = valid_payload()
        payload["evidence_refs"] = [
            "EV-0001",
            "EV-0001",
        ]

        with self.assertRaises(DiagnosisValidationError):
            self.validate(payload)


if __name__ == "__main__":
    unittest.main()
