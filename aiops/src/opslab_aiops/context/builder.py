from __future__ import annotations

from collections import defaultdict
from typing import Any

from opslab_aiops.observation.models import (
    EndpointSliceObservation,
    EventObservation,
    ObservationSnapshot,
    PodObservation,
    ReplicaSetObservation,
)

from .detector import current_replica_set_uids
from .models import (
    Evidence,
    IncidentCandidate,
    IncidentContext,
    IncidentCurrentState,
    IncidentSource,
    IncidentTrigger,
    TopologyObservation,
)


CURRENT_STATE = "CURRENT_STATE"
HISTORICAL_EVENT = "HISTORICAL_EVENT"


def _current_replica_sets(
    snapshot: ObservationSnapshot,
) -> tuple[ReplicaSetObservation, ...]:
    current_uids = current_replica_set_uids(snapshot)

    return tuple(
        sorted(
            (
                replica_set
                for replica_set in snapshot.replica_sets
                if replica_set.uid in current_uids
            ),
            key=lambda item: item.name,
        )
    )


def _current_pods(
    snapshot: ObservationSnapshot,
    current_rs_uids: frozenset[str],
) -> tuple[PodObservation, ...]:
    return tuple(
        sorted(
            (
                pod
                for pod in snapshot.pods
                if (
                    pod.owner_kind == "ReplicaSet"
                    and pod.owner_uid in current_rs_uids
                )
            ),
            key=lambda item: item.name,
        )
    )


def _topology(
    pods: tuple[PodObservation, ...],
) -> tuple[TopologyObservation, ...]:
    by_node: dict[str | None, list[str]] = defaultdict(list)

    for pod in pods:
        by_node[pod.node_name].append(pod.name)

    return tuple(
        TopologyObservation(
            node_name=node_name,
            pod_names=tuple(sorted(pod_names)),
        )
        for node_name, pod_names in sorted(
            by_node.items(),
            key=lambda item: item[0] or "",
        )
    )


def _historical_events(
    snapshot: ObservationSnapshot,
    current_replica_sets: tuple[ReplicaSetObservation, ...],
    current_pods: tuple[PodObservation, ...],
) -> tuple[EventObservation, ...]:
    deployment = snapshot.deployment

    if deployment is None:
        return ()

    current_uids = {
        deployment.uid,
        *(item.uid for item in current_replica_sets),
        *(item.uid for item in current_pods),
    }

    current_names = {
        deployment.name,
        *(item.name for item in current_replica_sets),
        *(item.name for item in current_pods),
    }

    result: list[EventObservation] = []

    for event in snapshot.events:
        # UID is stronger identity evidence than name.
        if event.involved_uid is not None:
            if event.involved_uid not in current_uids:
                continue
        elif event.involved_name not in current_names:
            continue

        result.append(event)

    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.timestamp or "",
                item.uid,
            ),
        )
    )


def build_incident_context(
    snapshot: ObservationSnapshot,
    candidate: IncidentCandidate,
) -> IncidentContext:
    deployment = snapshot.deployment

    if deployment is None:
        raise ValueError(
            "cannot build IncidentContext without Deployment observation"
        )

    current_rs = _current_replica_sets(snapshot)

    current_rs_uids = frozenset(
        item.uid
        for item in current_rs
    )

    current_pods = _current_pods(
        snapshot,
        current_rs_uids,
    )

    target_pod = next(
        (
            pod
            for pod in current_pods
            if (
                pod.uid == candidate.target_uid
                and pod.name == candidate.target_name
            )
        ),
        None,
    )

    if target_pod is None:
        raise ValueError(
            "candidate target is not present in current workload context"
        )

    historical_events = _historical_events(
        snapshot,
        current_rs,
        current_pods,
    )

    evidence: list[Evidence] = []

    def add_evidence(
        *,
        category: str,
        source_kind: str,
        source_name: str,
        source_uid: str | None,
        fact_type: str,
        fact: dict[str, Any],
        observed_at: str,
    ) -> str:
        evidence_id = f"EV-{len(evidence) + 1:04d}"

        evidence.append(
            Evidence(
                evidence_id=evidence_id,
                category=category,
                source_kind=source_kind,
                source_name=source_name,
                source_uid=source_uid,
                fact_type=fact_type,
                fact=fact,
                observed_at=observed_at,
            )
        )

        return evidence_id

    # --------------------------------------------------------
    # Trigger evidence
    # --------------------------------------------------------

    pod_trigger_evidence = add_evidence(
        category=CURRENT_STATE,
        source_kind="Pod",
        source_name=target_pod.name,
        source_uid=target_pod.uid,
        fact_type="POD_STATUS",
        fact={
            "ready": target_pod.ready,
            "phase": target_pod.phase,
            "node_name": target_pod.node_name,
            "deletion_timestamp": target_pod.deletion_timestamp,
        },
        observed_at=snapshot.collected_at,
    )

    deployment_trigger_evidence = add_evidence(
        category=CURRENT_STATE,
        source_kind="Deployment",
        source_name=deployment.name,
        source_uid=deployment.uid,
        fact_type="DEPLOYMENT_AVAILABILITY",
        fact={
            "generation": deployment.generation,
            "observed_generation": deployment.observed_generation,
            "replicas": deployment.replicas,
            "updated_replicas": deployment.updated_replicas,
            "ready_replicas": deployment.ready_replicas,
            "available_replicas": deployment.available_replicas,
            "unavailable_replicas": deployment.unavailable_replicas,
        },
        observed_at=snapshot.collected_at,
    )

    # --------------------------------------------------------
    # Target container evidence
    # --------------------------------------------------------

    for container in sorted(
        target_pod.containers,
        key=lambda item: item.name,
    ):
        add_evidence(
            category=CURRENT_STATE,
            source_kind="Container",
            source_name=container.name,
            source_uid=target_pod.uid,
            fact_type="CONTAINER_STATUS",
            fact={
                "pod_name": target_pod.name,
                "ready": container.ready,
                "restart_count": container.restart_count,
                "state": container.state,
                "reason": container.reason,
            },
            observed_at=snapshot.collected_at,
        )

    # --------------------------------------------------------
    # Endpoint evidence for the target Pod
    # --------------------------------------------------------

    for endpoint_slice in sorted(
        snapshot.endpoint_slices,
        key=lambda item: item.name,
    ):
        for endpoint in sorted(
            endpoint_slice.endpoints,
            key=lambda item: (
                item.pod_name or "",
                item.addresses,
            ),
        ):
            if not (
                endpoint.pod_uid == target_pod.uid
                or (
                    endpoint.pod_uid is None
                    and endpoint.pod_name == target_pod.name
                )
            ):
                continue

            add_evidence(
                category=CURRENT_STATE,
                source_kind="EndpointSlice",
                source_name=endpoint_slice.name,
                source_uid=endpoint_slice.uid,
                fact_type="TARGET_ENDPOINT_STATUS",
                fact={
                    "pod_name": endpoint.pod_name,
                    "pod_uid": endpoint.pod_uid,
                    "addresses": list(endpoint.addresses),
                    "ready": endpoint.ready,
                    "serving": endpoint.serving,
                    "terminating": endpoint.terminating,
                },
                observed_at=snapshot.collected_at,
            )

    # --------------------------------------------------------
    # Current ReplicaSet evidence
    # --------------------------------------------------------

    for replica_set in current_rs:
        add_evidence(
            category=CURRENT_STATE,
            source_kind="ReplicaSet",
            source_name=replica_set.name,
            source_uid=replica_set.uid,
            fact_type="REPLICASET_STATUS",
            fact={
                "replicas": replica_set.replicas,
                "ready_replicas": replica_set.ready_replicas,
                "available_replicas": replica_set.available_replicas,
            },
            observed_at=snapshot.collected_at,
        )

    # --------------------------------------------------------
    # Sibling Pod evidence
    # --------------------------------------------------------

    for pod in current_pods:
        if pod.uid == target_pod.uid:
            continue

        add_evidence(
            category=CURRENT_STATE,
            source_kind="Pod",
            source_name=pod.name,
            source_uid=pod.uid,
            fact_type="SIBLING_POD_STATUS",
            fact={
                "ready": pod.ready,
                "phase": pod.phase,
                "node_name": pod.node_name,
                "deletion_timestamp": pod.deletion_timestamp,
            },
            observed_at=snapshot.collected_at,
        )

    # --------------------------------------------------------
    # PDB / HPA evidence
    # --------------------------------------------------------

    for pdb in sorted(
        snapshot.pdbs,
        key=lambda item: item.name,
    ):
        add_evidence(
            category=CURRENT_STATE,
            source_kind="PodDisruptionBudget",
            source_name=pdb.name,
            source_uid=pdb.uid,
            fact_type="PDB_STATUS",
            fact={
                "disruptions_allowed": pdb.disruptions_allowed,
                "current_healthy": pdb.current_healthy,
                "desired_healthy": pdb.desired_healthy,
                "expected_pods": pdb.expected_pods,
            },
            observed_at=snapshot.collected_at,
        )

    for hpa in sorted(
        snapshot.hpas,
        key=lambda item: item.name,
    ):
        add_evidence(
            category=CURRENT_STATE,
            source_kind="HorizontalPodAutoscaler",
            source_name=hpa.name,
            source_uid=hpa.uid,
            fact_type="HPA_STATUS",
            fact={
                "min_replicas": hpa.min_replicas,
                "max_replicas": hpa.max_replicas,
                "current_replicas": hpa.current_replicas,
                "desired_replicas": hpa.desired_replicas,
            },
            observed_at=snapshot.collected_at,
        )

    topology = _topology(current_pods)

    for item in topology:
        add_evidence(
            category=CURRENT_STATE,
            source_kind="Topology",
            source_name=item.node_name or "<unassigned>",
            source_uid=None,
            fact_type="POD_PLACEMENT",
            fact={
                "node_name": item.node_name,
                "pod_names": list(item.pod_names),
            },
            observed_at=snapshot.collected_at,
        )

    # --------------------------------------------------------
    # Historical Event evidence
    # --------------------------------------------------------

    for event in historical_events:
        add_evidence(
            category=HISTORICAL_EVENT,
            source_kind="Event",
            source_name=event.uid,
            source_uid=event.uid,
            fact_type="KUBERNETES_EVENT",
            fact={
                "type": event.type,
                "reason": event.reason,
                "message": event.message,
                "involved_kind": event.involved_kind,
                "involved_name": event.involved_name,
                "involved_uid": event.involved_uid,
                "count": event.count,
                "reporting_controller": event.reporting_controller,
            },
            observed_at=(
                event.timestamp
                or snapshot.collected_at
            ),
        )

    incident_id = (
        f"inc-{snapshot.snapshot_id}-"
        f"{candidate.target_uid[:8]}"
    )

    return IncidentContext(
        incident_id=incident_id,
        incident_type=candidate.incident_type,
        created_at=snapshot.collected_at,
        source=IncidentSource(
            snapshot_id=snapshot.snapshot_id,
            collected_at=snapshot.collected_at,
            namespace=snapshot.namespace,
            workload_name=snapshot.workload_name,
            service_name=snapshot.service_name,
        ),
        trigger=IncidentTrigger(
            rule_id=candidate.rule_id,
            target_kind=candidate.target_kind,
            target_name=candidate.target_name,
            target_uid=candidate.target_uid,
            evidence_refs=(
                pod_trigger_evidence,
                deployment_trigger_evidence,
            ),
        ),
        current_state=IncidentCurrentState(
            deployment=deployment,
            current_replica_sets=current_rs,
            current_pods=current_pods,
            endpoint_slices=tuple(
                sorted(
                    snapshot.endpoint_slices,
                    key=lambda item: item.name,
                )
            ),
            pdbs=tuple(
                sorted(
                    snapshot.pdbs,
                    key=lambda item: item.name,
                )
            ),
            hpas=tuple(
                sorted(
                    snapshot.hpas,
                    key=lambda item: item.name,
                )
            ),
            topology=topology,
        ),
        historical_evidence=historical_events,
        evidence=tuple(evidence),
    )
