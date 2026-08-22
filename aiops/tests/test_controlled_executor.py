from __future__ import annotations

import unittest

from kubernetes.client.rest import ApiException

from opslab_aiops.execution.executor import ControlledExecutor
from opslab_aiops.execution.models import ExecutionRequest
from opslab_aiops.safety.models import SafetyDecision


TARGET_NAME = "opslab-api-797c8fdfbf-example"
TARGET_UID = "11111111-2222-3333-4444-555555555555"


def _decision(
    *,
    decision: str = "ALLOW",
    action: str = "EVICT_POD",
    target_kind: str = "Pod",
    target_name: str = TARGET_NAME,
    target_uid: str = TARGET_UID,
    human_approved: bool = True,
    schema_version: str = "v1alpha1",
) -> SafetyDecision:
    return SafetyDecision(
        decision_id="safety-test-001",
        incident_id="incident-test-001",
        action=action,
        target_kind=target_kind,
        target_name=target_name,
        target_uid=target_uid,
        decision=decision,
        reason_codes=("ALL_SAFETY_CHECKS_PASSED",),
        evaluated_at="2026-08-22T00:00:00Z",
        fresh_snapshot_id="obs-test-001",
        human_approved=human_approved,
        schema_version=schema_version,
    )


class FakeEvictionApi:
    def __init__(
        self,
        *,
        status_code: int = 201,
        exception: ApiException | None = None,
    ) -> None:
        self.status_code = status_code
        self.exception = exception
        self.calls: list[dict[str, object]] = []

    def create_namespaced_pod_eviction_with_http_info(
        self,
        name: str,
        namespace: str,
        body: object,
        **kwargs: object,
    ) -> tuple[object, int, dict[str, str]]:
        self.calls.append(
            {
                "name": name,
                "namespace": namespace,
                "body": body,
                "kwargs": kwargs,
            }
        )

        if self.exception is not None:
            raise self.exception

        return body, self.status_code, {}


class ControlledExecutorTests(unittest.TestCase):
    def _execute(
        self,
        api: FakeEvictionApi,
        *,
        safety_decision: SafetyDecision | None = None,
        namespace: str = "opslab",
    ):
        executor = ControlledExecutor(
            api,
            allowed_namespace="opslab",
        )

        request = ExecutionRequest(
            namespace=namespace,
            safety_decision=safety_decision or _decision(),
        )

        return executor.execute(request)

    def test_deny_safety_decision_is_refused_without_api_call(self) -> None:
        api = FakeEvictionApi()

        result = self._execute(
            api,
            safety_decision=_decision(decision="DENY"),
        )

        self.assertEqual(result.status, "REFUSED")
        self.assertEqual(
            result.reason_codes,
            ("REFUSE_SAFETY_DECISION_NOT_ALLOW",),
        )
        self.assertEqual(api.calls, [])

    def test_require_human_is_refused_without_api_call(self) -> None:
        api = FakeEvictionApi()

        result = self._execute(
            api,
            safety_decision=_decision(decision="REQUIRE_HUMAN"),
        )

        self.assertEqual(result.status, "REFUSED")
        self.assertEqual(
            result.reason_codes,
            ("REFUSE_SAFETY_DECISION_NOT_ALLOW",),
        )
        self.assertEqual(api.calls, [])

    def test_allow_without_human_approval_is_refused(self) -> None:
        api = FakeEvictionApi()

        result = self._execute(
            api,
            safety_decision=_decision(human_approved=False),
        )

        self.assertEqual(result.status, "REFUSED")
        self.assertEqual(
            result.reason_codes,
            ("REFUSE_HUMAN_APPROVAL_MISSING",),
        )
        self.assertEqual(api.calls, [])

    def test_unsupported_action_is_refused_without_api_call(self) -> None:
        api = FakeEvictionApi()

        result = self._execute(
            api,
            safety_decision=_decision(action="DELETE_POD"),
        )

        self.assertEqual(result.status, "REFUSED")
        self.assertEqual(
            result.reason_codes,
            ("REFUSE_UNSUPPORTED_ACTION",),
        )
        self.assertEqual(api.calls, [])

    def test_wrong_namespace_is_refused_without_api_call(self) -> None:
        api = FakeEvictionApi()

        result = self._execute(
            api,
            namespace="default",
        )

        self.assertEqual(result.status, "REFUSED")
        self.assertEqual(
            result.reason_codes,
            ("REFUSE_NAMESPACE_NOT_ALLOWED",),
        )
        self.assertEqual(api.calls, [])

    def test_allow_builds_uid_bound_policy_v1_eviction(self) -> None:
        api = FakeEvictionApi(status_code=201)

        result = self._execute(api)

        self.assertEqual(result.status, "ACCEPTED")
        self.assertEqual(result.reason_codes, ("EVICTION_ACCEPTED",))
        self.assertEqual(result.api_status_code, 201)

        self.assertEqual(len(api.calls), 1)

        call = api.calls[0]
        body = call["body"]

        self.assertEqual(call["name"], TARGET_NAME)
        self.assertEqual(call["namespace"], "opslab")

        self.assertEqual(body.api_version, "policy/v1")
        self.assertEqual(body.kind, "Eviction")
        self.assertEqual(body.metadata.name, TARGET_NAME)
        self.assertEqual(body.metadata.namespace, "opslab")

        self.assertIsNotNone(body.delete_options)
        self.assertIsNotNone(body.delete_options.preconditions)
        self.assertEqual(
            body.delete_options.preconditions.uid,
            TARGET_UID,
        )

    def test_uid_precondition_conflict_is_refused(self) -> None:
        api = FakeEvictionApi(
            exception=ApiException(
                status=409,
                reason="Conflict",
            )
        )

        result = self._execute(api)

        self.assertEqual(result.status, "REFUSED")
        self.assertEqual(result.reason_codes, ("EVICTION_CONFLICT",))
        self.assertEqual(result.api_status_code, 409)
        self.assertEqual(len(api.calls), 1)

    def test_pdb_or_eviction_policy_rejection_is_refused(self) -> None:
        api = FakeEvictionApi(
            exception=ApiException(
                status=429,
                reason="Too Many Requests",
            )
        )

        result = self._execute(api)

        self.assertEqual(result.status, "REFUSED")
        self.assertEqual(
            result.reason_codes,
            ("EVICTION_POLICY_BLOCKED",),
        )
        self.assertEqual(result.api_status_code, 429)
        self.assertEqual(len(api.calls), 1)

    def test_executor_never_needs_pod_get_for_valid_execution(self) -> None:
        # FakeEvictionApi deliberately implements no Pod GET operation.
        # A valid request must succeed using only the eviction subresource.
        api = FakeEvictionApi(status_code=202)

        result = self._execute(api)

        self.assertEqual(result.status, "ACCEPTED")
        self.assertEqual(result.api_status_code, 202)
        self.assertEqual(len(api.calls), 1)


if __name__ == "__main__":
    unittest.main()
