# Phase 3 — Deterministic Safety Gate 验证报告

## 1. 阶段目标

Phase 3 的目标是建立独立于 Hermes / LLM 的确定性授权层：

~~~text
DiagnosisResult
        ↓
Deterministic Safety Gate
        ↓
DENY / REQUIRE_HUMAN / ALLOW
        ↓
Controlled Executor（后续阶段）
~~~

本阶段只负责 Authorization。

本阶段不负责：

- Kubernetes 执行
- Pod Eviction 实际调用
- kubectl delete
- 任意 shell 执行
- SSH 执行
- Hermes / LLM 直接决定 ALLOW

`ALLOW` 只表示当前动作满足授权条件。

`ALLOW != Execution`。

---

## 2. Phase 3 输入边界

Safety Gate 输入包括：

1. 已通过 Diagnosis Validator 验证的 `DiagnosisResult`
2. fresh `ObservationSnapshot`
3. 独立显式的 `human_approved` 状态

Safety Gate 不使用旧 `IncidentContext` 作为当前 Kubernetes 状态。

Safety Gate 必须重新读取 fresh Observation，并对资源身份、Deployment 状态、Pod 状态、PDB 状态和人工审批状态进行确定性判断。

---

## 3. Observation Schema 扩展

Phase 3 将：

~~~text
ObservationSnapshot
v1alpha2
~~~

扩展为：

~~~text
ObservationSnapshot
v1alpha3
~~~

PDBObservation 新增：

~~~text
generation
observed_generation
unhealthy_pod_eviction_policy
~~~

同时保留：

~~~text
current_healthy
desired_healthy
disruptions_allowed
expected_pods
~~~

这些字段用于 Phase 3 Safety Gate 的 PDB 授权判断。

---

## 4. Phase 2 / Phase 3 隔离

Phase 3 新增的 PDB Safety 字段不会泄漏到 Phase 2 `IncidentContext`。

Hermes 仍然只能看到 Phase 2 已封板的诊断 Evidence。

即：

~~~text
Hermes
!= PDB Authorization Engine
~~~

最终 PDB eviction eligibility 由 deterministic Safety Gate 根据 fresh Kubernetes state 判断。

对应回归测试：

~~~text
test_phase3_pdb_safety_fields_do_not_leak_to_phase2_context
PASS
~~~

---

## 5. SafetyDecision v1alpha1

Phase 3 新增：

~~~text
SafetyDecision v1alpha1
~~~

核心字段包括：

~~~text
decision_id
incident_id
action
target_kind
target_name
target_uid
decision
reason_codes
evaluated_at
fresh_snapshot_id
human_approved
~~~

decision：

~~~text
DENY
REQUIRE_HUMAN
ALLOW
~~~

当前唯一允许进入 Safety Gate 授权流程的动作：

~~~text
EVICT_POD
~~~

---

## 6. Static Validation

Phase 3 首轮实现完成后执行：

~~~text
Python compile
Unit tests
git diff --check
~~~

结果：

~~~text
PYTHON_COMPILE_RC=0
UNIT_TEST_RC=0
GIT_DIFF_CHECK_RC=0

Ran 42 tests
OK

PHASE3_STEP1_STATIC_VALIDATION=PASS
~~~

测试覆盖包括：

- unsupported action DENY
- recovered Pod DENY
- target UID mismatch DENY
- terminating Pod DENY
- rollout in progress DENY
- no healthy sibling DENY
- stale PDB status DENY
- unknown PDB eviction policy DENY
- ambiguous multiple PDB DENY
- IfHealthyBudget semantic check
- AlwaysAllow semantic check
- no PDB branch
- explicit human approval requirement
- safe approved ALLOW

---

## 7. Live Collector Smoke

真实 Kubernetes collector 执行结果：

~~~text
schema_version=v1alpha3
snapshot_id=obs-20260822T024411Z-dec616a3
~~~

两个 FastAPI Pod：

~~~text
opslab-api-797c8fdfbf-2mrbr
Running
Ready=True
k8s-worker2

opslab-api-797c8fdfbf-9mz4x
Running
Ready=True
k8s-worker2
~~~

PDB：

~~~text
name=opslab-api
generation=1
observed_generation=1
unhealthyPodEvictionPolicy=None
currentHealthy=2
desiredHealthy=1
disruptionsAllowed=1
expectedPods=2
~~~

结果：

~~~text
PHASE3_LIVE_COLLECTOR_SMOKE=PASS
~~~

这证明 Phase 3 新增 PDB 字段能够被真实 Kubernetes Client 正确采集。

---

## 8. Real Stale Phase 2 Diagnosis

Phase 2 已封板 Diagnosis 原目标：

~~~text
Pod:
opslab-api-797c8fdfbf-bftj5

UID:
abf1d051-0472-4094-831c-438e9d9c0c1d
~~~

Phase 3 运行时，该 Pod 已经不存在。

将旧 Diagnosis 与 fresh Observation 输入 Safety Gate：

~~~text
stale_decision=DENY

reason_codes:
DENY_TARGET_NOT_FOUND
~~~

结果：

~~~text
PHASE3_REAL_STALE_DIAGNOSIS_DENY=PASS
~~~

这证明：

~~~text
Diagnosis authorization
不能漂移到 replacement Pod
~~~

Safety Gate 使用：

~~~text
Pod name + Pod UID
~~~

进行目标身份绑定。

---

## 9. Real Running + NotReady Drill

本次真实测试目标：

~~~text
Pod:
opslab-api-797c8fdfbf-2mrbr

UID:
f1387f0a-e8ea-44bb-9e6b-74b03a80d37e

Node:
k8s-worker2
~~~

故障前：

~~~text
phase=Running
Ready=True
restarts=0
~~~

真实 host PID：

~~~text
4188
~~~

SIGSTOP 前：

~~~text
STAT=Ssl
python -m uvicorn app.main:app --host=0.0.0.0 --port=8000
~~~

发送：

~~~text
SIGSTOP
~~~

进程状态变为：

~~~text
STAT=Tsl
~~~

证明 Uvicorn host process 被真实停止。

随后 Kubernetes fresh Observation 得到：

~~~text
phase=Running
Ready=False
~~~

因此本次故障属于：

~~~text
Running + NotReady service impairment
~~~

而不是 container crash。

---

## 10. Fresh Deployment State

故障期间 Deployment：

~~~text
generation=33
observed_generation=33

replicas=2
updatedReplicas=2
readyReplicas=1
availableReplicas=1
~~~

因此：

~~~text
generation == observedGeneration
replicas == updatedReplicas
~~~

Deployment 不处于 rollout。

Safety Gate rollout guard 通过。

---

## 11. Deterministic Candidate Detection

故障期间：

~~~text
candidate_count=1
~~~

Candidate Detector 正确识别：

~~~text
FASTAPI_UNHEALTHY_INSTANCE
~~~

目标绑定到本次真实故障 Pod。

---

## 12. PDB Freshness

故障期间 PDB：

~~~text
generation=1
observed_generation=1
~~~

因此：

~~~text
metadata.generation
==
status.observedGeneration
~~~

PDB status fresh。

没有触发：

~~~text
DENY_PDB_STATUS_STALE
~~~

---

## 13. PDB unhealthy Pod Eviction 语义

故障期间 PDB：

~~~text
unhealthyPodEvictionPolicy=None

currentHealthy=1
desiredHealthy=1
disruptionsAllowed=0
expectedPods=2
~~~

`unhealthyPodEvictionPolicy=None` 按 Kubernetes 默认语义处理为：

~~~text
IfHealthyBudget
~~~

目标 Pod：

~~~text
Running
Ready=False
~~~

因此 unhealthy Pod eviction 分支判断：

~~~text
currentHealthy >= desiredHealthy
~~~

真实数据：

~~~text
1 >= 1
~~~

成立。

虽然：

~~~text
disruptionsAllowed=0
~~~

Safety Gate 没有错误地仅根据：

~~~text
disruptionsAllowed > 0
~~~

决定 unhealthy Pod eviction eligibility。

这验证了 Phase 2 中发现并冻结的关键 PDB 语义边界。

---

## 14. Diagnosis Validation Boundary

本次 Phase 3 真实授权测试没有伪造“新的 Hermes 诊断”。

真实故障先经过：

~~~text
ObservationSnapshot
→ Candidate Detector
→ IncidentContext
~~~

随后构造 Phase 3 Gate 测试 Diagnosis JSON，并重新经过 Phase 2 已封板的：

~~~text
parse_and_validate_diagnosis()
~~~

验证。

结果：

~~~text
VALIDATED_GATE_TEST_DIAGNOSIS=PASS
~~~

该 DiagnosisResult：

- incident_id 与本次真实 IncidentContext 一致
- affected_resource 与当前 target 一致
- evidence_refs 引用当前 Evidence
- action 为 EVICT_POD
- requires_human_approval=True

它只用于验证 Phase 3 Authorization Layer。

本次不宣称发生了新的 Hermes 推理。

Hermes → Safety Gate → Executor 的完整 End-to-End 验证留到最终 AIOps 闭环验收。

---

## 15. Human Approval Boundary

相同 fresh Observation 和相同合法 DiagnosisResult：

### 未人工批准

输入：

~~~text
human_approved=False
~~~

结果：

~~~text
decision=REQUIRE_HUMAN

reason_codes:
HUMAN_APPROVAL_REQUIRED
~~~

验证：

~~~text
PHASE3_REAL_REQUIRE_HUMAN=PASS
~~~

### 已人工批准

输入：

~~~text
human_approved=True
~~~

同时其他 deterministic safety conditions 全部满足。

结果：

~~~text
decision=ALLOW

reason_codes:
ALL_SAFETY_CHECKS_PASSED
~~~

验证：

~~~text
PHASE3_REAL_ALLOW=PASS
~~~

这证明：

~~~text
Hermes requires_human_approval=True
!=
Human actually approved
~~~

人工批准是 Safety Gate 的独立显式输入。

---

## 16. ALLOW != Execution

虽然 Safety Gate 返回：

~~~text
ALLOW
~~~

Phase 3 没有调用：

- policy/v1 Eviction API
- kubectl delete
- Kubernetes DELETE
- shell executor
- SSH executor

真实验证：

~~~text
NO_EVICTION_EXECUTED=PASS
~~~

因此：

~~~text
Authorization
!=
Execution
~~~

架构边界成立。

---

## 17. Recovery

24 秒故障窗口后执行：

~~~text
SIGCONT
~~~

host process：

~~~text
Tsl
→ Rsl
~~~

Kubernetes 最终恢复：

~~~text
phase=Running
Ready=True
~~~

Container restart：

~~~text
restarts_before=0
restarts_after=0
~~~

验证：

~~~text
NO_CONTAINER_RESTART_DURING_PHASE3_DRILL=PASS
~~~

因此恢复来自：

~~~text
SIGCONT
~~~

而不是 Kubernetes 重启容器。

---

## 18. Post-Recovery Stale Authorization

Pod 恢复 Ready=True 后，没有重新生成 Diagnosis。

直接使用故障期间曾得到 ALLOW 的同一个 DiagnosisResult，再次输入 Safety Gate，并使用 fresh recovered Observation。

结果：

~~~text
decision=DENY

reason_codes:
DENY_TARGET_RECOVERED
~~~

验证：

~~~text
PHASE3_POST_RECOVERY_DENY=PASS
~~~

这证明：

~~~text
过去曾经 ALLOW
!=
现在仍然 ALLOW
~~~

Safety authorization 不可永久复用。

每次授权必须重新绑定 fresh Kubernetes state。

---

## 19. Phase 3 最终真实验证结果

最终：

~~~text
PHASE3_LIVE_COLLECTOR_SMOKE=PASS

PHASE3_REAL_STALE_DIAGNOSIS_DENY=PASS

candidate_count=1

VALIDATED_GATE_TEST_DIAGNOSIS=PASS

PHASE3_REAL_REQUIRE_HUMAN=PASS

PHASE3_REAL_ALLOW=PASS

NO_EVICTION_EXECUTED=PASS

NO_CONTAINER_RESTART_DURING_PHASE3_DRILL=PASS

PHASE3_POST_RECOVERY_DENY=PASS

PHASE3_REAL_SAFETY_GATE_VALIDATION=PASS
~~~

---

## 20. Phase 3 结论

Phase 3 当前已经证明：

~~~text
DiagnosisResult
        ↓
Fresh ObservationSnapshot
        ↓
Deterministic Safety Gate
        ↓
DENY / REQUIRE_HUMAN / ALLOW
~~~

Safety Gate 可以确定性处理：

- stale diagnosis
- target disappearance
- UID identity binding
- Pod recovery
- terminating Pod
- Running state
- Ready state
- current workload ownership
- Deployment rollout
- healthy sibling
- PDB status freshness
- unhealthyPodEvictionPolicy
- IfHealthyBudget
- AlwaysAllow
- unknown PDB policy fail closed
- ambiguous PDB fail closed
- explicit human approval

并保持：

~~~text
Hermes
!= Authorization

Safety Gate
!= Executor

ALLOW
!= Execution
~~~

Phase 3 的核心目标：

~~~text
Safety-bounded
~~~

已经获得真实 Kubernetes evidence 支持。

---

## 21. 下一阶段边界

Phase 4 才进入：

~~~text
Controlled Executor
~~~

第一种允许执行的动作仍然只有：

~~~text
policy/v1 Eviction API
~~~

禁止：

~~~text
kubectl delete pod
arbitrary DELETE
shell
SSH
Hermes direct execution
~~~

Executor 必须绑定：

~~~text
namespace
Pod name
Pod UID
SafetyDecision
fresh authorization identity
~~~

以防止 stale authorization。
