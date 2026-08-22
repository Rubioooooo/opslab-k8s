from __future__ import annotations

from opslab_aiops.context.detector import current_replica_set_uids
from opslab_aiops.diagnosis.models import DiagnosisResult
from opslab_aiops.observation.models import (
    ObservationSnapshot,
    PodObservation,
    ReplicaSetObservation,
)

from .models import SafetyDecision

ALLOW = "ALLOW"
DENY = "DENY"
REQUIRE_HUMAN = "REQUIRE_HUMAN"

EVICT_POD = "EVICT_POD"

IF_HEALTHY_BUDGET = "IfHealthyBudget"
ALWAYS_ALLOW = "AlwaysAllow"
KNOWN_UNHEALTHY_POD_EVICTION_POLICIES = frozenset(
    {
        IF_HEALTHY_BUDGET,
        ALWAYS_ALLOW,
    }
)


def _decision(
    diagnosis: DiagnosisResult,
    snapshot: ObservationSnapshot,
    *,
    human_approved: bool,
    decision: str,
    reason_codes: tuple[str, ...],
) -> SafetyDecision:
    return SafetyDecision(
        decision_id=(
            f"safety-{diagnosis.diagnosis_id}-"
            f"{snapshot.snapshot_id}"
        ),
        incident_id=diagnosis.incident_id,
        action=diagnosis.recommended_action,
        target_kind=diagnosis.affected_resource.kind,
        target_name=diagnosis.affected_resource.name,
        target_uid=diagnosis.affected_resource.uid,
        decision=decision,
        reason_codes=reason_codes,
        evaluated_at=snapshot.collected_at,
        fresh_snapshot_id=snapshot.snapshot_id,
        human_approved=human_approved,
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


def _pod_belongs_to_current_workload(
    pod: PodObservation,
    snapshot: ObservationSnapshot,
    current_rs_uids: frozenset[str],
    rs_by_uid: dict[str, ReplicaSetObservation],
) -> bool:
    if pod.owner_kind != "ReplicaSet":
        return False

    if pod.owner_uid is None:
        return False

    if pod.owner_uid not in current_rs_uids:
        return False

    replica_set = rs_by_uid.get(pod.owner_uid)

    if replica_set is None:
        return False

    return _replica_set_belongs_to_deployment(
        replica_set,
        snapshot,
    )


def evaluate_safety_decision(
    diagnosis: DiagnosisResult,
    snapshot: ObservationSnapshot,
    *,
    human_approved: bool,
) -> SafetyDecision:
    blockers: list[str] = []

    if diagnosis.schema_version != "v1alpha1":
        blockers.append("DENY_DIAGNOSIS_SCHEMA_UNSUPPORTED")

    if diagnosis.recommended_action != EVICT_POD:
        blockers.append("DENY_UNSUPPORTED_ACTION")

    if diagnosis.affected_resource.kind != "Pod":
        blockers.append("DENY_TARGET_KIND_UNSUPPORTED")

    if not diagnosis.requires_human_approval:
        blockers.append("DENY_DIAGNOSIS_APPROVAL_CONTRACT")

    if not isinstance(human_approved, bool):
        blockers.append("DENY_HUMAN_APPROVAL_INVALID")

    deployment = snapshot.deployment

    if deployment is None:
        blockers.append("DENY_DEPLOYMENT_MISSING")
    else:
        if (
            deployment.generation is None
            or deployment.observed_generation is None
            or deployment.generation
            != deployment.observed_generation
        ):
            blockers.append("DENY_DEPLOYMENT_STATUS_STALE")

        if (
            deployment.replicas is None
            or deployment.updated_replicas is None
            or deployment.replicas
            != deployment.updated_replicas
        ):
            blockers.append("DENY_ROLLOUT_IN_PROGRESS")

    same_name = tuple(
        pod
        for pod in snapshot.pods
        if pod.name == diagnosis.affected_resource.name
    )

    target = next(
        (
            pod
            for pod in same_name
            if pod.uid == diagnosis.affected_resource.uid
        ),
        None,
    )

    if not same_name:
        blockers.append("DENY_TARGET_NOT_FOUND")
    elif target is None:
        blockers.append("DENY_TARGET_UID_MISMATCH")

    current_rs_uids = current_replica_set_uids(snapshot)
    rs_by_uid = {
        replica_set.uid: replica_set
        for replica_set in snapshot.replica_sets
    }

    if target is not None:
        if target.deletion_timestamp is not None:
            blockers.append("DENY_TARGET_TERMINATING")

        if target.phase != "Running":
            blockers.append("DENY_TARGET_NOT_RUNNING")

        if target.ready:
            blockers.append("DENY_TARGET_RECOVERED")

        if not _pod_belongs_to_current_workload(
            target,
            snapshot,
            current_rs_uids,
            rs_by_uid,
        ):
            blockers.append("DENY_WORKLOAD_IDENTITY_MISMATCH")

        healthy_sibling_exists = any(
            pod.uid != target.uid
            and pod.deletion_timestamp is None
            and pod.phase == "Running"
            and pod.ready
            and _pod_belongs_to_current_workload(
                pod,
                snapshot,
                current_rs_uids,
                rs_by_uid,
            )
            for pod in snapshot.pods
        )

        if not healthy_sibling_exists:
            blockers.append("DENY_NO_HEALTHY_SIBLING")

        if target.phase == "Running" and not target.ready:
            if len(snapshot.pdbs) > 1:
                blockers.append("DENY_AMBIGUOUS_PDB")

            elif len(snapshot.pdbs) == 1:
                pdb = snapshot.pdbs[0]

                if (
                    pdb.generation is None
                    or pdb.observed_generation is None
                    or pdb.generation
                    != pdb.observed_generation
                ):
                    blockers.append("DENY_PDB_STATUS_STALE")
                else:
                    status_fields = (
                        pdb.disruptions_allowed,
                        pdb.current_healthy,
                        pdb.desired_healthy,
                        pdb.expected_pods,
                    )

                    if any(
                        value is None
                        for value in status_fields
                    ):
                        blockers.append(
                            "DENY_PDB_STATUS_INCOMPLETE"
                        )
                    else:
                        policy = (
                            pdb.unhealthy_pod_eviction_policy
                            or IF_HEALTHY_BUDGET
                        )

                        if (
                            policy
                            not in KNOWN_UNHEALTHY_POD_EVICTION_POLICIES
                        ):
                            blockers.append(
                                "DENY_PDB_UNHEALTHY_POLICY_UNKNOWN"
                            )

                        elif policy == IF_HEALTHY_BUDGET:
                            assert pdb.current_healthy is not None
                            assert pdb.desired_healthy is not None

                            if (
                                pdb.current_healthy
                                < pdb.desired_healthy
                            ):
                                blockers.append(
                                    "DENY_PDB_UNHEALTHY_EVICTION_BLOCKED"
                                )

    if blockers:
        return _decision(
            diagnosis,
            snapshot,
            human_approved=human_approved,
            decision=DENY,
            reason_codes=tuple(blockers),
        )

    if not human_approved:
        return _decision(
            diagnosis,
            snapshot,
            human_approved=False,
            decision=REQUIRE_HUMAN,
            reason_codes=("HUMAN_APPROVAL_REQUIRED",),
        )

    return _decision(
        diagnosis,
        snapshot,
        human_approved=True,
        decision=ALLOW,
        reason_codes=("ALL_SAFETY_CHECKS_PASSED",),
    )
