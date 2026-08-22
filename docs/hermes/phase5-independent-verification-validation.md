# Hermes Phase 5：Independent Recovery Verification 验证记录

## 1. 阶段目标

Phase 5 的目标不是证明 Kubernetes Eviction API 接受了请求，而是建立：

> Execution accepted != Recovery verified

完整边界为：

~~~text
SafetyDecision(ALLOW)
        ↓
Controlled Executor
        ↓
ExecutionResult(ACCEPTED)
        ↓
Independent Observation
        ↓
Replacement / Deployment / EndpointSlice / Business verification
        ↓
VerificationResult
~~~

只有独立 Verifier 返回：

~~~text
outcome=VERIFIED
reason_codes=ALL_VERIFICATION_CHECKS_PASSED
~~~

才允许声明恢复成功。

Phase 5 完成后，项目能力状态为：

~~~text
Observable        PASS
Diagnosable       PASS
Explainable       PASS
Safety-bounded    PASS
Executable        PASS
Verifiable        PASS
Experience        PENDING
~~~

---

## 2. 权限与信任边界

### 2.1 Executor 不承担恢复验证

Executor 的职责仍然只有：

~~~text
SafetyDecision(ALLOW)
→ policy/v1 Eviction
→ ExecutionResult
~~~

Executor 不读取 Pod 当前状态，不判断 Deployment 是否恢复，也不判断业务是否恢复。

### 2.2 Verifier 不具有 Kubernetes 写能力

Phase 5 最终静态检查：

~~~text
PHASE5_VERIFICATION_WRITE_SURFACE=PASS
~~~

Verifier 未引入：

- Pod delete；
- Deployment/Pod patch；
- Eviction；
- kubectl；
- shell；
- SSH；
- 任意 Kubernetes write。

恢复验证继续复用 Phase 1 Observer 的只读 Observation 能力。

### 2.3 Hermes 仍然不具有工具调用能力

最终 Hermes preflight：

~~~text
model=deepseek-v4-flash
provider=deepseek
tool_count=0
PHASE5_HERMES_NO_TOOL_BOUNDARY=PASS
~~~

Hermes 继续只承担 Diagnosis / Reasoning，不承担 Kubernetes 执行。

---

## 3. Verification Contract

Phase 5 使用以下输入：

~~~text
SafetyDecision(ALLOW)
+
ExecutionResult
+
Baseline ObservationSnapshot
+
Post-execution ObservationSnapshot
+
BusinessProbeResult
+
Evidence references
        ↓
Independent Verifier
        ↓
VerificationResult
~~~

### 3.1 BusinessProbeResult

schema_version：

~~~text
v1alpha1
~~~

主要字段：

- url；
- success；
- status_code；
- checked_at；
- error。

不保存业务响应正文。

### 3.2 VerificationResult

schema_version：

~~~text
v1alpha1
~~~

主要绑定字段：

- verification_id；
- incident_id；
- execution_id；
- target_uid；
- replacement_uid；
- baseline_snapshot_id；
- fresh_snapshot_id；
- outcome；
- reason_codes；
- verified_at；
- evidence_refs。

结果枚举：

~~~text
VERIFIED
NOT_RECOVERED
INCONCLUSIVE
~~~

---

## 4. 核心 Fail-Closed 规则

Verifier 不信任单一信号。

至少检查：

1. VerificationRequest / SafetyDecision / ExecutionResult / Observation / Probe schema；
2. SafetyDecision 必须为 ALLOW；
3. human_approved 必须为 true；
4. action 必须为 EVICT_POD；
5. target_kind 必须为 Pod；
6. incident_id / decision_id / target UID / target name 必须绑定一致；
7. SafetyDecision.fresh_snapshot_id 必须绑定 baseline snapshot；
8. baseline collected_at 必须绑定 SafetyDecision.evaluated_at；
9. baseline 必须严格早于 execution；
10. post snapshot 必须晚于 execution；
11. business probe 必须晚于 execution；
12. ExecutionResult 必须为 ACCEPTED；
13. ExecutionResult 必须包含 EVICTION_ACCEPTED；
14. accepted execution 的 API status 必须为 200 / 201 / 202；
15. old target UID 必须消失；
16. replacement 必须唯一；
17. replacement 必须属于当前 workload；
18. replacement 必须保持与 baseline target 相同的 ReplicaSet identity；
19. Deployment UID 必须保持不变；
20. baseline → post 期间 Deployment generation 必须保持不变；
21. Deployment generation 必须与 observedGeneration 收敛；
22. desired / updated / ready / available replicas 必须收敛；
23. replacement Pod 必须 Running / Ready / non-terminating；
24. EndpointSlice 中 replacement endpoint 必须 ready=true / serving=true；
25. business probe 必须成功。

其中 Deployment generation continuity 与 ReplicaSet identity continuity 是为了避免：

~~~text
并发 rollout
HPA / controller 并发变化
其他 Pod replacement
~~~

被错误归因成当前 Eviction 的恢复结果。

---

## 5. Phase 5E / 5E2 自动化验证

### 5.1 Fail-Closed Matrix

Phase 5E 曾通过 adversarial tests 发现多项真实 fail-closed 缺口，例如：

- accepted execution + HTTP 500；
- action binding mismatch；
- ALLOW reason 不正确；
- baseline 时间边界；
- probe 状态自相矛盾；
- target kind 错误；
- evidence_refs 缺失。

最小修复完成后全部通过。

### 5.2 Controller Attribution Guard

Phase 5E2 进一步加入：

- baseline Deployment 缺失 → INCONCLUSIVE；
- baseline → post Deployment generation 改变 → INCONCLUSIVE；
- replacement 来自不同 ReplicaSet → INCONCLUSIVE。

最终结果：

~~~text
Ran 54 tests
OK

Ran 108 tests
OK
~~~

因此：

~~~text
PHASE5_FINAL_VERIFIER_RC=0
PHASE5_FINAL_REGRESSION_RC=0
~~~

---

## 6. Phase 5F：Live-Safe Validation

Phase 5F 不执行真实 Eviction，而是使用真实健康 Kubernetes Observation 和真实业务请求验证 Verifier 不会被伪造执行结果欺骗。

### 6.1 Negative Control 1

条件：

~~~text
真实健康 cluster
真实 business HTTP 200
ExecutionResult.status=REFUSED
~~~

结果：

~~~text
outcome=INCONCLUSIVE
reason_codes:
- EXECUTION_NOT_ACCEPTED
- EXECUTION_ACCEPTANCE_REASON_MISSING

PHASE5F_NO_EXECUTION_NEGATIVE_CONTROL=PASS
~~~

证明：

> 健康系统本身不能被当作一次成功的自动恢复。

### 6.2 Negative Control 2

条件：

~~~text
真实健康 cluster
真实 business HTTP 200
伪造：
ExecutionResult.status=ACCEPTED
api_status_code=201
reason=EVICTION_ACCEPTED

但 Kubernetes 中没有执行 Eviction
~~~

Verifier 独立观察到旧 Pod UID 仍存在。

结果：

~~~text
outcome=NOT_RECOVERED
reason_codes:
- TARGET_UID_STILL_PRESENT

PHASE5F_FORGED_ACCEPTANCE_NEGATIVE_CONTROL=PASS
~~~

因此：

~~~text
PHASE5F_LIVE_SAFE_VALIDATION=PASS
~~~

这直接证明：

> Executor 声称 ACCEPTED 不能替代独立恢复验证。

---

## 7. Phase 5G：Real End-to-End Validation

### 7.1 故障设计

早期 SIGSTOP 方案存在 liveness restart race。

最终 Phase 5G 使用：

~~~text
仅在目标 Pod network namespace
阻断 TCP/3306
~~~

该故障使：

~~~text
Pod phase      Running
container      running
restartCount   0
/healthz       healthy
/readyz        503
mysql          error
redis          ok
Pod Ready      false
Endpoint       serving=false
~~~

故障只作用于目标实例访问 MySQL 的路径，不直接破坏 sibling。

---

## 8. Phase 5G Attempts 与 RCA

### 8.1 Attempt 1：Executor runtime credential 缺失

Harness 启动时：

~~~text
OPSLAB_K8S_EXECUTOR_TOKEN missing
~~~

ExecutorClientConfig 直接 fail closed。

没有进入：

- REAL Hermes diagnosis；
- Safety Gate；
- Eviction；
- Verification。

该结果证明 Executor 不会 fallback 到 Observer token。

---

### 8.2 Attempt 2：Kubernetes Python Client API 适配错误

Attempt 2 已完成：

- REAL Hermes；
- validated diagnosis；
- Safety REQUIRE_HUMAN；
- human approval；
- fresh Safety ALLOW。

但 Harness 将 ControlledExecutor 的 Kubernetes client 实例化为：

~~~text
PolicyV1Api
~~~

实际 kubernetes Python client v36.0.3 中：

~~~text
CoreV1Api.create_namespaced_pod_eviction=True
CoreV1Api.create_namespaced_pod_eviction_with_http_info=True
PolicyV1Api.create_namespaced_pod_eviction_with_http_info=False
~~~

因此 RCA：

~~~text
PHASE5G_EVICTION_CLIENT_RCA=PROVEN
~~~

最小修复：

~~~text
PolicyV1Api
→ CoreV1Api
~~~

ControlledExecutor 本身未修改。

---

### 8.3 Attempt 3：Phase 2 Current Evidence Gap

真实故障产生：

~~~text
Pod Running
Ready=False
Endpoint ready=false
Endpoint serving=false
healthy sibling exists
readiness probe HTTP 503
~~~

REAL Hermes：

~~~text
model=deepseek-v4-flash
tool_count=0
recommended_action=ESCALATE
~~~

Hermes 的判断是：

- 当前可以证明 Pod 不健康；
- 但不能证明 HTTP 503 的底层原因；
- 因此不应在证据不足时直接推荐 disruptive remediation。

这证明 Phase 2 IncidentContext 对 Phase 5 最终场景存在一个最小 current-state evidence gap。

Harness 正确停止，没有覆盖模型决策，也没有执行 Eviction。

---

### 8.4 Attempt 4：Target Dependency Semantics

通过 WSL 到 Pod IP 的临时 /32 route，可以直接探测目标 Pod：

~~~text
/readyz
HTTP 503
status=not_ready
mysql=error
redis=ok
~~~

该事实以 CURRENT_STATE Evidence 追加到 IncidentContext：

~~~text
fact_type=TARGET_READINESS_SEMANTICS
~~~

REAL Hermes 能够明确识别 MySQL dependency failure，但仍返回：

~~~text
ESCALATE
~~~

原因是只有：

~~~text
target mysql=error
sibling Ready=True
~~~

仍不能直接证明 sibling 的 MySQL dependency 正常，因此实例局部性证据仍不充分。

Harness 再次 fail closed，没有执行 Eviction。

---

### 8.5 Attempt 5：Target + Sibling Semantic Comparison

Attempt 5 同时采集：

目标：

~~~text
target:
/readyz=503
status=not_ready
mysql=error
redis=ok
~~~

健康 sibling：

~~~text
sibling:
/readyz=200
status=ready
mysql=ok
redis=ok
~~~

并验证二者属于同一个 ReplicaSet。

IncidentContext 中新增：

~~~text
TARGET_READINESS_SEMANTICS
SIBLING_READINESS_SEMANTICS
~~~

这些都是实时观察事实。

未向 Hermes 提供人工故障注入的实现细节，也没有修改 Hermes 输出。

REAL Hermes：

~~~text
model=deepseek-v4-flash
provider=deepseek
tool_count=0
recommended_action=EVICT_POD

REAL_HERMES_VALIDATED_DIAGNOSIS=PASS
~~~

---

## 9. Human-in-the-Loop Safety Boundary

REAL Hermes 的：

~~~text
recommended_action=EVICT_POD
~~~

只是 recommendation，不是 authorization。

首次 Safety Gate：

~~~text
decision=REQUIRE_HUMAN
reason=HUMAN_APPROVAL_REQUIRED
~~~

用户明确输入：

~~~text
APPROVE_EVICTION
~~~

随后系统重新采集 fresh Observation，再运行 Safety Gate。

结果：

~~~text
decision=ALLOW
reason=ALL_SAFETY_CHECKS_PASSED
~~~

因此人工审批并没有直接绕过 Safety Gate。

真实执行顺序为：

~~~text
Diagnosis
→ REQUIRE_HUMAN
→ Human Approval
→ Fresh Observation
→ Safety Gate
→ ALLOW
→ Executor
~~~

---

## 10. Real Controlled Eviction

Attempt 5 execution：

~~~text
incident_id=
inc-obs-20260822T154701Z-38c35073-87f81972

target_name=
opslab-api-797c8fdfbf-jh9h2

target_uid=
87f81972-4ad6-4851-926c-310060df64c6

execution_id=
exec-16961229-0a61-49c5-aced-835db43faf23

status=
ACCEPTED

reason_codes=
EVICTION_ACCEPTED

api_status_code=
201
~~~

因此：

~~~text
PHASE5G_REAL_EVICTION=PASS
~~~

但此时仍不声明业务恢复。

---

## 11. Independent Recovery Observation

Eviction 后 Observer 独立轮询。

旧 UID：

~~~text
87f81972-4ad6-4851-926c-310060df64c6
~~~

最终消失。

唯一 replacement UID：

~~~text
0840c6a9-c605-4ed9-8ccc-3190b2ceb271
~~~

replacement Pod：

~~~text
opslab-api-797c8fdfbf-vgs57
Running
Ready=true
restartCount=0
~~~

最终 Deployment：

~~~text
generation=35
observedGeneration=35
desired=2
updated=2
ready=2
available=2
~~~

最终 HPA：

~~~text
min=2
max=4
current=2
desired=2
~~~

最终 PDB：

~~~text
currentHealthy=2
desiredHealthy=1
disruptionsAllowed=1
expectedPods=2
~~~

replacement EndpointSlice：

~~~text
UID=0840c6a9-c605-4ed9-8ccc-3190b2ceb271
ready=true
serving=true
terminating=false
~~~

最终 Kubernetes 健康验收：

~~~text
PHASE5G_OLD_UID_ABSENT=PASS
PHASE5G_REPLACEMENT_UID_PRESENT=PASS
PHASE5G_DEPLOYMENT_CONVERGED=PASS
PHASE5G_REPLACEMENT_ENDPOINT=PASS
PHASE5G_FINAL_KUBERNETES_HEALTH=PASS
~~~

---

## 12. Application Recovery

最终 readiness：

~~~text
{"status":"ready","mysql":"ok","redis":"ok"}
HTTP_CODE=200
~~~

最终业务请求：

~~~text
GET /api/v1/events/1
HTTP_CODE=200
~~~

返回已存在的业务事件数据。

这里的 GET 是幂等业务读取路径验证。

它不会修改 MySQL 中的事件记录，但当 Redis cache miss 时，应用实现可能执行缓存填充，因此不描述为“绝对无写入请求”。

---

## 13. VerificationResult

最终 VerificationResult：

~~~text
verification_id=
verify-f1d42eab-0818-4f28-8697-04dd3172754a

execution_id=
exec-16961229-0a61-49c5-aced-835db43faf23

incident_id=
inc-obs-20260822T154701Z-38c35073-87f81972

target_uid=
87f81972-4ad6-4851-926c-310060df64c6

replacement_uid=
0840c6a9-c605-4ed9-8ccc-3190b2ceb271

outcome=
VERIFIED

reason_codes=
ALL_VERIFICATION_CHECKS_PASSED
~~~

因此：

~~~text
PHASE5G_INDEPENDENT_VERIFICATION=PASS
PHASE5G_REAL_E2E=PASS
~~~

---

## 14. Evidence Binding Audit

最终自动审计：

~~~text
incident_binding=PASS
execution_binding=PASS
old_uid_binding=PASS
replacement_binding=PASS
execution_accepted=PASS
verification_verified=PASS
real_hermes_no_tool=PASS

PHASE5_ATTEMPT5_BINDING_AUDIT=PASS
~~~

Attempt 5 关键证据文件完整：

~~~text
00-hermes-preflight.json
01-fault-snapshot.json
02-incident-context.json
02a-target-readiness-probe.json
02b-sibling-readiness-probe.json
03-hermes-raw-response.txt
04-hermes-run-metadata.json
05-validated-diagnosis.json
06-require-human.json
07-pre-execution-snapshot.json
08-allow-decision.json
09-execution-result.json
10-recovery-poll.json
11-post-recovery-snapshot.json
12-readiness-probe.json
13-business-probe.json
14-verification-result.json
15-phase5g-summary.json
~~~

验证：

~~~text
PHASE5_ATTEMPT5_EVIDENCE_COMPLETE=PASS
~~~

---

## 15. Sensitive Information / Runtime Cleanup

Phase 5 最终敏感信息扫描：

~~~text
PHASE5_SENSITIVE_LITERAL_SCAN=PASS
~~~

运行结束后：

~~~text
OBSERVER_TOKEN_CLEANUP=PASS
EXECUTOR_TOKEN_CLEANUP=PASS
~~~

Phase 5G 为 target 与 sibling 创建的 Windows 临时 /32 route 也已删除：

~~~text
10.244.1.127/32=PASS_ABSENT
10.244.2.98/32=PASS_ABSENT
~~~

没有把临时运行凭据写入仓库证据。

---

## 16. Final Regression

Phase 5 Final Pre-Seal：

~~~text
PHASE5_FINAL_COMPILE_RC=0

Ran 54 tests
OK
PHASE5_FINAL_VERIFIER_RC=0

Ran 108 tests
OK
PHASE5_FINAL_REGRESSION_RC=0

PHASE5_VERIFICATION_WRITE_SURFACE=PASS
PHASE5_EXECUTOR_BOUNDARY=PASS
PHASE5_HERMES_NO_TOOL_BOUNDARY=PASS
PHASE5_ATTEMPT5_BINDING_AUDIT=PASS
PHASE5_ATTEMPT5_EVIDENCE_COMPLETE=PASS
PHASE5_SENSITIVE_LITERAL_SCAN=PASS

PHASE5_FINAL_PRESEAL=PASS
~~~

---

## 17. Phase 5 最终结论

Phase 5 已真实证明：

~~~text
Execution accepted
!=
Recovery verified
~~~

真实恢复链路为：

~~~text
Kubernetes
→ Observation
→ Detector
→ IncidentContext
→ REAL Hermes
→ validated Diagnosis
→ Safety Gate REQUIRE_HUMAN
→ Human Approval
→ Fresh Observation
→ Safety Gate ALLOW
→ Controlled Executor
→ policy/v1 Eviction
→ ExecutionResult ACCEPTED
→ Independent Observation
→ unique replacement
→ Deployment convergence
→ EndpointSlice serving
→ readiness recovery
→ business recovery
→ VerificationResult VERIFIED
~~~

最终状态：

~~~text
Observable        PASS
Diagnosable       PASS
Explainable       PASS
Safety-bounded    PASS
Executable        PASS
Verifiable        PASS
Experience        PENDING
~~~

Phase 5 至此满足封板条件。
