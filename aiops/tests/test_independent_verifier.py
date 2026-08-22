from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from opslab_aiops.execution.models import ExecutionResult
from opslab_aiops.observation.models import (
    DeploymentObservation,
    EndpointObservation,
    EndpointSliceObservation,
    ObservationSnapshot,
    PodObservation,
    ReplicaSetObservation,
)
from opslab_aiops.safety.models import SafetyDecision
from opslab_aiops.verification import (
    INCONCLUSIVE,
    NOT_RECOVERED,
    VERIFIED,
    BusinessProbeResult,
    VerificationRequest,
    perform_http_probe,
    verify_recovery,
)


DEPLOYMENT_UID = "deployment-uid"
RS_UID = "replicaset-uid"

TARGET_NAME = "opslab-api-old"
TARGET_UID = "old-pod-uid"

SIBLING_NAME = "opslab-api-sibling"
SIBLING_UID = "sibling-pod-uid"

REPLACEMENT_NAME = "opslab-api-replacement"
REPLACEMENT_UID = "replacement-pod-uid"

BASELINE_ID = "obs-baseline"
POST_ID = "obs-post"


def make_deployment(
    *,
    healthy: bool,
    generation: int = 10,
    observed_generation: int = 10,
) -> DeploymentObservation:
    return DeploymentObservation(
        name="opslab-api",
        uid=DEPLOYMENT_UID,
        generation=generation,
        observed_generation=observed_generation,
        replicas=2,
        updated_replicas=2,
        ready_replicas=2 if healthy else 1,
        available_replicas=2 if healthy else 1,
        unavailable_replicas=None if healthy else 1,
    )


def make_rs(
    *,
    owner_uid: str = DEPLOYMENT_UID,
) -> ReplicaSetObservation:
    return ReplicaSetObservation(
        name="opslab-api-rs",
        uid=RS_UID,
        owner_name="opslab-api",
        owner_kind="Deployment",
        owner_uid=owner_uid,
        replicas=2,
        ready_replicas=2,
        available_replicas=2,
    )


def make_pod(
    name: str,
    uid: str,
    *,
    ready: bool,
    phase: str | None = "Running",
    deletion_timestamp: str | None = None,
    owner_uid: str = RS_UID,
) -> PodObservation:
    return PodObservation(
        name=name,
        uid=uid,
        owner_name="opslab-api-rs",
        owner_kind="ReplicaSet",
        owner_uid=owner_uid,
        node_name="k8s-worker1",
        phase=phase,
        pod_ip="10.244.1.10",
        ready=ready,
        deletion_timestamp=deletion_timestamp,
        start_time="2026-08-22T00:00:00Z",
    )


def make_endpoint(
    uid: str = REPLACEMENT_UID,
    *,
    ready: bool | None = True,
    serving: bool | None = True,
    terminating: bool | None = False,
) -> EndpointObservation:
    return EndpointObservation(
        addresses=("10.244.1.20",),
        pod_name=REPLACEMENT_NAME,
        pod_uid=uid,
        node_name="k8s-worker2",
        ready=ready,
        serving=serving,
        terminating=terminating,
    )


def make_endpoint_slice(
    endpoint: EndpointObservation | None = None,
) -> EndpointSliceObservation:
    return EndpointSliceObservation(
        name="opslab-api-slice",
        uid="endpoint-slice-uid",
        endpoints=(
            endpoint or make_endpoint(),
        ),
    )


def make_baseline() -> ObservationSnapshot:
    return ObservationSnapshot(
        snapshot_id=BASELINE_ID,
        collected_at="2026-08-22T00:01:00Z",
        namespace="opslab",
        workload_name="opslab-api",
        service_name="opslab-api",
        deployment=make_deployment(healthy=False),
        replica_sets=(make_rs(),),
        pods=(
            make_pod(
                TARGET_NAME,
                TARGET_UID,
                ready=False,
            ),
            make_pod(
                SIBLING_NAME,
                SIBLING_UID,
                ready=True,
            ),
        ),
    )


def make_post(
    *,
    replacement: PodObservation | None = None,
    extra_pods: tuple[PodObservation, ...] = (),
    endpoint_slices: tuple[
        EndpointSliceObservation,
        ...,
    ] | None = None,
    deployment: DeploymentObservation | None = None,
    collected_at: str = "2026-08-22T00:03:00Z",
) -> ObservationSnapshot:
    return ObservationSnapshot(
        snapshot_id=POST_ID,
        collected_at=collected_at,
        namespace="opslab",
        workload_name="opslab-api",
        service_name="opslab-api",
        deployment=(
            deployment
            if deployment is not None
            else make_deployment(healthy=True)
        ),
        replica_sets=(make_rs(),),
        pods=(
            make_pod(
                SIBLING_NAME,
                SIBLING_UID,
                ready=True,
            ),
            replacement
            or make_pod(
                REPLACEMENT_NAME,
                REPLACEMENT_UID,
                ready=True,
            ),
            *extra_pods,
        ),
        endpoint_slices=(
            endpoint_slices
            if endpoint_slices is not None
            else (make_endpoint_slice(),)
        ),
    )


def make_safety() -> SafetyDecision:
    return SafetyDecision(
        decision_id="safety-001",
        incident_id="incident-001",
        action="EVICT_POD",
        target_kind="Pod",
        target_name=TARGET_NAME,
        target_uid=TARGET_UID,
        decision="ALLOW",
        reason_codes=(
            "ALL_SAFETY_CHECKS_PASSED",
        ),
        evaluated_at="2026-08-22T00:01:00Z",
        fresh_snapshot_id=BASELINE_ID,
        human_approved=True,
    )


def make_execution(
    *,
    status: str = "ACCEPTED",
    target_uid: str = TARGET_UID,
    attempted_at: str = "2026-08-22T00:02:00Z",
) -> ExecutionResult:
    return ExecutionResult(
        execution_id="exec-001",
        decision_id="safety-001",
        incident_id="incident-001",
        action="EVICT_POD",
        namespace="opslab",
        target_name=TARGET_NAME,
        target_uid=target_uid,
        status=status,
        reason_codes=(
            ("EVICTION_ACCEPTED",)
            if status == "ACCEPTED"
            else ("EVICTION_API_ERROR",)
        ),
        attempted_at=attempted_at,
        api_status_code=(
            201
            if status == "ACCEPTED"
            else 500
        ),
    )


def make_probe(
    *,
    success: bool = True,
    status_code: int | None = 200,
    checked_at: str = "2026-08-22T00:04:00Z",
) -> BusinessProbeResult:
    return BusinessProbeResult(
        url="http://api.opslab.local/readyz",
        success=success,
        status_code=status_code,
        checked_at=checked_at,
        error=None if success else "probe failed",
    )


def make_request(
    *,
    safety: SafetyDecision | None = None,
    execution: ExecutionResult | None = None,
    baseline: ObservationSnapshot | None = None,
    post: ObservationSnapshot | None = None,
    probe: BusinessProbeResult | None = None,
) -> VerificationRequest:
    return VerificationRequest(
        safety_decision=safety or make_safety(),
        execution_result=execution or make_execution(),
        baseline_snapshot=baseline or make_baseline(),
        post_snapshot=post or make_post(),
        business_probe=probe or make_probe(),
        evidence_refs=(
            "EV-EXECUTION",
            "EV-POST-SNAPSHOT",
            "EV-BUSINESS-PROBE",
        ),
    )


class IndependentVerifierTests(unittest.TestCase):
    def test_full_recovery_is_verified(self) -> None:
        result = verify_recovery(make_request())

        self.assertEqual(result.outcome, VERIFIED)
        self.assertEqual(
            result.reason_codes,
            ("ALL_VERIFICATION_CHECKS_PASSED",),
        )
        self.assertEqual(
            result.replacement_uid,
            REPLACEMENT_UID,
        )
        self.assertEqual(
            result.target_uid,
            TARGET_UID,
        )
        self.assertEqual(
            result.baseline_snapshot_id,
            BASELINE_ID,
        )
        self.assertEqual(
            result.fresh_snapshot_id,
            POST_ID,
        )

    def test_execution_not_accepted_is_inconclusive(
        self,
    ) -> None:
        result = verify_recovery(
            make_request(
                execution=make_execution(
                    status="REFUSED"
                )
            )
        )

        self.assertEqual(
            result.outcome,
            INCONCLUSIVE,
        )
        self.assertIn(
            "EXECUTION_NOT_ACCEPTED",
            result.reason_codes,
        )

    def test_target_uid_binding_mismatch_is_inconclusive(
        self,
    ) -> None:
        result = verify_recovery(
            make_request(
                execution=make_execution(
                    target_uid="wrong-uid"
                )
            )
        )

        self.assertEqual(
            result.outcome,
            INCONCLUSIVE,
        )
        self.assertIn(
            "TARGET_UID_BINDING_MISMATCH",
            result.reason_codes,
        )

    def test_baseline_snapshot_binding_is_enforced(
        self,
    ) -> None:
        safety = make_safety()

        safety = SafetyDecision(
            decision_id=safety.decision_id,
            incident_id=safety.incident_id,
            action=safety.action,
            target_kind=safety.target_kind,
            target_name=safety.target_name,
            target_uid=safety.target_uid,
            decision=safety.decision,
            reason_codes=safety.reason_codes,
            evaluated_at=safety.evaluated_at,
            fresh_snapshot_id="wrong-snapshot",
            human_approved=True,
        )

        result = verify_recovery(
            make_request(safety=safety)
        )

        self.assertEqual(
            result.outcome,
            INCONCLUSIVE,
        )
        self.assertIn(
            "BASELINE_SNAPSHOT_BINDING_MISMATCH",
            result.reason_codes,
        )

    def test_post_snapshot_must_be_post_execution(
        self,
    ) -> None:
        result = verify_recovery(
            make_request(
                post=make_post(
                    collected_at=(
                        "2026-08-22T00:02:00Z"
                    )
                )
            )
        )

        self.assertEqual(
            result.outcome,
            INCONCLUSIVE,
        )
        self.assertIn(
            "POST_SNAPSHOT_NOT_FRESH",
            result.reason_codes,
        )

    def test_old_target_still_present_is_not_recovered(
        self,
    ) -> None:
        post = make_post(
            extra_pods=(
                make_pod(
                    TARGET_NAME,
                    TARGET_UID,
                    ready=False,
                ),
            )
        )

        result = verify_recovery(
            make_request(post=post)
        )

        self.assertEqual(
            result.outcome,
            NOT_RECOVERED,
        )
        self.assertEqual(
            result.reason_codes,
            ("TARGET_UID_STILL_PRESENT",),
        )

    def test_missing_replacement_is_not_recovered(
        self,
    ) -> None:
        post = make_post()

        post = ObservationSnapshot(
            snapshot_id=post.snapshot_id,
            collected_at=post.collected_at,
            namespace=post.namespace,
            workload_name=post.workload_name,
            service_name=post.service_name,
            deployment=post.deployment,
            replica_sets=post.replica_sets,
            pods=(
                make_pod(
                    SIBLING_NAME,
                    SIBLING_UID,
                    ready=True,
                ),
            ),
            endpoint_slices=(),
        )

        result = verify_recovery(
            make_request(post=post)
        )

        self.assertEqual(
            result.outcome,
            NOT_RECOVERED,
        )
        self.assertEqual(
            result.reason_codes,
            ("REPLACEMENT_NOT_FOUND",),
        )

    def test_multiple_new_pods_are_ambiguous(
        self,
    ) -> None:
        result = verify_recovery(
            make_request(
                post=make_post(
                    extra_pods=(
                        make_pod(
                            "opslab-api-extra",
                            "extra-pod-uid",
                            ready=True,
                        ),
                    )
                )
            )
        )

        self.assertEqual(
            result.outcome,
            INCONCLUSIVE,
        )
        self.assertEqual(
            result.reason_codes,
            ("REPLACEMENT_AMBIGUOUS",),
        )

    def test_replacement_not_ready_is_not_recovered(
        self,
    ) -> None:
        result = verify_recovery(
            make_request(
                post=make_post(
                    replacement=make_pod(
                        REPLACEMENT_NAME,
                        REPLACEMENT_UID,
                        ready=False,
                    )
                )
            )
        )

        self.assertEqual(
            result.outcome,
            NOT_RECOVERED,
        )
        self.assertIn(
            "REPLACEMENT_NOT_READY",
            result.reason_codes,
        )

    def test_deployment_generation_not_converged(
        self,
    ) -> None:
        result = verify_recovery(
            make_request(
                post=make_post(
                    deployment=make_deployment(
                        healthy=True,
                        generation=10,
                        observed_generation=9,
                    )
                )
            )
        )

        self.assertEqual(
            result.outcome,
            NOT_RECOVERED,
        )
        self.assertIn(
            "DEPLOYMENT_GENERATION_NOT_CONVERGED",
            result.reason_codes,
        )

    def test_missing_replacement_endpoint_is_not_recovered(
        self,
    ) -> None:
        result = verify_recovery(
            make_request(
                post=make_post(
                    endpoint_slices=(),
                )
            )
        )

        self.assertEqual(
            result.outcome,
            NOT_RECOVERED,
        )
        self.assertIn(
            "REPLACEMENT_ENDPOINT_NOT_FOUND",
            result.reason_codes,
        )

    def test_endpoint_not_serving_is_not_recovered(
        self,
    ) -> None:
        endpoint = make_endpoint(
            serving=False,
        )

        result = verify_recovery(
            make_request(
                post=make_post(
                    endpoint_slices=(
                        make_endpoint_slice(
                            endpoint
                        ),
                    )
                )
            )
        )

        self.assertEqual(
            result.outcome,
            NOT_RECOVERED,
        )
        self.assertIn(
            "REPLACEMENT_ENDPOINT_NOT_SERVING",
            result.reason_codes,
        )

    def test_unknown_endpoint_state_is_inconclusive(
        self,
    ) -> None:
        endpoint = make_endpoint(
            ready=None,
            serving=None,
            terminating=None,
        )

        result = verify_recovery(
            make_request(
                post=make_post(
                    endpoint_slices=(
                        make_endpoint_slice(
                            endpoint
                        ),
                    )
                )
            )
        )

        self.assertEqual(
            result.outcome,
            INCONCLUSIVE,
        )
        self.assertIn(
            "REPLACEMENT_ENDPOINT_STATE_UNKNOWN",
            result.reason_codes,
        )

    def test_business_probe_failure_is_not_recovered(
        self,
    ) -> None:
        result = verify_recovery(
            make_request(
                probe=make_probe(
                    success=False,
                    status_code=503,
                )
            )
        )

        self.assertEqual(
            result.outcome,
            NOT_RECOVERED,
        )
        self.assertIn(
            "BUSINESS_PROBE_FAILED",
            result.reason_codes,
        )

    def test_stale_probe_is_inconclusive(
        self,
    ) -> None:
        result = verify_recovery(
            make_request(
                probe=make_probe(
                    checked_at=(
                        "2026-08-22T00:02:00Z"
                    )
                )
            )
        )

        self.assertEqual(
            result.outcome,
            INCONCLUSIVE,
        )
        self.assertIn(
            "PROBE_RESULT_STALE",
            result.reason_codes,
        )

    def test_result_serializes_binding_fields(
        self,
    ) -> None:
        result = verify_recovery(make_request())
        payload = result.to_dict()

        self.assertEqual(
            payload["incident_id"],
            "incident-001",
        )
        self.assertEqual(
            payload["execution_id"],
            "exec-001",
        )
        self.assertEqual(
            payload["target_uid"],
            TARGET_UID,
        )
        self.assertEqual(
            payload["replacement_uid"],
            REPLACEMENT_UID,
        )
        self.assertEqual(
            payload["schema_version"],
            "v1alpha1",
        )


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class BusinessProbeTests(unittest.TestCase):
    @patch(
        "opslab_aiops.verification.probe.requests.get"
    )
    def test_probe_2xx_is_success(
        self,
        get,
    ) -> None:
        get.return_value = FakeResponse(200)

        result = perform_http_probe(
            "http://api.opslab.local/readyz"
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.error)

    @patch(
        "opslab_aiops.verification.probe.requests.get"
    )
    def test_probe_non_2xx_is_failure(
        self,
        get,
    ) -> None:
        get.return_value = FakeResponse(503)

        result = perform_http_probe(
            "http://api.opslab.local/readyz"
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 503)

    @patch(
        "opslab_aiops.verification.probe.requests.get"
    )
    def test_probe_connection_error_is_failure(
        self,
        get,
    ) -> None:
        get.side_effect = requests.ConnectionError(
            "connection failed"
        )

        result = perform_http_probe(
            "http://api.opslab.local/readyz"
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.status_code)
        self.assertIn(
            "ConnectionError",
            result.error or "",
        )


from dataclasses import replace as dc_replace


class Phase5FailClosedMatrixTests(unittest.TestCase):
    def assert_inconclusive(
        self,
        request: VerificationRequest,
        reason: str,
    ) -> None:
        result = verify_recovery(request)

        self.assertEqual(
            result.outcome,
            INCONCLUSIVE,
        )
        self.assertIn(
            reason,
            result.reason_codes,
        )

    def assert_not_recovered(
        self,
        request: VerificationRequest,
        reason: str,
    ) -> None:
        result = verify_recovery(request)

        self.assertEqual(
            result.outcome,
            NOT_RECOVERED,
        )
        self.assertIn(
            reason,
            result.reason_codes,
        )

    def test_request_schema_fails_closed(self) -> None:
        request = dc_replace(
            make_request(),
            schema_version="v9",
        )

        self.assert_inconclusive(
            request,
            "VERIFICATION_REQUEST_SCHEMA_UNSUPPORTED",
        )

    def test_safety_schema_fails_closed(self) -> None:
        safety = dc_replace(
            make_safety(),
            schema_version="v9",
        )

        self.assert_inconclusive(
            make_request(safety=safety),
            "SAFETY_DECISION_SCHEMA_UNSUPPORTED",
        )

    def test_execution_schema_fails_closed(self) -> None:
        execution = dc_replace(
            make_execution(),
            schema_version="v9",
        )

        self.assert_inconclusive(
            make_request(execution=execution),
            "EXECUTION_RESULT_SCHEMA_UNSUPPORTED",
        )

    def test_baseline_schema_fails_closed(self) -> None:
        baseline = dc_replace(
            make_baseline(),
            schema_version="v9",
        )

        self.assert_inconclusive(
            make_request(baseline=baseline),
            "BASELINE_OBSERVATION_SCHEMA_UNSUPPORTED",
        )

    def test_post_schema_fails_closed(self) -> None:
        post = dc_replace(
            make_post(),
            schema_version="v9",
        )

        self.assert_inconclusive(
            make_request(post=post),
            "POST_OBSERVATION_SCHEMA_UNSUPPORTED",
        )

    def test_probe_schema_fails_closed(self) -> None:
        probe = dc_replace(
            make_probe(),
            schema_version="v9",
        )

        self.assert_inconclusive(
            make_request(probe=probe),
            "BUSINESS_PROBE_SCHEMA_UNSUPPORTED",
        )

    def test_allow_reason_must_be_proven(self) -> None:
        safety = dc_replace(
            make_safety(),
            reason_codes=("SOMETHING_ELSE",),
        )

        self.assert_inconclusive(
            make_request(safety=safety),
            "SAFETY_ALLOW_REASON_MISSING",
        )

    def test_action_binding_must_match(self) -> None:
        execution = dc_replace(
            make_execution(),
            action="DELETE_POD",
        )

        self.assert_inconclusive(
            make_request(execution=execution),
            "ACTION_BINDING_MISMATCH",
        )

    def test_safety_target_kind_must_be_pod(self) -> None:
        safety = dc_replace(
            make_safety(),
            target_kind="Deployment",
        )

        self.assert_inconclusive(
            make_request(safety=safety),
            "TARGET_KIND_UNSUPPORTED",
        )

    def test_accepted_execution_requires_success_api_status(
        self,
    ) -> None:
        execution = dc_replace(
            make_execution(),
            api_status_code=500,
        )

        self.assert_inconclusive(
            make_request(execution=execution),
            "EXECUTION_API_STATUS_INCONSISTENT",
        )

    def test_decision_id_binding_must_match(self) -> None:
        execution = dc_replace(
            make_execution(),
            decision_id="wrong-decision",
        )

        self.assert_inconclusive(
            make_request(execution=execution),
            "DECISION_ID_BINDING_MISMATCH",
        )

    def test_incident_id_binding_must_match(self) -> None:
        execution = dc_replace(
            make_execution(),
            incident_id="wrong-incident",
        )

        self.assert_inconclusive(
            make_request(execution=execution),
            "INCIDENT_ID_BINDING_MISMATCH",
        )

    def test_target_name_binding_must_match(self) -> None:
        execution = dc_replace(
            make_execution(),
            target_name="wrong-pod",
        )

        self.assert_inconclusive(
            make_request(execution=execution),
            "TARGET_NAME_BINDING_MISMATCH",
        )

    def test_baseline_timestamp_binding_must_match(
        self,
    ) -> None:
        safety = dc_replace(
            make_safety(),
            evaluated_at="2026-08-22T00:00:59Z",
        )

        self.assert_inconclusive(
            make_request(safety=safety),
            "BASELINE_TIMESTAMP_BINDING_MISMATCH",
        )

    def test_baseline_must_be_strictly_pre_execution(
        self,
    ) -> None:
        baseline = dc_replace(
            make_baseline(),
            collected_at="2026-08-22T00:02:00Z",
        )

        safety = dc_replace(
            make_safety(),
            evaluated_at="2026-08-22T00:02:00Z",
        )

        self.assert_inconclusive(
            make_request(
                safety=safety,
                baseline=baseline,
            ),
            "BASELINE_NOT_PRE_EXECUTION",
        )

    def test_invalid_timestamp_fails_closed(self) -> None:
        execution = dc_replace(
            make_execution(),
            attempted_at="not-a-timestamp",
        )

        self.assert_inconclusive(
            make_request(execution=execution),
            "TIMESTAMP_INVALID",
        )

    def test_post_namespace_must_match_execution(
        self,
    ) -> None:
        post = dc_replace(
            make_post(),
            namespace="default",
        )

        self.assert_inconclusive(
            make_request(post=post),
            "POST_NAMESPACE_BINDING_MISMATCH",
        )

    def test_workload_binding_must_match(self) -> None:
        post = dc_replace(
            make_post(),
            workload_name="another-workload",
        )

        self.assert_inconclusive(
            make_request(post=post),
            "WORKLOAD_BINDING_MISMATCH",
        )

    def test_service_binding_must_match(self) -> None:
        post = dc_replace(
            make_post(),
            service_name="another-service",
        )

        self.assert_inconclusive(
            make_request(post=post),
            "SERVICE_BINDING_MISMATCH",
        )

    def test_deployment_uid_change_is_inconclusive(
        self,
    ) -> None:
        post = make_post()

        deployment = dc_replace(
            post.deployment,
            uid="different-deployment-uid",
        )

        post = dc_replace(
            post,
            deployment=deployment,
        )

        self.assert_inconclusive(
            make_request(post=post),
            "DEPLOYMENT_IDENTITY_CHANGED",
        )

    def test_baseline_target_wrong_workload_is_inconclusive(
        self,
    ) -> None:
        baseline = make_baseline()

        wrong_target = make_pod(
            TARGET_NAME,
            TARGET_UID,
            ready=False,
            owner_uid="wrong-rs",
        )

        sibling = make_pod(
            SIBLING_NAME,
            SIBLING_UID,
            ready=True,
        )

        baseline = dc_replace(
            baseline,
            pods=(
                wrong_target,
                sibling,
            ),
        )

        self.assert_inconclusive(
            make_request(baseline=baseline),
            "BASELINE_TARGET_WORKLOAD_IDENTITY_MISMATCH",
        )

    def test_replacement_wrong_workload_is_inconclusive(
        self,
    ) -> None:
        replacement = make_pod(
            REPLACEMENT_NAME,
            REPLACEMENT_UID,
            ready=True,
            owner_uid="wrong-rs",
        )

        self.assert_inconclusive(
            make_request(
                post=make_post(
                    replacement=replacement,
                )
            ),
            "REPLACEMENT_WORKLOAD_IDENTITY_MISMATCH",
        )

    def test_replacement_not_running_is_not_recovered(
        self,
    ) -> None:
        replacement = make_pod(
            REPLACEMENT_NAME,
            REPLACEMENT_UID,
            ready=False,
            phase="Pending",
        )

        self.assert_not_recovered(
            make_request(
                post=make_post(
                    replacement=replacement,
                )
            ),
            "REPLACEMENT_NOT_RUNNING",
        )

    def test_replacement_terminating_is_not_recovered(
        self,
    ) -> None:
        replacement = make_pod(
            REPLACEMENT_NAME,
            REPLACEMENT_UID,
            ready=True,
            deletion_timestamp=(
                "2026-08-22T00:03:30Z"
            ),
        )

        self.assert_not_recovered(
            make_request(
                post=make_post(
                    replacement=replacement,
                )
            ),
            "REPLACEMENT_TERMINATING",
        )

    def test_missing_deployment_is_inconclusive(
        self,
    ) -> None:
        post = dc_replace(
            make_post(),
            deployment=None,
        )

        self.assert_inconclusive(
            make_request(post=post),
            "DEPLOYMENT_MISSING",
        )

    def test_incomplete_deployment_replica_status_is_inconclusive(
        self,
    ) -> None:
        deployment = dc_replace(
            make_deployment(healthy=True),
            ready_replicas=None,
        )

        self.assert_inconclusive(
            make_request(
                post=make_post(
                    deployment=deployment,
                )
            ),
            "DEPLOYMENT_REPLICA_STATUS_INCOMPLETE",
        )

    def test_unavailable_replicas_is_not_recovered(
        self,
    ) -> None:
        deployment = dc_replace(
            make_deployment(healthy=True),
            unavailable_replicas=1,
        )

        self.assert_not_recovered(
            make_request(
                post=make_post(
                    deployment=deployment,
                )
            ),
            "DEPLOYMENT_UNAVAILABLE_REPLICAS_PRESENT",
        )

    def test_endpoint_not_ready_is_not_recovered(
        self,
    ) -> None:
        endpoint = make_endpoint(
            ready=False,
            serving=True,
            terminating=False,
        )

        post = make_post(
            endpoint_slices=(
                make_endpoint_slice(endpoint),
            )
        )

        self.assert_not_recovered(
            make_request(post=post),
            "REPLACEMENT_ENDPOINT_NOT_READY",
        )

    def test_endpoint_terminating_is_not_recovered(
        self,
    ) -> None:
        endpoint = make_endpoint(
            ready=True,
            serving=True,
            terminating=True,
        )

        post = make_post(
            endpoint_slices=(
                make_endpoint_slice(endpoint),
            )
        )

        self.assert_not_recovered(
            make_request(post=post),
            "REPLACEMENT_ENDPOINT_TERMINATING",
        )

    def test_successful_probe_with_bad_status_is_inconclusive(
        self,
    ) -> None:
        probe = dc_replace(
            make_probe(),
            success=True,
            status_code=503,
        )

        self.assert_inconclusive(
            make_request(probe=probe),
            "BUSINESS_PROBE_RESULT_INCONSISTENT",
        )

    def test_failed_probe_with_http_200_is_inconclusive(
        self,
    ) -> None:
        probe = BusinessProbeResult(
            url="http://api.opslab.local/readyz",
            success=False,
            status_code=200,
            checked_at="2026-08-22T00:04:00Z",
            error=None,
        )

        self.assert_inconclusive(
            make_request(probe=probe),
            "BUSINESS_PROBE_RESULT_INCONSISTENT",
        )

    def test_verified_result_requires_evidence_refs(
        self,
    ) -> None:
        request = dc_replace(
            make_request(),
            evidence_refs=(),
        )

        self.assert_inconclusive(
            request,
            "VERIFICATION_EVIDENCE_MISSING",
        )


class Phase5ConcurrentControllerGuardTests(
    unittest.TestCase
):
    def test_baseline_deployment_missing_is_inconclusive(
        self,
    ) -> None:
        baseline = dc_replace(
            make_baseline(),
            deployment=None,
        )

        result = verify_recovery(
            make_request(
                baseline=baseline,
            )
        )

        self.assertEqual(
            result.outcome,
            INCONCLUSIVE,
        )
        self.assertIn(
            "BASELINE_DEPLOYMENT_MISSING",
            result.reason_codes,
        )

    def test_concurrent_deployment_generation_change_is_inconclusive(
        self,
    ) -> None:
        deployment = dc_replace(
            make_deployment(healthy=True),
            generation=11,
            observed_generation=11,
        )

        post = make_post(
            deployment=deployment,
        )

        result = verify_recovery(
            make_request(post=post)
        )

        self.assertEqual(
            result.outcome,
            INCONCLUSIVE,
        )
        self.assertIn(
            "DEPLOYMENT_GENERATION_CHANGED",
            result.reason_codes,
        )

    def test_replacement_from_different_replica_set_is_inconclusive(
        self,
    ) -> None:
        new_rs = dc_replace(
            make_rs(),
            name="opslab-api-new-rs",
            uid="new-rs-uid",
        )

        replacement = make_pod(
            REPLACEMENT_NAME,
            REPLACEMENT_UID,
            ready=True,
            owner_uid="new-rs-uid",
        )

        post = make_post(
            replacement=replacement,
        )

        post = dc_replace(
            post,
            replica_sets=(
                make_rs(),
                new_rs,
            ),
        )

        result = verify_recovery(
            make_request(post=post)
        )

        self.assertEqual(
            result.outcome,
            INCONCLUSIVE,
        )
        self.assertIn(
            "REPLACEMENT_REPLICA_SET_CHANGED",
            result.reason_codes,
        )


if __name__ == "__main__":
    unittest.main()
