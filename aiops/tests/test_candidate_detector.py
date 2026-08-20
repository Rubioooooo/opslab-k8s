from __future__ import annotations

import unittest

from opslab_aiops.context.detector import (
    FASTAPI_UNHEALTHY_INSTANCE,
    current_replica_set_uids,
    detect_fastapi_unhealthy_instances,
)
from opslab_aiops.observation.models import (
    DeploymentObservation,
    EventObservation,
    ObservationSnapshot,
    PodObservation,
    ReplicaSetObservation,
)


DEPLOYMENT_UID = "deployment-uid"
RS_UID = "current-rs-uid"


def deployment(
    *,
    replicas: int = 2,
    updated_replicas: int = 2,
    ready_replicas: int = 2,
    generation: int = 10,
    observed_generation: int = 10,
) -> DeploymentObservation:
    return DeploymentObservation(
        name="opslab-api",
        uid=DEPLOYMENT_UID,
        generation=generation,
        observed_generation=observed_generation,
        replicas=replicas,
        updated_replicas=updated_replicas,
        ready_replicas=ready_replicas,
        available_replicas=ready_replicas,
        unavailable_replicas=replicas - ready_replicas,
    )


def replica_set(
    *,
    uid: str = RS_UID,
    name: str = "opslab-api-current",
    replicas: int = 2,
) -> ReplicaSetObservation:
    return ReplicaSetObservation(
        name=name,
        uid=uid,
        owner_name="opslab-api",
        owner_kind="Deployment",
        owner_uid=DEPLOYMENT_UID,
        replicas=replicas,
        ready_replicas=replicas,
        available_replicas=replicas,
    )


def pod(
    name: str,
    *,
    ready: bool,
    node: str = "worker1",
    phase: str = "Running",
    deletion_timestamp: str | None = None,
    owner_uid: str = RS_UID,
) -> PodObservation:
    return PodObservation(
        name=name,
        uid=f"{name}-uid",
        owner_name="opslab-api-current",
        owner_kind="ReplicaSet",
        owner_uid=owner_uid,
        node_name=node,
        phase=phase,
        pod_ip=None,
        ready=ready,
        deletion_timestamp=deletion_timestamp,
        start_time="2026-08-20T00:00:00Z",
    )


def snapshot(
    *,
    deployment_value: DeploymentObservation | None = None,
    pods: tuple[PodObservation, ...] = (),
    replica_sets: tuple[ReplicaSetObservation, ...] = (),
    events: tuple[EventObservation, ...] = (),
) -> ObservationSnapshot:
    return ObservationSnapshot(
        snapshot_id="obs-test",
        collected_at="2026-08-20T00:01:00Z",
        namespace="opslab",
        workload_name="opslab-api",
        service_name="opslab-api",
        deployment=(
            deployment()
            if deployment_value is None
            else deployment_value
        ),
        replica_sets=replica_sets,
        pods=pods,
        events=events,
    )


class CandidateDetectorTests(unittest.TestCase):
    def test_healthy_baseline_has_no_candidate(self) -> None:
        value = snapshot(
            replica_sets=(replica_set(),),
            pods=(
                pod("api-a", ready=True, node="worker1"),
                pod("api-b", ready=True, node="worker2"),
            ),
        )

        self.assertEqual(
            detect_fastapi_unhealthy_instances(value),
            (),
        )

    def test_topology_drift_is_not_unhealthy_instance(self) -> None:
        value = snapshot(
            replica_sets=(replica_set(),),
            pods=(
                pod("api-a", ready=True, node="worker1"),
                pod("api-b", ready=True, node="worker1"),
            ),
        )

        self.assertEqual(
            detect_fastapi_unhealthy_instances(value),
            (),
        )

    def test_historical_warning_does_not_trigger_candidate(self) -> None:
        warning = EventObservation(
            uid="event-uid",
            type="Warning",
            reason="Unhealthy",
            message="Readiness probe failed",
            involved_kind="Pod",
            involved_name="api-a",
            involved_uid="api-a-old-uid",
            count=1,
            timestamp="2026-08-19T23:00:00Z",
            reporting_controller="kubelet",
        )

        value = snapshot(
            replica_sets=(replica_set(),),
            pods=(
                pod("api-a", ready=True),
                pod("api-b", ready=True),
            ),
            events=(warning,),
        )

        self.assertEqual(
            detect_fastapi_unhealthy_instances(value),
            (),
        )

    def test_running_not_ready_pod_creates_candidate(self) -> None:
        value = snapshot(
            deployment_value=deployment(
                replicas=2,
                updated_replicas=2,
                ready_replicas=1,
            ),
            replica_sets=(replica_set(),),
            pods=(
                pod("api-a", ready=True),
                pod("api-b", ready=False),
            ),
        )

        result = detect_fastapi_unhealthy_instances(value)

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0].incident_type,
            FASTAPI_UNHEALTHY_INSTANCE,
        )
        self.assertEqual(result[0].target_name, "api-b")

    def test_terminating_not_ready_pod_is_ignored(self) -> None:
        value = snapshot(
            deployment_value=deployment(
                replicas=2,
                updated_replicas=2,
                ready_replicas=1,
            ),
            replica_sets=(replica_set(),),
            pods=(
                pod("api-a", ready=True),
                pod(
                    "api-b",
                    ready=False,
                    deletion_timestamp="2026-08-20T00:00:30Z",
                ),
            ),
        )

        self.assertEqual(
            detect_fastapi_unhealthy_instances(value),
            (),
        )

    def test_pending_pod_is_not_v1_candidate(self) -> None:
        value = snapshot(
            deployment_value=deployment(
                replicas=2,
                updated_replicas=2,
                ready_replicas=1,
            ),
            replica_sets=(replica_set(),),
            pods=(
                pod("api-a", ready=True),
                pod("api-b", ready=False, phase="Pending"),
            ),
        )

        self.assertEqual(
            detect_fastapi_unhealthy_instances(value),
            (),
        )

    def test_generation_mismatch_suppresses_detection(self) -> None:
        value = snapshot(
            deployment_value=deployment(
                replicas=2,
                updated_replicas=2,
                ready_replicas=1,
                generation=11,
                observed_generation=10,
            ),
            replica_sets=(replica_set(),),
            pods=(
                pod("api-a", ready=True),
                pod("api-b", ready=False),
            ),
        )

        self.assertEqual(
            detect_fastapi_unhealthy_instances(value),
            (),
        )

    def test_rollout_in_progress_suppresses_detection(self) -> None:
        value = snapshot(
            deployment_value=deployment(
                replicas=2,
                updated_replicas=1,
                ready_replicas=1,
            ),
            replica_sets=(replica_set(),),
            pods=(
                pod("api-a", ready=True),
                pod("api-b", ready=False),
            ),
        )

        self.assertEqual(
            detect_fastapi_unhealthy_instances(value),
            (),
        )

    def test_historical_zero_replica_rs_is_not_current(self) -> None:
        historical = replica_set(
            uid="historical-rs-uid",
            name="opslab-api-old",
            replicas=0,
        )

        value = snapshot(
            replica_sets=(
                historical,
                replica_set(),
            ),
            pods=(
                pod("api-a", ready=True),
                pod("api-b", ready=True),
            ),
        )

        self.assertEqual(
            current_replica_set_uids(value),
            frozenset({RS_UID}),
        )


if __name__ == "__main__":
    unittest.main()
