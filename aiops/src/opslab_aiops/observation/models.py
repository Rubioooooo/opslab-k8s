from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ConditionObservation:
    type: str
    status: str
    reason: str | None = None
    message: str | None = None
    last_transition_time: str | None = None


@dataclass(frozen=True, slots=True)
class DeploymentObservation:
    name: str
    uid: str
    generation: int | None
    observed_generation: int | None
    replicas: int | None
    updated_replicas: int | None
    ready_replicas: int | None
    available_replicas: int | None
    unavailable_replicas: int | None
    conditions: tuple[ConditionObservation, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ReplicaSetObservation:
    name: str
    uid: str
    owner_name: str | None
    replicas: int | None
    ready_replicas: int | None
    available_replicas: int | None


@dataclass(frozen=True, slots=True)
class ContainerObservation:
    name: str
    ready: bool
    restart_count: int
    state: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PodObservation:
    name: str
    uid: str
    owner_name: str | None
    node_name: str | None
    phase: str | None
    pod_ip: str | None
    ready: bool
    deletion_timestamp: str | None
    start_time: str | None
    containers: tuple[ContainerObservation, ...] = field(default_factory=tuple)
    conditions: tuple[ConditionObservation, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class EndpointObservation:
    addresses: tuple[str, ...]
    pod_name: str | None
    node_name: str | None
    ready: bool | None
    serving: bool | None
    terminating: bool | None


@dataclass(frozen=True, slots=True)
class EndpointSliceObservation:
    name: str
    uid: str
    endpoints: tuple[EndpointObservation, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PDBObservation:
    name: str
    uid: str
    disruptions_allowed: int | None
    current_healthy: int | None
    desired_healthy: int | None
    expected_pods: int | None


@dataclass(frozen=True, slots=True)
class HPAObservation:
    name: str
    uid: str
    min_replicas: int | None
    max_replicas: int
    current_replicas: int | None
    desired_replicas: int | None


@dataclass(frozen=True, slots=True)
class EventObservation:
    uid: str
    type: str | None
    reason: str | None
    message: str | None
    involved_kind: str | None
    involved_name: str | None
    involved_uid: str | None
    count: int | None
    timestamp: str | None
    reporting_controller: str | None


@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    snapshot_id: str
    collected_at: str
    namespace: str
    workload_name: str

    deployment: DeploymentObservation | None

    replica_sets: tuple[ReplicaSetObservation, ...] = field(default_factory=tuple)
    pods: tuple[PodObservation, ...] = field(default_factory=tuple)
    endpoint_slices: tuple[EndpointSliceObservation, ...] = field(default_factory=tuple)
    pdbs: tuple[PDBObservation, ...] = field(default_factory=tuple)
    hpas: tuple[HPAObservation, ...] = field(default_factory=tuple)
    events: tuple[EventObservation, ...] = field(default_factory=tuple)

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
