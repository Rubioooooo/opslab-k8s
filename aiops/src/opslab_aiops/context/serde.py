from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opslab_aiops.observation.models import (
    ConditionObservation,
    ContainerObservation,
    DeploymentObservation,
    EndpointObservation,
    EndpointSliceObservation,
    EventObservation,
    HPAObservation,
    PDBObservation,
    PodObservation,
    ReplicaSetObservation,
)

from .models import (
    Evidence,
    IncidentContext,
    IncidentCurrentState,
    IncidentSource,
    IncidentTrigger,
    TopologyObservation,
)


def _conditions(
    values: list[dict[str, Any]],
) -> tuple[ConditionObservation, ...]:
    return tuple(
        ConditionObservation(**value)
        for value in values
    )


def _containers(
    values: list[dict[str, Any]],
) -> tuple[ContainerObservation, ...]:
    return tuple(
        ContainerObservation(**value)
        for value in values
    )


def _deployment(
    value: dict[str, Any],
) -> DeploymentObservation:
    return DeploymentObservation(
        name=value["name"],
        uid=value["uid"],
        generation=value.get("generation"),
        observed_generation=value.get("observed_generation"),
        replicas=value.get("replicas"),
        updated_replicas=value.get("updated_replicas"),
        ready_replicas=value.get("ready_replicas"),
        available_replicas=value.get("available_replicas"),
        unavailable_replicas=value.get("unavailable_replicas"),
        conditions=_conditions(
            value.get("conditions", [])
        ),
    )


def _replica_set(
    value: dict[str, Any],
) -> ReplicaSetObservation:
    return ReplicaSetObservation(
        name=value["name"],
        uid=value["uid"],
        owner_name=value.get("owner_name"),
        owner_kind=value.get("owner_kind"),
        owner_uid=value.get("owner_uid"),
        replicas=value.get("replicas"),
        ready_replicas=value.get("ready_replicas"),
        available_replicas=value.get("available_replicas"),
    )


def _pod(
    value: dict[str, Any],
) -> PodObservation:
    return PodObservation(
        name=value["name"],
        uid=value["uid"],
        owner_name=value.get("owner_name"),
        owner_kind=value.get("owner_kind"),
        owner_uid=value.get("owner_uid"),
        node_name=value.get("node_name"),
        phase=value.get("phase"),
        pod_ip=value.get("pod_ip"),
        ready=bool(value["ready"]),
        deletion_timestamp=value.get("deletion_timestamp"),
        start_time=value.get("start_time"),
        containers=_containers(
            value.get("containers", [])
        ),
        conditions=_conditions(
            value.get("conditions", [])
        ),
    )


def _endpoint_slice(
    value: dict[str, Any],
) -> EndpointSliceObservation:
    return EndpointSliceObservation(
        name=value["name"],
        uid=value["uid"],
        endpoints=tuple(
            EndpointObservation(
                addresses=tuple(
                    endpoint.get("addresses", [])
                ),
                pod_name=endpoint.get("pod_name"),
                pod_uid=endpoint.get("pod_uid"),
                node_name=endpoint.get("node_name"),
                ready=endpoint.get("ready"),
                serving=endpoint.get("serving"),
                terminating=endpoint.get("terminating"),
            )
            for endpoint in value.get("endpoints", [])
        ),
    )


def _pdb(
    value: dict[str, Any],
) -> PDBObservation:
    return PDBObservation(**value)


def _hpa(
    value: dict[str, Any],
) -> HPAObservation:
    return HPAObservation(**value)


def _event(
    value: dict[str, Any],
) -> EventObservation:
    return EventObservation(**value)


def incident_context_from_dict(
    value: dict[str, Any],
) -> IncidentContext:
    source = value["source"]
    trigger = value["trigger"]
    current = value["current_state"]

    return IncidentContext(
        incident_id=value["incident_id"],
        incident_type=value["incident_type"],
        created_at=value["created_at"],
        source=IncidentSource(
            snapshot_id=source["snapshot_id"],
            collected_at=source["collected_at"],
            namespace=source["namespace"],
            workload_name=source["workload_name"],
            service_name=source["service_name"],
        ),
        trigger=IncidentTrigger(
            rule_id=trigger["rule_id"],
            target_kind=trigger["target_kind"],
            target_name=trigger["target_name"],
            target_uid=trigger["target_uid"],
            evidence_refs=tuple(
                trigger["evidence_refs"]
            ),
        ),
        current_state=IncidentCurrentState(
            deployment=_deployment(
                current["deployment"]
            ),
            current_replica_sets=tuple(
                _replica_set(item)
                for item in current.get(
                    "current_replica_sets",
                    [],
                )
            ),
            current_pods=tuple(
                _pod(item)
                for item in current.get(
                    "current_pods",
                    [],
                )
            ),
            endpoint_slices=tuple(
                _endpoint_slice(item)
                for item in current.get(
                    "endpoint_slices",
                    [],
                )
            ),
            pdbs=tuple(
                _pdb(item)
                for item in current.get(
                    "pdbs",
                    [],
                )
            ),
            hpas=tuple(
                _hpa(item)
                for item in current.get(
                    "hpas",
                    [],
                )
            ),
            topology=tuple(
                TopologyObservation(
                    node_name=item.get("node_name"),
                    pod_names=tuple(
                        item.get("pod_names", [])
                    ),
                )
                for item in current.get(
                    "topology",
                    [],
                )
            ),
        ),
        historical_evidence=tuple(
            _event(item)
            for item in value.get(
                "historical_evidence",
                [],
            )
        ),
        evidence=tuple(
            Evidence(
                evidence_id=item["evidence_id"],
                category=item["category"],
                source_kind=item["source_kind"],
                source_name=item["source_name"],
                source_uid=item.get("source_uid"),
                fact_type=item["fact_type"],
                fact=item["fact"],
                observed_at=item["observed_at"],
            )
            for item in value.get(
                "evidence",
                [],
            )
        ),
        schema_version=value.get(
            "schema_version",
            "v1alpha1",
        ),
    )


def load_incident_context(
    path: str | Path,
) -> IncidentContext:
    value = json.loads(
        Path(path).read_text()
    )

    if not isinstance(value, dict):
        raise ValueError(
            "IncidentContext JSON must contain an object"
        )

    return incident_context_from_dict(value)
