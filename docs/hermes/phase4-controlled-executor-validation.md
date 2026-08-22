# Phase 4 — Controlled Executor 验证报告

## 1. 阶段目标

Phase 4 的目标是实现受控执行能力：

~~~text
SafetyDecision(ALLOW)
        ↓
Controlled Executor
        ↓
Kubernetes policy/v1 Eviction API
~~~

本阶段是 Hermes/AIOps 链路中第一次真正产生 Kubernetes 写操作的阶段。

Phase 4 只负责证明：

1. Executor 只接受合法的 `ALLOW`
2. Executor 只能执行 `EVICT_POD`
3. Executor 使用独立最小权限身份
4. Executor 将授权目标绑定到 Pod UID
5. 实际执行使用 Kubernetes `policy/v1` Eviction API
6. Kubernetes 接受 Eviction
7. 原 Pod UID 消失
8. Deployment / ReplicaSet 创建新的 replacement Pod

Phase 4 不负责最终业务恢复判断。

必须保持：

~~~text
Eviction API accepted
!=
Business recovery verified
~~~

最终恢复验证属于 Phase 5 Independent Verification。

---

## 2. 安全边界

Phase 4 唯一允许的动作：

~~~text
EVICT_POD
~~~

唯一允许的 Kubernetes 写接口：

~~~text
policy/v1 Eviction API
~~~

禁止：

~~~text
kubectl delete pod
CoreV1Api.delete_namespaced_pod()
arbitrary Kubernetes DELETE
Pod patch
Deployment patch
shell execution
SSH execution
Hermes direct execution
arbitrary Kubernetes write
~~~

Hermes 在本阶段仍然没有 Kubernetes 写工具。

职责边界：

~~~text
Hermes
→ Diagnosis

Safety Gate
→ Authorization

Controlled Executor
→ Execution
~~~

---

## 3. Executor 独立身份与 RBAC

Phase 4 创建独立 ServiceAccount：

~~~text
system:serviceaccount:opslab:opslab-aiops-executor
~~~

Namespace：

~~~text
opslab
~~~

Role 唯一权限：

~~~text
apiGroups: [""]
resources: ["pods/eviction"]
verbs: ["create"]
~~~

Executor 不具有：

~~~text
get pods
list pods
delete pods
patch pods
pods/exec
pods/attach
pods/portforward
secrets
deployment patch
namespace escape
~~~

Observer ServiceAccount 同样无法执行 Eviction。

真实 RBAC 验证结果：

~~~text
EXECUTOR_EVICTION_PERMISSION=PASS
EXECUTOR_NAMESPACE_ESCAPE_DENIED=PASS
OBSERVER_EXECUTION_SEPARATION=PASS
EXECUTOR_CREATE_PODS_EVICTION_ONLY=PASS
EXECUTOR_NO_POD_READ_PERMISSION=PASS
EXECUTOR_NAMESPACE_BOUNDARY=PASS
~~~

---

## 4. Executor Credential Separation

Executor 使用独立环境变量：

~~~text
OPSLAB_K8S_EXECUTOR_TOKEN
~~~

Observer：

~~~text
OPSLAB_K8S_TOKEN
~~~

Executor 不允许 fallback 到 Observer token。

真实 WSL → Kubernetes API Server 认证验证：

~~~text
username=
system:serviceaccount:opslab:opslab-aiops-executor

EXECUTOR_LIVE_IDENTITY=PASS
EXECUTOR_LIVE_AUTHENTICATION=PASS
EXECUTOR_LIVE_AUTHORIZATION=PASS
~~~

短期 ServiceAccount Token 只在环境变量中使用，不写入仓库和 Evidence。

---

## 5. Controlled Executor 合约

`ExecutionRequest` 绑定：

~~~text
namespace
SafetyDecision
~~~

`SafetyDecision` 中继续绑定：

~~~text
decision_id
incident_id
action
target_kind
target_name
target_uid
decision
fresh_snapshot_id
human_approved
~~~

Executor fail-closed 检查至少包括：

~~~text
Execution schema
SafetyDecision schema
decision == ALLOW
action == EVICT_POD
target_kind == Pod
human_approved == True
namespace == allowed namespace
target_name non-empty
target_uid non-empty
~~~

---

## 6. Pod UID Precondition

真实 Eviction 请求使用：

~~~text
policy/v1 Eviction
~~~

并将授权 UID 放入：

~~~text
deleteOptions.preconditions.uid
~~~

目标：

~~~text
Safety Gate 授权 Pod-A
        ↓
Pod-A 在执行前消失
        ↓
Executor 不得误作用于另一个对象
~~~

UID 不一致时由 Kubernetes API Server fail closed。

---

## 7. Live-safe Server Dry-run

真实写入前首先进行了 Kubernetes server-side dry-run。

### 7.1 DENY

结果：

~~~text
status=REFUSED
reason=REFUSE_SAFETY_DECISION_NOT_ALLOW
api_calls_delta=0

PHASE4_LIVE_DENY_REFUSAL=PASS
~~~

### 7.2 REQUIRE_HUMAN

结果：

~~~text
status=REFUSED
reason=REFUSE_SAFETY_DECISION_NOT_ALLOW
api_calls_delta=0

PHASE4_LIVE_REQUIRE_HUMAN_REFUSAL=PASS
~~~

### 7.3 Unsupported Action

输入：

~~~text
DELETE_POD
~~~

结果：

~~~text
status=REFUSED
reason=REFUSE_UNSUPPORTED_ACTION
api_calls_delta=0

PHASE4_LIVE_UNSUPPORTED_ACTION_REFUSAL=PASS
~~~

### 7.4 正确 UID

真实 Kubernetes server-side dry-run：

~~~text
status=ACCEPTED
reason=EVICTION_ACCEPTED
api_status_code=201
api_calls_delta=1

PHASE4_LIVE_CORRECT_UID_DRY_RUN=PASS
~~~

### 7.5 错误 UID

使用随机错误 Pod UID：

~~~text
status=REFUSED
reason=EVICTION_CONFLICT
api_status_code=409

PHASE4_LIVE_WRONG_UID_REFUSAL=PASS
~~~

证明：

~~~text
UID precondition
→ Kubernetes API Server
→ 409 Conflict
→ fail closed
~~~

Dry-run 后原 Pod name / UID / Ready 状态均未变化。

最终：

~~~text
SERVER_DRY_RUN_ONLY=PASS
NO_REAL_EVICTION_EXECUTED=PASS
PHASE4_LIVE_SAFE_EXECUTOR_VALIDATION=PASS
~~~

---

## 8. Attempt 1 — 中止实验与真实 RCA

第一次真实实验目标：

~~~text
Pod:
opslab-api-797c8fdfbf-2mrbr

UID:
f1387f0a-e8ea-44bb-9e6b-74b03a80d37e
~~~

在 worker2 上定位到 Uvicorn host PID：

~~~text
4188
~~~

真实执行：

~~~text
SIGSTOP
~~~

进程状态：

~~~text
Ssl
→
Tsl
~~~

Kubernetes 随后观察到：

~~~text
Running
Ready=False
~~~

Fresh Observation、Candidate、IncidentContext、Diagnosis Validator 均正常。

Safety Gate：

~~~text
decision=REQUIRE_HUMAN
reason=HUMAN_APPROVAL_REQUIRED

PHASE4_REAL_REQUIRE_HUMAN=PASS
~~~

但第一次人工审批测试 harness 使用：

~~~python
open("/dev/tty", "r+")
~~~

当前终端环境返回：

~~~text
io.UnsupportedOperation:
File or stream is not seekable
~~~

因此第一次实验在人工批准之前中止。

重要边界：

~~~text
Safety Gate ALLOW      NOT REACHED
Controlled Executor    NOT CALLED
Real Eviction          NOT SENT
~~~

### 8.1 Container Restart RCA

故障持续期间 Kubernetes Events 明确记录：

~~~text
Liveness probe failed
Container failed liveness probe, will be restarted
Killing
Created
Started
~~~

因此：

~~~text
restartCount:
0 → 1
~~~

并出现新的 container ID / host PID。

结果：

~~~text
PHASE4_ATTEMPT1_CONTAINER_RESTART_RCA=PROVEN
~~~

### 8.2 原 Pod 后续消失 RCA

Attempt 1 后：

~~~text
HPA:
2 → 3

reason:
CPU resource utilization above target
~~~

ReplicaSet 创建新 Pod。

随后：

~~~text
HPA:
3 → 2

reason:
All metrics below target
~~~

Deployment / ReplicaSet Events：

~~~text
Scaled down replica set opslab-api-797c8fdfbf from 3 to 2
Deleted pod: opslab-api-797c8fdfbf-2mrbr
~~~

因此原 Pod 后续消失：

~~~text
!= Controlled Executor Eviction
!= Deployment rollout
~~~

真实原因：

~~~text
HPA scale-down
→ Deployment / ReplicaSet reconciliation
→ Pod deletion
~~~

结果：

~~~text
PHASE4_ATTEMPT1_POD_DISAPPEAR_RCA=
PROVEN_HPA_SCALE_DOWN
~~~

Attempt 1 的失败属于测试 harness 问题，而不是 Safety Gate、Executor 或 Kubernetes API 问题。

---

## 9. Attempt 2 — 最终真实执行

Attempt 2 开始前系统稳定：

~~~text
Deployment:
generation=35
observedGeneration=35
desired=2
current=2
updated=2
ready=2
available=2

HPA:
current=2
desired=2
min=2
~~~

动态选择真实目标：

~~~text
Pod:
opslab-api-797c8fdfbf-9mz4x

UID:
8985df8d-77f0-4a53-ae0d-67e538e313aa

Node:
k8s-worker2

restartCount:
0
~~~

Container：

~~~text
03ec382b24702811a3cebf0d53276889a58d76da3df90ef5336d79c15710346f
~~~

Host PID：

~~~text
4224
~~~

进程：

~~~text
python -m uvicorn app.main:app
--host=0.0.0.0
--port=8000
~~~

Process Identity Guard：

~~~text
PHASE4_PROCESS_IDENTITY_GUARD=PASS
~~~

---

## 10. Attempt 2 真实故障

执行：

~~~text
SIGSTOP
~~~

进程状态：

~~~text
Ssl
→
Tsl
~~~

结果：

~~~text
PHASE4_ATTEMPT2_SIGSTOP=PASS
~~~

约 11 次采样后：

~~~text
Pod UID unchanged
containerID unchanged
restartCount unchanged
phase=Running
Ready=False

PHASE4_ATTEMPT2_RUNNING_NOTREADY=PASS
~~~

故障状态 Deployment：

~~~text
generation=35
observedGeneration=35
desired=2
updated=2
ready=1
available=1
~~~

PDB：

~~~text
generation=1
observedGeneration=1
currentHealthy=1
desiredHealthy=1
disruptionsAllowed=0
unhealthyPodEvictionPolicy=None
~~~

---

## 11. Fresh Observation

Fresh snapshot：

~~~text
snapshot_id=
obs-20260822T124450Z-80769555

schema_version=
v1alpha3
~~~

目标：

~~~text
name=
opslab-api-797c8fdfbf-9mz4x

uid=
8985df8d-77f0-4a53-ae0d-67e538e313aa

phase=
Running

ready=
False

deletionTimestamp=
None
~~~

结果：

~~~text
PHASE4_ATTEMPT2_FRESH_OBSERVATION=PASS
~~~

---

## 12. Candidate / IncidentContext

Candidate Detector：

~~~text
candidate_count=1

FASTAPI_UNHEALTHY_INSTANCE
~~~

目标 UID 与冻结 UID 完全一致。

结果：

~~~text
PHASE4_ATTEMPT2_CANDIDATE=PASS
~~~

Incident：

~~~text
incident_id=
inc-obs-20260822T124450Z-80769555-8985df8d
~~~

Evidence refs：

~~~text
EV-0001
EV-0002
~~~

结果：

~~~text
PHASE4_ATTEMPT2_INCIDENT_CONTEXT=PASS
~~~

---

## 13. Deterministic Validator Diagnosis

Phase 4 组件级验证使用 deterministic validator-approved Diagnosis。

明确：

~~~text
not a new Hermes run
~~~

Diagnosis：

~~~text
diagnosis_id=
diag-inc-obs-20260822T124450Z-80769555-8985df8d-phase4-1f3fc86b

recommended_action=
EVICT_POD

requires_human_approval=
True
~~~

目标：

~~~text
opslab-api-797c8fdfbf-9mz4x
8985df8d-77f0-4a53-ae0d-67e538e313aa
~~~

结果：

~~~text
VALIDATED_PHASE4_ATTEMPT2_DIAGNOSIS=PASS
~~~

最终项目 End-to-End 验证仍要求重新运行真实 Hermes。

---

## 14. Human Approval Boundary

人工批准前：

~~~text
decision=
REQUIRE_HUMAN

reason=
HUMAN_APPROVAL_REQUIRED
~~~

结果：

~~~text
PHASE4_ATTEMPT2_REQUIRE_HUMAN=PASS
~~~

人工终端明确展示：

~~~text
Action:
EVICT_POD

Target:
opslab-api-797c8fdfbf-9mz4x

UID:
8985df8d-77f0-4a53-ae0d-67e538e313aa
~~~

人工输入：

~~~text
APPROVE_EVICTION
~~~

结果：

~~~text
HUMAN_APPROVAL_EXPLICIT=PASS
~~~

---

## 15. Post-Approval Fresh Authorization

人工批准之后没有直接使用旧 SafetyDecision。

系统重新执行 fresh Observation：

~~~text
execution_snapshot_id=
obs-20260822T124522Z-bc524a64
~~~

目标仍然：

~~~text
UID=
8985df8d-77f0-4a53-ae0d-67e538e313aa

phase=
Running

ready=
False

deletionTimestamp=
None
~~~

重新经过 Safety Gate：

~~~text
decision=
ALLOW

reasons=
ALL_SAFETY_CHECKS_PASSED

human_approved=
True
~~~

结果：

~~~text
PHASE4_ATTEMPT2_FRESH_ALLOW=PASS
~~~

这证明：

~~~text
Human Approval
!=
blind execution

Human Approval
→ fresh Observation
→ fresh Safety Gate
→ ALLOW
~~~

---

## 16. Controlled Executor 真实 Eviction

Execution：

~~~text
execution_id=
exec-3bb1b5d7-0bf3-4e64-b0b4-675ef71daa1f
~~~

SafetyDecision：

~~~text
decision_id=
safety-diag-inc-obs-20260822T124450Z-80769555-8985df8d-phase4-1f3fc86b-obs-20260822T124522Z-bc524a64
~~~

Executor 结果：

~~~text
status=
ACCEPTED

reason=
EVICTION_ACCEPTED

api_status_code=
201
~~~

目标：

~~~text
opslab-api-797c8fdfbf-9mz4x

8985df8d-77f0-4a53-ae0d-67e538e313aa
~~~

结果：

~~~text
PHASE4_REAL_EVICTION_ACCEPTED=PASS
PHASE4_CONTROLLED_EXECUTOR_REAL_WRITE=PASS
~~~

这是真实 Kubernetes 写操作。

---

## 17. Kubernetes Controller Replacement

Eviction 后旧目标 UID：

~~~text
8985df8d-77f0-4a53-ae0d-67e538e313aa
~~~

消失：

~~~text
PHASE4_OLD_UID_DISAPPEARED=PASS
~~~

ReplicaSet 创建 replacement Pod：

~~~text
opslab-api-797c8fdfbf-qhwq2
~~~

新 UID：

~~~text
aac2b948-5a10-49d9-8e0d-ffd1b8ee3fc2
~~~

结果：

~~~text
PHASE4_REPLACEMENT_UID_APPEARED=PASS
PHASE4_CONTROLLER_REPLACEMENT=PASS
~~~

当时 Deployment Observation：

~~~text
generation=35
observedGeneration=35
desired=2
updated=2
ready=2
available=2
~~~

但 Phase 4 明确不把这一观察升级为最终业务恢复证明。

结果：

~~~text
EVICTION_ACCEPTED != RECOVERY_VERIFIED

PHASE4_DOES_NOT_DECLARE_BUSINESS_RECOVERY=PASS
~~~

---

## 18. Evidence UID Binding Audit

Attempt 2 Evidence：

~~~text
attempt2-fault-snapshot.json
attempt2-incident-context.json
attempt2-validated-diagnosis.json
attempt2-require-human.json
attempt2-pre-execution-snapshot.json
attempt2-post-approval-decision.json
attempt2-execution.json
~~~

绑定链：

~~~text
Diagnosis UID
=
REQUIRE_HUMAN UID
=
ALLOW UID
=
Execution UID
=
8985df8d-77f0-4a53-ae0d-67e538e313aa
~~~

结果：

~~~text
PHASE4_UID_BINDING_CHAIN=PASS
PHASE4_AUTHORIZATION_EXECUTION_CHAIN=PASS
~~~

---

## 19. Static / Regression Validation

最终预封板验证：

~~~text
PYTHON_COMPILE_RC=0

Ran 54 tests
OK

AIOPS_FULL_REGRESSION_RC=0

PHASE4_FORBIDDEN_EXECUTION_SURFACE=PASS

PHASE3_IMMUTABILITY=PASS

SENSITIVE_LITERAL_SCAN=PASS
~~~

Phase 3 未被修改。

---

## 20. Phase 4 最终结论

已真实验证：

~~~text
DENY
→ Executor refuses

REQUIRE_HUMAN
→ Executor refuses

unsupported action
→ Executor refuses

wrong Pod UID
→ Kubernetes 409
→ Executor fail closed

fresh Running + NotReady fault
→ Observation
→ Candidate
→ IncidentContext
→ Diagnosis Validator
→ REQUIRE_HUMAN
→ explicit Human Approval
→ fresh Observation
→ fresh Safety Gate
→ ALLOW
→ Controlled Executor
→ policy/v1 Eviction
→ HTTP 201
→ old Pod UID disappears
→ replacement Pod UID appears
~~~

因此：

~~~text
Executable = PASS
~~~

阶段状态：

~~~text
Observable       PASS
Diagnosable      PASS
Explainable      PASS
Safety-bounded   PASS
Executable       PASS
~~~

下一阶段：

~~~text
Phase 5
Independent Verification
~~~

用于独立证明：

~~~text
Execution accepted
!=
Recovery successful
~~~

并验证 replacement Pod、Deployment、EndpointSlice、Service 和应用实际业务状态。
