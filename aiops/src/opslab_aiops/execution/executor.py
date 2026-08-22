from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from kubernetes import client
from kubernetes.client.rest import ApiException

from opslab_aiops.execution.models import ExecutionRequest, ExecutionResult
from opslab_aiops.safety.models import SafetyDecision


EXECUTION_SCHEMA_VERSION = "v1alpha1"
SUPPORTED_SAFETY_SCHEMA_VERSION = "v1alpha1"

SUPPORTED_ACTION = "EVICT_POD"
SUPPORTED_TARGET_KIND = "Pod"
ALLOW_DECISION = "ALLOW"

STATUS_ACCEPTED = "ACCEPTED"
STATUS_REFUSED = "REFUSED"


class EvictionApi(Protocol):
    def create_namespaced_pod_eviction_with_http_info(
        self,
        name: str,
        namespace: str,
        body: client.V1Eviction,
        **kwargs: object,
    ) -> object:
        ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _execution_id() -> str:
    return f"exec-{uuid4()}"


def _result(
    *,
    request: ExecutionRequest,
    status: str,
    reason_code: str,
    api_status_code: int | None = None,
) -> ExecutionResult:
    decision = request.safety_decision

    return ExecutionResult(
        execution_id=_execution_id(),
        decision_id=decision.decision_id,
        incident_id=decision.incident_id,
        action=decision.action,
        namespace=request.namespace,
        target_name=decision.target_name,
        target_uid=decision.target_uid,
        status=status,
        reason_codes=(reason_code,),
        attempted_at=_utc_now(),
        api_status_code=api_status_code,
    )


class ControlledExecutor:
    """
    Phase 4 controlled execution boundary.

    This class does not perform diagnosis or Safety Gate reasoning.

    It only accepts an already-authorized SafetyDecision and performs the
    single supported operation:

        EVICT_POD -> policy/v1 Eviction

    Pod identity is bound atomically at the Kubernetes API server by
    deleteOptions.preconditions.uid.

    The executor intentionally does not GET the Pod before eviction.
    """

    def __init__(
        self,
        eviction_api: EvictionApi,
        *,
        allowed_namespace: str,
    ) -> None:
        if not allowed_namespace:
            raise ValueError("allowed_namespace must not be empty")

        self._eviction_api = eviction_api
        self._allowed_namespace = allowed_namespace

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        decision: SafetyDecision = request.safety_decision

        if request.schema_version != EXECUTION_SCHEMA_VERSION:
            return _result(
                request=request,
                status=STATUS_REFUSED,
                reason_code="REFUSE_EXECUTION_SCHEMA_UNSUPPORTED",
            )

        if decision.schema_version != SUPPORTED_SAFETY_SCHEMA_VERSION:
            return _result(
                request=request,
                status=STATUS_REFUSED,
                reason_code="REFUSE_SAFETY_DECISION_SCHEMA_UNSUPPORTED",
            )

        if decision.decision != ALLOW_DECISION:
            return _result(
                request=request,
                status=STATUS_REFUSED,
                reason_code="REFUSE_SAFETY_DECISION_NOT_ALLOW",
            )

        if decision.action != SUPPORTED_ACTION:
            return _result(
                request=request,
                status=STATUS_REFUSED,
                reason_code="REFUSE_UNSUPPORTED_ACTION",
            )

        if decision.target_kind != SUPPORTED_TARGET_KIND:
            return _result(
                request=request,
                status=STATUS_REFUSED,
                reason_code="REFUSE_TARGET_KIND_UNSUPPORTED",
            )

        if decision.human_approved is not True:
            return _result(
                request=request,
                status=STATUS_REFUSED,
                reason_code="REFUSE_HUMAN_APPROVAL_MISSING",
            )

        if request.namespace != self._allowed_namespace:
            return _result(
                request=request,
                status=STATUS_REFUSED,
                reason_code="REFUSE_NAMESPACE_NOT_ALLOWED",
            )

        if not decision.target_name or not decision.target_uid:
            return _result(
                request=request,
                status=STATUS_REFUSED,
                reason_code="REFUSE_TARGET_IDENTITY_INCOMPLETE",
            )

        eviction = client.V1Eviction(
            api_version="policy/v1",
            kind="Eviction",
            metadata=client.V1ObjectMeta(
                name=decision.target_name,
                namespace=request.namespace,
            ),
            delete_options=client.V1DeleteOptions(
                preconditions=client.V1Preconditions(
                    uid=decision.target_uid,
                ),
            ),
        )

        try:
            response = (
                self._eviction_api.create_namespaced_pod_eviction_with_http_info(
                    name=decision.target_name,
                    namespace=request.namespace,
                    body=eviction,
                    _return_http_data_only=False,
                )
            )

        except ApiException as exc:
            status_code = exc.status

            if status_code == 409:
                reason = "EVICTION_CONFLICT"
            elif status_code == 429:
                reason = "EVICTION_POLICY_BLOCKED"
            elif status_code == 404:
                reason = "EVICTION_TARGET_NOT_FOUND"
            elif status_code == 403:
                reason = "EVICTION_FORBIDDEN"
            elif status_code == 401:
                reason = "EVICTION_UNAUTHORIZED"
            else:
                reason = "EVICTION_API_ERROR"

            return _result(
                request=request,
                status=STATUS_REFUSED,
                reason_code=reason,
                api_status_code=status_code,
            )

        if not isinstance(response, tuple) or len(response) < 2:
            return _result(
                request=request,
                status=STATUS_REFUSED,
                reason_code="EVICTION_API_RESPONSE_INVALID",
            )

        api_status_code = response[1]

        if api_status_code not in (200, 201, 202):
            return _result(
                request=request,
                status=STATUS_REFUSED,
                reason_code="EVICTION_API_UNEXPECTED_STATUS",
                api_status_code=api_status_code,
            )

        return _result(
            request=request,
            status=STATUS_ACCEPTED,
            reason_code="EVICTION_ACCEPTED",
            api_status_code=api_status_code,
        )
