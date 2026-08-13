# OpsLab Systematic Fault Drills Validation

> 项目：基于 kubeadm 的 Kubernetes 云原生应用部署与 SRE 稳定性实践
> 文档类型：Systematic Fault Drills 统一验证报告
> 状态：进行中（Drill 1、Drill 2、Drill 3 已完成并封存；Drill 4～7 待执行）
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
| Drill 3 | MySQL Pod Self-Healing | SEALED | PASS（首次 FAIL，修复后复测 PASS） |
| Drill 4 | HPA Load / Recovery | SEALED | PASS |
| Drill 5 | Monitoring Target Failure | SEALED | PASS（复用历史真实告警证据并完成当前 Runtime 交叉验证） |
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

## 5.1 实验目标

验证 MySQL Pod 发生单实例级故障时，系统是否能够依赖 Kubernetes StatefulSet 与既有持久化存储完成自动恢复，并确认：

* StatefulSet 能够自动重新创建 `opslab-mysql-0`；
* 重建后的 MySQL Pod 继续挂载原 PVC；
* 原 PVC 继续绑定原 Local PV；
* `/data/mysql` 中的持久化数据不因 Pod 重建而丢失；
* FastAPI 在 MySQL HARD dependency 故障期间能够正确反映不可就绪状态；
* MySQL 恢复后 FastAPI 能够自动恢复，而不依赖人工重启；
* 业务数据能够在恢复后继续读取；
* Pod 级故障恢复不依赖 MySQL Backup / Restore 流程。

本 Drill 与已经完成的 MySQL Backup / Restore 实验边界不同：

* 本 Drill 验证 StatefulSet + PVC + Local PV 的 Pod 级自愈；
* Backup / Restore 验证逻辑备份及人工恢复能力；
* 本 Drill 不删除 PVC、PV、StorageClass 或 `/data/mysql`。

---

## 5.2 风险范围与禁止操作

Fault Injection 仅允许：

```bash
kubectl delete pod \
  -n opslab \
  opslab-mysql-0 \
  --wait=false
```

本实验明确禁止：

* 删除 StatefulSet；
* 删除 PVC；
* 删除 PV；
* 删除 StorageClass；
* 删除 `/data/mysql`；
* 修改数据目录权限；
* reboot `k8s-worker1`；
* 重新执行 MySQL Backup / Restore；
* 人工重建 MySQL Pod；
* 为获得 PASS 人工重启 FastAPI。

---

## 5.3 PRE-FAULT BASELINE

实验前状态：

### Kubernetes

* 三节点均为 `Ready`；
* MySQL StatefulSet：`opslab-mysql`，`1/1 Ready`；
* MySQL Pod：`opslab-mysql-0`，`1/1 Running`；
* MySQL 所在节点：`k8s-worker1`；
* MySQL Pod Restart：`0`。

首次实验前 MySQL Pod：

```text
POD_UID=c921ba90-6488-4a1a-aa3a-ca76be53ad42
POD_IP=10.244.1.36
NODE=k8s-worker1
START_TIME=2026-08-12T07:13:40Z
READY=True
READY_LAST_TRANSITION=2026-08-12T07:14:04Z
```

### Storage

```text
PVC=opslab-mysql-data
PV=opslab-mysql-local-pv
PVC_STATUS=Bound
PV_STATUS=Bound
STORAGE_CLASS=local-storage
LOCAL_PATH=/data/mysql
NODE_AFFINITY=k8s-worker1
RECLAIM_POLICY=Retain
```

### MySQL Endpoint

```text
10.244.1.36
ready=true
serving=true
terminating=false
```

### FastAPI

FastAPI Deployment 正常运行，业务链路正常。

应用正常状态：

```text
/healthz
HTTP 200
{"status":"ok"}

/readyz
HTTP 200
{"status":"ready","mysql":"ok","redis":"ok"}
```

业务基线：

```text
GET /api/v1/events/1
HTTP 200
```

数据：

```text
id=1
message=fastapi-mysql-write-read-ok
created_at=2026-08-08 15:49:47.595942
```

同时直接进入 MySQL 查询确认该记录真实存在于：

```text
opslab.opslab_events
```

### Ingress 测试说明

测试终端本地没有 `api.opslab.local` 的静态解析记录。

该问题被确认只是客户端 hostname resolution gap，而非 Ingress、Service、FastAPI 或 MySQL 故障。

后续实验固定使用：

```bash
curl --resolve 'api.opslab.local:80:192.168.8.11' ...
```

或 worker2：

```bash
curl --resolve 'api.opslab.local:80:192.168.8.12' ...
```

保持正确 HTTP Host Header，同时避免修改 `/etc/hosts`。

两个 Ingress 节点均验证：

```text
/healthz = HTTP 200
/readyz  = HTTP 200
Event ID=1 = HTTP 200
```

因此：

```text
DRILL_3_PRE_FAULT_BASELINE=PASS
```

---

## 5.4 第一次 Fault Injection

仅删除：

```text
opslab-mysql-0
```

未操作：

* StatefulSet；
* PVC；
* PV；
* `/data/mysql`；
* worker1；
* FastAPI Deployment。

StatefulSet 随后自动重新创建 MySQL Pod。

新 Pod：

```text
UID=88f03466-5987-4002-9566-6c0996600b0e
IP=10.244.1.54
NODE=k8s-worker1
START_TIME=2026-08-13T09:00:32Z
READY=True
READY_TRANSITION=2026-08-13T09:00:38Z
```

MySQL EndpointSlice 恢复：

```text
10.244.1.54
ready=true
serving=true
terminating=false
```

因此 StatefulSet 与 MySQL Pod 本身已经完成自动恢复。

---

## 5.5 非预期结果：MySQL 已恢复，但 FastAPI 未恢复

MySQL Pod 已经：

```text
1/1 Running
Ready=True
```

MySQL Endpoint 也已经：

```text
ready=true
serving=true
```

但两个 FastAPI Pod 持续：

```text
0/1 Running
Ready=False
```

FastAPI EndpointSlice：

```text
ready=false
serving=false
```

应用内部：

```text
/healthz
HTTP 200

/readyz
HTTP 503
{"status":"not_ready","mysql":"error","redis":"ok"}
```

Ingress 最终表现：

```text
HTTP 502 Bad Gateway
```

FastAPI 日志持续出现：

```text
MySQL readiness check failed: RuntimeError
```

MySQL 在 `09:00:38Z` 已进入 Ready。

在 `09:05:23` 保存现场时，FastAPI 仍未恢复。

即：

```text
MYSQL_RECOVERED=YES
FASTAPI_AUTO_RECOVERY=NO
```

该持续时间已经明显超过多个 readiness probe 周期，因此不能解释为普通恢复收敛延迟。

第一次 Drill 3 判定：

```text
DRILL_3_INITIAL_RUN=FAIL
```

---

## 5.6 分层故障定位

继续从 FastAPI Pod 内进行诊断。

### DNS

```text
MYSQL_HOST=opslab-mysql
RESOLVED_ADDRESSES=['10.103.121.36']

DNS_RESOLUTION=PASS
```

### TCP

FastAPI Pod → MySQL Service `3306/TCP`：

```text
TCP_CONNECT=PASS
```

因此排除：

* Kubernetes DNS 故障；
* MySQL Service 故障；
* ClusterIP 不可达；
* TCP 3306 网络不通。

### Fresh aiomysql connection

在两个 FastAPI Pod 内分别创建全新的 aiomysql connection：

```text
FRESH_AIOMYSQL_CONNECTION=FAIL
ERROR_TYPE=RuntimeError
```

错误：

```text
'cryptography' package is required for
sha256_password or caching_sha2_password auth methods
```

这证明问题发生在：

```text
TCP connection established
        ↓
MySQL authentication handshake
        ↓
FAIL
```

而不是网络层。

---

## 5.7 Root Cause

进一步确认：

FastAPI 镜像：

```text
cryptography=NOT_INSTALLED
```

显式 import：

```text
CRYPTOGRAPHY_IMPORT=FAIL
ModuleNotFoundError:
No module named 'cryptography'
```

MySQL：

```text
VERSION=8.4.10
```

业务用户：

```text
opslab_app@%
authentication plugin=caching_sha2_password
```

原 FastAPI direct dependency：

```text
aiomysql==0.3.2
```

但没有：

```text
cryptography
```

因此根因确定为：

```text
MYSQL_CACHING_SHA2_FULL_AUTH_DEPENDENCY_GAP
```

故障链：

```text
MySQL Pod replacement
        ↓
MySQL Server restart
        ↓
后续连接进入 caching_sha2_password 完整认证路径
        ↓
aiomysql / PyMySQL 需要 cryptographic/RSA client capability
        ↓
FastAPI image 中 cryptography 缺失
        ↓
RuntimeError
        ↓
check_mysql() 持续失败
        ↓
/readyz = 503
        ↓
FastAPI Pod Ready=False
        ↓
FastAPI EndpointSlice ready=false
        ↓
NGINX 无 Ready upstream
        ↓
502 Bad Gateway
```

本问题不是：

* Kubernetes StatefulSet 故障；
* PVC / PV 故障；
* Local PV 数据丢失；
* MySQL Service / DNS 故障；
* Ingress 故障；
* stale connection pool；
* Redis 故障；
* readiness timeout budget collision。

---

## 5.8 最小修复

没有修改：

* `database.py`；
* `dependency_checks.py`；
* `main.py`；
* MySQL authentication plugin；
* readiness probe；
* dependency timeout；
* PVC / PV；
* MySQL StatefulSet。

仅补齐客户端认证能力。

`requirements.txt` 新增：

```text
cryptography==49.0.0
```

`requirements.lock.txt` 新增：

```text
cffi==2.1.1
cryptography==49.0.0
pycparser==3.0
```

隔离虚拟环境验证：

```text
pip check
No broken requirements found.

MYSQL_CLIENT_RUNTIME_IMPORTS=PASS
PYTHON_COMPILEALL=PASS
DIRECT_TO_LOCK_CONSISTENCY=PASS
LOCK_SEMANTIC_MATCH=PASS
SENSITIVE_LITERAL_SCAN=PASS
```

---

## 5.9 v0.3.2 Release

修复 commit：

```text
6a5d81e
fix(api): support mysql caching sha2 authentication
```

Git Tag：

```text
opslab-api-v0.3.2
```

ACR 构建完成后的 immutable RepoDigest：

```text
sha256:4e268edf2609de2c5477323b104548c141999bfb2251507ed14e2f4c723135b8
```

完整镜像：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/opslab-api@sha256:4e268edf2609de2c5477323b104548c141999bfb2251507ed14e2f4c723135b8
```

Deployment pin commit：

```text
09d0ee3
chore(deploy): pin opslab api v0.3.2 image
```

---

## 5.10 v0.3.2 Remediation Deployment Validation

新镜像部署后：

```text
APP_VERSION=v0.3.2
READINESS_DEPENDENCY_TIMEOUT_SECONDS=1
DEPENDENCY_TIMEOUT_SECONDS=2
```

运行时：

```text
cryptography_METADATA=49.0.0
aiomysql_METADATA=0.3.2
PyMySQL_METADATA=1.2.0
cffi_METADATA=2.1.1
pycparser_METADATA=3.0
```

`pip freeze` 与 lock 一致。

所有新 FastAPI Pod：

```text
MYSQL_FULL_AUTH_CLIENT_CAPABILITY=PASS
FRESH_MYSQL_CONNECTION=PASS
SELECT_1=(1,)
```

应用恢复：

```text
/healthz
HTTP 200

/readyz
HTTP 200
{"status":"ready","mysql":"ok","redis":"ok"}
```

FastAPI EndpointSlice：

```text
ready=true
serving=true
```

业务：

```text
GET /api/v1/events/1
HTTP 200
```

因此：

```text
V0_3_2_ROLLOUT=PASS
MYSQL_AUTH_REMEDIATION_DEPLOYED=PASS
CURRENT_INCIDENT_RECOVERY=PASS
```

---

## 5.11 Same-Scenario Regression

为了证明修复不是由于应用重启或认证状态偶然变化造成，再次执行完全相同的 Fault Injection：

```bash
kubectl delete pod \
  -n opslab \
  opslab-mysql-0 \
  --wait=false
```

回归前 MySQL：

```text
OLD_UID=88f03466-5987-4002-9566-6c0996600b0e
OLD_IP=10.244.1.54
```

回归后：

```text
NEW_UID=b4b31e01-b32c-4f6c-a5c8-1f9e88acca54
NEW_IP=10.244.1.59
NEW_START_TIME=2026-08-13T09:47:26Z
READY=True
READY_TRANSITION=2026-08-13T09:47:32Z
```

确认：

```text
MYSQL_POD_UID_CHANGED=PASS
MYSQL_STATEFULSET_RECREATION=PASS
```

---

## 5.12 应用层故障与恢复时间线

Ingress `/readyz` 观测：

```text
09:47:23.103
HTTP 200
{"status":"ready","mysql":"ok","redis":"ok"}

09:47:23.656
HTTP 503
{"status":"not_ready","mysql":"error","redis":"ok"}

...

09:47:32.215
HTTP 503

09:47:33.777
HTTP 200
{"status":"ready","mysql":"ok","redis":"ok"}
```

说明：

```text
FastAPI readiness
ready
→ not_ready
→ ready
```

并且整个过程中没有人工重启 FastAPI。

最终两个 FastAPI Pod：

```text
RESTARTS=0
READY=True
```

旧版本出现的：

```text
cryptography package is required...
```

错误没有再次出现。

故障期间日志只出现与 MySQL 真正暂时不可用相符的：

```text
OperationalError
TimeoutError
```

MySQL 恢复后这些错误停止影响 readiness。

因此：

```text
MYSQL_FULL_AUTH_RECOVERY=PASS
FASTAPI_MANUAL_RESTART_REQUIRED=NO
PREVIOUS_CRYPTOGRAPHY_ERROR_REPRODUCED=NO
```

---

## 5.13 Kubernetes Ready 状态说明

本次回归虽然应用 `/readyz` 短暂返回 HTTP 503，但 Kubernetes 中 FastAPI Pod 并没有观察到：

```text
Ready=True → Ready=False
```

FastAPI EndpointSlice 也没有观察到：

```text
ready=true → ready=false
```

实际状态始终保持：

```text
Pod Ready=True
Endpoint ready=true
serving=true
```

原因是此次 MySQL 故障窗口较短，应用已经在 kubelet readiness probe 达到连续失败阈值之前自行恢复。

因此本 Drill 不能写成：

```text
FastAPI Pod Ready=False → Ready=True
```

真实结论应为：

```text
APPLICATION_READINESS_TEMPORARY_503=OBSERVED
KUBERNETES_POD_READY_STATE_TRANSITION=NOT_OBSERVED
FASTAPI_ENDPOINT_REMOVAL=NOT_OBSERVED
```

这与第一次失败形成明显对比：

第一次由于认证缺陷长期存在，最终导致 FastAPI Pod 真正进入 `Ready=False`；

修复后应用能够在较短时间内自行恢复，因此未触发 Kubernetes Endpoint 摘除。

---

## 5.14 Storage 与数据恢复验证

Same-Scenario Regression 后：

```text
PVC=opslab-mysql-data
STATUS=Bound

PV=opslab-mysql-local-pv
STATUS=Bound
RECLAIM_POLICY=Retain
```

MySQL Endpoint：

```text
10.244.1.59
ready=true
serving=true
terminating=false
```

业务：

```text
GET /api/v1/events/1
HTTP 200
```

返回：

```json
{
  "id": 1,
  "message": "fastapi-mysql-write-read-ok",
  "created_at": "2026-08-08T15:49:47.595942"
}
```

直接进入 MySQL 查询：

```text
id  message                       created_at
1   fastapi-mysql-write-read-ok   2026-08-08 15:49:47.595942
```

因此：

```text
MYSQL_PVC_PRESERVED=PASS
MYSQL_PV_PRESERVED=PASS
MYSQL_DATA_PRESERVED=PASS
BUSINESS_EVENT_POST_RECOVERY=PASS
```

没有使用 Backup / Restore。

---

## 5.15 Recovery Observation

现有采样能够严谨记录：

### Application readiness disruption

首次观察到 HTTP 503：

```text
09:47:23.656
```

首次重新观察到 HTTP 200：

```text
09:47:33.777
```

因此：

```text
OBSERVED_APPLICATION_READINESS_DISRUPTION≈10.121s
```

该指标只是采样意义上的 application readiness disruption observation，不等同严格业务 RTO。

### MySQL Pod Start → Ready

```text
START_TIME=09:47:26Z
READY_TRANSITION=09:47:32Z
```

因此：

```text
OBSERVED_MYSQL_POD_START_TO_READY≈6s
```

### Observed MySQL Ready → Application Readiness Recovery

Kubernetes 观察循环首次看到 MySQL Ready：

```text
09:47:33.281
```

Ingress 首次重新观察到 `/readyz=200`：

```text
09:47:33.777
```

因此：

```text
OBSERVED_MYSQL_READY_TO_APP_RECOVERY≈0.496s
```

以上均为 Observation，不声明为严格业务 RTO。

---

## 5.16 System Boundary

本 Drill 能够证明：

* StatefulSet 能够自动补回被删除的 MySQL Pod；
* Pod UID / Pod IP 可以变化而业务数据保持；
* PVC 与 Local PV 能够在同一节点上的 Pod 重建过程中保持数据；
* MySQL Server 重启后，v0.3.2 FastAPI 能够重新完成认证；
* FastAPI 不需要人工 restart 即可恢复；
* 业务数据恢复不依赖 Backup / Restore；
* systematic fault drill 能够发现正常运行状态下未暴露的 runtime dependency 缺陷。

本 Drill 不能证明：

* worker1 永久损坏后的 MySQL Storage HA；
* Local PV 跨节点自动迁移；
* MySQL 主从 / Group Replication 高可用；
* 零业务错误；
* 严格业务 RTO；
* 节点级灾难恢复。

Local PV 仍然只证明：

```text
Pod rebuild on same storage node → persistence
```

不能描述成：

```text
Cross-node Storage HA
```

---

## 5.17 最终结论

```text
DRILL_3_PRE_FAULT_BASELINE=PASS

DRILL_3_INITIAL_RUN=FAIL

ROOT_CAUSE=
MYSQL_CACHING_SHA2_FULL_AUTH_DEPENDENCY_GAP

REMEDIATION=
ADD_CRYPTOGRAPHY_CLIENT_CAPABILITY

V0_3_2_RELEASE=PASS
V0_3_2_IMMUTABLE_IMAGE=PASS
V0_3_2_ROLLOUT=PASS

MYSQL_POD_UID_CHANGED=PASS
MYSQL_STATEFULSET_RECREATION=PASS

MYSQL_PVC_PRESERVED=PASS
MYSQL_PV_PRESERVED=PASS
MYSQL_DATA_PRESERVED=PASS

MYSQL_ENDPOINT_RECOVERY=PASS

APPLICATION_READINESS_TEMPORARY_503=EXPECTED
APPLICATION_READINESS_AUTO_RECOVERY=PASS

KUBERNETES_POD_READY_STATE_TRANSITION=NOT_OBSERVED
FASTAPI_ENDPOINT_REMOVAL=NOT_OBSERVED

FASTAPI_MANUAL_RESTART_REQUIRED=NO
FASTAPI_RESTART_COUNT=0

PREVIOUS_CRYPTOGRAPHY_ERROR_REPRODUCED=NO
MYSQL_FULL_AUTH_RECOVERY=PASS

HEALTHZ_POST_RECOVERY=PASS
READYZ_POST_RECOVERY=PASS
BUSINESS_EVENT_POST_RECOVERY=PASS

OBSERVED_APPLICATION_READINESS_DISRUPTION≈10.121s
OBSERVED_MYSQL_POD_START_TO_READY≈6s
OBSERVED_MYSQL_READY_TO_APP_RECOVERY≈0.496s

SAME_SCENARIO_REGRESSION=PASS

DRILL_3_MYSQL_POD_SELF_HEALING=PASS
```

**Drill 3 状态：SEALED。**




后续继续 Drill 4 → Drill 7。全部完成后再进入 Final SRE Validation，并冻结传统 SRE Baseline。

# 6. Drill 4 — HPA Load / Recovery

## 6.1 验证目标

本 Drill 用于验证 FastAPI 在 CPU 负载变化下，Kubernetes HPA 能否形成完整的自动扩缩容闭环：

```text
Baseline
  ↓
HTTP Load
  ↓
CPU Utilization > Target
  ↓
HPA Scale Out
  ↓
New Pods Ready
  ↓
Load Removed
  ↓
CPU Utilization Decrease
  ↓
Scale Down Stabilization
  ↓
HPA Scale In
  ↓
minReplicas
  ↓
Business Healthy
```

本阶段不以“重新执行一次高负载测试”为目标。

Repository 中已经存在完整的历史 HPA 验证：

```text
docs/validation/metrics-server-and-hpa-validation.md
```

以及负载测试脚本：

```text
scripts/hpa-load-test.sh
```

因此，本次 Systematic Fault Drill 采用：

```text
Historical Evidence Reuse
+
Current Runtime Corroboration
```

进行验证。

基本原则：

```text
已有真实证据足够
→ 不机械重复高负载实验
→ 只补当前状态与最终业务健康证据
```

---

## 6.2 HPA 配置基线

正式 HPA Manifest：

```text
kubernetes/hpa/opslab-api-hpa.yaml
```

关键配置：

```text
API: autoscaling/v2
Target: Deployment/opslab-api

minReplicas: 2
maxReplicas: 4

Metric:
CPU Resource Utilization

averageUtilization:
60%
```

FastAPI Pod CPU Request：

```text
50m
```

HPA CPU Utilization 的基本计算关系为：

```text
CPU Utilization
=
Pod CPU Usage
/
Pod CPU Request
×
100%
```

因此本实验的控制目标为：

```text
Target CPU Utilization = 60%
Replica Range = 2 ～ 4
```

验证：

```text
HPA_CONFIGURATION_BASELINE=PASS
```

---

## 6.3 历史空闲基线

历史 HPA 验证中，空闲状态曾观察到：

```text
cpu: 12% / 60%
replicas: 2
```

FastAPI Pod 分布：

```text
worker1 → 1 Pod
worker2 → 1 Pod
```

说明在低负载情况下：

```text
CPU < Target
```

同时 HPA 保持：

```text
minReplicas=2
```

作为应用基础副本数。

验证：

```text
IDLE_BASELINE=PASS
```

---

## 6.4 CPU Load 与 Scale Out

真实 HTTP Load 实验期间，HPA 曾连续观察到：

```text
122% / 60%
305% / 60%
280% / 60%
242% / 60%
```

说明：

```text
FastAPI CPU Utilization
>
HPA Target 60%
```

随后 HPA 自动执行扩容：

```text
2 replicas
↓
4 replicas
```

历史 HPA Event：

```text
SuccessfulRescale
New size: 4
reason:
cpu resource utilization
(percentage of request) above target
```

因此：

```text
CPU_ABOVE_TARGET=PASS
HPA_SCALE_OUT=PASS
```

---

## 6.5 maxReplicas 边界验证

持续负载期间，即使 FastAPI 已经扩容至：

```text
4 Pods
```

CPU Utilization 仍观察到：

```text
305%
280%
242%
```

HPA Condition：

```text
ScalingLimited=True
Reason=TooManyReplicas
```

Message：

```text
the desired replica count is more than
the maximum replica count
```

实际控制过程：

```text
CPU 持续高于 Target
        ↓
HPA Desired Replicas > 4
        ↓
maxReplicas=4
        ↓
实际副本保持 4
```

因此：

```text
MAX_REPLICAS_ENFORCEMENT=PASS
```

这里的：

```text
ScalingLimited=True
```

并不代表 HPA 故障。

在本实验中，它表示 HPA 已经根据指标计算出更高的 desired replicas，但受到 `maxReplicas=4` 的策略边界限制。

---

## 6.6 扩容后的 Pod 调度

历史实验中，扩容后的 4 个 FastAPI Pod 全部 Ready。

最终分布：

```text
worker1 → 2 Pods
worker2 → 2 Pods
```

因此能够确认 HPA 扩容之后：

```text
HPA
 ↓
Deployment Replica Change
 ↓
ReplicaSet
 ↓
Scheduler
 ↓
topologySpreadConstraints
 ↓
worker1 ×2 / worker2 ×2
```

仍然能够正常工作。

验证：

```text
SCALED_PODS_READY=PASS
TOPOLOGY_SPREAD_AFTER_SCALE_OUT=PASS
```

---

## 6.7 Load Removal 与 Metrics Recovery

停止 HTTP Load 后，FastAPI CPU Utilization 从此前的 300% 级别下降到约：

```text
11%
```

即：

```text
High CPU Load
↓
Load Removed
↓
CPU Utilization Drops
```

Metrics Server 能够继续向 HPA 提供有效 CPU Resource Metric。

因此：

```text
LOAD_REMOVAL=PASS
METRICS_RECOVERY=PASS
```

---

## 6.8 Scale Down Stabilization

压力解除以后，Deployment 并没有立即：

```text
4 → 2
```

而是观察到：

```text
AbleToScale=True
Reason=ScaleDownStabilized
```

Message：

```text
recent recommendations were higher than
current one, applying the highest recent
recommendation
```

说明 HPA 在缩容时不会因为单次低负载采样立即删除 Pod，而是通过 Scale Down Stabilization 降低副本数量快速上下波动的风险。

控制过程表现为：

```text
High Load
↓
Scale Out
↓
Load Removed
↓
CPU Drops
↓
Scale Down Stabilization
↓
Scale In
```

验证：

```text
SCALE_DOWN_STABILIZATION=PASS
```

---

## 6.9 Scale In 与 minReplicas

经过稳定阶段以后，HPA 自动完成：

```text
4 replicas
↓
2 replicas
```

历史 Event：

```text
SuccessfulRescale
New size: 2
reason:
All metrics below target
```

低负载状态还曾观察到：

```text
ScalingLimited=True
Reason=TooFewReplicas
```

说明：

```text
CPU 低于 Target
        ↓
理论 Desired Replicas < 2
        ↓
minReplicas=2
        ↓
实际保持 2 Pods
```

因此：

```text
HPA_SCALE_IN=PASS
MIN_REPLICAS_ENFORCEMENT=PASS
```

最终恢复：

```text
worker1 → 1 Pod
worker2 → 1 Pod
```

重新获得基础的跨 Worker 应用副本冗余。

---

## 6.10 当前 Runtime 交叉验证

本次 Drill 没有重新运行高负载测试。

首先对当前 Kubernetes Runtime 进行了检查。

当前 HPA：

```text
TARGETS:
cpu: 13% / 60%

MINPODS:
2

MAXPODS:
4

REPLICAS:
2
```

当前两个 FastAPI Pod：

```text
worker1:
1/1 Running
RESTARTS=0

worker2:
1/1 Running
RESTARTS=0
```

说明当前系统已经重新收敛至：

```text
Low CPU Load
↓
2 Replicas
↓
worker1 ×1
worker2 ×1
```

此前同一次 Runtime Audit 中还观察到：

```text
AbleToScale=True
Reason=ScaleDownStabilized

ScalingActive=True
Reason=ValidMetricFound

ScalingLimited=False
Reason=DesiredWithinRange
```

其中：

```text
ScalingActive=True
Reason=ValidMetricFound
```

证明 HPA 当前仍然能够获取有效的 CPU Resource Metric 并计算 desired replicas。

同时，当前 HPA Events 中仍保留：

```text
New size: 3
reason:
cpu resource utilization above target

New size: 4
reason:
cpu resource utilization above target

New size: 3
reason:
All metrics below target

New size: 2
reason:
All metrics below target
```

形成新的 Runtime 佐证：

```text
CPU Above Target
↓
Scale Out
↓
3
↓
4
↓
Metrics Below Target
↓
Scale In
↓
3
↓
2
```

因此：

```text
CURRENT_HPA_RUNTIME=PASS
CURRENT_REPLICA_CONVERGENCE=PASS
```

---

## 6.11 最终业务健康验证

HPA 已经恢复至：

```text
replicas=2
```

随后通过 worker1 上的 NGINX Ingress 进行最终业务检查。

### 6.11.1 Liveness

请求：

```text
GET /healthz
```

返回：

```json
{"status":"ok"}
```

HTTP：

```text
200
```

验证：

```text
FINAL_HEALTHZ=PASS
```

### 6.11.2 Readiness

请求：

```text
GET /readyz
```

返回：

```json
{
  "status": "ready",
  "mysql": "ok",
  "redis": "ok"
}
```

HTTP：

```text
200
```

验证：

```text
FINAL_READYZ=PASS
```

这同时证明最终状态下：

```text
FastAPI = healthy
MySQL = ok
Redis = ok
```

### 6.11.3 Business Read

请求：

```text
GET /api/v1/events/1
```

返回：

```json
{
  "id": 1,
  "message": "fastapi-mysql-write-read-ok",
  "created_at": "2026-08-08T15:49:47.595942"
}
```

HTTP：

```text
200
```

该验证不仅证明 Pod 处于 Ready，还真实经过：

```text
Client
  ↓
NGINX Ingress
  ↓
Service
  ↓
EndpointSlice / Pod
  ↓
FastAPI
  ↓
MySQL
  ↓
Business Response
```

最终：

```text
FINAL_BUSINESS_HEALTH=PASS
```

---

## 6.12 Evidence Reuse 决策

Drill 4 开始前首先进行了 Repository Evidence Audit。

确认已经存在：

```text
kubernetes/hpa/opslab-api-hpa.yaml
scripts/hpa-load-test.sh
docs/validation/metrics-server-and-hpa-validation.md
```

历史验证已经真实覆盖：

```text
HTTP Load
→ CPU > 60%
→ HPA 2 → 4
→ maxReplicas
→ New Pods Ready
→ Topology Spread
→ Load Removal
→ CPU Recovery
→ ScaleDownStabilized
→ HPA 4 → 2
→ minReplicas
```

本轮 Systematic Fault Drill 又补充：

```text
Current HPA Runtime
+
Current Pod State
+
Current HPA Events
+
Ingress Health Check
+
Readiness Dependency Check
+
Real Business Read
```

因此重新执行高 CPU 压测不会显著增加新的有效证据，反而会增加不必要的实验扰动和 blast radius。

本 Drill 最终采用：

```text
EVIDENCE_REUSE
+
TARGETED_CORROBORATION
```

而不是：

```text
MECHANICAL_RETEST
```

这一决策符合本项目故障演练原则：

```text
已有真实证据优先复用
↓
审计证据完整性
↓
只补缺失证据
↓
避免无意义重复故障注入
```

---

## 6.13 Drill 4 最终证据链

```text
Idle Baseline
      ↓
HTTP Load
      ↓
CPU > 60% Target
      ↓
HPA Scale Out
      ↓
2 → 4 Replicas
      ↓
maxReplicas=4
      ↓
4 Pods Ready
      ↓
worker1 ×2 / worker2 ×2
      ↓
Load Removed
      ↓
CPU Drops
      ↓
ScaleDownStabilized
      ↓
HPA Scale In
      ↓
4 → 2 Replicas
      ↓
minReplicas=2
      ↓
worker1 ×1 / worker2 ×1
      ↓
/healthz HTTP 200
      ↓
/readyz HTTP 200
      ↓
Business Read HTTP 200
```

最终判定：

```text
DRILL4_EVIDENCE_DECISION=REUSE
HPA_LOAD_RETEST=NOT_REQUIRED

HPA_CONFIGURATION_BASELINE=PASS
IDLE_BASELINE=PASS
CPU_ABOVE_TARGET=PASS
HPA_SCALE_OUT=PASS
MAX_REPLICAS_ENFORCEMENT=PASS
SCALED_PODS_READY=PASS
TOPOLOGY_SPREAD_AFTER_SCALE_OUT=PASS
LOAD_REMOVAL=PASS
METRICS_RECOVERY=PASS
SCALE_DOWN_STABILIZATION=PASS
HPA_SCALE_IN=PASS
MIN_REPLICAS_ENFORCEMENT=PASS
CURRENT_HPA_RUNTIME=PASS
CURRENT_REPLICA_CONVERGENCE=PASS
FINAL_HEALTHZ=PASS
FINAL_READYZ=PASS
FINAL_BUSINESS_HEALTH=PASS

DRILL_4_HPA_LOAD_RECOVERY=PASS
```

**Drill 4 状态：SEALED。**

# 7. Drill 5 — Monitoring Target Failure

## 7.1 验证目标

本 Drill 用于验证 OpsLab 的监控与告警链路是否能够形成完整故障检测闭环：

```text
Healthy Target
  ↓
Prometheus up=1
  ↓
Scrape Failure
  ↓
Prometheus up=0
  ↓
Alert Pending
  ↓
Alert Firing
  ↓
Alertmanager Active
  ↓
External Notification
  ↓
Target Recovery
  ↓
Prometheus up=1
  ↓
Rule Inactive
  ↓
Alertmanager Resolved
  ↓
Resolved Notification
```

本阶段重点不是证明“Prometheus Pod 能 Running”，而是验证：

```text
Metric Collection
+
Failure Detection
+
Alert State Machine
+
Alert Routing
+
External Notification
+
Recovery Convergence
```

完整 SRE 告警闭环。

---

## 7.2 Evidence Reuse 决策

Drill 5 开始前首先审计 Repository 中已有监控与告警证据。

确认存在：

```text
docs/validation/fastapi-alerting-end-to-end-validation.md
docs/observability/fastapi-prometheus-grafana-observability-stage-report.md

kubernetes/monitoring/opslab-api-servicemonitor.yaml
kubernetes/monitoring/rules/opslab-api-alerts.yaml

scripts/apply-alertmanager-email.sh
```

历史验证已经通过真实故障注入完整覆盖：

```text
FastAPI Target up=1
↓
Scrape Fault Injection
↓
FastAPI Target up=0
↓
Inactive → Pending
↓
Pending → Firing
↓
Alertmanager Active
↓
QQ Firing Email
↓
Scrape Recovery
↓
FastAPI Target up=1
↓
Rule Inactive
↓
Alertmanager Active Alert Cleared
↓
QQ Resolved Email
```

因此本次 Systematic Fault Drill 决定：

```text
DRILL5_EVIDENCE_DECISION=REUSE
MONITORING_TARGET_FAILURE_RETEST=NOT_REQUIRED
```

不机械重复已经真实执行过的 scrape fault injection。

本轮只补充：

```text
Current Runtime Corroboration
```

以验证历史证据所对应的配置和监控链路当前仍然有效。

---

## 7.3 PrometheusRule 配置证据

当前 Kubernetes 中存在：

```text
PrometheusRule:
monitoring/opslab-api-alerts
```

核心规则：

```text
alert:
FastAPITargetDown

expression:
up{
  namespace="opslab",
  job="opslab-api",
  service="opslab-api"
} == 0

for:
2m

severity:
warning
```

该规则意味着：

```text
Prometheus 无法成功 scrape FastAPI Target
↓
up == 0
↓
持续达到 for: 2m
↓
FastAPITargetDown Firing
```

当前资源还带有：

```text
prometheus-operator-validated: "true"
```

验证：

```text
PROMETHEUS_RULE_PRESENT=PASS
FASTAPI_TARGET_DOWN_RULE_PRESENT=PASS
```

---

## 7.4 ServiceMonitor 配置证据

当前 Kubernetes 中存在：

```text
ServiceMonitor:
monitoring/opslab-api
```

配置：

```text
namespace:
opslab

path:
/metrics

port:
http

interval:
15s

scrapeTimeout:
5s
```

Selector：

```text
app.kubernetes.io/instance=opslab
app.kubernetes.io/name=opslab-api
```

因此监控发现链路为：

```text
ServiceMonitor
↓
opslab namespace
↓
opslab-api Service
↓
FastAPI Endpoints
↓
/metrics
↓
Prometheus
```

验证：

```text
FASTAPI_SERVICEMONITOR_PRESENT=PASS
SCRAPE_CONFIGURATION_PRESENT=PASS
```

---

## 7.5 历史健康基线

历史端到端告警验证开始前，两个 FastAPI Target 均为：

```text
up=1
up=1
```

同时：

```text
FastAPITargetDown
state=inactive
```

因此：

```text
HEALTHY_TARGET_BASELINE=PASS
INITIAL_ALERT_STATE_INACTIVE=PASS
```

---

## 7.6 历史 Scrape Fault Injection

历史实验使用最小化 scrape fault injection。

故障期间两个 FastAPI Target 实际变为：

```text
up=0
up=0
```

这里验证的是：

```text
Application /metrics
        ↓
Prometheus Scrape
        ↓
up Metric
```

这一监控链路确实能够反映 Target 不可抓取状态。

因此：

```text
SCRAPE_FAULT_INJECTION=PASS
PROMETHEUS_TARGET_DOWN_DETECTION=PASS
```

---

## 7.7 Inactive → Pending

当：

```text
up == 0
```

满足 FastAPITargetDown 表达式以后，PrometheusRule 实际进入：

```text
Inactive
↓
Pending
```

并观察到两个 FastAPI Target 对应的 pending alerts。

因此：

```text
ALERT_PENDING=PASS
```

该阶段证明告警表达式能够真实匹配故障指标，而不是只存在一份未生效的 YAML。

---

## 7.8 Pending → Firing

FastAPITargetDown 配置：

```text
for: 2m
```

故障持续达到要求后，历史实验实际观察：

```text
Pending
↓
Firing
```

两个 FastAPI Target 对应的 alert 均进入 firing。

因此：

```text
ALERT_FIRING=PASS
```

该过程同时验证了 Prometheus Alert Rule 的状态机：

```text
Inactive
→ Pending
→ Firing
```

---

## 7.9 Alertmanager Active

FastAPITargetDown 进入 Firing 后，Alertmanager API 中实际出现两个：

```text
alertname=FastAPITargetDown
```

Active Alert。

因此链路进一步成立：

```text
PrometheusRule
↓
Firing Alert
↓
Alertmanager
```

验证：

```text
ALERTMANAGER_ACTIVE=PASS
```

---

## 7.10 External Firing Notification

历史实验中：

```text
FastAPITargetDown
```

进入 Firing 后，QQ 邮箱真实收到告警邮件。

告警链路：

```text
Prometheus
↓
FastAPITargetDown Firing
↓
Alertmanager
↓
AlertmanagerConfig
↓
qq-email receiver
↓
SMTP
↓
QQ Email
```

实际完成外部通知。

验证：

```text
FIRING_EMAIL_DELIVERY=PASS
```

这证明告警能力没有停止在：

```text
Prometheus UI 有红色告警
```

而是已经完成真实外部通知。

---

## 7.11 Historical Recovery

恢复 scrape 链路以后，两个 FastAPI Target 实际恢复：

```text
up=1
up=1
```

随后：

```text
FastAPITargetDown
Firing
↓
Inactive
```

Prometheus 中：

```text
alerts=0
```

Alertmanager 中：

```text
FastAPITargetDown:
no active alerts
```

因此：

```text
TARGET_RECOVERY=PASS
RULE_RECOVERY_INACTIVE=PASS
ALERTMANAGER_ACTIVE_CLEAR=PASS
```

---

## 7.12 Resolved Notification

AlertmanagerConfig 配置：

```text
sendResolved=true
```

恢复后 QQ 邮箱真实收到 Resolved 邮件。

因此完整通知生命周期为：

```text
Fault
↓
Firing Email
↓
Recovery
↓
Resolved Email
```

验证：

```text
RESOLVED_EMAIL_DELIVERY=PASS
```

---

## 7.13 当前 Prometheus / Alertmanager Runtime

本次 Drill 没有重新注入 scrape fault。

使用临时 port-forward 对当前 Runtime 进行交叉验证。

结果：

```text
PROMETHEUS_READY=1
ALERTMANAGER_READY=1
```

说明当前：

```text
Prometheus API = Ready
Alertmanager API = Ready
```

验证：

```text
CURRENT_PROMETHEUS_READY=PASS
CURRENT_ALERTMANAGER_READY=PASS
```

---

## 7.14 当前 FastAPI Target 状态

当前 Prometheus 查询：

```text
up{namespace="opslab"}
```

返回 FastAPI 两个 Target：

```text
job=opslab-api
pod=opslab-api-797c8fdfbf-tmtx2
instance=10.244.1.57:8000
up=1

job=opslab-api
pod=opslab-api-797c8fdfbf-zwmzk
instance=10.244.2.69:8000
up=1
```

说明当前：

```text
worker1 FastAPI Target = UP
worker2 FastAPI Target = UP
```

同时 MySQL exporter 与 Redis exporter 也返回：

```text
mysql exporter up=1
redis exporter up=1
```

当前监控采集链路正常。

验证：

```text
CURRENT_FASTAPI_TARGETS_UP=PASS
```

---

## 7.15 当前 FastAPITargetDown Rule Runtime

Prometheus `/api/v1/rules` 当前返回：

```text
name:
FastAPITargetDown

state:
inactive

health:
ok

duration:
120

alerts_count:
0
```

说明当前状态满足：

```text
FastAPI Targets up=1
↓
FastAPITargetDown Expression=False
↓
Rule State=inactive
↓
No Active Rule Alerts
```

因此：

```text
CURRENT_ALERT_RULE_HEALTH=PASS
CURRENT_ALERT_RULE_INACTIVE=PASS
```

---

## 7.16 当前 Alertmanager 状态

Alertmanager `/api/v2/alerts` 当前返回：

```text
FASTAPI_TARGET_DOWN_ACTIVE_COUNT=0
```

即当前没有任何 Active：

```text
FastAPITargetDown
```

说明历史故障恢复以后，告警状态没有残留。

验证：

```text
CURRENT_ALERTMANAGER_CLEAN=PASS
```

---

## 7.17 为什么没有重新制造故障

Repository 已经保存了一次完整的真实告警故障实验：

```text
Healthy
↓
up=1
↓
Real Scrape Fault
↓
up=0
↓
Pending
↓
Firing
↓
Alertmanager Active
↓
Real External Firing Email
↓
Recovery
↓
up=1
↓
Inactive
↓
Alertmanager Cleared
↓
Real External Resolved Email
```

而本轮又证明：

```text
Prometheus Ready
+
Alertmanager Ready
+
ServiceMonitor Still Present
+
PrometheusRule Still Present
+
FastAPI Targets up=1
+
Rule health=ok
+
Rule state=inactive
+
Alertmanager active=0
```

因此再次进行同样的 scrape fault injection：

```text
不会明显增加新的有效证据
```

反而会制造不必要的：

```text
Monitoring Disruption
+
Alert Noise
+
External Email Noise
```

所以 Drill 5 采用：

```text
HISTORICAL_REAL_FAULT_EVIDENCE
+
CURRENT_RUNTIME_CORROBORATION
```

而不是：

```text
MECHANICAL_FAULT_RETEST
```

---

## 7.18 Drill 5 最终证据链

```text
Historical Healthy Target
        ↓
up=1
        ↓
Real Scrape Fault Injection
        ↓
up=0
        ↓
Inactive → Pending
        ↓
Pending → Firing
        ↓
Alertmanager Active
        ↓
Real QQ Firing Email
        ↓
Scrape Recovery
        ↓
up=1
        ↓
Rule Inactive
        ↓
Alertmanager Active Cleared
        ↓
Real QQ Resolved Email
        ↓
Current Prometheus Ready
        ↓
Current Alertmanager Ready
        ↓
Current FastAPI Targets up=1
        ↓
Current Rule health=ok
        ↓
Current Rule inactive
        ↓
Current Alertmanager Active=0
```

最终判定：

```text
DRILL5_EVIDENCE_DECISION=REUSE
MONITORING_TARGET_FAILURE_RETEST=NOT_REQUIRED

PROMETHEUS_RULE_PRESENT=PASS
FASTAPI_SERVICEMONITOR_PRESENT=PASS

HISTORICAL_HEALTHY_TARGET_BASELINE=PASS
SCRAPE_FAULT_INJECTION=PASS
PROMETHEUS_TARGET_DOWN_DETECTION=PASS
ALERT_PENDING=PASS
ALERT_FIRING=PASS
ALERTMANAGER_ACTIVE=PASS
FIRING_EMAIL_DELIVERY=PASS
TARGET_RECOVERY=PASS
RULE_RECOVERY_INACTIVE=PASS
ALERTMANAGER_ACTIVE_CLEAR=PASS
RESOLVED_EMAIL_DELIVERY=PASS

CURRENT_PROMETHEUS_READY=PASS
CURRENT_ALERTMANAGER_READY=PASS
CURRENT_FASTAPI_TARGETS_UP=PASS
CURRENT_ALERT_RULE_HEALTH=PASS
CURRENT_ALERT_RULE_INACTIVE=PASS
CURRENT_ALERTMANAGER_CLEAN=PASS

DRILL_5_MONITORING_TARGET_FAILURE=PASS
```

**Drill 5 状态：SEALED。**

# 8. Drill 6 — Ingress / Service / Pod 链路诊断

状态：Pending。通过最小故障验证 `Client -> Ingress -> Service -> EndpointSlice -> Pod` 的分层诊断方法。

# 9. Drill 7 — Worker Node / Local PV Boundary

状态：Pending，最后执行。验证 Worker 正常 reboot 后 Node 恢复与 Local PV 同节点数据持久性，并明确 Local PV 不等于跨节点 Storage HA。

---

# 10. 当前阶段结论

截至 Drill 5 完成，Systematic Fault Drills 当前状态为：

~~~text
DRILL_1_FASTAPI_POD_SELF_HEALING=PASS
DRILL_2_REDIS_DEPENDENCY_FAILURE=PASS
DRILL_3_MYSQL_POD_SELF_HEALING=PASS
DRILL_4_HPA_LOAD_RECOVERY=PASS
DRILL_5_MONITORING_TARGET_FAILURE=PASS

COMPLETED_DRILLS=5/7
SEALED_DRILLS=5/7

NEXT_DRILL=DRILL_6_INGRESS_SERVICE_POD_CHAIN_DIAGNOSIS
~~~

当前已经完成的 Drill 覆盖：

~~~text
Drill 1
FastAPI Pod Self-Healing

Drill 2
Redis Soft Dependency Failure
+
RCA
+
Immutable Remediation
+
Same-Scenario Regression

Drill 3
MySQL Pod Self-Healing
+
Application Authentication Dependency Failure
+
RCA
+
Immutable Remediation
+
Same-Scenario Regression

Drill 4
HPA Load / Recovery
+
Scale Out / Scale In
+
minReplicas / maxReplicas
+
Business Recovery

Drill 5
Prometheus Target Failure
+
Pending / Firing
+
Alertmanager
+
External Firing / Resolved Notification
+
Current Runtime Corroboration
~~~

Systematic Fault Drills 当前进度：

~~~text
5 / 7
~~~

剩余：

~~~text
Drill 6
Ingress / Service / Pod Chain Diagnosis

Drill 7
Worker Node / Local PV Boundary
~~~

Drill 7 仍作为风险最高的 Node-Level Validation 最后执行。

全部 Drill 完成以后进入：

~~~text
Final SRE Validation
↓
TRADITIONAL_SRE_BASELINE=PASS
↓
Git Baseline Freeze / Milestone
↓
course-design/hermes-closed-loop
↓
Hermes 智能运维阶段
~~~
