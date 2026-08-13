# Incident — FastAPI Readiness Timeout Budget Collision

> Incident ID：INC-2026-08-13-FASTAPI-READINESS-TIMEOUT-BUDGET  
> 项目：OpsLab Kubernetes/SRE  
> 类型：故障演练发现的可靠性设计缺陷  
> 发现方式：Systematic Fault Drill 2 — Redis Dependency Failure  
> 最终状态：Resolved / Regression Validated  
> 修复版本：opslab-api v0.3.1  
> 日期：2026-08-13

---

## 1. Incident Summary

在 Systematic Fault Drill 2 中，主动将 Redis StatefulSet 从 1 个副本缩容至 0，以验证 Redis 作为 FastAPI 软依赖时的降级行为。

预期：

```text
Redis Down
  ↓
/healthz = 200
/readyz  = 200 degraded
FastAPI Pod 保持 Ready
业务绕过 Redis
X-Cache = BYPASS
MySQL 继续提供数据
```

首次实验实际发现：

```text
Redis Down
  ↓
/readyz 最终仍返回 HTTP 200 degraded
但响应耗时约 2.0s
  ↓
kubelet readinessProbe timeoutSeconds 同样为 2s
  ↓
Probe 出现 context deadline exceeded
  ↓
FastAPI Pod Ready=False
```

这意味着一个应被系统吸收的 Redis 软依赖故障，可能被放大为 FastAPI Endpoint 被 Kubernetes 移除的问题。

根因：

```text
READINESS_TIMEOUT_BUDGET_COLLISION
```

最终修复：

```text
READINESS_DEPENDENCY_TIMEOUT_SECONDS=1
DEPENDENCY_TIMEOUT_SECONDS=2
readinessProbe.timeoutSeconds=2
```

修复发布为 `opslab-api v0.3.1`，重新执行相同 Redis 故障后验证：`/readyz` 约 1.0～1.13 秒返回、HTTP 200 degraded、FastAPI 两个 Pod 始终 Ready、Endpoint 始终 serving、业务 GET HTTP 200、`X-Cache: BYPASS`、MySQL fallback 成功、Redis 恢复后 Cache 从 MISS 恢复到 HIT。

---

## 2. 背景与依赖等级

FastAPI 依赖：

```text
MySQL = HARD dependency
Redis = SOFT dependency
```

MySQL 保存核心业务数据。MySQL 不可用时 `/readyz` 返回 HTTP 503，Pod 应从 Ready Endpoint 中退出。

Redis 仅用于 Cache-Aside。Redis 不可用时 `/readyz` 设计为：

```text
HTTP 200
status=degraded
mysql=ok
redis=error
```

Pod 应继续 `Ready=True / Serving=True`，业务请求应 `BYPASS -> MySQL`。

---

## 3. Incident Detection

故障注入：

```bash
kubectl scale statefulset opslab-redis -n opslab --replicas=0
```

故障开始：

```text
2026-08-13 07:16:01.434
```

明确未删除 Redis PVC、PV、`/data/redis`，未修改 Redis StatefulSet 定义，也未修改 FastAPI Deployment 和 MySQL。

---

## 4. Expected Behavior

```text
Redis StatefulSet = 0/0
Redis Endpoint    = empty
FastAPI process   = alive
/healthz          = HTTP 200
/readyz           = HTTP 200 degraded
FastAPI Pod       = Ready=True
Endpoint          = ready=true / serving=true
Business GET      = HTTP 200
X-Cache           = BYPASS
Data Source       = MySQL
```

软依赖故障只能导致降级，不能因为 readiness 实现细节把核心业务实例踢出 Service Endpoint。

---

## 5. Actual Behavior — Initial Run

Redis 停止后：

```text
Redis StatefulSet = 0/0
Redis Pod         = unavailable
Redis Endpoint    = empty
```

FastAPI 进程未崩溃：

```text
/healthz -> HTTP 200
/readyz  -> HTTP 200
            status=degraded
            mysql=ok
            redis=error
```

但 Kubernetes Events 出现：

```text
Readiness probe failed: context deadline exceeded
```

Ready 条件：

```text
opslab-api-...-xsn9z
Ready=False
lastTransitionTime=2026-08-13T07:16:09Z

opslab-api-...-ptdqj
Ready=False
lastTransitionTime=2026-08-13T07:18:26Z
```

---

## 6. Evidence — Readiness Latency

Redis Down 时分别直连两个 FastAPI Pod，多次请求 `/readyz`：

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

关键证据：

```text
应用内部 Redis readiness timeout ≈ 2s
kubelet probe timeout             = 2s
```

二者没有安全余量。

---

## 7. Source-Level Investigation

### 7.1 `/readyz` HTTP 语义正确

`main.py` 的设计：

```python
if not mysql_ok:
    response.status_code = 503
    return ReadinessStatus(status="not_ready", mysql="error", redis=...)

if not redis_ok:
    return ReadinessStatus(status="degraded", mysql="ok", redis="error")
```

因此：

```text
MySQL failure -> HTTP 503
Redis failure -> HTTP 200 degraded
REDIS_DEGRADED_HTTP_200=BY_DESIGN
```

问题不在 HTTP 状态码。

### 7.2 Cache-Aside 降级逻辑正确

业务 GET：

```text
Redis read success + hit -> HIT
Redis read success + miss -> MISS -> MySQL
Redis exception          -> BYPASS -> MySQL
```

Redis SET 失败为 non-fatal：

```text
REDIS_CACHE_READ_FAILURE=BYPASS_TO_MYSQL
REDIS_CACHE_WRITE_FAILURE=NON_FATAL
```

### 7.3 Timeout 配置复用

原配置：

```text
DEPENDENCY_TIMEOUT_SECONDS=2
```

同时用于 readiness dependency check 与业务 MySQL / Redis 操作，因此一个配置项同时承担 `Readiness latency budget` 与 `Business dependency timeout policy` 两种不同职责。

---

## 8. Root Cause

```text
READINESS_TIMEOUT_BUDGET_COLLISION
```

链路：

```text
Redis Down
  ↓
check_redis()
  ↓
内部 timeout = 2s
  ↓
应用准备在约 2s 后返回 degraded HTTP 200
  ↓
kubelet readinessProbe timeout = 2s
  ↓
内外 timeout 几乎同时到达
  ↓
kubelet 可能先放弃请求
  ↓
context deadline exceeded
  ↓
readiness probe failure
  ↓
Pod Ready=False
```

这不是 Redis 缺陷、Kubernetes bug、HTTP 200 degraded 设计错误或 Cache-Aside 设计错误，而是 Timeout Budget Design Defect。

---

## 9. Impact Analysis

Redis 原本是 Soft Dependency，理想故障模式：

```text
Redis failure
  ↓
cache unavailable
  ↓
性能/延迟退化
  ↓
core business still available
```

原实现可能放大成：

```text
Redis failure
  ↓
readiness check blocks ~2s
  ↓
kubelet timeout
  ↓
Pod NotReady
  ↓
Endpoint removed
```

因此局部缓存故障可能扩大为服务发现/流量承载能力下降。

---

## 10. Remediation Design

修复目标：保持 Redis Soft Dependency 语义，不改变 `/readyz` HTTP 200 degraded 设计，不简单提高 kubelet probe timeout，也不降低业务依赖 timeout。

最终：

```text
READINESS_DEPENDENCY_TIMEOUT_SECONDS=1
DEPENDENCY_TIMEOUT_SECONDS=2
readinessProbe.timeoutSeconds=2
```

形成：

```text
readiness internal timeout = 1s
           <
kubelet probe timeout      = 2s
```

业务层仍保持 2 秒 timeout。

---

## 11. Code Changes

修复范围严格控制在 5 个文件：

```text
applications/fastapi/Dockerfile
applications/fastapi/app/config.py
applications/fastapi/app/dependency_checks.py
kubernetes/apps/opslab-api/00-configmap.yaml
kubernetes/apps/opslab-api/02-deployment.yaml
```

`config.py` 新增 `readiness_dependency_timeout_seconds`，环境变量 `READINESS_DEPENDENCY_TIMEOUT_SECONDS`，默认 1。

`dependency_checks.py` 中 MySQL/Redis readiness check 改用该新 timeout，包括 Redis socket connect、socket timeout 与外层 asyncio timeout。

`database.py` / `cache.py` 不修改，继续使用 `DEPENDENCY_TIMEOUT_SECONDS=2`。

ConfigMap：

```yaml
APP_VERSION: v0.3.1
READINESS_DEPENDENCY_TIMEOUT_SECONDS: "1"
DEPENDENCY_TIMEOUT_SECONDS: "2"
```

---

## 12. Release

修复版本：

```text
opslab-api v0.3.1
```

最终部署 RepoDigest：

```text
sha256:81eeb46199267b97d98bcefabb7dc126a1bf255c2253e5183b604d8bc0d31fdb
```

完整镜像：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/opslab-api@sha256:81eeb46199267b97d98bcefabb7dc126a1bf255c2253e5183b604d8bc0d31fdb
```

发布后：

```text
Deployment READY=2/2
APP_VERSION=v0.3.1
READINESS timeout=1
BUSINESS timeout=2
```

新 Pod 的 imageID 与目标 RepoDigest 一致。

---

## 13. Regression Test — Same Failure, Same Boundary

修复后重新执行完全相同的 Redis 故障：

```bash
kubectl scale statefulset opslab-redis -n opslab --replicas=0
```

用相同场景直接比较修复前后行为。

---

## 14. Regression Evidence — `/readyz`

Pod `10.244.1.53`：

```text
HTTP=200 total=1.128876s
HTTP=200 total=1.022134s
HTTP=200 total=1.008235s
HTTP=200 total=1.019000s
HTTP=200 total=1.022437s
```

Pod `10.244.2.65`：

```text
HTTP=200 total=1.003772s
HTTP=200 total=1.013084s
HTTP=200 total=1.006899s
HTTP=200 total=1.003339s
HTTP=200 total=1.005516s
```

全部 Body：

```json
{"status":"degraded","mysql":"ok","redis":"error"}
```

对比：

```text
修复前 ≈ 2.005～2.021s
修复后 ≈ 1.003～1.129s
```

最慢观测：

```text
1.128876s < 2s kubelet timeout
READYZ_LATENCY_BUDGET=PASS
```

---

## 15. Regression Evidence — Kubernetes Readiness

Redis Down 超过多个 readiness probe 周期后：

```text
opslab-api-755c64d485-hvr7q   1/1 Running
opslab-api-755c64d485-trglr   1/1 Running
```

EndpointSlice：

```text
10.244.1.53 | ready=true | serving=true
10.244.2.65 | ready=true | serving=true
```

Recent Events 中未出现由本次 Redis 故障导致的新 `readiness probe failed: context deadline exceeded`。

```text
FASTAPI_READY_DURING_REDIS_FAILURE=PASS
FASTAPI_ENDPOINT_CONTINUITY=PASS
```

---

## 16. Regression Evidence — Business Fallback

Redis Down 时：

```text
GET /api/v1/events/1
HTTP/1.1 200 OK
x-cache: BYPASS
```

Body：

```json
{
  "id": 1,
  "message": "fastapi-mysql-write-read-ok",
  "created_at": "2026-08-08T15:49:47.595942"
}
```

证明：

```text
Redis cache unavailable
  ↓
FastAPI catches cache exception
  ↓
BYPASS
  ↓
MySQL query
  ↓
HTTP 200
```

```text
REDIS_CACHE_BYPASS=PASS
MYSQL_FALLBACK=PASS
BUSINESS_CONTINUITY=PASS
```

---

## 17. Recovery Validation

恢复 Redis：

```bash
kubectl scale statefulset opslab-redis -n opslab --replicas=1
```

最终：

```text
Redis StatefulSet 1/1
Redis Pod         1/1 Running
FastAPI Pod A     1/1 Running
FastAPI Pod B     1/1 Running
Endpoint A        ready=true / serving=true
Endpoint B        ready=true / serving=true
```

`/readyz`：

```json
{"status":"ready","mysql":"ok","redis":"ok"}
```

业务 GET：第一次 `MISS`，第二次 `HIT`，证明 Redis 缓存链路恢复。

---

## 18. Before / After Comparison

| 项目 | 修复前 | 修复后 |
|---|---|---|
| Redis 依赖等级 | Soft | Soft |
| Redis Down `/readyz` HTTP | 200 | 200 |
| `/readyz` 状态 | degraded | degraded |
| 内部 readiness timeout | 2s | 1s |
| kubelet readiness timeout | 2s | 2s |
| `/readyz` 实测 | ~2.0s | ~1.0–1.13s |
| context deadline exceeded | 出现 | 未出现 |
| FastAPI Ready | 最终 False | 持续 True |
| Endpoint serving | 受影响 | 持续 true |
| Redis Down 业务 GET | 设计可 BYPASS | 实测 BYPASS |
| MySQL fallback | 实现存在 | 实测成功 |
| Redis 恢复后 Cache | - | MISS → HIT |

---

## 19. Final Validation Flags

```text
REDIS_FAULT_INJECTION=PASS
REDIS_POD_UNAVAILABLE=PASS
REDIS_ENDPOINT_REMOVAL=PASS
FASTAPI_PROCESS_SURVIVAL=PASS
HEALTHZ_DURING_REDIS_FAILURE=PASS
REDIS_FAILURE_DETECTION=PASS
MYSQL_DEPENDENCY_ISOLATION=PASS
REDIS_DEGRADED_SEMANTICS=PASS
DRILL_2_INITIAL_RUN=FAIL
EXPECTED_FASTAPI_READY_DURING_SOFT_DEPENDENCY_FAILURE=FAIL
ROOT_CAUSE=READINESS_TIMEOUT_BUDGET_COLLISION
TIMEOUT_CONFIG_SEPARATION=PASS
V0_3_1_IMAGE_DIGEST=PASS
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
INCIDENT_STATUS=RESOLVED
REGRESSION_VALIDATION=PASS
```

---

## 20. Lessons Learned

### 20.1 Readiness 的 HTTP 语义正确，不代表 Probe 一定正确

`/readyz` 最终返回 HTTP 200，但 kubelet 仍可能因外部 timeout 先到达而判定失败。健康检查必须同时考虑 response semantics 与 latency budget。

### 20.2 Soft Dependency 必须在所有层面都保持 Soft

仅在业务代码里 catch exception 不够，Soft Dependency 还必须在 `/readyz` HTTP status、readiness latency、kubelet probe、Service Endpoint、Cache fallback 等层面保持一致。

### 20.3 内外 Timeout 必须有预算层级

更安全的关系是：

```text
inner operation timeout
   <
outer health check timeout
   <
higher-level request/orchestration timeout
```

不能简单设置成 `inner timeout == outer timeout`。

### 20.4 Readiness Timeout 与 Business Timeout 是不同策略

Readiness 的目标是快速判断实例是否可承载流量；Business timeout 的目标是在用户请求场景中给予依赖合理完成机会。拆分 `READINESS_DEPENDENCY_TIMEOUT_SECONDS` 与 `DEPENDENCY_TIMEOUT_SECONDS` 是职责分离，不只是参数调优。

### 20.5 故障演练必须允许 FAIL

本次价值最高的部分不是 Redis 最终恢复，而是：

```text
预期 PASS
  ↓
真实 FAIL
  ↓
保留证据
  ↓
定位根因
  ↓
最小修复
  ↓
同场景复测
  ↓
PASS
```

---

## 21. Scope / What This Incident Does Not Prove

本 Incident 可以证明：Redis 软依赖故障不会再因为 readiness timeout budget collision 导致 FastAPI NotReady；Redis Down 时业务 GET 可通过 MySQL fallback 继续；Redis 恢复后缓存功能可恢复；v0.3.1 修复已在 Kubernetes 真实环境回归验证。

本 Incident 不证明 Redis HA、MySQL 故障时 FastAPI 仍应 Ready、Local PV 跨节点 HA，也不覆盖所有类型的网络抖动。

---

## 22. Closure

```text
Detection
  ↓
Evidence Collection
  ↓
Source-Level Analysis
  ↓
Root Cause
  ↓
Minimal Remediation
  ↓
Version Release
  ↓
Immutable Image Deployment
  ↓
Same-Fault Regression Test
  ↓
Recovery Validation
  ↓
Closure
```

最终：

```text
INCIDENT_STATUS=RESOLVED
DRILL_2_REDIS_DEPENDENCY_FAILURE=PASS
```

后续无需重复本场景；统一 Systematic Fault Drills 报告保留 Drill 2 核心摘要，并引用本文作为完整 Incident 证据。
