from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from kubernetes import client as k8s_client

from opslab_aiops.context import (
    Evidence,
    build_incident_context,
    detect_fastapi_unhealthy_instances,
)
from opslab_aiops.diagnosis import HermesAdapter
from opslab_aiops.execution import (
    ControlledExecutor,
    ExecutionRequest,
    ExecutorClientConfig,
    build_executor_api_client,
)
from opslab_aiops.observation import (
    KubernetesObservationCollector,
    ObserverClientConfig,
    build_api_client,
)
from opslab_aiops.safety import (
    ALLOW,
    REQUIRE_HUMAN,
    evaluate_safety_decision,
)
from opslab_aiops.verification import (
    VERIFIED,
    VerificationRequest,
    perform_http_probe,
    verify_recovery,
)


NAMESPACE = "opslab"
WORKLOAD = "opslab-api"
SERVICE = "opslab-api"

BUSINESS_URL = (
    "http://api.opslab.local/api/v1/events/1"
)
READINESS_URL = (
    "http://api.opslab.local/readyz"
)

TARGET_NAME = os.environ.get(
    "PHASE5G_TARGET_NAME",
    "",
).strip()

TARGET_UID = os.environ.get(
    "PHASE5G_TARGET_UID",
    "",
).strip()

TARGET_NODE = os.environ.get(
    "PHASE5G_TARGET_NODE",
    "",
).strip()

EVIDENCE_DIR = Path(
    os.environ.get(
        "PHASE5G_EVIDENCE_DIR",
        (
            "../docs/hermes/evidence/"
            "phase5/phase5g/attempt1"
        ),
    )
).resolve()


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {
            key: jsonable(item)
            for key, item in asdict(value).items()
        }

    if isinstance(value, dict):
        return {
            str(key): jsonable(item)
            for key, item in value.items()
        }

    if isinstance(value, (tuple, list)):
        return [
            jsonable(item)
            for item in value
        ]

    return value


def write_json(
    name: str,
    value: Any,
) -> None:
    EVIDENCE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = EVIDENCE_DIR / name

    path.write_text(
        json.dumps(
            jsonable(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def write_text(
    name: str,
    value: str,
) -> None:
    EVIDENCE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    (EVIDENCE_DIR / name).write_text(
        value,
        encoding="utf-8",
    )


def target_from_snapshot(snapshot):
    return next(
        (
            pod
            for pod in snapshot.pods
            if (
                pod.name == TARGET_NAME
                and pod.uid == TARGET_UID
            )
        ),
        None,
    )


def deployment_converged(snapshot) -> bool:
    deployment = snapshot.deployment

    if deployment is None:
        return False

    required = (
        deployment.generation,
        deployment.observed_generation,
        deployment.replicas,
        deployment.updated_replicas,
        deployment.ready_replicas,
        deployment.available_replicas,
    )

    if any(
        value is None
        for value in required
    ):
        return False

    return (
        deployment.generation
        == deployment.observed_generation
        and deployment.replicas
        == deployment.updated_replicas
        == deployment.ready_replicas
        == deployment.available_replicas
        and deployment.unavailable_replicas
        in (None, 0)
    )


def replacement_endpoint_healthy(
    snapshot,
    replacement_uid: str,
) -> bool:
    endpoints = [
        endpoint
        for slice_ in snapshot.endpoint_slices
        for endpoint in slice_.endpoints
        if endpoint.pod_uid == replacement_uid
    ]

    return any(
        endpoint.ready is True
        and endpoint.serving is True
        and endpoint.terminating is not True
        for endpoint in endpoints
    )


def target_readiness_semantic_probe(
    *,
    pod_name: str,
    pod_uid: str,
    pod_ip: str | None,
) -> dict[str, Any]:
    checked_at = utc_now()

    if not pod_ip:
        return {
            "pod_name": pod_name,
            "pod_uid": pod_uid,
            "pod_ip": None,
            "probe_path": "/readyz",
            "checked_at": checked_at,
            "http_status": None,
            "status": None,
            "mysql": None,
            "redis": None,
            "error": "POD_IP_MISSING",
        }

    url = (
        f"http://{pod_ip}:8000/readyz"
    )

    session = requests.Session()

    # Direct PodIP observation must not be diverted
    # through an HTTP proxy from the shell environment.
    session.trust_env = False

    try:
        try:
            response = session.get(
                url,
                timeout=2.0,
            )

        except requests.RequestException as exc:
            return {
                "pod_name": pod_name,
                "pod_uid": pod_uid,
                "pod_ip": pod_ip,
                "probe_path": "/readyz",
                "checked_at": checked_at,
                "http_status": None,
                "status": None,
                "mysql": None,
                "redis": None,
                "error": (
                    f"{type(exc).__name__}: {exc}"
                )[:500],
            }

        try:
            body = response.json()
        except ValueError:
            body = None

        if not isinstance(body, dict):
            return {
                "pod_name": pod_name,
                "pod_uid": pod_uid,
                "pod_ip": pod_ip,
                "probe_path": "/readyz",
                "checked_at": checked_at,
                "http_status": response.status_code,
                "status": None,
                "mysql": None,
                "redis": None,
                "error": "NON_JSON_OBJECT_RESPONSE",
            }

        def safe_string(key: str) -> str | None:
            value = body.get(key)

            if isinstance(value, str):
                return value[:100]

            return None

        return {
            "pod_name": pod_name,
            "pod_uid": pod_uid,
            "pod_ip": pod_ip,
            "probe_path": "/readyz",
            "checked_at": checked_at,
            "http_status": response.status_code,
            "status": safe_string("status"),
            "mysql": safe_string("mysql"),
            "redis": safe_string("redis"),
            "error": None,
        }

    finally:
        session.close()


def readiness_semantic_probe() -> dict[str, Any]:
    checked_at = utc_now()

    try:
        response = requests.get(
            READINESS_URL,
            timeout=3.0,
        )
    except requests.RequestException as exc:
        return {
            "checked_at": checked_at,
            "success": False,
            "status_code": None,
            "body": None,
            "error": (
                f"{type(exc).__name__}: {exc}"
            ),
        }

    try:
        body = response.json()
    except ValueError:
        body = None

    success = (
        response.status_code == 200
        and isinstance(body, dict)
        and body.get("status") == "ready"
        and body.get("mysql") == "ok"
        and body.get("redis") == "ok"
    )

    return {
        "checked_at": checked_at,
        "success": success,
        "status_code": response.status_code,
        "body": body,
        "error": None,
    }


def main() -> int:
    if not TARGET_NAME:
        raise RuntimeError(
            "PHASE5G_TARGET_NAME is required"
        )

    if not TARGET_UID:
        raise RuntimeError(
            "PHASE5G_TARGET_UID is required"
        )

    if not TARGET_NODE:
        raise RuntimeError(
            "PHASE5G_TARGET_NODE is required"
        )

    print(
        f"target_name={TARGET_NAME}"
    )
    print(
        f"target_uid={TARGET_UID}"
    )
    print(
        f"target_node={TARGET_NODE}"
    )

    observer_config = (
        ObserverClientConfig.from_env()
    )

    executor_config = (
        ExecutorClientConfig.from_env()
    )

    hermes = HermesAdapter()

    hermes_preflight = hermes.preflight()

    write_json(
        "00-hermes-preflight.json",
        hermes_preflight,
    )

    if (
        hermes_preflight.get("status") != "ok"
        or hermes_preflight.get(
            "tool_count"
        ) != 0
    ):
        raise RuntimeError(
            "Hermes no-tool preflight failed"
        )

    print(
        "HERMES_NO_TOOL_PREFLIGHT=PASS"
    )

    with (
        build_api_client(
            observer_config
        ) as observer_api_client,
        build_executor_api_client(
            executor_config
        ) as executor_api_client,
    ):
        collector = KubernetesObservationCollector(
            observer_api_client,
            namespace=NAMESPACE,
            workload_name=WORKLOAD,
            service_name=SERVICE,
        )

        core_api = k8s_client.CoreV1Api(
            executor_api_client
        )

        executor = ControlledExecutor(
            core_api,
            allowed_namespace=NAMESPACE,
        )

        # -----------------------------------------
        # 1. Wait for the real fault to become
        #    Running-but-NotReady.
        # -----------------------------------------

        print(
            "WAITING_FOR_REAL_FAULT=YES"
        )

        fault_snapshot = None
        candidate = None

        deadline = time.monotonic() + 90

        while time.monotonic() < deadline:
            snapshot = collector.collect()

            target = target_from_snapshot(
                snapshot
            )

            if target is None:
                raise RuntimeError(
                    "target identity disappeared "
                    "before diagnosis"
                )

            if target.node_name != TARGET_NODE:
                raise RuntimeError(
                    "target node changed"
                )

            candidates = (
                detect_fastapi_unhealthy_instances(
                    snapshot
                )
            )

            matching = tuple(
                item
                for item in candidates
                if (
                    item.target_name
                    == TARGET_NAME
                    and item.target_uid
                    == TARGET_UID
                )
            )

            print(
                "fault_poll="
                f"snapshot:{snapshot.snapshot_id} "
                f"phase:{target.phase} "
                f"ready:{target.ready} "
                f"candidate_count:{len(candidates)}"
            )

            if len(candidates) > 1:
                write_json(
                    "01-ambiguous-fault-snapshot.json",
                    snapshot,
                )
                raise RuntimeError(
                    "multiple incident candidates"
                )

            if len(matching) == 1:
                fault_snapshot = snapshot
                candidate = matching[0]
                break

            time.sleep(1)

        if (
            fault_snapshot is None
            or candidate is None
        ):
            raise RuntimeError(
                "fault did not produce target candidate"
            )

        write_json(
            "01-fault-snapshot.json",
            fault_snapshot,
        )

        print(
            "REAL_FAULT_CANDIDATE=PASS"
        )
        print(
            f"fault_snapshot_id="
            f"{fault_snapshot.snapshot_id}"
        )

        # -----------------------------------------
        # 2. Deterministic IncidentContext
        # -----------------------------------------

        context = build_incident_context(
            fault_snapshot,
            candidate,
        )

        fault_target = target_from_snapshot(
            fault_snapshot
        )

        if fault_target is None:
            raise RuntimeError(
                "target disappeared before "
                "semantic readiness probe"
            )

        readiness_fact = (
            target_readiness_semantic_probe(
                pod_name=fault_target.name,
                pod_uid=fault_target.uid,
                pod_ip=fault_target.pod_ip,
            )
        )

        write_json(
            "02a-target-readiness-probe.json",
            readiness_fact,
        )

        print(
            "target_probe_http_status="
            f"{readiness_fact['http_status']}"
        )
        print(
            "target_probe_status="
            f"{readiness_fact['status']}"
        )
        print(
            "target_probe_mysql="
            f"{readiness_fact['mysql']}"
        )
        print(
            "target_probe_redis="
            f"{readiness_fact['redis']}"
        )

        # Attempt4 is specifically validating the
        # observed per-Pod MySQL dependency fault.
        # Do not manufacture Evidence when the live
        # semantic probe does not prove that state.
        if (
            readiness_fact["error"] is not None
            or readiness_fact["http_status"] != 503
            or readiness_fact["status"] != "not_ready"
            or readiness_fact["mysql"] != "error"
            or readiness_fact["redis"] != "ok"
        ):
            raise RuntimeError(
                "target readiness semantics did not "
                "prove the expected live MySQL fault"
            )

        evidence_id = (
            f"EV-{len(context.evidence) + 1:04d}"
        )

        readiness_evidence = Evidence(
            evidence_id=evidence_id,
            category="CURRENT_STATE",
            source_kind="ApplicationProbe",
            source_name=fault_target.name,
            source_uid=fault_target.uid,
            fact_type=(
                "TARGET_READINESS_SEMANTICS"
            ),
            fact={
                "probe_path": (
                    readiness_fact["probe_path"]
                ),
                "http_status": (
                    readiness_fact["http_status"]
                ),
                "status": (
                    readiness_fact["status"]
                ),
                "mysql": (
                    readiness_fact["mysql"]
                ),
                "redis": (
                    readiness_fact["redis"]
                ),
            },
            observed_at=(
                readiness_fact["checked_at"]
            ),
        )

        sibling_candidates = tuple(
            pod
            for pod in fault_snapshot.pods
            if (
                pod.uid != fault_target.uid
                and pod.owner_kind == "ReplicaSet"
                and pod.owner_uid
                == fault_target.owner_uid
                and pod.phase == "Running"
                and pod.ready is True
                and pod.deletion_timestamp is None
            )
        )

        if len(sibling_candidates) != 1:
            raise RuntimeError(
                "expected exactly one healthy "
                "same-ReplicaSet sibling; "
                f"found {len(sibling_candidates)}"
            )

        sibling = sibling_candidates[0]

        sibling_readiness_fact = (
            target_readiness_semantic_probe(
                pod_name=sibling.name,
                pod_uid=sibling.uid,
                pod_ip=sibling.pod_ip,
            )
        )

        write_json(
            "02b-sibling-readiness-probe.json",
            sibling_readiness_fact,
        )

        print(
            f"sibling_name={sibling.name}"
        )
        print(
            f"sibling_uid={sibling.uid}"
        )
        print(
            "sibling_probe_http_status="
            f"{sibling_readiness_fact['http_status']}"
        )
        print(
            "sibling_probe_status="
            f"{sibling_readiness_fact['status']}"
        )
        print(
            "sibling_probe_mysql="
            f"{sibling_readiness_fact['mysql']}"
        )
        print(
            "sibling_probe_redis="
            f"{sibling_readiness_fact['redis']}"
        )

        if (
            sibling_readiness_fact["error"] is not None
            or sibling_readiness_fact["http_status"] != 200
            or sibling_readiness_fact["status"] != "ready"
            or sibling_readiness_fact["mysql"] != "ok"
            or sibling_readiness_fact["redis"] != "ok"
        ):
            raise RuntimeError(
                "healthy sibling readiness semantics "
                "were not proven"
            )

        sibling_evidence_id = (
            f"EV-{len(context.evidence) + 2:04d}"
        )

        sibling_readiness_evidence = Evidence(
            evidence_id=sibling_evidence_id,
            category="CURRENT_STATE",
            source_kind="ApplicationProbe",
            source_name=sibling.name,
            source_uid=sibling.uid,
            fact_type=(
                "SIBLING_READINESS_SEMANTICS"
            ),
            fact={
                "probe_path": (
                    sibling_readiness_fact["probe_path"]
                ),
                "http_status": (
                    sibling_readiness_fact["http_status"]
                ),
                "status": (
                    sibling_readiness_fact["status"]
                ),
                "mysql": (
                    sibling_readiness_fact["mysql"]
                ),
                "redis": (
                    sibling_readiness_fact["redis"]
                ),
            },
            observed_at=(
                sibling_readiness_fact["checked_at"]
            ),
        )

        context = replace(
            context,
            evidence=(
                *context.evidence,
                readiness_evidence,
                sibling_readiness_evidence,
            ),
        )

        print(
            "sibling_semantic_evidence_id="
            f"{sibling_evidence_id}"
        )
        print(
            "SIBLING_READINESS_EVIDENCE=PASS"
        )

        write_json(
            "02-incident-context.json",
            context,
        )

        print(
            f"incident_id={context.incident_id}"
        )
        print(
            f"semantic_evidence_id="
            f"{evidence_id}"
        )
        print(
            "TARGET_READINESS_EVIDENCE=PASS"
        )

        # -----------------------------------------
        # 3. REAL Hermes diagnosis
        # -----------------------------------------

        print(
            "REAL_HERMES_DIAGNOSIS=START"
        )

        hermes_run = hermes.diagnose(
            context
        )

        diagnosis = hermes_run.diagnosis

        write_text(
            "03-hermes-raw-response.txt",
            hermes_run.raw_response,
        )

        write_json(
            "04-hermes-run-metadata.json",
            {
                "model": hermes_run.model,
                "provider": hermes_run.provider,
                "tool_count": hermes_run.tool_count,
            },
        )

        write_json(
            "05-validated-diagnosis.json",
            diagnosis,
        )

        print(
            f"hermes_model={hermes_run.model}"
        )
        print(
            f"hermes_provider="
            f"{hermes_run.provider}"
        )
        print(
            f"hermes_tool_count="
            f"{hermes_run.tool_count}"
        )
        print(
            f"diagnosis_action="
            f"{diagnosis.recommended_action}"
        )

        if hermes_run.tool_count != 0:
            raise RuntimeError(
                "Hermes exposed tools"
            )

        if (
            diagnosis.recommended_action
            != "EVICT_POD"
        ):
            write_json(
                "05a-hermes-safe-abort.json",
                {
                    "incident_id": (
                        diagnosis.incident_id
                    ),
                    "target_name": TARGET_NAME,
                    "target_uid": TARGET_UID,
                    "recommended_action": (
                        diagnosis.recommended_action
                    ),
                    "model": hermes_run.model,
                    "provider": hermes_run.provider,
                    "tool_count": (
                        hermes_run.tool_count
                    ),
                    "eviction_api_called": False,
                    "verification_performed": False,
                    "recorded_at": utc_now(),
                },
            )

            print(
                "PHASE5G_SAFE_ABORT="
                f"HERMES_{diagnosis.recommended_action}"
            )
            print(
                "PHASE5G_EVICTION_NOT_CALLED=PASS"
            )

            return 4

        print(
            "REAL_HERMES_VALIDATED_DIAGNOSIS=PASS"
        )

        # -----------------------------------------
        # 4. Safety Gate before approval
        # -----------------------------------------

        require_human = (
            evaluate_safety_decision(
                diagnosis,
                fault_snapshot,
                human_approved=False,
            )
        )

        write_json(
            "06-require-human.json",
            require_human,
        )

        print(
            f"pre_approval_decision="
            f"{require_human.decision}"
        )
        print(
            "pre_approval_reasons="
            + ",".join(
                require_human.reason_codes
            )
        )

        if (
            require_human.decision
            != REQUIRE_HUMAN
        ):
            raise RuntimeError(
                "Safety Gate did not require "
                "human approval"
            )

        print(
            "SAFETY_REQUIRE_HUMAN=PASS"
        )

        print()
        print(
            "========== HUMAN APPROVAL =========="
        )
        print(
            f"Incident: {diagnosis.incident_id}"
        )
        print(
            f"Target: {TARGET_NAME}"
        )
        print(
            f"UID: {TARGET_UID}"
        )
        print(
            "Action: EVICT_POD"
        )
        print(
            "Type exactly APPROVE_EVICTION "
            "to continue."
        )

        approval = input(
            "approval> "
        ).strip()

        if approval != "APPROVE_EVICTION":
            print(
                "HUMAN_APPROVAL=REJECTED"
            )
            return 3

        print(
            "HUMAN_APPROVAL=PASS"
        )

        # -----------------------------------------
        # 5. Fresh pre-execution observation
        # -----------------------------------------

        pre_execution = collector.collect()

        write_json(
            "07-pre-execution-snapshot.json",
            pre_execution,
        )

        target = target_from_snapshot(
            pre_execution
        )

        if target is None:
            raise RuntimeError(
                "target disappeared before execution"
            )

        if target.ready:
            raise RuntimeError(
                "target recovered before execution"
            )

        allow_decision = (
            evaluate_safety_decision(
                diagnosis,
                pre_execution,
                human_approved=True,
            )
        )

        write_json(
            "08-allow-decision.json",
            allow_decision,
        )

        print(
            f"post_approval_decision="
            f"{allow_decision.decision}"
        )
        print(
            "post_approval_reasons="
            + ",".join(
                allow_decision.reason_codes
            )
        )

        if allow_decision.decision != ALLOW:
            raise RuntimeError(
                "fresh Safety Gate did not ALLOW"
            )

        print(
            "SAFETY_ALLOW=PASS"
        )

        baseline_deployment = (
            pre_execution.deployment
        )

        if baseline_deployment is None:
            raise RuntimeError(
                "baseline deployment missing"
            )

        baseline_uids = {
            pod.uid
            for pod in pre_execution.pods
        }

        baseline_target = target

        # -----------------------------------------
        # 6. REAL Controlled Executor
        # -----------------------------------------

        execution = executor.execute(
            ExecutionRequest(
                namespace=NAMESPACE,
                safety_decision=allow_decision,
            )
        )

        write_json(
            "09-execution-result.json",
            execution,
        )

        print(
            f"execution_id="
            f"{execution.execution_id}"
        )
        print(
            f"execution_status="
            f"{execution.status}"
        )
        print(
            "execution_reasons="
            + ",".join(
                execution.reason_codes
            )
        )
        print(
            f"execution_api_status="
            f"{execution.api_status_code}"
        )

        if execution.status != "ACCEPTED":
            raise RuntimeError(
                "real eviction was not accepted"
            )

        if (
            "EVICTION_ACCEPTED"
            not in execution.reason_codes
        ):
            raise RuntimeError(
                "execution acceptance reason missing"
            )

        print(
            "REAL_EVICTION_ACCEPTED=PASS"
        )

        # -----------------------------------------
        # 7. Independent recovery polling
        # -----------------------------------------

        poll_log: list[dict[str, Any]] = []

        post_snapshot = None
        replacement_uid = None

        deadline = time.monotonic() + 120

        while time.monotonic() < deadline:
            snapshot = collector.collect()

            deployment = snapshot.deployment

            current_uids = {
                pod.uid
                for pod in snapshot.pods
            }

            added_uids = sorted(
                current_uids - baseline_uids
            )

            old_exists = (
                TARGET_UID in current_uids
            )

            poll_entry = {
                "snapshot_id": (
                    snapshot.snapshot_id
                ),
                "collected_at": (
                    snapshot.collected_at
                ),
                "old_target_exists": (
                    old_exists
                ),
                "added_uids": added_uids,
                "deployment_generation": (
                    deployment.generation
                    if deployment
                    else None
                ),
                "deployment_observed_generation": (
                    deployment.observed_generation
                    if deployment
                    else None
                ),
                "ready_replicas": (
                    deployment.ready_replicas
                    if deployment
                    else None
                ),
                "available_replicas": (
                    deployment.available_replicas
                    if deployment
                    else None
                ),
            }

            poll_log.append(
                poll_entry
            )

            print(
                "recovery_poll="
                f"snapshot:{snapshot.snapshot_id} "
                f"old_exists:{old_exists} "
                f"added:{added_uids} "
                f"ready:"
                f"{deployment.ready_replicas if deployment else None}"
            )

            if deployment is None:
                write_json(
                    "10-recovery-poll.json",
                    poll_log,
                )
                raise RuntimeError(
                    "deployment disappeared"
                )

            if (
                deployment.uid
                != baseline_deployment.uid
            ):
                write_json(
                    "10-recovery-poll.json",
                    poll_log,
                )
                raise RuntimeError(
                    "deployment UID changed "
                    "during recovery"
                )

            if (
                deployment.generation
                != baseline_deployment.generation
            ):
                write_json(
                    "10-recovery-poll.json",
                    poll_log,
                )
                raise RuntimeError(
                    "deployment generation changed "
                    "during recovery"
                )

            if len(added_uids) > 1:
                write_json(
                    "10-recovery-poll.json",
                    poll_log,
                )
                write_json(
                    "11-ambiguous-post-snapshot.json",
                    snapshot,
                )
                raise RuntimeError(
                    "multiple new Pod UIDs observed"
                )

            if old_exists:
                time.sleep(2)
                continue

            if len(added_uids) != 1:
                time.sleep(2)
                continue

            candidate_uid = added_uids[0]

            replacement = next(
                (
                    pod
                    for pod in snapshot.pods
                    if pod.uid
                    == candidate_uid
                ),
                None,
            )

            if replacement is None:
                time.sleep(2)
                continue

            if (
                replacement.owner_kind
                != "ReplicaSet"
                or replacement.owner_uid
                != baseline_target.owner_uid
            ):
                write_json(
                    "10-recovery-poll.json",
                    poll_log,
                )
                raise RuntimeError(
                    "replacement ReplicaSet changed"
                )

            if replacement.phase != "Running":
                time.sleep(2)
                continue

            if not replacement.ready:
                time.sleep(2)
                continue

            if (
                replacement.deletion_timestamp
                is not None
            ):
                time.sleep(2)
                continue

            if not deployment_converged(
                snapshot
            ):
                time.sleep(2)
                continue

            if not replacement_endpoint_healthy(
                snapshot,
                candidate_uid,
            ):
                time.sleep(2)
                continue

            post_snapshot = snapshot
            replacement_uid = candidate_uid
            break

        write_json(
            "10-recovery-poll.json",
            poll_log,
        )

        if (
            post_snapshot is None
            or replacement_uid is None
        ):
            raise RuntimeError(
                "recovery did not converge "
                "within timeout"
            )

        write_json(
            "11-post-recovery-snapshot.json",
            post_snapshot,
        )

        print(
            f"replacement_uid={replacement_uid}"
        )
        print(
            "KUBERNETES_RECOVERY_CONVERGED=PASS"
        )

        # -----------------------------------------
        # 8. Application readiness semantics
        # -----------------------------------------

        readiness = (
            readiness_semantic_probe()
        )

        write_json(
            "12-readiness-probe.json",
            readiness,
        )

        print(
            f"readiness_success="
            f"{readiness['success']}"
        )

        if not readiness["success"]:
            raise RuntimeError(
                "readiness semantic probe failed"
            )

        print(
            "READINESS_SEMANTIC_RECOVERY=PASS"
        )

        # -----------------------------------------
        # 9. Real business GET
        # -----------------------------------------

        business_probe = (
            perform_http_probe(
                BUSINESS_URL,
                timeout_seconds=3.0,
            )
        )

        write_json(
            "13-business-probe.json",
            business_probe,
        )

        print(
            f"business_probe_success="
            f"{business_probe.success}"
        )
        print(
            f"business_probe_status="
            f"{
business_probe.status_code}"
        )

        if (
            not business_probe.success
            or business_probe.status_code
            != 200
        ):
            raise RuntimeError(
                "business probe failed"
            )

        print(
            "REAL_BUSINESS_RECOVERY=PASS"
        )

        # -----------------------------------------
        # 10. Independent Verifier
        # -----------------------------------------

        verification = verify_recovery(
            VerificationRequest(
                safety_decision=allow_decision,
                execution_result=execution,
                baseline_snapshot=pre_execution,
                post_snapshot=post_snapshot,
                business_probe=business_probe,
                evidence_refs=(
                    "07-pre-execution-snapshot.json",
                    "08-allow-decision.json",
                    "09-execution-result.json",
                    "11-post-recovery-snapshot.json",
                    "12-readiness-probe.json",
                    "13-business-probe.json",
                ),
            )
        )

        write_json(
            "14-verification-result.json",
            verification,
        )

        print(
            f"verification_id="
            f"{verification.verification_id}"
        )
        print(
            f"verification_outcome="
            f"{verification.outcome}"
        )
        print(
            "verification_reasons="
            + ",".join(
                verification.reason_codes
            )
        )
        print(
            f"verification_target_uid="
            f"{verification.target_uid}"
        )
        print(
            f"verification_replacement_uid="
            f"{verification.replacement_uid}"
        )

        if verification.outcome != VERIFIED:
            raise RuntimeError(
                "independent verification "
                "did not return VERIFIED"
            )

        if (
            verification.replacement_uid
            != replacement_uid
        ):
            raise RuntimeError(
                "verification replacement UID "
                "does not match observed replacement"
            )

        write_json(
            "15-phase5g-summary.json",
            {
                "phase": "Phase5G",
                "incident_id": (
                    diagnosis.incident_id
                ),
                "model": (
                    hermes_run.model
                ),
                "provider": (
                    hermes_run.provider
                ),
                "tool_count": (
                    hermes_run.tool_count
                ),
                "target_name": (
                    TARGET_NAME
                ),
                "target_uid": (
                    TARGET_UID
                ),
                "replacement_uid": (
                    replacement_uid
                ),
                "execution_id": (
                    execution.execution_id
                ),
                "execution_status": (
                    execution.status
                ),
                "verification_id": (
                    verification.verification_id
                ),
                "verification_outcome": (
                    verification.outcome
                ),
                "completed_at": utc_now(),
            },
        )

        print()
        print(
            "PHASE5G_REAL_HERMES=PASS"
        )
        print(
            "PHASE5G_REAL_EVICTION=PASS"
        )
        print(
            "PHASE5G_INDEPENDENT_VERIFICATION=PASS"
        )
        print(
            "PHASE5G_REAL_E2E=PASS"
        )

        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())

    except KeyboardInterrupt:
        print(
            "PHASE5G_ABORTED_BY_USER",
            file=sys.stderr,
        )
        raise

    except Exception as exc:
        print(
            f"PHASE5G_E2E_ERROR="
            f"{type(exc).__name__}:{exc}",
            file=sys.stderr,
        )
        raise
