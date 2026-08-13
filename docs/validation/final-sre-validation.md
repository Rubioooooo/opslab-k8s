# Final SRE Validation — Traditional SRE Baseline 最终验收报告

## 1. 文档目的

本文档用于对 OpsLab Kubernetes/SRE 项目在完成基础部署、可观测性建设、备份恢复以及 7 个系统化故障演练之后的**当前集成系统状态**进行最终验收。

本次验证不是新的故障演练，也不是 Drill 8。

其目标是在所有历史部署、故障注入、根因分析、修复、版本发布、扩缩容和节点重启完成后，重新确认：

```text
Current Integrated System = PASS
```

并形成以下阶段结论：

```text
FINAL_SRE_VALIDATION=PASS
TRADITIONAL_SRE_BASELINE=PASS
```

本报告将作为后续：

```text
Traditional SRE Baseline Freeze
        ↓
Descheduler + TopologySpread
Automatic Rebalance Enhancement
        ↓
Hermes Closed Loop
```

之间的正式工程分界依据。

---

## 2. 验证原则

Final SRE Validation 采用以下策略：

```text
Historical Evidence Reuse
+
Current Runtime Read-Only Corroboration
+
少量无破坏性业务验证
```

已经完成并 SEALED 的故障演练不重新执行。

本次 Final Validation 未重新执行：

* Redis 故障注入；
* MySQL Pod 删除；
* HPA 压测；
* Service selector mismatch；
* Prometheus target failure；
* Worker reboot；
* MySQL destructive restore。

只有历史证据不足以说明当前状态时，才通过只读查询或无破坏性业务请求补充 Current Runtime Evidence。

整个 Final Validation 过程中：

```text
NEW_INCIDENT=NONE
RUNTIME_REMEDIATION_REQUIRED=NO
```

---

# 3. 验证基线

## 3.1 Git 基线

仓库：

```text
~/projects/opslab-k8s
```

分支：

```text
master
```

Final Validation 开始时最新提交：

```text
a6bd61c docs(validation): seal worker node local pv drill
```

精确 commit：

```text
a6bd61cfde06afd84d4576c09acae2d78f0382ae
```

远端：

```text
origin/master
```

最终审计确认：

```text
HEAD=a6bd61cfde06afd84d4576c09acae2d78f0382ae
ORIGIN_MASTER=a6bd61cfde06afd84d4576c09acae2d78f0382ae
```

且：

```text
## master...origin/master
```

无未提交修改、无 ahead、无 behind。

因此：

```text
GIT_HEAD_ORIGIN_CONSISTENCY=PASS
GIT_WORKTREE_CLEAN=PASS
```

---

## 3.2 Kubernetes 集群基线

三节点 Kubernetes 集群：

| 节点                | 角色            | IP           | Kubernetes | Runtime          |
| ----------------- | ------------- | ------------ | ---------- | ---------------- |
| k8s-control-plane | control-plane | 192.168.8.10 | v1.36.3    | containerd 2.2.1 |
| k8s-worker1       | worker        | 192.168.8.11 | v1.36.3    | containerd 2.2.1 |
| k8s-worker2       | worker        | 192.168.8.12 | v1.36.3    | containerd 2.2.1 |

操作系统：

```text
Ubuntu 24.04.4 LTS
Kernel 6.8.0-137-generic
```

CNI：

```text
Flannel v0.28.8
VXLAN
PodCIDR=10.244.0.0/16
```

Ingress：

```text
NGINX Ingress Controller
worker1 / worker2 均部署实例
Host=api.opslab.local
```

---

# 4. Round 1 — Read-Only Integrated Baseline Audit

## 4.1 Node 健康状态

三节点均为：

```text
Ready
```

Node Conditions：

| Node              | Ready | MemoryPressure | DiskPressure | PIDPressure | NetworkUnavailable |
| ----------------- | ----: | -------------: | -----------: | ----------: | -----------------: |
| k8s-control-plane |  True |          False |        False |       False |              False |
| k8s-worker1       |  True |          False |        False |       False |              False |
| k8s-worker2       |  True |          False |        False |       False |              False |

同时执行集群范围非健康 Pod 查询：

```text
No resources found
```

未发现新的：

* Pending；
* Failed；
* Unknown；
* CrashLoopBackOff。

判定：

```text
KUBERNETES_NODES=PASS
NODE_CONDITIONS=PASS
CLUSTER_POD_HEALTH=PASS
```

---

## 4.2 FastAPI 当前运行状态

Deployment：

```text
opslab-api
DESIRED=2
READY=2
AVAILABLE=2
```

实际 Pod：

```text
opslab-api-797c8fdfbf-km26v
IP=10.244.1.66
NODE=k8s-worker1

opslab-api-797c8fdfbf-zwmzk
IP=10.244.2.69
NODE=k8s-worker2
```

因此当前副本分布：

```text
worker1=1
worker2=1
```

EndpointSlice：

```text
10.244.1.66
10.244.2.69
```

应用镜像：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/opslab-api@sha256:4e268edf2609de2c5477323b104548c141999bfb2251507ed14e2f4c723135b8
```

该 digest 对应当前 OpsLab API v0.3.2 基线。

判定：

```text
FASTAPI_DEPLOYMENT=PASS
FASTAPI_MULTI_REPLICA=PASS
FASTAPI_CROSS_NODE_PLACEMENT=PASS
FASTAPI_ENDPOINTSLICE=PASS
FASTAPI_IMMUTABLE_IMAGE=PASS
```

---

## 4.3 MySQL / Redis 当前运行状态

MySQL：

```text
StatefulSet=opslab-mysql
READY=1/1
Pod=opslab-mysql-0
Node=k8s-worker1
```

Redis：

```text
StatefulSet=opslab-redis
READY=1/1
Pod=opslab-redis-0
Node=k8s-worker2
```

Exporter：

```text
opslab-mysqld-exporter 1/1
opslab-redis-exporter  1/1
```

EndpointSlice：

```text
MySQL:
10.244.1.60:3306

Redis:
10.244.2.66:6379

mysqld-exporter:
10.244.1.62:9104

redis-exporter:
10.244.1.63:9121
```

判定：

```text
MYSQL_RUNTIME=PASS
REDIS_RUNTIME=PASS
MYSQL_EXPORTER_RUNTIME=PASS
REDIS_EXPORTER_RUNTIME=PASS
```

---

## 4.4 Metrics Server / HPA 当前状态

Metrics API：

```text
v1beta1.metrics.k8s.io
AVAILABLE=True
```

`kubectl top nodes` 可以正常返回三节点 CPU / Memory 数据。

HPA：

```text
MIN=2
MAX=4
CPU_TARGET=60
CURRENT_REPLICAS=2
DESIRED_REPLICAS=2
```

审计时 CPU：

```text
cpu: 14% / 60%
```

当前已经恢复正常 steady state。

结合历史 Drill 4 和 Drill 7：

```text
2 → 4 → 2
```

已经真实验证动态扩缩容能力，因此 Final Validation 不重复 HPA 压测。

判定：

```text
METRICS_SERVER=PASS
METRICS_API=PASS
HPA_CONFIGURATION=PASS
HPA_NORMAL_STEADY_STATE=PASS
HPA=PASS
```

---

# 5. Round 2 — Integrated Business Path Corroboration

分别通过两个 Worker 上的 NGINX Ingress Controller 进行业务访问。

入口：

```text
192.168.8.11
192.168.8.12
```

请求统一使用：

```text
Host=api.opslab.local
```

---

## 5.1 worker1 Ingress

### /healthz

返回：

```json
{"status":"ok"}
```

HTTP：

```text
200
```

### /readyz

返回：

```json
{"status":"ready","mysql":"ok","redis":"ok"}
```

HTTP：

```text
200
```

### /api/v1/events/1

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

---

## 5.2 worker2 Ingress

同样得到：

```text
/healthz          → 200
/readyz           → 200
/api/v1/events/1  → 200
```

依赖状态：

```text
mysql=ok
redis=ok
```

业务历史记录：

```text
id=1
message=fastapi-mysql-write-read-ok
```

保持一致。

---

## 5.3 当前业务链路结论

当前已经重新证明：

```text
Client
   ↓
worker1 / worker2 NGINX Ingress
   ↓
opslab-api Service
   ↓
EndpointSlice
   ↓
FastAPI
   ↓
MySQL / Redis
```

同时通过读取历史 MySQL 数据证明此前持久业务记录当前仍可访问。

判定：

```text
FASTAPI_HEALTHZ=PASS
FASTAPI_READINESS=PASS

MYSQL_DEPENDENCY_CURRENT=PASS
REDIS_DEPENDENCY_CURRENT=PASS

MYSQL_PERSISTENT_BUSINESS_DATA_READ=PASS

INGRESS_WORKER1_PATH=PASS
INGRESS_WORKER2_PATH=PASS
INGRESS_SERVICE_POD_CHAIN_CURRENT=PASS
```

---

# 6. Round 3 — Local PV Boundary Audit

## 6.1 MySQL Local PV

PV：

```text
PV=opslab-mysql-local-pv
LOCAL_PATH=/data/mysql
STORAGE_CLASS=local-storage
RECLAIM_POLICY=Retain
CLAIM=opslab/opslab-mysql-data
NODE_AFFINITY=k8s-worker1
```

Pod：

```text
opslab-mysql-0
NODE=k8s-worker1
STATUS=Running
```

worker1 实际文件系统：

```text
/data/mysql
→ /dev/sdb1
→ xfs
→ 20G
```

形成：

```text
PVC
 ↓
opslab-mysql-local-pv
 ↓
nodeAffinity=k8s-worker1
 ↓
opslab-mysql-0
 ↓
/data/mysql
 ↓
/dev/sdb1
 ↓
XFS
```

判定：

```text
MYSQL_LOCAL_PV_DEFINITION=PASS
MYSQL_LOCAL_PV_NODE_AFFINITY=PASS
MYSQL_LOCAL_PV_PLACEMENT=PASS
MYSQL_BACKING_FILESYSTEM=PASS
MYSQL_LOCAL_PV=PASS
```

---

## 6.2 Redis Local PV

PV：

```text
PV=opslab-redis-local-pv
LOCAL_PATH=/data/redis
STORAGE_CLASS=local-storage
RECLAIM_POLICY=Retain
CLAIM=opslab/opslab-redis-data
NODE_AFFINITY=k8s-worker2
```

Pod：

```text
opslab-redis-0
NODE=k8s-worker2
STATUS=Running
```

worker2 实际文件系统：

```text
/data/redis
→ /dev/sdb1
→ xfs
→ 20G
```

形成：

```text
PVC
 ↓
opslab-redis-local-pv
 ↓
nodeAffinity=k8s-worker2
 ↓
opslab-redis-0
 ↓
/data/redis
 ↓
/dev/sdb1
 ↓
XFS
```

判定：

```text
REDIS_LOCAL_PV_DEFINITION=PASS
REDIS_LOCAL_PV_NODE_AFFINITY=PASS
REDIS_LOCAL_PV_PLACEMENT=PASS
REDIS_BACKING_FILESYSTEM=PASS
REDIS_LOCAL_PV=PASS
```

---

## 6.3 Local PV 能力边界

当前项目已经证明：

```text
LOCAL_PV_SAME_NODE_PERSISTENCE=PASS
```

尤其 Drill 7 已真实经历：

```text
worker1 reboot
→ NodeStatusUnknown
→ worker1 recovery
→ /dev/sdb1 remount
→ /data/mysql recovery
→ PVC/PV remain Bound
→ direct MySQL query PASS
```

但没有证明：

```text
Cross-Node Storage HA
Distributed Storage
MySQL Replication HA
Redis Replication HA
```

因此当前能力必须描述为：

> MySQL 与 Redis 使用具有节点亲和性的 Kubernetes Local PV，将数据持久化在指定 Worker 的独立本地磁盘中。当前已经验证同节点 Pod 重建和节点重启后的持久化恢复能力，但不具备跨节点数据层高可用，也不能将 Local PV 描述为分布式存储。

最终边界：

```text
LOCAL_PV_SAME_NODE_PERSISTENCE=PASS
CROSS_NODE_STORAGE_HA=NOT_IMPLEMENTED
DISTRIBUTED_STORAGE=NOT_IMPLEMENTED
```

---

# 7. Round 4 — Prometheus Targets / Rules Validation

## 7.1 ServiceMonitor 配置层

当前业务 ServiceMonitor：

```text
opslab-api
opslab-mysqld-exporter
opslab-redis-exporter
```

namespace 均正确指向：

```text
opslab
```

Service selectors 与当前 Service 标签一致。

业务 PrometheusRule：

```text
opslab-api-alerts
```

仍然存在。

配置层判定：

```text
FASTAPI_SERVICEMONITOR=PASS
MYSQL_SERVICEMONITOR=PASS
REDIS_SERVICEMONITOR=PASS
OPSLAB_PROMETHEUS_RULE_OBJECT=PASS
MONITORING_CONFIGURATION_LAYER=PASS
```

---

## 7.2 Prometheus 当前真实抓取状态

Prometheus Ready：

```text
Prometheus Server is Ready.
HTTP_CODE=200
```

查询：

```promql
up{namespace="opslab"}
```

得到：

```text
FastAPI worker2:
10.244.2.69:8000
up=1

FastAPI worker1:
10.244.1.66:8000
up=1

mysqld-exporter:
10.244.1.62:9104
up=1

redis-exporter:
10.244.1.63:9121
up=1
```

因此：

```text
PROMETHEUS_RUNTIME=PASS
FASTAPI_MONITORING=PASS
MYSQL_MONITORING=PASS
REDIS_MONITORING=PASS
```

---

## 7.3 FastAPITargetDown

规则：

```promql
up{job="opslab-api",namespace="opslab",service="opslab-api"} == 0
```

当前：

```text
RULE_NAME=FastAPITargetDown
STATE=inactive
HEALTH=ok
LAST_ERROR=
```

判定：

```text
FASTAPI_TARGET_DOWN_RULE_LOADED=PASS
FASTAPI_TARGET_DOWN_RULE_HEALTH=PASS
FASTAPI_TARGET_DOWN_CURRENT_STATE=INACTIVE
PROMETHEUS_RULES_CURRENT=PASS
```

历史 Drill 5 已经真实完成：

```text
up=1
→ fault
→ up=0
→ Pending
→ Firing
→ Alertmanager
→ QQ firing email
→ recovery
→ resolved email
```

因此 Final Validation 不重新注入 Monitoring Target Failure。

---

# 8. Round 5 — Grafana / Alertmanager / Prometheus TSDB

## 8.1 Grafana

Service：

```text
opslab-monitoring-grafana
ClusterIP
Port=80
```

健康接口：

```json
{
  "database": "ok",
  "version": "13.1.3",
  "commit": "45a27d64b64a82d666b06aa5c5bb3521587edb0d"
}
```

HTTP：

```text
200
```

判定：

```text
GRAFANA_SERVICE=PASS
GRAFANA_RUNTIME=PASS
GRAFANA_DATABASE_HEALTH=PASS
GRAFANA=PASS
```

---

## 8.2 Alertmanager

实际 StatefulSet：

```text
alertmanager-opslab-monitoring-kube-pro-alertmanager
READY=1/1
```

Ready API：

```text
OK
HTTP_CODE=200
```

结合历史已经真实收到：

```text
Firing Email
Resolved Email
```

判定：

```text
ALERTMANAGER_RUNTIME=PASS
ALERTMANAGER_READY=PASS
ALERTMANAGER=PASS
```

---

## 8.3 Prometheus TSDB Local PV

实际 StatefulSet：

```text
prometheus-opslab-monitoring-kube-pro-prometheus
READY=1/1
```

Pod：

```text
prometheus-opslab-monitoring-kube-pro-prometheus-0
NODE=k8s-worker2
STATUS=Running
```

PVC：

```text
prometheus-opslab-monitoring-kube-pro-prometheus-db-prometheus-opslab-monitoring-kube-pro-prometheus-0

STATUS=Bound
VOLUME=opslab-prometheus-local-pv
CAPACITY=28Gi
STORAGECLASS=local-storage
```

PV：

```text
PV=opslab-prometheus-local-pv
LOCAL_PATH=/data/prometheus
STORAGE_CLASS=local-storage
RECLAIM_POLICY=Retain
NODE_AFFINITY=k8s-worker2
```

形成：

```text
Prometheus
   ↓
PVC
   ↓
opslab-prometheus-local-pv
   ↓
nodeAffinity=k8s-worker2
   ↓
/data/prometheus
```

历史阶段已经完成真实 Prometheus TSDB persistence 验证。

Final Validation 仅确认当前存储绑定仍然正确，不重新删除 Prometheus Pod。

判定：

```text
PROMETHEUS_TSDB_PVC_BOUND=PASS
PROMETHEUS_TSDB_POD_PVC_ATTACHMENT=PASS
PROMETHEUS_TSDB_CURRENT_STORAGE_BINDING=PASS
PROMETHEUS_TSDB_PERSISTENCE_EVIDENCE=PASS

PROMETHEUS=PASS
```

---

## 8.4 Validation Command Assumption Observation

Round 5 初次查询 StatefulSet 时曾错误假设资源名为：

```text
opslab-monitoring-kube-pro-alertmanager
opslab-monitoring-kube-pro-prometheus
```

实际 StatefulSet 为：

```text
alertmanager-opslab-monitoring-kube-pro-alertmanager
prometheus-opslab-monitoring-kube-pro-prometheus
```

由于当时：

* Alertmanager Ready API 返回 200；
* Prometheus 已经 Ready；
* 对应 Pods 均 Running；
* PVC 正常 Bound；

因此该现象被分类为：

```text
TYPE=VALIDATION_COMMAND_ERROR
ROOT_CAUSE=RESOURCE_NAME_ASSUMPTION
RUNTIME_IMPACT=NONE
```

未对 Kubernetes Runtime 进行任何无意义 restart 或 remediation。

该过程再次验证了本项目坚持的故障处理原则：

```text
preserve evidence
→ classify
→ RCA
→ minimal remediation
```

---

# 9. Round 6 — MySQL Backup / Restore Evidence Audit

Final Validation 不重新执行 destructive restore。

当前仓库资产：

```text
scripts/mysql-backup.sh
scripts/mysql-restore.sh
docs/validation/mysql-backup-restore-validation.md
```

全部存在且被 Git 跟踪。

Bash syntax：

```text
MYSQL_BACKUP_SCRIPT_SYNTAX=PASS
MYSQL_RESTORE_SCRIPT_SYNTAX=PASS
```

对应 Git commit：

```text
c67d052 feat(mysql): add validated backup and restore workflow
```

历史验证证据包括：

* 真实备份；
* 备份文件非空验证；
* SHA256；
* 权限控制；
* 备份故障域分离；
* 整库隔离恢复；
* 单表恢复；
* Row Count 一致性；
* Data SHA256 一致性；
* Existing Database 覆盖保护；
* 临时恢复数据库清理；
* 测试对象清理；
* 既有业务验证数据保护。

历史机器可读结论：

```text
MYSQL_BACKUP_CREATE=PASS
MYSQL_FULL_BACKUP_RESTORE=PASS
MYSQL_RESTORE_DATA_INTEGRITY=PASS
MYSQL_TABLE_BACKUP_CREATE=PASS
MYSQL_TABLE_BACKUP_SCOPE=PASS
MYSQL_CONTROLLED_DATA_LOSS=PASS
MYSQL_TABLE_RESTORE=PASS
MYSQL_BUSINESS_DATA_GUARD=PASS

MYSQL_BACKUP_SCRIPT=PASS
MYSQL_RESTORE_SCRIPT=PASS
MYSQL_SCRIPT_RESTORE_INTEGRITY=PASS
RESTORE_EXISTING_DATABASE_GUARD=PASS

TEMP_RESTORE_DATABASE_CLEANUP=PASS
BACKUP_RESTORE_TEST_OBJECT_CLEANUP=PASS
MYSQL_EXISTING_VALIDATION_DATA_GUARD=PASS

MYSQL_BACKUP_RESTORE_VALIDATION=PASS
```

因此：

```text
MYSQL_BACKUP_RESTORE_EVIDENCE=PASS
MYSQL_BACKUP_RESTORE=PASS
```

当前能力不等于完整企业级灾备体系。

尚未实现：

```text
PITR
Binlog Replay Recovery
Object Storage Backup
Off-Site Multi-Copy Backup
Automated Scheduled Backup
Enterprise RPO/RTO Guarantee
```

---

# 10. Round 7 — Systematic Fault Drill Evidence Audit

## 10.1 全局状态

验证文档：

```text
docs/validation/systematic-fault-drills-validation.md
```

最终状态：

```text
COMPLETED_DRILLS=7/7
SEALED_DRILLS=7/7

SYSTEMATIC_FAULT_DRILLS_PROGRESS=7/7
SYSTEMATIC_FAULT_DRILLS_STATE=SEALED

NEXT_STAGE=FINAL_SRE_VALIDATION
```

---

## 10.2 Drill 1 — FastAPI Pod Self-Healing

```text
DRILL_1_FASTAPI_POD_SELF_HEALING=PASS
```

验证 Kubernetes Deployment 在单 Pod 故障后的副本恢复能力。

最终：

```text
PASS
SEALED
```

---

## 10.3 Drill 2 — Redis Dependency Failure

首次实验并非简单 PASS。

真实 RCA：

```text
ROOT_CAUSE=READINESS_TIMEOUT_BUDGET_COLLISION
```

随后完成应用修复：

```text
opslab-api v0.3.1
```

并执行 same-scenario regression。

最终：

```text
DRILL_2_REDIS_DEPENDENCY_FAILURE=PASS
```

证据链：

```text
FAIL
→ preserve evidence
→ READINESS_TIMEOUT_BUDGET_COLLISION
→ remediation
→ v0.3.1
→ same-scenario regression
→ PASS
→ SEALED
```

---

## 10.4 Drill 3 — MySQL Pod Self-Healing

首次恢复过程中出现应用认证失败。

真实 RCA：

```text
MYSQL_CACHING_SHA2_FULL_AUTH_DEPENDENCY_GAP
```

修复：

```text
cryptography=49.0.0
```

发布：

```text
opslab-api-v0.3.2
```

部署 manifest 固定 immutable image digest。

Regression 后：

```text
DRILL_3_MYSQL_POD_SELF_HEALING=PASS
```

证据链：

```text
MySQL restart
→ FastAPI reconnect failure
→ RCA
→ dependency remediation
→ v0.3.2
→ regression
→ PASS
→ SEALED
```

---

## 10.5 Drill 4 — HPA Load / Recovery

真实观察：

```text
2 replicas
→ CPU utilization above target
→ 4 replicas
→ load removed
→ metrics fall
→ 2 replicas
```

最终：

```text
DRILL_4_HPA_LOAD_RECOVERY=PASS
```

---

## 10.6 Drill 5 — Monitoring Target Failure

故障模型：

```text
Prometheus target failure
```

真实链路：

```text
up=1
→ up=0
→ Pending
→ Firing
→ Alertmanager
→ QQ firing email
→ recovery
→ resolved email
```

最终：

```text
DRILL_5_MONITORING_TARGET_FAILURE=PASS
```

---

## 10.7 Drill 6 — Ingress / Service / Pod Chain

故障模型：

```text
Service selector mismatch
```

真实因果链：

```text
FastAPI Pods remain Running / Ready
        ↓
Service selector mismatch
        ↓
EndpointSlice endpoints=0
        ↓
Ingress Controller has no upstream pod
        ↓
HTTP 502
        ↓
restore correct selector
        ↓
EndpointSlice automatically rebuilt
        ↓
HTTP 200
```

这一实验明确证明：

> Pod 健康并不等于业务访问链路健康。

最终：

```text
DRILL_6_INGRESS_SERVICE_POD_CHAIN=PASS
DRILL6=SEALED
```

---

## 10.8 Drill 7 — Worker Node / Local PV Boundary

故障模型：

```text
CONTROLLED_WORKER1_REBOOT
```

真实过程：

```text
worker1 reboot
→ Ready=True
→ Ready=Unknown / NodeStatusUnknown
→ MySQL unavailable
→ FastAPI readiness 503
→ Endpoint contraction
→ timeout / HTTP 502
→ worker1 returns
→ transient Flannel startup ordering issue
→ self recovery
→ MySQL container restart
→ /dev/sdb1 remount
→ /data/mysql available
→ PVC/PV remain Bound
→ direct MySQL data verification PASS
→ business recovery
```

恢复阶段 HPA 又出现：

```text
2 → 4 → 2
```

随后暴露：

```text
HPA scale-down
→ FastAPI placement drift
→ worker1=0
→ worker2=2
```

最终只进行了最小 remediation：

```text
delete one FastAPI Pod on worker2
        ↓
Scheduler creates replacement
        ↓
TopologySpreadConstraints acts on new scheduling
        ↓
worker1=1
worker2=1
```

最终：

```text
DRILL7_LOCAL_PV_SAME_NODE_PERSISTENCE=PASS
DRILL7_DIRECT_MYSQL_DATA_VERIFICATION=PASS
DRILL7_PLACEMENT_DRIFT_REMEDIATION=PASS
DRILL7_FINAL_VERDICT=PASS
DRILL7_STATE=SEALED
```

该实验同时证明一个重要调度边界：

> TopologySpreadConstraints 主要参与新 Pod 的调度决策，并不是一个持续扫描和重新平衡 existing Pods 的控制器。

因此形成后续增强项：

```text
Descheduler
+
TopologySpreadConstraints
+
Automatic Placement Rebalance
```

但该增强项不属于当前 Traditional SRE Baseline。

---

## 10.9 Drill Git Traceability

关键提交：

```text
4fa1853 docs(sre): document first systematic fault drills
04efd55 docs(validation): seal hpa load recovery drill
0eb6b04 docs(validation): seal monitoring target failure drill
e4ac891 docs(validation): seal ingress service pod chain drill
a6bd61c docs(validation): seal worker node local pv drill
```

MySQL Self-Healing Incident：

```text
dc5464a docs(sre): document mysql self-healing incident
```

因此：

```text
FAULT_DRILLS_COMPLETENESS=PASS
FAULT_DRILLS_SEAL_STATE=PASS
FAULT_DRILL_GIT_TRACEABILITY=PASS

SYSTEMATIC_FAULT_DRILLS=7/7_PASS
SYSTEMATIC_FAULT_DRILLS_STATE=SEALED
```

---

# 11. Round 8 — Repository / Runtime Consistency Audit

## 11.1 Git Exact State

```text
HEAD=a6bd61cfde06afd84d4576c09acae2d78f0382ae
ORIGIN_MASTER=a6bd61cfde06afd84d4576c09acae2d78f0382ae
```

Git：

```text
## master...origin/master
```

判定：

```text
GIT_HEAD_ORIGIN_CONSISTENCY=PASS
GIT_WORKTREE_CLEAN=PASS
```

---

## 11.2 FastAPI Repository / Runtime Image

Repository：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/opslab-api@sha256:4e268edf2609de2c5477323b104548c141999bfb2251507ed14e2f4c723135b8
```

Runtime：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/opslab-api@sha256:4e268edf2609de2c5477323b104548c141999bfb2251507ed14e2f4c723135b8
```

完全一致。

判定：

```text
FASTAPI_REPOSITORY_RUNTIME_IMAGE_CONSISTENCY=PASS
```

---

## 11.3 Runtime Replica Baseline

Deployment：

```text
DESIRED=2
READY=2
AVAILABLE=2
```

HPA：

```text
MIN=2
MAX=4
CPU_TARGET=60
CURRENT_REPLICAS=2
DESIRED_REPLICAS=2
```

判定：

```text
FASTAPI_RUNTIME_BASELINE=PASS
HPA_RUNTIME_BASELINE=PASS
```

---

## 11.4 Fault Injection Residue Audit

当前 Service selector：

```text
app.kubernetes.io/instance=opslab
app.kubernetes.io/name=opslab-api
```

当前 ServiceMonitor：

```text
PATH=/metrics
```

因此 Drill 5 与 Drill 6 故障注入已经完全恢复。

仓库扫描：

```text
fault-injection
fault_injection
selector-mismatch
broken-selector
```

无生产基线残留。

`git diff --check` 无输出。

判定：

```text
DRILL5_MONITORING_FAULT_RESIDUE=NONE
DRILL6_SELECTOR_FAULT_RESIDUE=NONE
EXPERIMENTAL_PATCH_RESIDUE=NONE
MANIFEST_INTEGRITY=PASS
```

最终：

```text
GIT_REPOSITORY_STATE=PASS
REPOSITORY_RUNTIME_CONSISTENCY=PASS
```

---

# 12. Final Validation 汇总结论

8 个 Final Validation 审计阶段：

| Round   | 验证范围                                     | 结果   |
| ------- | ---------------------------------------- | ---- |
| Round 1 | Kubernetes / Runtime Baseline            | PASS |
| Round 2 | Integrated Business Path                 | PASS |
| Round 3 | MySQL / Redis Local PV Boundary          | PASS |
| Round 4 | Prometheus Targets / Rules               | PASS |
| Round 5 | Grafana / Alertmanager / Prometheus TSDB | PASS |
| Round 6 | MySQL Backup / Restore Evidence          | PASS |
| Round 7 | Systematic Fault Drill Evidence          | PASS |
| Round 8 | Repository / Runtime Consistency         | PASS |

Final Validation 期间：

```text
NEW_INCIDENT=NONE
RUNTIME_REMEDIATION_REQUIRED=NO
```

唯一出现的异常输出为 StatefulSet 查询时的资源名称假设错误，已经通过实际资源发现完成分类，不属于 Runtime Incident。

---

# 13. 当前系统实际具备的能力

## 13.1 Kubernetes / Application HA

已经实现：

* 三节点 kubeadm Kubernetes；
* 两 Worker 承载业务；
* FastAPI 多副本；
* FastAPI 跨 Worker 分布；
* Deployment Self-Healing；
* Readiness / Liveness；
* Service / EndpointSlice；
* 双 Worker NGINX Ingress；
* HPA CPU 自动扩缩容；
* immutable image digest；
* TopologySpreadConstraints；
* Metrics Server；
* kubelet serving certificate 修复。

因此：

```text
FASTAPI_MULTI_REPLICA_HA=PASS
```

---

## 13.2 Data Persistence

已经实现：

* MySQL StatefulSet；
* Redis StatefulSet；
* MySQL Local PV；
* Redis Local PV；
* Redis AOF；
* Retain reclaim policy；
* Local PV nodeAffinity；
* 独立 XFS 数据盘；
* MySQL 同节点 Worker reboot 后持久数据恢复；
* MySQL backup / restore workflow。

但是：

```text
MYSQL_CROSS_NODE_HA=NOT_IMPLEMENTED
REDIS_CROSS_NODE_HA=NOT_IMPLEMENTED
DISTRIBUTED_STORAGE=NOT_IMPLEMENTED
```

---

## 13.3 Observability

已经实现：

* kube-prometheus-stack；
* Prometheus；
* Grafana；
* Alertmanager；
* kube-state-metrics；
* node-exporter；
* FastAPI metrics；
* FastAPI Grafana Dashboard；
* FastAPI ServiceMonitor；
* MySQL exporter；
* Redis exporter；
* Prometheus TSDB Local PV；
* PrometheusRule；
* FastAPITargetDown；
* QQ Email firing / resolved alert validation。

因此：

```text
OBSERVABILITY_BASELINE=PASS
```

---

## 13.4 SRE Fault Engineering

已经完成并 SEALED：

```text
Drill 1 FastAPI Pod Self-Healing
Drill 2 Redis Dependency Failure
Drill 3 MySQL Pod Self-Healing
Drill 4 HPA Load / Recovery
Drill 5 Monitoring Target Failure
Drill 6 Ingress / Service / Pod Chain
Drill 7 Worker Node / Local PV Boundary
```

其中 Drill 2、Drill 3 真实经历：

```text
FAIL
→ preserve evidence
→ RCA
→ minimal remediation
→ versioned release
→ same-scenario regression
→ PASS
```

因此当前项目不仅验证了 Kubernetes 正常功能，还积累了真实 Incident / RCA / Recovery Evidence。

---

# 14. 当前系统未实现的能力边界

Traditional SRE Baseline 不应被描述为企业级全栈高可用平台。

当前明确未实现：

* MySQL 主从 / Group Replication / InnoDB Cluster；
* Redis Sentinel / Redis Cluster；
* Ceph / Rook / Longhorn 等分布式存储；
* 跨节点数据库存储 HA；
* 多 Control Plane Kubernetes HA；
* 多站点 / 跨机房容灾；
* MySQL PITR；
* Binlog 自动回放；
* 自动定时异地数据库备份；
* 对象存储备份；
* 企业级 RPO / RTO SLA；
* Descheduler 自动 existing-Pod rebalance；
* Hermes Agent 智能运维闭环。

这些内容属于后续增强，而不应混入当前 Traditional SRE Baseline 的已完成能力描述。

---

# 15. Final Machine-Readable Verdict

```text
KUBERNETES_CLUSTER=PASS

FASTAPI_DEPLOYMENT=PASS
FASTAPI_MULTI_REPLICA=PASS
FASTAPI_CROSS_NODE_PLACEMENT=PASS

MYSQL_RUNTIME=PASS
MYSQL_LOCAL_PV=PASS

REDIS_RUNTIME=PASS
REDIS_LOCAL_PV=PASS

MYSQL_BACKUP_RESTORE=PASS

METRICS_SERVER=PASS

PROMETHEUS=PASS
GRAFANA=PASS
ALERTMANAGER=PASS

FASTAPI_MONITORING=PASS
MYSQL_MONITORING=PASS
REDIS_MONITORING=PASS

PROMETHEUS_TSDB_PERSISTENCE=PASS

HPA=PASS

SYSTEMATIC_FAULT_DRILLS=7/7_PASS

GIT_REPOSITORY_STATE=PASS
REPOSITORY_RUNTIME_CONSISTENCY=PASS

EXPERIMENTAL_PATCH_RESIDUE=NONE

CROSS_NODE_STORAGE_HA=NOT_IMPLEMENTED
DISTRIBUTED_STORAGE=NOT_IMPLEMENTED

FINAL_SRE_VALIDATION=PASS

TRADITIONAL_SRE_BASELINE=PASS
```

---

# 16. Final Verdict

基于：

1. 8 轮 Current Runtime Final Validation；
2. 已完成的 Kubernetes 集群部署证据；
3. FastAPI / MySQL / Redis 当前集成业务验证；
4. MySQL / Redis Local PV 当前绑定证据；
5. MySQL Worker reboot 后 same-node persistence 历史证据；
6. MySQL Backup / Restore 真实恢复证据；
7. Prometheus / Grafana / Alertmanager 当前健康证据；
8. FastAPI / MySQL / Redis Prometheus targets 当前 `up=1`；
9. HPA 历史 `2 → 4 → 2` 与当前 steady state；
10. Systematic Fault Drills `7/7 SEALED`；
11. Git / Repository / Runtime 一致性；
12. 无实验 fault patch 残留；

最终结论：

```text
FINAL_SRE_VALIDATION=PASS
TRADITIONAL_SRE_BASELINE=PASS
```

当前 Traditional SRE Baseline 已具备正式冻结条件。

---

# 17. 下一阶段

当前阶段完成后，不立即进入 Hermes。

下一步应首先进行：

```text
Git Baseline Freeze / Milestone
```

随后进入一个独立的小型增强阶段：

```text
Descheduler
+
TopologySpreadConstraints
+
Automatic Placement Rebalance
```

该增强来源于 Drill 7 暴露的真实问题：

```text
HPA scale-down
→ placement drift
→ TopologySpreadConstraints 不主动重平衡 existing Pods
```

增强阶段完成并单独验收后，再创建 Hermes 阶段分界：

```text
course-design/hermes-closed-loop
```

进入：

```text
Hermes Agent
+
Cloud Native Observability
+
Controlled Remediation
+
Experience Evolution
+
Closed-Loop Intelligent Operations
```

Traditional SRE Baseline 到此结束。
