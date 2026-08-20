from __future__ import annotations

from opslab_aiops.observation.models import ObservationSnapshot

from .models import IncidentCandidate


FASTAPI_UNHEALTHY_INSTANCE = "FASTAPI_UNHEALTHY_INSTANCE"
FASTAPI_UNHEALTHY_INSTANCE_RULE_V1 = (
    "fastapi_unhealthy_instance.v1"
)


def current_replica_set_uids(
    snapshot: ObservationSnapshot,
) -> frozenset[str]:
    """
    Return ReplicaSet UIDs that belong to the current workload context.

    A ReplicaSet is current when:
    1. it has replicas > 0, or
    2. it owns at least one current non-terminating Pod.

    Historical zero-replica ReplicaSets with no current Pods are excluded.
    """
    known_rs_uids = {
        replica_set.uid
        for replica_set in snapshot.replica_sets
    }

    current_uids = {
        replica_set.uid
        for replica_set in snapshot.replica_sets
        if (replica_set.replicas or 0) > 0
    }

    for pod in snapshot.pods:
        if pod.deletion_timestamp is not None:
            continue

        if pod.owner_kind != "ReplicaSet":
            continue

        if pod.owner_uid in known_rs_uids:
            current_uids.add(pod.owner_uid)

    return frozenset(current_uids)


def detect_fastapi_unhealthy_instances(
    snapshot: ObservationSnapshot,
) -> tuple[IncidentCandidate, ...]:
    """
    Detect FASTAPI_UNHEALTHY_INSTANCE candidates.

    Detection is intentionally deterministic and conservative.

    Historical Events, topology placement, HPA state, PDB state and
    EndpointSlice readiness do not independently create candidates.
    """
    deployment = snapshot.deployment

    if deployment is None:
        return ()

    # Controller has not yet observed the latest Deployment generation.
    if (
        deployment.generation is None
        or deployment.observed_generation is None
        or deployment.generation
        != deployment.observed_generation
    ):
        return ()

    # Do not diagnose an instance while a Deployment rollout is still
    # converging to the desired ReplicaSet generation.
    if (
        deployment.replicas is None
        or deployment.updated_replicas is None
        or deployment.updated_replicas
        != deployment.replicas
    ):
        return ()

    # Missing availability information is not enough evidence.
    if deployment.ready_replicas is None:
        return ()

    # Deployment is currently fully Ready.
    if deployment.ready_replicas >= deployment.replicas:
        return ()

    current_rs_uids = current_replica_set_uids(snapshot)

    candidates: list[IncidentCandidate] = []

    for pod in snapshot.pods:
        if pod.owner_kind != "ReplicaSet":
            continue

        if pod.owner_uid not in current_rs_uids:
            continue

        # Normal termination is not an unhealthy-instance candidate.
        if pod.deletion_timestamp is not None:
            continue

        # v1 deliberately focuses on Running-but-NotReady instances.
        if pod.phase != "Running":
            continue

        if pod.ready:
            continue

        candidates.append(
            IncidentCandidate(
                incident_type=FASTAPI_UNHEALTHY_INSTANCE,
                rule_id=FASTAPI_UNHEALTHY_INSTANCE_RULE_V1,
                target_kind="Pod",
                target_name=pod.name,
                target_uid=pod.uid,
            )
        )

    return tuple(
        sorted(
            candidates,
            key=lambda item: item.target_name,
        )
    )
