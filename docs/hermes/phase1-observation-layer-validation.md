# Hermes Phase 1：Observation Layer 实现与验证报告

## 1. 阶段目标

Phase 1 的目标是在 Windows 宿主机 WSL 中建立独立于 Kubernetes 集群的 External AIOps Control Plane，并通过专用、最小权限的 Kubernetes ServiceAccount 对 `opslab` namespace 进行只读观测。

本阶段不引入 Hermes 推理、不执行任何自动修复，也不向 Observer 提供 Kubernetes 写权限。

阶段数据流：

~~~text
WSL External AIOps Control Plane
        |
        | HTTPS + Kubernetes CA
        | short-lived ServiceAccount Token
        v
Kubernetes API Server
        |
        | RBAC
        v
namespace: opslab
        |
        +-- Deployment
        +-- ReplicaSet
        +-- Pod
        +-- EndpointSlice
        +-- PodDisruptionBudget
        +-- HorizontalPodAutoscaler
        +-- Event
        |
        v
ObservationSnapshot v1alpha1
~~~

---

## 2. 物理部署边界

Hermes/AIOps 不部署在 Kubernetes Pod 或 Kubernetes 节点内部。

实际部署边界：

~~~text
Windows Host
|
+-- WSL Ubuntu
|   |
|   +-- External AIOps Control Plane
|       +-- Python 3.14.4
|       +-- Kubernetes Python Client 36.0.3
|       +-- Observation Layer
|       +-- Hermes runtime
|
+-- VMware
    |
    +-- k8s-control-plane 192.168.8.10
    +-- k8s-worker1       192.168.8.11
    +-- k8s-worker2       192.168.8.12
~~~

WSL Ubuntu 已迁移至：

~~~text
D:\WSL\Ubuntu\ext4.vhdx
~~~

因此 AIOps 源码、Python venv 和后续 SQLite 数据仍可使用标准 Linux 路径，同时物理存储位于 D 盘。

---

## 3. Observer 身份与 RBAC

ServiceAccount：

~~~text
namespace: opslab
name: opslab-aiops-observer
~~~

RBAC 仅允许以下 namespaced `get/list` 操作：

- deployments.apps
- replicasets.apps
- pods
- endpointslices.discovery.k8s.io
- poddisruptionbudgets.policy
- horizontalpodautoscalers.autoscaling
- events

明确不授予：

- Secret
- ConfigMap
- Pod logs
- exec
- attach
- port-forward
- eviction
- Pod create/delete
- Deployment patch
- Node
- Namespace
- monitoring namespace
- kube-system namespace
- cluster-wide write

ServiceAccount 未配置永久 Token Secret。

运行时 Token 使用：

~~~text
kubectl create token opslab-aiops-observer \
  -n opslab \
  --duration=15m
~~~

Token 只进入当前 WSL shell 环境变量，不写入：

- Git 仓库
- kubeconfig
- runtime JSON
- SQLite
- 配置文件

---

## 4. Python Observation Layer

源码目录：

~~~text
aiops/
├── requirements.txt
├── requirements.lock.txt
└── src/
    └── opslab_aiops/
        └── observation/
            ├── __init__.py
            ├── __main__.py
            ├── client.py
            ├── collector.py
            └── models.py
~~~

运行环境：

~~~text
Python: 3.14.4
Kubernetes Python Client: 36.0.3
venv: aiops/.venv
~~~

`aiops/.venv` 被 `.gitignore` 排除，不进入版本库。

---

## 5. Observation 数据契约

Phase 1 定义：

~~~text
ObservationSnapshot
~~~

schema：

~~~text
v1alpha1
~~~

包含：

- DeploymentObservation
- ReplicaSetObservation
- PodObservation
- ContainerObservation
- EndpointSliceObservation
- EndpointObservation
- PDBObservation
- HPAObservation
- EventObservation
- ConditionObservation

Observation 层只负责记录事实，不生成：

- root cause
- diagnosis
- remediation
- shell command
- kubectl command
- eviction decision

其职责边界为：

~~~text
Kubernetes Raw Objects
        |
        v
Observation Collector
        |
        v
ObservationSnapshot
        |
        v
Phase 2 Incident Context Builder
~~~

---

## 6. Kubernetes API Client 认证设计

Python Client 不读取：

~~~text
~/.kube/config
/etc/kubernetes/admin.conf
~~~

也不调用：

~~~text
kubectl
shell
ssh
~~~

采集过程中直接构建 Kubernetes API Client：

~~~text
API Server:
https://192.168.8.10:6443

CA:
~/.config/opslab-aiops/ca.crt

Authentication:
short-lived ServiceAccount Bearer Token
~~~

CA 文件权限：

~~~text
mode=600
owner=rubio
group=rubio
~~~

---

## 7. Python Client 认证故障 RCA

### 7.1 现象

首次执行真实 Collector 时：

~~~text
Kubernetes API error:
status=403
reason=Forbidden
~~~

进一步逐项验证所有资源均返回：

~~~text
User "system:anonymous"
~~~

例如：

~~~text
pods is forbidden:
User "system:anonymous" cannot list resource "pods"
~~~

### 7.2 排除 RBAC 问题

使用完全相同的 ServiceAccount Token 直接执行 HTTPS 请求：

~~~text
GET /api/v1/namespaces/opslab/pods
HTTP 200
~~~

同时：

~~~text
kubectl auth can-i
~~~

对以下权限全部返回 `yes`：

- get deployment.apps/opslab-api
- list replicasets.apps
- list pods
- list endpointslices.discovery.k8s.io
- list poddisruptionbudgets.policy
- list horizontalpodautoscalers.autoscaling
- list events

因此可以排除：

~~~text
ServiceAccount Token 无效
RBAC 权限不足
namespace RoleBinding 缺失
~~~

### 7.3 根因

Python Client 初始配置错误使用：

~~~text
configuration.api_key["authorization"]
~~~

Kubernetes Python Client 的认证方案名称实际应为：

~~~text
BearerToken
~~~

因此 Bearer Token 没有被客户端认证模块注入 HTTP 请求。

API Server 接收到没有身份凭据的请求后，将其识别为：

~~~text
system:anonymous
~~~

最终返回 403。

### 7.4 最小修复

仅修改 Python API Client：

~~~text
configuration.api_key["BearerToken"] = token
configuration.api_key_prefix["BearerToken"] = "Bearer"
~~~

没有修改：

- Role
- RoleBinding
- ServiceAccount
- Kubernetes 集群权限
- API Server 配置

### 7.5 修复后验证

Python API authorization matrix：

~~~text
deployment=PASS
replicasets=PASS
pods=PASS
endpointslices=PASS
pdbs=PASS
hpas=PASS
events=PASS

PYTHON_OBSERVER_AUTH_MATRIX=PASS
~~~

该故障说明：

> HTTP 403 不必然代表 Authorization 权限不足。需要首先区分 Authentication 与 Authorization。

本次故障属于：

~~~text
Authentication credentials missing
        |
        v
system:anonymous
        |
        v
403 Forbidden
~~~

而不是 Observer RBAC 权限不足。

---

## 8. 真实 Observation Snapshot

真实采集时间：

~~~text
2026-08-14T16:48:41.310829Z
~~~

Snapshot：

~~~text
schema_version=v1alpha1
snapshot_id=obs-20260814T164841Z-c8824885

deployment=2/2
replica_sets=6
pods=2
endpoint_slices=1
pdbs=1
hpas=1
events=1
~~~

Collector：

~~~text
collector_rc=0
REAL_OBSERVATION_COLLECTION=PASS
SNAPSHOT_SCHEMA_VALIDATION=PASS
~~~

### FastAPI Pod 状态

~~~text
opslab-api-797c8fdfbf-bftj5
ready=True
phase=Running
node=k8s-worker1
ip=10.244.1.85

opslab-api-797c8fdfbf-r697x
ready=True
phase=Running
node=k8s-worker1
ip=10.244.1.77
~~~

两个 Pod 当前均健康。

两个 Pod 同时位于 worker1 属于已经观察到的 topology drift，不等同于 `FASTAPI_UNHEALTHY_INSTANCE`。

Observation Layer 仅忠实记录该事实，不进行自动重平衡。

### EndpointSlice

两个 FastAPI Pod 均存在 Ready Endpoint：

~~~text
ready=true
serving=true
terminating=false
~~~

### PDB

~~~text
current_healthy=2
desired_healthy=1
disruptions_allowed=1
expected_pods=2
~~~

这为后续 Safety Gate 判断 `EVICT_POD` 是否具备 disruption budget 提供事实依据。

### HPA

~~~text
min_replicas=2
max_replicas=4
current_replicas=2
desired_replicas=2
~~~

### Event

Snapshot 捕获到一条近期 Warning：

~~~text
reason=Unhealthy
reporting_controller=kubelet

Readiness probe failed:
context deadline exceeded
~~~

该事件是历史观测证据。

当前两个 Pod 均处于 Ready 状态，因此 Observation Layer 不应将历史 Warning 直接解释成当前故障。

---

## 9. ReplicaSet 数据边界

Snapshot 当前收集到：

~~~text
replica_sets=6
~~~

原因是 Deployment 历史滚动更新产生的 ReplicaSet 均由：

~~~text
Deployment/opslab-api
~~~

控制。

Phase 1 Observation Layer 保留这些原始事实。

是否只选取当前 active ReplicaSet 属于 Phase 2 Incident Context Builder 的上下文裁剪职责，不在 Observation 层进行诊断性过滤。

---

## 10. 最小权限安全验证

### Secret

使用和真实 Collector 完全相同的 Python API Client 主动尝试：

~~~text
list_namespaced_secret(namespace="opslab")
~~~

API Server 返回：

~~~text
HTTP 403 Forbidden
~~~

验收：

~~~text
SECRET_ACCESS_DENIED=PASS
~~~

说明安全边界由 Kubernetes RBAC 强制执行，而非依赖应用代码自律。

### Cross Namespace

尝试读取：

~~~text
namespace=monitoring
resource=pods
~~~

结果：

~~~text
HTTP 403 Forbidden
CROSS_NAMESPACE_ACCESS_DENIED=PASS
~~~

证明 Observer 权限被限制在 `opslab` namespace。

---

## 11. Runtime 数据保护

真实 Snapshot 保存于：

~~~text
aiops/runtime/opslab-api-observation.json
~~~

`.gitignore`：

~~~text
/aiops/runtime/
~~~

验证：

~~~text
git check-ignore
PASS
~~~

因此运行时 Snapshot 不进入 Git。

Python venv：

~~~text
/aiops/.venv/
~~~

同样被 Git 忽略。

---

## 12. Token 生命周期

运行开始：

~~~text
SHORT_LIVED_TOKEN=PASS
token_length=965
~~~

验证完成后：

~~~text
unset OPSLAB_K8S_TOKEN
SHORT_LIVED_TOKEN_CLEANUP=PASS
~~~

仓库不保存该 Token。

---

## 13. Phase 1 验收矩阵

| 验收项 | 结果 |
|---|---|
| WSL External AIOps Control Plane | PASS |
| WSL 位于 D 盘 | PASS |
| Python 3.14.4 venv | PASS |
| Kubernetes Client 36.0.3 | PASS |
| Dedicated Observer ServiceAccount | PASS |
| Namespace-scoped RBAC | PASS |
| CA TLS verification | PASS |
| Short-lived Token | PASS |
| Persistent Token avoided | PASS |
| Deployment observation | PASS |
| ReplicaSet observation | PASS |
| Pod observation | PASS |
| EndpointSlice observation | PASS |
| PDB observation | PASS |
| HPA observation | PASS |
| Event observation | PASS |
| ObservationSnapshot v1alpha1 | PASS |
| Real cluster snapshot | PASS |
| Secret denial | PASS |
| Cross-namespace denial | PASS |
| Runtime snapshot ignored by Git | PASS |
| Token cleanup | PASS |
| Kubernetes write capability | NOT GRANTED |
| Hermes diagnosis | NOT IN PHASE 1 |
| Automatic remediation | NOT IN PHASE 1 |

---

## 14. Phase 1 边界

Phase 1 已实现：

~~~text
Observable
~~~

尚未实现：

~~~text
Diagnosable
Explainable
Safety-bounded remediation
Executable remediation
Post-action verification
Experience accumulation
~~~

这些能力属于后续阶段。

---

## 15. 最终结论

Phase 1 已建立真实、最小权限、外部运行的 Kubernetes Observation Layer。

其核心能力为：

~~~text
WSL External AIOps Control Plane
        |
        v
short-lived ServiceAccount identity
        |
        v
Kubernetes API Server
        |
        v
least-privilege namespaced observation
        |
        v
ObservationSnapshot v1alpha1
~~~

并通过真实集群证明：

1. Observer 可以读取诊断所需 Kubernetes 状态；
2. Observer 无法读取 Secret；
3. Observer 无法跨 namespace 读取 monitoring Pod；
4. Observer 不持有永久 Token；
5. Runtime Snapshot 不进入 Git；
6. Observation 与 Diagnosis 职责保持分离。

最终判定：

~~~text
HERMES_PHASE1_OBSERVER_RBAC=PASS
HERMES_PHASE1_EXTERNAL_AUTHENTICATION=PASS
HERMES_PHASE1_REAL_OBSERVATION=PASS
HERMES_PHASE1_LEAST_PRIVILEGE=PASS
HERMES_PHASE1_OBSERVATION_LAYER=PASS
~~~
