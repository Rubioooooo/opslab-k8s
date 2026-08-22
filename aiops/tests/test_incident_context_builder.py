from __future__ import annotations

import json
import unittest

from opslab_aiops.context.builder import (
    CURRENT_STATE,
    HISTORICAL_EVENT,
    build_incident_context,
)
from opslab_aiops.context.detector import (
    detect_fastapi_unhealthy_instances,
)
from opslab_aiops.observation.models import (
    DeploymentObservation,
    EndpointObservation,
    EndpointSliceObservation,
    EventObservation,
    ObservationSnapshot,
    PDBObservation,
    PodObservation,
    ReplicaSetObservation,
)


DEPLOYMENT_UID = "deployment-uid"
CURRENT_RS_UID = "current-rs-uid"
OLD_RS_UID = "old-rs-uid"
TARGET_POD_UID = "target-pod-uid"
HEALTHY_POD_UID = "healthy-pod-uid"


def make_snapshot() -> ObservationSnapshot:
    deployment = DeploymentObservation(
        name="opslab-api",
        uid=DEPLOYMENT_UID,
        generation=10,
        observed_generation=10,
        replicas=2,
        updated_replicas=2,
        ready_replicas=1,
        available_replicas=1,
        unavailable_replicas=1,
    )

    old_rs = ReplicaSetObservation(
        name="opslab-api-old",
        uid=OLD_RS_UID,
        owner_name="opslab-api",
        owner_kind="Deployment",
        owner_uid=DEPLOYMENT_UID,
        replicas=0,
        ready_replicas=0,
        available_replicas=0,
    )

    current_rs = ReplicaSetObservation(
        name="opslab-api-current",
        uid=CURRENT_RS_UID,
        owner_name="opslab-api",
        owner_kind="Deployment",
        owner_uid=DEPLOYMENT_UID,
        replicas=2,
        ready_replicas=1,
        available_replicas=1,
    )

    target_pod = PodObservation(
        name="opslab-api-target",
        uid=TARGET_POD_UID,
        owner_name="opslab-api-current",
        owner_kind="ReplicaSet",
        owner_uid=CURRENT_RS_UID,
        node_name="worker1",
        phase="Running",
        pod_ip="10.244.1.10",
        ready=False,
        deletion_timestamp=None,
        start_time="2026-08-20T00:00:00Z",
    )

    healthy_pod = PodObservation(
        name="opslab-api-healthy",
        uid=HEALTHY_POD_UID,
        owner_name="opslab-api-current",
        owner_kind="ReplicaSet",
        owner_uid=CURRENT_RS_UID,
        node_name="worker1",
        phase="Running",
        pod_ip="10.244.1.11",
        ready=True,
        deletion_timestamp=None,
        start_time="2026-08-20T00:00:00Z",
    )

    endpoint_slice = EndpointSliceObservation(
        name="opslab-api-test",
        uid="endpoint-slice-uid",
        endpoints=(
            EndpointObservation(
                addresses=("10.244.1.10",),
                pod_name="opslab-api-target",
                pod_uid=TARGET_POD_UID,
                node_name="worker1",
                ready=False,
                serving=True,
                terminating=False,
            ),
            EndpointObservation(
                addresses=("10.244.1.11",),
                pod_name="opslab-api-healthy",
                pod_uid=HEALTHY_POD_UID,
                node_name="worker1",
                ready=True,
                serving=True,
                terminating=False,
            ),
        ),
    )

    target_event = EventObservation(
        uid="event-target",
        type="Warning",
        reason="Unhealthy",
        message="Readiness probe failed",
        involved_kind="Pod",
        involved_name="opslab-api-target",
        involved_uid=TARGET_POD_UID,
        count=2,
        timestamp="2026-08-20T00:00:30Z",
        reporting_controller="kubelet",
    )

    stale_event = EventObservation(
        uid="event-old-pod",
        type="Warning",
        reason="Unhealthy",
        message="Old Pod readiness probe failed",
        involved_kind="Pod",
        involved_name="opslab-api-old-pod",
        involved_uid="old-pod-uid",
        count=1,
        timestamp="2026-08-19T23:00:00Z",
        reporting_controller="kubelet",
    )

    old_rs_event = EventObservation(
        uid="event-old-rs",
        type="Normal",
        reason="ScalingReplicaSet",
        message="Historical ReplicaSet event",
        involved_kind="ReplicaSet",
        involved_name="opslab-api-old",
        involved_uid=OLD_RS_UID,
        count=1,
        timestamp="2026-08-19T22:00:00Z",
        reporting_controller="deployment-controller",
    )

    return ObservationSnapshot(
        snapshot_id="obs-builder-test",
        collected_at="2026-08-20T00:01:00Z",
        namespace="opslab",
        workload_name="opslab-api",
        service_name="opslab-api",
        deployment=deployment,
        replica_sets=(
            old_rs,
            current_rs,
        ),
        pods=(
            target_pod,
            healthy_pod,
        ),
        endpoint_slices=(endpoint_slice,),
        pdbs=(
            PDBObservation(
                name="opslab-api",
                uid="pdb-uid",
                disruptions_allowed=0,
                current_healthy=1,
                desired_healthy=1,
                expected_pods=2,
                generation=1,
                observed_generation=1,
                unhealthy_pod_eviction_policy="AlwaysAllow",
            ),
        ),
        events=(
            stale_event,
            old_rs_event,
            target_event,
        ),
    )


class IncidentContextBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = make_snapshot()

        candidates = detect_fastapi_unhealthy_instances(
            self.snapshot
        )

        self.assertEqual(len(candidates), 1)

        self.context = build_incident_context(
            self.snapshot,
            candidates[0],
        )

    def test_historical_replica_set_is_removed(self) -> None:
        names = {
            item.name
            for item in self.context.current_state.current_replica_sets
        }

        self.assertEqual(
            names,
            {"opslab-api-current"},
        )

    def test_stale_events_are_not_in_historical_context(self) -> None:
        event_uids = {
            item.uid
            for item in self.context.historical_evidence
        }

        self.assertEqual(
            event_uids,
            {"event-target"},
        )

    def test_trigger_references_existing_evidence(self) -> None:
        evidence_ids = {
            item.evidence_id
            for item in self.context.evidence
        }

        self.assertTrue(
            set(self.context.trigger.evidence_refs)
            <= evidence_ids
        )

        self.assertEqual(
            self.context.trigger.evidence_refs,
            ("EV-0001", "EV-0002"),
        )

    def test_evidence_ids_are_sequential(self) -> None:
        expected = tuple(
            f"EV-{index:04d}"
            for index in range(
                1,
                len(self.context.evidence) + 1,
            )
        )

        actual = tuple(
            item.evidence_id
            for item in self.context.evidence
        )

        self.assertEqual(actual, expected)

    def test_current_and_historical_categories_are_separate(self) -> None:
        current = [
            item
            for item in self.context.evidence
            if item.category == CURRENT_STATE
        ]

        historical = [
            item
            for item in self.context.evidence
            if item.category == HISTORICAL_EVENT
        ]

        self.assertGreater(len(current), 0)
        self.assertEqual(len(historical), 1)

        self.assertEqual(
            historical[0].source_uid,
            "event-target",
        )

    def test_topology_preserves_same_node_drift(self) -> None:
        self.assertEqual(
            len(self.context.current_state.topology),
            1,
        )

        topology = self.context.current_state.topology[0]

        self.assertEqual(topology.node_name, "worker1")

        self.assertEqual(
            set(topology.pod_names),
            {
                "opslab-api-target",
                "opslab-api-healthy",
            },
        )

    def test_source_traceability_is_preserved(self) -> None:
        self.assertEqual(
            self.context.source.snapshot_id,
            self.snapshot.snapshot_id,
        )

        self.assertEqual(
            self.context.trigger.target_uid,
            TARGET_POD_UID,
        )

    def test_context_is_json_serializable(self) -> None:
        value = json.loads(
            self.context.to_json()
        )

        self.assertEqual(
            value["schema_version"],
            "v1alpha1",
        )

        self.assertEqual(
            value["source"]["snapshot_id"],
            "obs-builder-test",
        )


    def test_phase3_pdb_safety_fields_do_not_leak_to_phase2_context(
        self,
    ) -> None:
        value = json.loads(self.context.to_json())
        pdb = value["current_state"]["pdbs"][0]

        self.assertEqual(pdb["name"], "opslab-api")
        self.assertEqual(pdb["disruptions_allowed"], 0)
        self.assertNotIn("generation", pdb)
        self.assertNotIn("observed_generation", pdb)
        self.assertNotIn("unhealthy_pod_eviction_policy", pdb)


if __name__ == "__main__":
    unittest.main()
