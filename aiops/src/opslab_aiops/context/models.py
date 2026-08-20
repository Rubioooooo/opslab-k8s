from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from opslab_aiops.observation.models import (
    DeploymentObservation,
    EndpointSliceObservation,
    EventObservation,
    HPAObservation,
    PDBObservation,
    PodObservation,
    ReplicaSetObservation,
)


@dataclass(frozen=True, slots=True)
class IncidentCandidate:
    incident_type: str
    rule_id: str

    target_kind: str
    target_name: str
    target_uid: str

    schema_version: str = "v1alpha1"


@dataclass(frozen=True, slots=True)
class IncidentSource:
    snapshot_id: str
    collected_at: str
    namespace: str
    workload_name: str
    service_name: str


@dataclass(frozen=True, slots=True)
class IncidentTrigger:
    rule_id: str

    target_kind: str
    target_name: str
    target_uid: str

    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TopologyObservation:
    node_name: str | None
    pod_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    category: str

    source_kind: str
    source_name: str
    source_uid: str | None

    fact_type: str
    fact: dict[str, Any]

    observed_at: str


@dataclass(frozen=True, slots=True)
class IncidentCurrentState:
    deployment: DeploymentObservation

    current_replica_sets: tuple[
        ReplicaSetObservation,
        ...,
    ]

    current_pods: tuple[
        PodObservation,
        ...,
    ]

    endpoint_slices: tuple[
        EndpointSliceObservation,
        ...,
    ]

    pdbs: tuple[
        PDBObservation,
        ...,
    ]

    hpas: tuple[
        HPAObservation,
        ...,
    ]

    topology: tuple[
        TopologyObservation,
        ...,
    ]


@dataclass(frozen=True, slots=True)
class IncidentContext:
    incident_id: str
    incident_type: str
    created_at: str

    source: IncidentSource
    trigger: IncidentTrigger

    current_state: IncidentCurrentState

    historical_evidence: tuple[
        EventObservation,
        ...,
    ]

    evidence: tuple[
        Evidence,
        ...,
    ]

    schema_version: str = "v1alpha1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )
