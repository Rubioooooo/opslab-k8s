from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from opslab_aiops.observation.models import (
    ObservationSnapshot,
    PodObservation,
    ReplicaSetObservation,
)

from .models import VerificationRequest, VerificationResult


VERIFIED = "VERIFIED"
NOT_RECOVERED = "NOT_RECOVERED"
INCONCLUSIVE = "INCONCLUSIVE"

VERIFICATION_SCHEMA_VERSION = "v1alpha1"
SAFETY_SCHEMA_VERSION = "v1alpha1"
EXECUTION_SCHEMA_VERSION = "v1alpha1"
OBSERVATION_SCHEMA_VERSION = "v1alpha3"
PROBE_SCHEMA_VERSION = "v1alpha1"

ALLOW = "ALLOW"
ACCEPTED = "ACCEPTED"
SUPPORTED_ACTION = "EVICT_POD"
SUPPORTED_TARGET_KIND = "Pod"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None

    normalized = value.strip()

    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return None

    return parsed.astimezone(timezone.utc)


def _verification_result(
    request: VerificationRequest,
    *,
    outcome: str,
    reason_codes: tuple[str, ...],
    replacement_uid: str | None = None,
) -> VerificationResult:
    execution = request.execution_result

    return VerificationResult(
        verification_id=f"verify-{uuid4()}",
        incident_id=execution.incident_id,
        execution_id=execution.execution_id,
        target_uid=execution.target_uid,
        replacement_uid=replacement_uid,
        outcome=outcome,
        reason_codes=reason_codes,
        verified_at=_utc_now(),
        baseline_snapshot_id=request.baseline_snapshot.snapshot_id,
        fresh_snapshot_id=request.post_snapshot.snapshot_id,
        evidence_refs=request.evidence_refs,
    )


def _replica_set_belongs_to_deployment(
    replica_set: ReplicaSetObservation,
    snapshot: ObservationSnapshot,
) -> bool:
    deployment = snapshot.deployment

    if deployment is None:
        return False

    return (
        replica_set.owner_kind == "Deployment"
        and replica_set.owner_name == deployment.name
        and replica_set.owner_uid == deployment.uid
    )


def _pod_belongs_to_workload(
    pod: PodObservation,
    snapshot: ObservationSnapshot,
) -> bool:
    if pod.owner_kind != "ReplicaSet":
        return False

    if pod.owner_uid is None:
        return False

    replica_set = next(
        (
            item
            for item in snapshot.replica_sets
            if item.uid == pod.owner_uid
        ),
        None,
    )

    if replica_set is None:
        return False

    return _replica_set_belongs_to_deployment(
        replica_set,
        snapshot,
    )


def verify_recovery(
    request: VerificationRequest,
) -> VerificationResult:
    safety = request.safety_decision
    execution = request.execution_result
    baseline = request.baseline_snapshot
    post = request.post_snapshot
    probe = request.business_probe

    integrity_blockers: list[str] = []

    if request.schema_version != VERIFICATION_SCHEMA_VERSION:
        integrity_blockers.append(
            "VERIFICATION_REQUEST_SCHEMA_UNSUPPORTED"
        )

    if not request.evidence_refs:
        integrity_blockers.append(
            "VERIFICATION_EVIDENCE_MISSING"
        )

    if safety.schema_version != SAFETY_SCHEMA_VERSION:
        integrity_blockers.append(
            "SAFETY_DECISION_SCHEMA_UNSUPPORTED"
        )

    if execution.schema_version != EXECUTION_SCHEMA_VERSION:
        integrity_blockers.append(
            "EXECUTION_RESULT_SCHEMA_UNSUPPORTED"
        )

    if baseline.schema_version != OBSERVATION_SCHEMA_VERSION:
        integrity_blockers.append(
            "BASELINE_OBSERVATION_SCHEMA_UNSUPPORTED"
        )

    if post.schema_version != OBSERVATION_SCHEMA_VERSION:
        integrity_blockers.append(
            "POST_OBSERVATION_SCHEMA_UNSUPPORTED"
        )

    if probe.schema_version != PROBE_SCHEMA_VERSION:
        integrity_blockers.append(
            "BUSINESS_PROBE_SCHEMA_UNSUPPORTED"
        )

    if safety.decision != ALLOW:
        integrity_blockers.append(
            "SAFETY_DECISION_NOT_ALLOW"
        )

    if safety.human_approved is not True:
        integrity_blockers.append(
            "HUMAN_APPROVAL_NOT_PROVEN"
        )

    if safety.reason_codes != (
        "ALL_SAFETY_CHECKS_PASSED",
    ):
        integrity_blockers.append(
            "SAFETY_ALLOW_REASON_MISSING"
        )

    if safety.target_kind != SUPPORTED_TARGET_KIND:
        integrity_blockers.append(
            "TARGET_KIND_UNSUPPORTED"
        )

    if safety.action != SUPPORTED_ACTION:
        integrity_blockers.append(
            "ACTION_UNSUPPORTED"
        )

    if execution.status != ACCEPTED:
        integrity_blockers.append(
            "EXECUTION_NOT_ACCEPTED"
        )

    if "EVICTION_ACCEPTED" not in execution.reason_codes:
        integrity_blockers.append(
            "EXECUTION_ACCEPTANCE_REASON_MISSING"
        )

    if (
        execution.status == ACCEPTED
        and execution.api_status_code
        not in (200, 201, 202)
    ):
        integrity_blockers.append(
            "EXECUTION_API_STATUS_INCONSISTENT"
        )

    if safety.action != execution.action:
        integrity_blockers.append(
            "ACTION_BINDING_MISMATCH"
        )

    if safety.decision_id != execution.decision_id:
        integrity_blockers.append(
            "DECISION_ID_BINDING_MISMATCH"
        )

    if safety.incident_id != execution.incident_id:
        integrity_blockers.append(
            "INCIDENT_ID_BINDING_MISMATCH"
        )

    if safety.target_uid != execution.target_uid:
        integrity_blockers.append(
            "TARGET_UID_BINDING_MISMATCH"
        )

    if safety.target_name != execution.target_name:
        integrity_blockers.append(
            "TARGET_NAME_BINDING_MISMATCH"
        )

    if baseline.snapshot_id != safety.fresh_snapshot_id:
        integrity_blockers.append(
            "BASELINE_SNAPSHOT_BINDING_MISMATCH"
        )

    if baseline.collected_at != safety.evaluated_at:
        integrity_blockers.append(
            "BASELINE_TIMESTAMP_BINDING_MISMATCH"
        )

    if baseline.namespace != execution.namespace:
        integrity_blockers.append(
            "BASELINE_NAMESPACE_BINDING_MISMATCH"
        )

    if post.namespace != execution.namespace:
        integrity_blockers.append(
            "POST_NAMESPACE_BINDING_MISMATCH"
        )

    if baseline.workload_name != post.workload_name:
        integrity_blockers.append(
            "WORKLOAD_BINDING_MISMATCH"
        )

    if baseline.service_name != post.service_name:
        integrity_blockers.append(
            "SERVICE_BINDING_MISMATCH"
        )

    if post.deployment is None:
        integrity_blockers.append(
            "DEPLOYMENT_MISSING"
        )

    if probe.success:
        if (
            probe.status_code is None
            or not 200 <= probe.status_code < 300
            or probe.error is not None
        ):
            integrity_blockers.append(
                "BUSINESS_PROBE_RESULT_INCONSISTENT"
            )
    else:
        if (
            (
                probe.status_code is not None
                and 200 <= probe.status_code < 300
            )
            or (
                probe.status_code is None
                and not probe.error
            )
        ):
            integrity_blockers.append(
                "BUSINESS_PROBE_RESULT_INCONSISTENT"
            )

    baseline_time = _parse_timestamp(
        baseline.collected_at
    )
    execution_time = _parse_timestamp(
        execution.attempted_at
    )
    post_time = _parse_timestamp(
        post.collected_at
    )
    probe_time = _parse_timestamp(
        probe.checked_at
    )

    if (
        baseline_time is None
        or execution_time is None
        or post_time is None
        or probe_time is None
    ):
        integrity_blockers.append(
            "TIMESTAMP_INVALID"
        )
    else:
        if baseline_time >= execution_time:
            integrity_blockers.append(
                "BASELINE_NOT_PRE_EXECUTION"
            )

        if post_time <= execution_time:
            integrity_blockers.append(
                "POST_SNAPSHOT_NOT_FRESH"
            )

        if probe_time <= execution_time:
            integrity_blockers.append(
                "PROBE_RESULT_STALE"
            )

    baseline_deployment = baseline.deployment
    post_deployment = post.deployment

    if baseline_deployment is None:
        integrity_blockers.append(
            "BASELINE_DEPLOYMENT_MISSING"
        )
    elif baseline_deployment.generation is None:
        integrity_blockers.append(
            "BASELINE_DEPLOYMENT_GENERATION_UNKNOWN"
        )

    if (
        baseline_deployment is not None
        and post_deployment is not None
    ):
        if (
            baseline_deployment.uid
            != post_deployment.uid
        ):
            integrity_blockers.append(
                "DEPLOYMENT_IDENTITY_CHANGED"
            )

        if (
            baseline_deployment.generation
            is not None
            and post_deployment.generation
            is not None
            and baseline_deployment.generation
            != post_deployment.generation
        ):
            integrity_blockers.append(
                "DEPLOYMENT_GENERATION_CHANGED"
            )

    baseline_target = next(
        (
            pod
            for pod in baseline.pods
            if pod.uid == execution.target_uid
        ),
        None,
    )

    if baseline_target is None:
        integrity_blockers.append(
            "BASELINE_TARGET_UID_MISSING"
        )
    else:
        if baseline_target.name != execution.target_name:
            integrity_blockers.append(
                "BASELINE_TARGET_NAME_MISMATCH"
            )

        if not _pod_belongs_to_workload(
            baseline_target,
            baseline,
        ):
            integrity_blockers.append(
                "BASELINE_TARGET_WORKLOAD_IDENTITY_MISMATCH"
            )

    if integrity_blockers:
        return _verification_result(
            request,
            outcome=INCONCLUSIVE,
            reason_codes=tuple(integrity_blockers),
        )

    old_target_still_exists = any(
        pod.uid == execution.target_uid
        for pod in post.pods
    )

    if old_target_still_exists:
        return _verification_result(
            request,
            outcome=NOT_RECOVERED,
            reason_codes=("TARGET_UID_STILL_PRESENT",),
        )

    baseline_uids = {
        pod.uid
        for pod in baseline.pods
    }

    new_pods = tuple(
        pod
        for pod in post.pods
        if pod.uid not in baseline_uids
    )

    if not new_pods:
        return _verification_result(
            request,
            outcome=NOT_RECOVERED,
            reason_codes=("REPLACEMENT_NOT_FOUND",),
        )

    if len(new_pods) > 1:
        return _verification_result(
            request,
            outcome=INCONCLUSIVE,
            reason_codes=("REPLACEMENT_AMBIGUOUS",),
        )

    replacement = new_pods[0]

    if not _pod_belongs_to_workload(
        replacement,
        post,
    ):
        return _verification_result(
            request,
            outcome=INCONCLUSIVE,
            reason_codes=(
                "REPLACEMENT_WORKLOAD_IDENTITY_MISMATCH",
            ),
            replacement_uid=replacement.uid,
        )

    if (
        baseline_target is None
        or baseline_target.owner_kind != "ReplicaSet"
        or baseline_target.owner_uid is None
        or replacement.owner_kind != "ReplicaSet"
        or replacement.owner_uid is None
        or replacement.owner_uid
        != baseline_target.owner_uid
    ):
        return _verification_result(
            request,
            outcome=INCONCLUSIVE,
            reason_codes=(
                "REPLACEMENT_REPLICA_SET_CHANGED",
            ),
            replacement_uid=replacement.uid,
        )

    not_recovered: list[str] = []
    unknown: list[str] = []

    if replacement.phase is None:
        unknown.append(
            "REPLACEMENT_PHASE_UNKNOWN"
        )
    elif replacement.phase != "Running":
        not_recovered.append(
            "REPLACEMENT_NOT_RUNNING"
        )

    if not replacement.ready:
        not_recovered.append(
            "REPLACEMENT_NOT_READY"
        )

    if replacement.deletion_timestamp is not None:
        not_recovered.append(
            "REPLACEMENT_TERMINATING"
        )

    deployment = post.deployment

    if deployment is None:
        unknown.append(
            "DEPLOYMENT_MISSING"
        )
    else:
        if (
            deployment.generation is None
            or deployment.observed_generation is None
        ):
            unknown.append(
                "DEPLOYMENT_GENERATION_UNKNOWN"
            )
        elif (
            deployment.generation
            != deployment.observed_generation
        ):
            not_recovered.append(
                "DEPLOYMENT_GENERATION_NOT_CONVERGED"
            )

        replica_fields = (
            deployment.replicas,
            deployment.updated_replicas,
            deployment.ready_replicas,
            deployment.available_replicas,
        )

        if any(
            value is None
            for value in replica_fields
        ):
            unknown.append(
                "DEPLOYMENT_REPLICA_STATUS_INCOMPLETE"
            )
        else:
            assert deployment.replicas is not None
            assert deployment.updated_replicas is not None
            assert deployment.ready_replicas is not None
            assert deployment.available_replicas is not None

            if not (
                deployment.replicas
                == deployment.updated_replicas
                == deployment.ready_replicas
                == deployment.available_replicas
            ):
                not_recovered.append(
                    "DEPLOYMENT_REPLICAS_NOT_CONVERGED"
                )

        if deployment.unavailable_replicas not in (
            None,
            0,
        ):
            not_recovered.append(
                "DEPLOYMENT_UNAVAILABLE_REPLICAS_PRESENT"
            )

    replacement_endpoints = tuple(
        endpoint
        for endpoint_slice in post.endpoint_slices
        for endpoint in endpoint_slice.endpoints
        if endpoint.pod_uid == replacement.uid
    )

    if not replacement_endpoints:
        not_recovered.append(
            "REPLACEMENT_ENDPOINT_NOT_FOUND"
        )
    else:
        healthy_endpoint_exists = any(
            endpoint.ready is True
            and endpoint.serving is True
            and endpoint.terminating is not True
            for endpoint in replacement_endpoints
        )

        if not healthy_endpoint_exists:
            if any(
                endpoint.ready is None
                or endpoint.serving is None
                or endpoint.terminating is None
                for endpoint in replacement_endpoints
            ):
                unknown.append(
                    "REPLACEMENT_ENDPOINT_STATE_UNKNOWN"
                )
            else:
                if all(
                    endpoint.ready is not True
                    for endpoint in replacement_endpoints
                ):
                    not_recovered.append(
                        "REPLACEMENT_ENDPOINT_NOT_READY"
                    )

                if all(
                    endpoint.serving is not True
                    for endpoint in replacement_endpoints
                ):
                    not_recovered.append(
                        "REPLACEMENT_ENDPOINT_NOT_SERVING"
                    )

                if any(
                    endpoint.terminating is True
                    for endpoint in replacement_endpoints
                ):
                    not_recovered.append(
                        "REPLACEMENT_ENDPOINT_TERMINATING"
                    )

    if probe.success:
        if (
            probe.status_code is None
            or not 200 <= probe.status_code < 300
            or probe.error is not None
        ):
            unknown.append(
                "BUSINESS_PROBE_RESULT_INCONSISTENT"
            )
    else:
        not_recovered.append(
            "BUSINESS_PROBE_FAILED"
        )

    if not_recovered:
        return _verification_result(
            request,
            outcome=NOT_RECOVERED,
            reason_codes=tuple(not_recovered),
            replacement_uid=replacement.uid,
        )

    if unknown:
        return _verification_result(
            request,
            outcome=INCONCLUSIVE,
            reason_codes=tuple(unknown),
            replacement_uid=replacement.uid,
        )

    return _verification_result(
        request,
        outcome=VERIFIED,
        reason_codes=(
            "ALL_VERIFICATION_CHECKS_PASSED",
        ),
        replacement_uid=replacement.uid,
    )
