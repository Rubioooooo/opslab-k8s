from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from kubernetes import client

from .models import (
    ConditionObservation,
    ContainerObservation,
    DeploymentObservation,
    EndpointObservation,
    EndpointSliceObservation,
    EventObservation,
    HPAObservation,
    ObservationSnapshot,
    PDBObservation,
    PodObservation,
    ReplicaSetObservation,
)


def _iso(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")

    return str(value)


def _owner_name(metadata: Any) -> str | None:
    references = getattr(metadata, "owner_references", None) or ()

    for reference in references:
        if getattr(reference, "controller", False):
            return reference.name

    if references:
        return references[0].name

    return None


def _conditions(
    items: Any,
) -> tuple[ConditionObservation, ...]:
    result: list[ConditionObservation] = []

    for item in items or ():
        result.append(
            ConditionObservation(
                type=str(item.type),
                status=str(item.status),
                reason=getattr(item, "reason", None),
                message=getattr(item, "message", None),
                last_transition_time=_iso(
                    getattr(item, "last_transition_time", None)
                ),
            )
        )

    return tuple(result)


def _container_state(status: Any) -> tuple[str, str | None]:
    state = status.state

    if state is None:
        return "unknown", None

    if state.running is not None:
        return "running", None

    if state.waiting is not None:
        return "waiting", state.waiting.reason

    if state.terminated is not None:
        return "terminated", state.terminated.reason

    return "unknown", None


def _pod_ready(status: Any) -> bool:
    for condition in status.conditions or ():
        if condition.type == "Ready":
            return str(condition.status).lower() == "true"

    return False


def _selector_matches(
    labels: dict[str, str],
    selector: Any,
) -> bool:
    if selector is None:
        return False

    for key, value in (selector.match_labels or {}).items():
        if labels.get(key) != value:
            return False

    for expression in selector.match_expressions or ():
        key = expression.key
        operator = expression.operator
        values = set(expression.values or ())

        if operator == "In":
            if labels.get(key) not in values:
                return False

        elif operator == "NotIn":
            if labels.get(key) in values:
                return False

        elif operator == "Exists":
            if key not in labels:
                return False

        elif operator == "DoesNotExist":
            if key in labels:
                return False

        else:
            return False

    return True


class KubernetesObservationCollector:
    def __init__(
        self,
        api_client: client.ApiClient,
        *,
        namespace: str,
        workload_name: str,
        service_name: str | None = None,
    ) -> None:
        self.namespace = namespace
        self.workload_name = workload_name
        self.service_name = service_name or workload_name

        self.core = client.CoreV1Api(api_client)
        self.apps = client.AppsV1Api(api_client)
        self.discovery = client.DiscoveryV1Api(api_client)
        self.policy = client.PolicyV1Api(api_client)
        self.autoscaling = client.AutoscalingV2Api(api_client)

    def collect(self) -> ObservationSnapshot:
        deployment_raw = self.apps.read_namespaced_deployment(
            name=self.workload_name,
            namespace=self.namespace,
        )

        deployment = DeploymentObservation(
            name=deployment_raw.metadata.name,
            uid=str(deployment_raw.metadata.uid),
            generation=deployment_raw.metadata.generation,
            observed_generation=deployment_raw.status.observed_generation,
            replicas=deployment_raw.status.replicas,
            updated_replicas=deployment_raw.status.updated_replicas,
            ready_replicas=deployment_raw.status.ready_replicas,
            available_replicas=deployment_raw.status.available_replicas,
            unavailable_replicas=deployment_raw.status.unavailable_replicas,
            conditions=_conditions(deployment_raw.status.conditions),
        )

        replica_sets_raw = (
            self.apps.list_namespaced_replica_set(
                namespace=self.namespace,
            ).items
        )

        relevant_rs_raw = [
            item
            for item in replica_sets_raw
            if _owner_name(item.metadata) == self.workload_name
        ]

        replica_sets = tuple(
            ReplicaSetObservation(
                name=item.metadata.name,
                uid=str(item.metadata.uid),
                owner_name=_owner_name(item.metadata),
                replicas=item.status.replicas,
                ready_replicas=item.status.ready_replicas,
                available_replicas=item.status.available_replicas,
            )
            for item in sorted(
                relevant_rs_raw,
                key=lambda obj: obj.metadata.name,
            )
        )

        rs_names = {
            item.metadata.name
            for item in relevant_rs_raw
        }

        pods_raw = self.core.list_namespaced_pod(
            namespace=self.namespace,
        ).items

        relevant_pods_raw = [
            item
            for item in pods_raw
            if _owner_name(item.metadata) in rs_names
        ]

        pods: list[PodObservation] = []

        for item in sorted(
            relevant_pods_raw,
            key=lambda obj: obj.metadata.name,
        ):
            containers: list[ContainerObservation] = []

            for status in item.status.container_statuses or ():
                state, reason = _container_state(status)

                containers.append(
                    ContainerObservation(
                        name=status.name,
                        ready=bool(status.ready),
                        restart_count=int(status.restart_count),
                        state=state,
                        reason=reason,
                    )
                )

            pods.append(
                PodObservation(
                    name=item.metadata.name,
                    uid=str(item.metadata.uid),
                    owner_name=_owner_name(item.metadata),
                    node_name=item.spec.node_name,
                    phase=item.status.phase,
                    pod_ip=item.status.pod_ip,
                    ready=_pod_ready(item.status),
                    deletion_timestamp=_iso(
                        item.metadata.deletion_timestamp
                    ),
                    start_time=_iso(item.status.start_time),
                    containers=tuple(containers),
                    conditions=_conditions(item.status.conditions),
                )
            )

        endpoint_slices_raw = (
            self.discovery.list_namespaced_endpoint_slice(
                namespace=self.namespace,
            ).items
        )

        relevant_endpoint_slices_raw = [
            item
            for item in endpoint_slices_raw
            if (
                item.metadata.labels or {}
            ).get("kubernetes.io/service-name") == self.service_name
        ]

        endpoint_slices: list[EndpointSliceObservation] = []

        for item in sorted(
            relevant_endpoint_slices_raw,
            key=lambda obj: obj.metadata.name,
        ):
            endpoints: list[EndpointObservation] = []

            for endpoint in item.endpoints or ():
                target_ref = endpoint.target_ref
                conditions = endpoint.conditions

                endpoints.append(
                    EndpointObservation(
                        addresses=tuple(endpoint.addresses or ()),
                        pod_name=(
                            target_ref.name
                            if target_ref is not None
                            and target_ref.kind == "Pod"
                            else None
                        ),
                        node_name=endpoint.node_name,
                        ready=conditions.ready,
                        serving=conditions.serving,
                        terminating=conditions.terminating,
                    )
                )

            endpoint_slices.append(
                EndpointSliceObservation(
                    name=item.metadata.name,
                    uid=str(item.metadata.uid),
                    endpoints=tuple(endpoints),
                )
            )

        target_pod_labels = [
            item.metadata.labels or {}
            for item in relevant_pods_raw
        ]

        pdbs_raw = self.policy.list_namespaced_pod_disruption_budget(
            namespace=self.namespace,
        ).items

        relevant_pdbs_raw = [
            item
            for item in pdbs_raw
            if any(
                _selector_matches(labels, item.spec.selector)
                for labels in target_pod_labels
            )
        ]

        pdbs = tuple(
            PDBObservation(
                name=item.metadata.name,
                uid=str(item.metadata.uid),
                disruptions_allowed=item.status.disruptions_allowed,
                current_healthy=item.status.current_healthy,
                desired_healthy=item.status.desired_healthy,
                expected_pods=item.status.expected_pods,
            )
            for item in sorted(
                relevant_pdbs_raw,
                key=lambda obj: obj.metadata.name,
            )
        )

        hpas_raw = (
            self.autoscaling.list_namespaced_horizontal_pod_autoscaler(
                namespace=self.namespace,
            ).items
        )

        relevant_hpas_raw = [
            item
            for item in hpas_raw
            if (
                item.spec.scale_target_ref.kind == "Deployment"
                and item.spec.scale_target_ref.name == self.workload_name
            )
        ]

        hpas = tuple(
            HPAObservation(
                name=item.metadata.name,
                uid=str(item.metadata.uid),
                min_replicas=item.spec.min_replicas,
                max_replicas=item.spec.max_replicas,
                current_replicas=item.status.current_replicas,
                desired_replicas=item.status.desired_replicas,
            )
            for item in sorted(
                relevant_hpas_raw,
                key=lambda obj: obj.metadata.name,
            )
        )

        relevant_names = {
            self.workload_name,
            *(item.metadata.name for item in relevant_rs_raw),
            *(item.metadata.name for item in relevant_pods_raw),
        }

        relevant_uids = {
            str(deployment_raw.metadata.uid),
            *(str(item.metadata.uid) for item in relevant_rs_raw),
            *(str(item.metadata.uid) for item in relevant_pods_raw),
        }

        events_raw = self.core.list_namespaced_event(
            namespace=self.namespace,
        ).items

        relevant_events_raw = [
            item
            for item in events_raw
            if (
                str(item.involved_object.uid) in relevant_uids
                or item.involved_object.name in relevant_names
            )
        ]

        events = tuple(
            EventObservation(
                uid=str(item.metadata.uid),
                type=item.type,
                reason=item.reason,
                message=item.message,
                involved_kind=item.involved_object.kind,
                involved_name=item.involved_object.name,
                involved_uid=(
                    str(item.involved_object.uid)
                    if item.involved_object.uid is not None
                    else None
                ),
                count=item.count,
                timestamp=_iso(
                    item.event_time
                    or item.last_timestamp
                    or item.first_timestamp
                    or item.metadata.creation_timestamp
                ),
                reporting_controller=item.reporting_component,
            )
            for item in sorted(
                relevant_events_raw,
                key=lambda obj: (
                    _iso(
                        obj.event_time
                        or obj.last_timestamp
                        or obj.first_timestamp
                        or obj.metadata.creation_timestamp
                    )
                    or ""
                ),
            )
        )

        now = datetime.now(timezone.utc)

        return ObservationSnapshot(
            snapshot_id=(
                f"obs-{now.strftime('%Y%m%dT%H%M%SZ')}-"
                f"{uuid4().hex[:8]}"
            ),
            collected_at=_iso(now) or "",
            namespace=self.namespace,
            workload_name=self.workload_name,
            deployment=deployment,
            replica_sets=replica_sets,
            pods=tuple(pods),
            endpoint_slices=tuple(endpoint_slices),
            pdbs=pdbs,
            hpas=hpas,
            events=events,
        )
