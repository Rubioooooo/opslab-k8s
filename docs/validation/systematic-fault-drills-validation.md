# OpsLab Systematic Fault Drills Validation

> 项目：基于 kubeadm 的 Kubernetes 云原生应用部署与 SRE 稳定性实践  
> 文档类型：Systematic Fault Drills 统一验证报告  
> 状态：进行中（Drill 1、Drill 2 已完成并封存；Drill 3～7 待执行）  
> 更新日期：2026-08-13

---

## 1. 文档目的

本报告统一记录 OpsLab Kubernetes/SRE 项目的系统化故障演练。每个 Drill 按“基线 → 故障注入 → Kubernetes 现象 → 应用现象 → 可观测性 → 恢复 → 验证 → 系统边界 → PASS/FAIL”的方式记录。

本阶段不追求制造更多故障，而是验证：Kubernetes 自愈、软/硬依赖边界、业务降级、Endpoint 连续性、HPA、监控链路、Ingress/Service/Pod 诊断，以及 Local PV 的能力边界。

## 2. Drill 总览

| Drill | 场景 | 状态 | 最终结论 |
|---|---|---:|---|
| Drill 1 | FastAPI Pod Self-Healing | SEALED | PASS |
| Drill 2 | Redis Dependency Failure | SEALED | PASS（首次 FAIL，修复后复测 PASS） |
| Drill 3 | MySQL Pod Self-Healing | Pending | 待执行 |
| Drill 4 | HPA Load / Recovery | Pending | 待执行/复用历史证据 |
| Drill 5 | Monitoring Target Failure | Pending | 待执行 |
| Drill 6 | Ingress / Service / Pod 链路诊断 | Pending | 待执行 |
| Drill 7 | Worker Node / Local PV Boundary | Pending | 最后执行，高风险 |

---

# 3. Drill 1 — FastAPI Pod Self-Healing

## 3.1 实验目标

验证单个 FastAPI Pod 被删除后，Deployment / ReplicaSet 是否自动补齐副本；故障期间业务是否连续；新 Pod 是否重新 Ready 并进入 Endpoint；新的调度过程是否体现 topology spread 约束。

## 3.2 正常基线

实验前确认：FastAPI Deployment 2 副本正常；`/healthz` HTTP 200；`/readyz` HTTP 200；MySQL / Redis 均为 `ok`；Ingress 正常。

```text
FASTAPI_PRE_FAULT_HEALTH=PASS
PRE_FAULT_BASELINE=PASS
```

## 3.3 风险边界

仅删除一个 FastAPI Pod。不删除 Deployment、ReplicaSet、Service、Ingress，不修改 ConfigMap / Secret，不操作 MySQL / Redis，不修改 CNI、kube-proxy 或节点网络。

## 3.4 故障注入

故障开始：

```text
2026-08-13 06:56:41.861
```

删除对象：

```text
opslab-api-74cc9b59dd-8jtmg
```

故障方式：

```bash
kubectl delete pod -n opslab opslab-api-74cc9b59dd-8jtmg --wait=false
```

## 3.5 Kubernetes 现象

- Deployment 可用副本短暂从 `2/2` 变为 `1/2`；
- ReplicaSet 自动创建 `opslab-api-74cc9b59dd-ptdqj`；
- 新 Pod 调度至 `k8s-worker2`，IP `10.244.2.63`；
- 存活 Pod 在 worker1，IP `10.244.1.48`；
- 最终恢复为 worker1 / worker2 各一个副本；
- 新 Pod 启动早期出现一次 startup probe `connection refused`，随后自动恢复，Restart=0。

## 3.6 应用连续性

故障期间以约 0.5 秒间隔持续通过 Ingress 请求 `/readyz`：

```text
FAILED_HTTP_SAMPLES=0
APPLICATION_OBSERVED_CONTINUITY=PASS
```

本次采样窗口内未观测到 HTTP 请求失败。

## 3.7 Endpoint 恢复

```text
10.244.1.48 | ready=true | serving=true | terminating=false
10.244.2.63 | ready=true | serving=true | terminating=false
```

## 3.8 Recovery Time Observation

新 Pod `Ready=True` 的 `lastTransitionTime`：

```text
2026-08-13T06:56:48Z
```

按故障注入时间与 Ready 转换时间计算：

```text
POD_READY_RECOVERY_TIME≈6.139s
```

该值是实验恢复观测，不直接等同于严格业务 RTO。

## 3.9 系统边界

本实验可以证明单个无状态 FastAPI Pod 故障后的控制器自愈、业务连续性和 Endpoint 恢复；不能证明节点永久损坏、数据库 HA、Local PV 跨节点 HA 或所有网络故障都能被多副本掩盖。

## 3.10 最终结论

```text
FASTAPI_PRE_FAULT_HEALTH=PASS
FAULT_INJECTION=PASS
REPLICASET_REPLACEMENT=PASS
DEPLOYMENT_SELF_HEALING=PASS
NEW_POD_SCHEDULED_TO_WORKER2=PASS
TOPOLOGY_SPREAD_ENFORCEMENT=PASS
APPLICATION_OBSERVED_CONTINUITY=PASS
FAILED_HTTP_SAMPLES=0
POD_READY_RECOVERY_TIME≈6.139s
NEW_POD_READY=PASS
ENDPOINT_READY_RECOVERY=PASS
ENDPOINT_SERVING_RECOVERY=PASS
HEALTHZ_POST_RECOVERY=PASS
READYZ_POST_RECOVERY=PASS
MYSQL_DEPENDENCY_POST_RECOVERY=PASS
REDIS_DEPENDENCY_POST_RECOVERY=PASS
DRILL_1_FASTAPI_POD_SELF_HEALING=PASS
```

**Drill 1 状态：SEALED。**

---

# 4. Drill 2 — Redis Dependency Failure

## 4.1 实验目标

验证 Redis 作为 FastAPI 软依赖（Soft Dependency）不可用时：FastAPI 进程继续存活；`/healthz` 正常；`/readyz` 以 `HTTP 200 + degraded` 表达降级；FastAPI Pod 保持 Ready；Endpoint 持续 serving；Cache-Aside 从 Redis 退化到 MySQL；Redis 恢复后缓存能力恢复。

本 Drill 首次执行发现非预期设计缺陷，完整调查见：

```text
../incidents/fastapi-readiness-timeout-budget-collision.md
```

## 4.2 设计语义

```text
MySQL = HARD dependency
Redis = SOFT dependency
```

`/readyz`：MySQL 失败返回 HTTP 503；Redis 单独失败返回 HTTP 200 degraded。业务 GET 在 Redis 异常时应 `BYPASS -> MySQL`。

## 4.3 首次运行：FAIL

故障方式：

```bash
kubectl scale statefulset opslab-redis -n opslab --replicas=0
```

故障开始：

```text
2026-08-13 07:16:01.434
```

故障后 Redis StatefulSet 0/0、Redis Pod 不存在、Redis Endpoint 为空。FastAPI：

```text
/healthz -> HTTP 200
/readyz  -> HTTP 200
            status=degraded
            mysql=ok
            redis=error
```

但 Kubernetes Events 出现：

```text
readiness probe failed: context deadline exceeded
```

随后 FastAPI Pod Ready 发生变化：

```text
opslab-api-...-xsn9z | Ready=False | lastTransitionTime=2026-08-13T07:16:09Z
opslab-api-...-ptdqj | Ready=False | lastTransitionTime=2026-08-13T07:18:26Z
```

Redis Down 时 `/readyz` 延迟：

```text
worker1:
2.007498s
2.010531s
2.021336s
2.007549s
2.008013s

worker2:
2.006203s
2.007982s
2.009560s
2.005377s
2.009733s
```

readinessProbe：

```text
timeoutSeconds=2
periodSeconds=5
failureThreshold=3
successThreshold=1
```

首次结论：

```text
REDIS_FAULT_INJECTION=PASS
FASTAPI_PROCESS_SURVIVAL=PASS
HEALTHZ_DURING_REDIS_FAILURE=PASS
REDIS_DEGRADED_SEMANTICS=PASS
EXPECTED_FASTAPI_READY_DURING_SOFT_DEPENDENCY_FAILURE=FAIL
ACTUAL_FASTAPI_READY=False
ROOT_CAUSE=READINESS_TIMEOUT_BUDGET_COLLISION
DRILL_2_INITIAL_RUN=FAIL
DRILL_2_REMEDIATION_REQUIRED=YES
```

## 4.4 根因

原配置 `DEPENDENCY_TIMEOUT_SECONDS=2` 同时用于 readiness dependency check 与业务 MySQL / Redis 操作，而 kubelet readinessProbe 的 `timeoutSeconds` 也是 2。

```text
应用内部等待 Redis ≈ 2s
        ↓
应用准备返回 HTTP 200 degraded
        ↓
kubelet 外部探针也在 2s 超时
        ↓
可能先触发 context deadline exceeded
        ↓
Pod Ready=False
```

根因：

```text
READINESS_TIMEOUT_BUDGET_COLLISION
```

## 4.5 最小修复

修复为：

```text
READINESS_DEPENDENCY_TIMEOUT_SECONDS=1
DEPENDENCY_TIMEOUT_SECONDS=2
readinessProbe.timeoutSeconds=2
```

即 readiness 内部超时 1 秒，业务依赖 timeout 仍为 2 秒，kubelet readiness timeout 保持 2 秒。

修复版本：

```text
opslab-api v0.3.1
```

不可变镜像：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/opslab-api@sha256:81eeb46199267b97d98bcefabb7dc126a1bf255c2253e5183b604d8bc0d31fdb
```

## 4.6 修复后发布验收

```text
Deployment READY=2/2
APP_VERSION=v0.3.1
READINESS_DEPENDENCY_TIMEOUT_SECONDS=1
DEPENDENCY_TIMEOUT_SECONDS=2
/healthz -> HTTP 200
/readyz  -> HTTP 200 ready mysql=ok redis=ok
```

## 4.7 修复后第二次故障验证：PASS

Redis 再次 Down 后，Pod `10.244.1.53`：

```text
1.128876s
1.022134s
1.008235s
1.019000s
1.022437s
```

Pod `10.244.2.65`：

```text
1.003772s
1.013084s
1.006899s
1.003339s
1.005516s
```

全部返回：

```json
{"status":"degraded","mysql":"ok","redis":"error"}
```

HTTP 全部为 200，最大观测值 `1.128876s < 2s`。

Redis 已不可用超过多个 readiness probe 周期后：

```text
opslab-api-755c64d485-hvr7q   1/1 Running
opslab-api-755c64d485-trglr   1/1 Running

10.244.1.53 | ready=true | serving=true
10.244.2.65 | ready=true | serving=true
```

业务请求：

```text
GET /api/v1/events/1
HTTP/1.1 200 OK
x-cache: BYPASS
```

Body：

```json
{"id":1,"message":"fastapi-mysql-write-read-ok","created_at":"2026-08-08T15:49:47.595942"}
```

证明 Redis 不可用时 FastAPI 成功绕过缓存并由 MySQL 提供业务数据。

## 4.8 Redis 恢复验证

恢复：

```bash
kubectl scale statefulset opslab-redis -n opslab --replicas=1
```

最终：

```text
Redis StatefulSet 1/1
Redis Pod         1/1 Running
FastAPI Pods      2 x 1/1 Running
Endpoint          ready=true / serving=true
/readyz           HTTP 200 ready mysql=ok redis=ok
```

业务 GET：第一次 `MISS`，第二次 `HIT`，证明 Redis 缓存能力已恢复。

## 4.9 最终结论

```text
DRILL_2_INITIAL_RUN=FAIL
ROOT_CAUSE=READINESS_TIMEOUT_BUDGET_COLLISION
REMEDIATION=SEPARATE_READINESS_TIMEOUT
READINESS_TIMEOUT=1s
BUSINESS_DEPENDENCY_TIMEOUT=2s
V0_3_1_ROLLOUT=PASS
READINESS_TIMEOUT_REMEDIATION_DEPLOYED=PASS
REDIS_DEGRADED_RESPONSE=PASS
READYZ_LATENCY_BUDGET=PASS
FASTAPI_READY_DURING_REDIS_FAILURE=PASS
FASTAPI_ENDPOINT_CONTINUITY=PASS
REDIS_CACHE_BYPASS=PASS
MYSQL_FALLBACK=PASS
BUSINESS_CONTINUITY=PASS
REDIS_RECOVERY=PASS
FASTAPI_READINESS_RECOVERY=PASS
ENDPOINT_RECOVERY=PASS
CACHE_RECOVERY_MISS_TO_HIT=PASS
DRILL_2_REMEDIATION_VALIDATION=PASS
DRILL_2_REDIS_DEPENDENCY_FAILURE=PASS
```

**Drill 2 状态：SEALED。**

---

# 5. Drill 3 — MySQL Pod Self-Healing

状态：Pending。只删除 `opslab-mysql-0`，不删除 StatefulSet、PVC、PV，不操作 `/data/mysql`；验证 StatefulSet 重建、PVC 重挂载、数据仍存在、FastAPI readiness 与业务恢复。

# 6. Drill 4 — HPA Load / Recovery

状态：Pending。优先复用既有 HPA 压测与扩缩容证据，必要时轻量复验。

# 7. Drill 5 — Monitoring Target Failure

状态：Pending。只破坏 exporter / scrape 链路中的一个最小点，不同时破坏数据库；验证 Target `up=0`、告警触发、恢复后 `up=1`。

# 8. Drill 6 — Ingress / Service / Pod 链路诊断

状态：Pending。通过最小故障验证 `Client -> Ingress -> Service -> EndpointSlice -> Pod` 的分层诊断方法。

# 9. Drill 7 — Worker Node / Local PV Boundary

状态：Pending，最后执行。验证 Worker 正常 reboot 后 Node 恢复与 Local PV 同节点数据持久性，并明确 Local PV 不等于跨节点 Storage HA。

---

# 10. 当前阶段结论

```text
DRILL_1_FASTAPI_POD_SELF_HEALING=PASS
DRILL_2_REDIS_DEPENDENCY_FAILURE=PASS
COMPLETED_DRILLS=2/7
SEALED_DRILLS=2/7
```

Drill 2 已形成一次完整 SRE 闭环：

```text
Fault Injection
  ↓
Unexpected Failure
  ↓
Evidence Collection
  ↓
Root Cause Analysis
  ↓
Minimal Remediation
  ↓
Immutable Image Release
  ↓
RollingUpdate
  ↓
Same-Scenario Regression Test
  ↓
PASS
```

后续继续 Drill 3 → Drill 7。全部完成后再进入 Final SRE Validation，并冻结传统 SRE Baseline。
