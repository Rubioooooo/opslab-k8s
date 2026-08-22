from __future__ import annotations

import unittest

from opslab_aiops.diagnosis.models import (
    AffectedResource,
    DiagnosisResult,
)
from opslab_aiops.observation.models import (
    DeploymentObservation,
    ObservationSnapshot,
    PDBObservation,
    PodObservation,
    ReplicaSetObservation,
)
from opslab_aiops.safety import (
    ALLOW,
    DENY,
    REQUIRE_HUMAN,
    evaluate_safety_decision,
)

DEPLOYMENT_UID = "deployment-uid"
RS_UID = "current-rs-uid"
TARGET_UID = "target-pod-uid"
HEALTHY_UID = "healthy-pod-uid"


def make_diagnosis(action: str = "EVICT_POD") -> DiagnosisResult:
    return DiagnosisResult(
        diagnosis_id="diag-test",
        incident_id="inc-test",
        summary="Running but NotReady",
        root_cause="bounded hypothesis",
        confidence="MEDIUM",
        evidence_refs=("EV-0001",),
        affected_resource=AffectedResource(
            kind="Pod",
            name="opslab-api-target",
            uid=TARGET_UID,
        ),
        recommended_action=action,
        rationale="replace unhealthy instance",
        risk_notes=("Safety Gate required",),
        requires_human_approval=(
            action in {"EVICT_POD", "ESCALATE"}
        ),
    )


def make_deployment(
    *,
    generation: int = 10,
    observed_generation: int = 10,
    replicas: int = 2,
    updated_replicas: int = 2,
) -> DeploymentObservation:
    return DeploymentObservation(
        name="opslab-api",
        uid=DEPLOYMENT_UID,
        generation=generation,
        observed_generation=observed_generation,
        replicas=replicas,
        updated_replicas=updated_replicas,
        ready_replicas=1,
        available_replicas=1,
        unavailable_replicas=1,
    )


def make_rs() -> ReplicaSetObservation:
    return ReplicaSetObservation(
        name="opslab-api-current",
        uid=RS_UID,
        owner_name="opslab-api",
        owner_kind="Deployment",
        owner_uid=DEPLOYMENT_UID,
        replicas=2,
        ready_replicas=1,
        available_replicas=1,
    )


def make_pod(
    name: str,
    uid: str,
    *,
    ready: bool,
    phase: str = "Running",
    deletion_timestamp: str | None = None,
    owner_uid: str = RS_UID,
) -> PodObservation:
    return PodObservation(
        name=name,
        uid=uid,
        owner_name="opslab-api-current",
        owner_kind="ReplicaSet",
        owner_uid=owner_uid,
        node_name="worker1",
        phase=phase,
        pod_ip=None,
        ready=ready,
        deletion_timestamp=deletion_timestamp,
        start_time="2026-08-22T00:00:00Z",
    )


def make_pdb(
    *,
    name: str = "opslab-api",
    generation: int = 1,
    observed_generation: int = 1,
    policy: str | None = None,
    current_healthy: int = 1,
    desired_healthy: int = 1,
    disruptions_allowed: int = 0,
) -> PDBObservation:
    return PDBObservation(
        name=name,
        uid=f"{name}-uid",
        disruptions_allowed=disruptions_allowed,
        current_healthy=current_healthy,
        desired_healthy=desired_healthy,
        expected_pods=2,
        generation=generation,
        observed_generation=observed_generation,
        unhealthy_pod_eviction_policy=policy,
    )


def make_snapshot(
    *,
    deployment: DeploymentObservation | None = None,
    target: PodObservation | None = None,
    siblings: tuple[PodObservation, ...] | None = None,
    pdbs: tuple[PDBObservation, ...] | None = None,
) -> ObservationSnapshot:
    return ObservationSnapshot(
        snapshot_id="obs-fresh-test",
        collected_at="2026-08-22T00:01:00Z",
        namespace="opslab",
        workload_name="opslab-api",
        service_name="opslab-api",
        deployment=deployment or make_deployment(),
        replica_sets=(make_rs(),),
        pods=(
            target
            or make_pod(
                "opslab-api-target",
                TARGET_UID,
                ready=False,
            ),
            *(
                siblings
                if siblings is not None
                else (
                    make_pod(
                        "opslab-api-healthy",
                        HEALTHY_UID,
                        ready=True,
                    ),
                )
            ),
        ),
        pdbs=(
            pdbs
            if pdbs is not None
            else (make_pdb(),)
        ),
    )


class SafetyGateTests(unittest.TestCase):
    def assert_denied(
        self,
        snapshot: ObservationSnapshot,
        reason: str,
        *,
        diagnosis: DiagnosisResult | None = None,
    ) -> None:
        result = evaluate_safety_decision(
            diagnosis or make_diagnosis(),
            snapshot,
            human_approved=True,
        )

        self.assertEqual(result.decision, DENY)
        self.assertIn(reason, result.reason_codes)

    def test_requires_explicit_human_approval(self) -> None:
        result = evaluate_safety_decision(
            make_diagnosis(),
            make_snapshot(),
            human_approved=False,
        )

        self.assertEqual(result.decision, REQUIRE_HUMAN)

    def test_safe_and_approved_allows(self) -> None:
        result = evaluate_safety_decision(
            make_diagnosis(),
            make_snapshot(),
            human_approved=True,
        )

        self.assertEqual(result.decision, ALLOW)

    def test_stale_or_changed_target_fails_closed(self) -> None:
        cases = (
            (
                make_snapshot(
                    target=make_pod(
                        "opslab-api-target",
                        TARGET_UID,
                        ready=True,
                    )
                ),
                "DENY_TARGET_RECOVERED",
            ),
            (
                make_snapshot(
                    target=make_pod(
                        "opslab-api-target",
                        "replacement-uid",
                        ready=False,
                    )
                ),
                "DENY_TARGET_UID_MISMATCH",
            ),
            (
                make_snapshot(
                    target=make_pod(
                        "opslab-api-target",
                        TARGET_UID,
                        ready=False,
                        deletion_timestamp=(
                            "2026-08-22T00:00:30Z"
                        ),
                    )
                ),
                "DENY_TARGET_TERMINATING",
            ),
            (
                make_snapshot(
                    deployment=make_deployment(
                        replicas=2,
                        updated_replicas=1,
                    )
                ),
                "DENY_ROLLOUT_IN_PROGRESS",
            ),
            (
                make_snapshot(
                    siblings=(
                        make_pod(
                            "opslab-api-sibling",
                            HEALTHY_UID,
                            ready=False,
                        ),
                    )
                ),
                "DENY_NO_HEALTHY_SIBLING",
            ),
        )

        for value, reason in cases:
            with self.subTest(reason=reason):
                self.assert_denied(value, reason)

    def test_pdb_semantics_fail_closed(self) -> None:
        cases = (
            (
                (
                    make_pdb(
                        generation=2,
                        observed_generation=1,
                    ),
                ),
                "DENY_PDB_STATUS_STALE",
            ),
            (
                (
                    make_pdb(
                        policy="IfHealthyBudget",
                        current_healthy=0,
                        desired_healthy=1,
                    ),
                ),
                "DENY_PDB_UNHEALTHY_EVICTION_BLOCKED",
            ),
            (
                (
                    make_pdb(policy="FuturePolicy"),
                ),
                "DENY_PDB_UNHEALTHY_POLICY_UNKNOWN",
            ),
            (
                (
                    make_pdb(name="pdb-a"),
                    make_pdb(name="pdb-b"),
                ),
                "DENY_AMBIGUOUS_PDB",
            ),
        )

        for pdbs, reason in cases:
            with self.subTest(reason=reason):
                self.assert_denied(
                    make_snapshot(pdbs=pdbs),
                    reason,
                )

    def test_default_if_healthy_budget_not_disruptions_only(
        self,
    ) -> None:
        result = evaluate_safety_decision(
            make_diagnosis(),
            make_snapshot(
                pdbs=(
                    make_pdb(
                        policy=None,
                        current_healthy=1,
                        desired_healthy=1,
                        disruptions_allowed=0,
                    ),
                )
            ),
            human_approved=True,
        )

        self.assertEqual(result.decision, ALLOW)

    def test_always_allow_passes_pdb_unhealthy_branch(self) -> None:
        result = evaluate_safety_decision(
            make_diagnosis(),
            make_snapshot(
                pdbs=(
                    make_pdb(
                        policy="AlwaysAllow",
                        current_healthy=0,
                        desired_healthy=1,
                        disruptions_allowed=0,
                    ),
                )
            ),
            human_approved=True,
        )

        self.assertEqual(result.decision, ALLOW)

    def test_no_pdb_has_no_pdb_specific_block(self) -> None:
        result = evaluate_safety_decision(
            make_diagnosis(),
            make_snapshot(pdbs=()),
            human_approved=True,
        )

        self.assertEqual(result.decision, ALLOW)

    def test_non_eviction_action_is_denied(self) -> None:
        self.assert_denied(
            make_snapshot(),
            "DENY_UNSUPPORTED_ACTION",
            diagnosis=make_diagnosis("NO_ACTION"),
        )


if __name__ == "__main__":
    unittest.main()
