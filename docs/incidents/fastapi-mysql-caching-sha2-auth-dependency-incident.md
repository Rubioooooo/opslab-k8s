# FastAPI 在 MySQL Pod 重建后无法恢复 — caching_sha2_password 完整认证依赖缺失 Incident

## 1. Incident Summary

在 Systematic Fault Drill 3 — MySQL Pod Self-Healing 中，仅删除 `opslab-mysql-0`。

MySQL StatefulSet 成功自动重新创建 Pod，原 PVC / PV 与业务数据均保持。

但是 MySQL 已经重新进入 `Ready=True`、EndpointSlice 已恢复后，两个 FastAPI Pod 仍持续：

```text
Ready=False
```

应用：

```text
/healthz = HTTP 200
/readyz  = HTTP 503
```

Ingress：

```text
HTTP 502 Bad Gateway
```

问题最终定位为：

```text
MySQL 8.4.10
+
caching_sha2_password
+
FastAPI image 缺少 cryptography
```

导致 MySQL Pod 重建后，FastAPI 无法完成新的 MySQL authentication handshake。

最终通过 OpsLab API `v0.3.2` 补齐客户端 cryptographic capability，并执行相同 MySQL Pod 删除场景回归，验证应用能够自动恢复。

---

## 2. Detection

来源：

```text
Systematic Fault Drill 3
MySQL Pod Self-Healing
```

Fault Injection：

```bash
kubectl delete pod \
  -n opslab \
  opslab-mysql-0 \
  --wait=false
```

Blast radius 仅限单个 MySQL Pod。

未删除：

* StatefulSet；
* PVC；
* PV；
* `/data/mysql`；
* StorageClass。

---

## 3. Expected Behavior

设计中 MySQL 属于 FastAPI 的 HARD dependency。

因此 MySQL 真正不可用期间允许：

```text
/healthz = HTTP 200

/readyz = HTTP 503
status=not_ready
mysql=error
```

MySQL 恢复后预期：

```text
MySQL Ready
        ↓
FastAPI check_mysql() 成功
        ↓
/readyz = HTTP 200
        ↓
FastAPI Ready
        ↓
业务恢复
```

FastAPI 不应依赖人工 restart 才能恢复数据库连接。

---

## 4. Actual Behavior

第一次 Fault Injection 后，StatefulSet 自动重新创建：

```text
opslab-mysql-0
```

新 MySQL Pod：

```text
UID=88f03466-5987-4002-9566-6c0996600b0e
IP=10.244.1.54
START_TIME=2026-08-13T09:00:32Z
READY=True
READY_TRANSITION=2026-08-13T09:00:38Z
```

MySQL Endpoint：

```text
10.244.1.54
ready=true
serving=true
```

但是两个 FastAPI Pod：

```text
0/1 Running
Ready=False
```

FastAPI EndpointSlice：

```text
ready=false
serving=false
```

应用：

```text
/healthz
HTTP 200

/readyz
HTTP 503
{"status":"not_ready","mysql":"error","redis":"ok"}
```

Ingress：

```text
HTTP 502 Bad Gateway
```

在 MySQL Ready 后约 4 分 45 秒保存现场时，FastAPI 仍未恢复。

因此不能解释成普通 readiness 收敛延迟。

---

## 5. Fault Propagation

实际故障传播：

```text
Delete MySQL Pod
        ↓
MySQL temporarily unavailable
        ↓
FastAPI /readyz = 503
        ↓
MySQL StatefulSet recreates Pod
        ↓
MySQL Ready=True
        ↓
BUT FastAPI MySQL authentication still fails
        ↓
FastAPI remains Ready=False
        ↓
FastAPI EndpointSlice ready=false
        ↓
NGINX has no Ready upstream
        ↓
502 Bad Gateway
```

NGINX 的 502 是下游结果，不是根因。

---

## 6. Investigation

### 6.1 MySQL / Storage 已恢复

确认：

```text
MYSQL_STATEFULSET=1/1
MYSQL_POD=1/1 Running
MYSQL_ENDPOINT_READY=true
PVC=Bound
PV=Bound
```

因此没有继续破坏或重建 Storage。

### 6.2 FastAPI Process 仍存活

FastAPI：

```text
/healthz = HTTP 200
```

说明：

* Uvicorn 仍运行；
* FastAPI 进程没有退出；
* Pod 没有发生 crash loop。

### 6.3 DNS

FastAPI Pod 内：

```text
MYSQL_HOST=opslab-mysql
RESOLVED_ADDRESSES=['10.103.121.36']
DNS_RESOLUTION=PASS
```

排除 Kubernetes Service DNS。

### 6.4 TCP

FastAPI Pod → `opslab-mysql:3306`：

```text
TCP_CONNECT=PASS
```

排除基本网络连通问题。

### 6.5 Fresh aiomysql connection

两个 FastAPI Pod 中建立全新 aiomysql connection：

```text
FRESH_AIOMYSQL_CONNECTION=FAIL
```

错误：

```text
RuntimeError(
  "'cryptography' package is required for "
  "sha256_password or caching_sha2_password auth methods"
)
```

故障由此定位到：

```text
MySQL authentication stage
```

而不是 DNS/TCP。

---

## 7. Root Cause Confirmation

FastAPI container：

```text
CRYPTOGRAPHY_IMPORT=FAIL
ModuleNotFoundError:
No module named 'cryptography'
```

MySQL：

```text
VERSION=8.4.10
```

应用账号：

```text
opslab_app@%
plugin=caching_sha2_password
```

Repository：

```text
requirements.txt:
aiomysql==0.3.2
```

没有声明：

```text
cryptography
```

最终 Root Cause：

```text
MYSQL_CACHING_SHA2_FULL_AUTH_DEPENDENCY_GAP
```

即：

FastAPI 镜像缺少 MySQL `caching_sha2_password` 完整认证路径所需的 cryptographic/RSA client capability。

MySQL Pod 重建使该认证路径重新被触发，因此一个在正常稳定运行期间未暴露的依赖缺陷被系统化故障演练发现。

---

## 8. 排除的错误方向

本 Incident 不是：

```text
Kubernetes bug
StatefulSet failure
PVC failure
PV failure
Local PV data loss
Service DNS failure
ClusterIP failure
TCP network failure
Ingress failure
Redis failure
stale connection pool
```

特别是 stale connection pool 被源码直接排除。

`database.py` 和 `dependency_checks.py` 均使用：

```python
aiomysql.connect(...)
```

每次重新建立 MySQL connection，并未维护长期 aiomysql connection pool。

---

## 9. Remediation Design

采用最小修复原则。

不修改：

* MySQL authentication plugin；
* `database.py`；
* `dependency_checks.py`；
* `main.py`；
* readiness timeout；
* business dependency timeout；
* Kubernetes probe；
* StatefulSet；
* PVC/PV。

只补齐客户端缺失的认证能力。

`requirements.txt`：

```text
cryptography==49.0.0
```

`requirements.lock.txt`：

```text
cffi==2.1.1
cryptography==49.0.0
pycparser==3.0
```

没有通过切换到旧认证插件来规避问题。

---

## 10. Dependency Validation

使用隔离 Python virtualenv 验证新依赖。

结果：

```text
pip check:
No broken requirements found.

aiomysql=0.3.2
PyMySQL=1.2.0
cryptography=49.0.0
cffi=2.1.1
pycparser=3.0

MYSQL_CLIENT_RUNTIME_IMPORTS=PASS
PYTHON_COMPILEALL=PASS
DIRECT_TO_LOCK_CONSISTENCY=PASS
LOCK_SEMANTIC_MATCH=PASS
SENSITIVE_LITERAL_SCAN=PASS
```

运行时进一步确认：

```text
PyMySQL_METADATA=1.2.0
pip freeze:
PyMySQL==1.2.0
```

因此没有 runtime dependency drift。

---

## 11. v0.3.2 Release

修复 commit：

```text
6a5d81e
fix(api): support mysql caching sha2 authentication
```

Tag：

```text
opslab-api-v0.3.2
```

ACR immutable RepoDigest：

```text
sha256:4e268edf2609de2c5477323b104548c141999bfb2251507ed14e2f4c723135b8
```

完整镜像：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/opslab-api@sha256:4e268edf2609de2c5477323b104548c141999bfb2251507ed14e2f4c723135b8
```

Deployment pin：

```text
09d0ee3
chore(deploy): pin opslab api v0.3.2 image
```

---

## 12. Remediation Deployment Validation

v0.3.2 Pod：

```text
APP_VERSION=v0.3.2
cryptography=49.0.0
aiomysql=0.3.2
PyMySQL_METADATA=1.2.0
```

每个 FastAPI Pod：

```text
FRESH_MYSQL_CONNECTION=PASS
SELECT_1=(1,)
```

应用：

```text
/healthz = HTTP 200
/readyz  = HTTP 200

{"status":"ready","mysql":"ok","redis":"ok"}
```

业务：

```text
GET /api/v1/events/1
HTTP 200
```

原故障现场因此恢复。

---

## 13. Same-Scenario Regression Test

为了避免将“新 FastAPI Pod 启动”误判为修复成功，再次执行与第一次完全一致的故障：

```bash
kubectl delete pod \
  -n opslab \
  opslab-mysql-0 \
  --wait=false
```

回归前：

```text
UID=88f03466-5987-4002-9566-6c0996600b0e
IP=10.244.1.54
```

回归后：

```text
UID=b4b31e01-b32c-4f6c-a5c8-1f9e88acca54
IP=10.244.1.59
START_TIME=2026-08-13T09:47:26Z
READY_TRANSITION=2026-08-13T09:47:32Z
```

MySQL StatefulSet：

```text
1/1 Ready
```

原 PVC / PV：

```text
opslab-mysql-data
opslab-mysql-local-pv
```

均继续保持 `Bound`。

---

## 14. Regression Application Timeline

HTTP sampling：

```text
09:47:23.103
HTTP 200

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

因此 v0.3.2 实现：

```text
MySQL failure
        ↓
FastAPI readiness failure
        ↓
MySQL recovery
        ↓
FastAPI authentication succeeds
        ↓
FastAPI automatically ready
```

没有执行：

```text
kubectl rollout restart
kubectl delete fastapi pod
```

FastAPI 最终：

```text
RESTARTS=0
```

---

## 15. Readiness Probe Observation

回归期间应用 `/readyz` 短暂 503。

但 Kubernetes FastAPI Pod 没有观察到：

```text
Ready=False
```

EndpointSlice 也没有观察到：

```text
ready=false
```

最终 Pod Ready transition time 仍然保持原值。

因此真实现象是：

```text
APPLICATION_READINESS_TEMPORARY_503=OBSERVED
KUBERNETES_POD_READY_STATE_TRANSITION=NOT_OBSERVED
ENDPOINT_REMOVAL=NOT_OBSERVED
```

说明应用在 kubelet readiness 连续失败达到摘除阈值以前已经完成自动恢复。

不能在 Incident 中写成：

```text
FastAPI Pod Ready=False → Ready=True
```

---

## 16. Data Integrity

回归完成后：

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

MySQL 直接查询：

```text
1 fastapi-mysql-write-read-ok 2026-08-08 15:49:47.595942
```

因此：

```text
MYSQL_PVC_PRESERVED=PASS
MYSQL_PV_PRESERVED=PASS
MYSQL_DATA_PRESERVED=PASS
```

恢复过程中没有使用 Backup / Restore。

---

## 17. Recovery Observations

### Application readiness disruption

```text
first observed 503:
09:47:23.656

first observed recovered 200:
09:47:33.777
```

因此：

```text
OBSERVED_APPLICATION_READINESS_DISRUPTION≈10.121s
```

### MySQL Pod start-to-ready

```text
START_TIME=09:47:26Z
READY_TRANSITION=09:47:32Z
```

因此：

```text
OBSERVED_MYSQL_POD_START_TO_READY≈6s
```

### Observed MySQL Ready → FastAPI readiness

首次采样观察到 MySQL Ready：

```text
09:47:33.281
```

首次采样观察到应用恢复：

```text
09:47:33.777
```

因此：

```text
OBSERVED_MYSQL_READY_TO_APP_RECOVERY≈0.496s
```

以上均属于 Observation，而不是严格业务 RTO。

---

## 18. Why Normal Validation Missed This

普通功能验证主要发生在已经稳定运行的 MySQL 实例上。

在稳定状态下：

```text
FastAPI
→ MySQL
→ CRUD PASS
```

因此客户端认证依赖缺失没有明显表现。

只有在：

```text
MySQL Server restart
```

之后重新经历完整认证路径时，该缺陷才稳定暴露。

这说明：

> “系统当前可用”不能替代“系统经历依赖重启后仍能恢复”的验证。

这也是 Systematic Fault Drill 相对于普通功能测试的重要价值。

---

## 19. Lessons Learned

### 19.1 健康状态必须分层观察

本 Incident 同时出现：

```text
MySQL Pod Ready=True
FastAPI process alive
FastAPI readiness failed
Ingress 502
```

单看任何一层都可能误判。

正确诊断路径应沿：

```text
Client
→ Ingress
→ Service
→ EndpointSlice
→ FastAPI
→ DNS
→ TCP
→ MySQL authentication
```

逐层缩小故障域。

### 19.2 TCP 通不代表数据库连接成功

本次：

```text
DNS=PASS
TCP 3306=PASS
```

但：

```text
MySQL authentication=FAIL
```

因此 TCP 连通只能证明网络 transport path，不代表应用能够真正使用数据库。

### 19.3 不应该用重启应用掩盖恢复缺陷

如果第一次发现 FastAPI 长期 503 后直接：

```text
kubectl rollout restart deployment/opslab-api
```

虽然可能恢复业务，但会丢失真正的 Root Cause。

正确流程是：

```text
FAIL
→ preserve evidence
→ RCA
→ minimal remediation
→ versioned release
→ same-scenario regression
→ PASS
```

### 19.4 Fault Drill 可以发现被稳定状态掩盖的缺陷

此次问题并非正常业务测试无法连接 MySQL。

真正的触发条件是：

```text
dependency restart
→ authentication path changes
→ hidden runtime dependency gap exposed
```

这是普通 CRUD 验证难以覆盖的恢复性缺陷。

---

## 20. System Boundary

本 Incident 修复能够证明：

* FastAPI v0.3.2 支持当前 MySQL `caching_sha2_password` 认证；
* MySQL Pod 重建后应用可以自行重新认证；
* 不需要人工重启 FastAPI；
* StatefulSet + Local PV 能够承受 Pod 级重建；
* 原业务数据保持；
* 修复经过完全相同故障场景回归。

不能证明：

* MySQL 节点级 HA；
* MySQL 跨节点存储 HA；
* MySQL 集群级高可用；
* Local PV 跨节点迁移；
* 零中断；
* 严格 RTO；
* worker1 永久损坏后的自动恢复。

---

## 21. Final Result

```text
INCIDENT_DETECTED=PASS

INITIAL_DRILL_RESULT=FAIL

ROOT_CAUSE=
MYSQL_CACHING_SHA2_FULL_AUTH_DEPENDENCY_GAP

REMEDIATION=
ADD_CRYPTOGRAPHY_CLIENT_CAPABILITY

V0_3_2_RELEASE=PASS
V0_3_2_ROLLOUT=PASS

SAME_SCENARIO_REGRESSION=PASS

MYSQL_POD_SELF_HEALING=PASS
MYSQL_STORAGE_PERSISTENCE=PASS
FASTAPI_AUTO_RECOVERY=PASS
MYSQL_FULL_AUTH_RECOVERY=PASS
BUSINESS_DATA_RECOVERY=PASS

INCIDENT_STATUS=RESOLVED
```

