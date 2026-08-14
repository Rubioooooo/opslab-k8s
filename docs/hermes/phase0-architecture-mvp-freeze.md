# Hermes 智能运维闭环 Phase 0：架构与 MVP 冻结

## 1. 文档目的

本文档用于冻结 OpsLab 项目从传统 Kubernetes / SRE 阶段进入 Hermes 智能运维阶段后的核心设计边界，包括：

- 系统总体架构
- 物理部署边界
- OpsLab 与 Hermes 的职责划分
- 系统模块边界
- 数据流
- Observation Layer 输入
- Incident Context 数据结构
- Agent 输入 / 输出 Schema
- Safety Gate
- Controlled Executor
- Verification
- Experience Store
- 第一个 MVP Incident
- Non-Goals
- 仓库目录规划
- Git 分支与版本边界
- Phase 0 完成标准
- Phase 1 实操入口

本阶段只进行：

> 架构冻结、MVP 范围冻结和安全边界冻结。

本阶段明确不进行：

- Hermes Agent 新部署
- LLM 调用
- Kubernetes 写操作
- 新故障演练
- 自动修复
- Executor RBAC 写权限配置
- MySQL / Redis 自动运维
- 任意 AI Shell / kubectl 执行

---

## 2. 传统 SRE 稳定基线

Hermes 阶段建立在已经完成并真实验证的传统 OpsLab Kubernetes / SRE 系统之上。

当前传统 SRE 最终增强边界：

~~~text
commit:
22fd65d
feat(descheduler): add guarded automatic rebalance

tag:
traditional-sre-enhanced-v1.1.0
~~~

Hermes 开发分支：

~~~text
hermes-mvp
~~~

分支关系：

~~~text
master
  |
  +-- 22fd65d
      |
      +-- traditional-sre-enhanced-v1.1.0
      |
      +-- hermes-mvp
~~~

因此：

> `traditional-sre-enhanced-v1.1.0` 是 Hermes 阶段开始前的最终传统 SRE 稳定边界。

从该边界之后产生的 Hermes / AIOps 代码、Schema、测试和文档才属于课程设计智能运维阶段。

传统 SRE 基线已经完成，不在 Hermes 阶段重新实现或者重复演练。

---

## 3. 当前传统 OpsLab 能力

Hermes 智能运维层建立在一个已经具备真实 SRE 能力的 Kubernetes 环境上。

当前 Kubernetes 集群：

~~~text
Kubernetes:
v1.36.3

containerd:
2.2.1

CNI:
Flannel v0.28.8
VXLAN

PodCIDR:
10.244.0.0/16
~~~

三节点：

~~~text
control-plane:
192.168.8.10

worker1:
192.168.8.11

worker2:
192.168.8.12
~~~

当前业务系统已经具备：

- FastAPI OpsLab API
- 多副本 Deployment
- readiness / liveness 体系
- MySQL dependency check
- Redis dependency check
- MySQL CRUD
- Redis Cache
- Prometheus Metrics
- HPA
- PDB
- TopologySpreadConstraints
- Descheduler
- NGINX Ingress
- Prometheus
- Grafana
- Alertmanager
- MySQL exporter
- Redis exporter
- Prometheus 持久化
- MySQL Backup / Restore
- 系统化故障演练
- 自动重平衡增强

传统 SRE 基线已经完成：

~~~text
Drill:
COMPLETED=7/7
SEALED=7/7

Final SRE Validation:
PASS

Automatic Rebalance Enhancement:
PASS
~~~

---

## 4. 数据层能力边界

当前 MySQL：

~~~text
StatefulSet
worker1
Local PV
/data/mysql
~~~

当前 Redis：

~~~text
StatefulSet
worker2
Local PV
/data/redis
~~~

必须明确：

> 当前 Local PV 不是分布式存储，也不是跨节点数据库高可用。

因此课程设计中不得把 MySQL / Redis 描述成：

~~~text
Cross-node HA
Distributed Storage
Database Cluster HA
~~~

Hermes MVP 也不会让 AI 自动操作这些 Stateful Workload。

---

## 5. 物理部署拓扑

Hermes 阶段存在两个明确分离的运行环境。

### 5.1 External AIOps Control Plane

运行位置：

~~~text
Windows Host
└── WSL Ubuntu
~~~

当前 WSL：

~~~text
hostname:
Rubio

IPv4:
172.28.11.184/20

default gateway:
172.28.0.1
~~~

当前 Hermes Agent 已安装在该 WSL Ubuntu 环境中。

后续计划在 WSL 中运行：

~~~text
Hermes Agent

OpsLab AIOps Controller
├── Observation Layer
├── Incident Context Builder
├── Hermes Adapter
├── Safety Gate
├── Controlled Executor
├── Verification Engine
└── Experience Store
~~~

这一层正式定义为：

> External AIOps Control Plane

中文：

> 外部智能运维控制层。

这里的 AIOps Control Plane 与 Kubernetes 自身的 control-plane 是两个完全不同的概念。

---

### 5.2 Kubernetes Managed Plane

Kubernetes 集群运行于 Windows 宿主机中的 VMware 虚拟机环境。

~~~text
Windows Host
└── VMware
    ├── control-plane
    │   └── 192.168.8.10
    │
    ├── worker1
    │   └── 192.168.8.11
    │
    └── worker2
        └── 192.168.8.12
~~~

这一层正式定义为：

> Kubernetes Managed Plane

中文：

> Kubernetes 被管理平面 / 被运维目标系统。

它是 Hermes 智能运维系统进行：

- Observation
- Diagnosis
- Safety Evaluation
- Controlled Remediation
- Verification

的真实目标环境。

---

## 6. WSL 到 Kubernetes 网络基线

Phase 0 已经真实验证：

~~~text
WSL
172.28.11.184
        |
        |
        v
VMware Network
        |
        |
        v
control-plane
192.168.8.10:6443
        |
        |
        v
kube-apiserver
~~~

Ping 验证：

~~~text
3 packets transmitted
3 received
0% packet loss
~~~

WSL 执行：

~~~text
curl -k \
  --connect-timeout 5 \
  https://192.168.8.10:6443/version
~~~

成功获得：

~~~text
gitVersion:
v1.36.3

platform:
linux/amd64
~~~

因此已经验证：

~~~text
WSL_NETWORK_TO_VMWARE=PASS

WSL_TO_KUBERNETES_APISERVER=PASS
~~~

但该结果目前只证明：

~~~text
Network Reachability
API Server Reachability
~~~

尚未证明：

~~~text
Kubernetes Authentication

Kubernetes Authorization

RBAC
~~~

认证、授权和最小权限访问将在 Phase 1 独立设计和验证。

---

## 7. 系统总体架构

完整架构冻结如下：

~~~text
┌────────────────────────────────────────────────────────────┐
│              External AIOps Control Plane                  │
│                     WSL Ubuntu                             │
│                                                            │
│                Observation Layer                           │
│                        │                                   │
│                        ▼                                   │
│             Incident Context Builder                       │
│                        │                                   │
│                        ▼                                   │
│                 Hermes Adapter                             │
│                        │                                   │
│                        ▼                                   │
│                  Hermes Agent                              │
│                        │                                   │
│                        ▼                                   │
│             Structured Diagnosis                           │
│                        │                                   │
│                        ▼                                   │
│                   Safety Gate                              │
│                        │                                   │
│                        ▼                                   │
│             Human Approval / Policy                        │
│                        │                                   │
│                        ▼                                   │
│              Controlled Executor                           │
│                        │                                   │
│                        ▼                                   │
│             Verification Engine                            │
│                        │                                   │
│                        ▼                                   │
│               Experience Store                             │
│                                                            │
└────────────────────────┬───────────────────────────────────┘
                         │
                         │ Network / APIs
                         │
                         ▼
┌────────────────────────────────────────────────────────────┐
│               Kubernetes Managed Plane                     │
│                       VMware                               │
│                                                            │
│ Kubernetes API                                             │
│ FastAPI                                                    │
│ MySQL                                                      │
│ Redis                                                      │
│ Prometheus                                                 │
│ Alertmanager                                               │
│ Grafana                                                     │
│ HPA                                                        │
│ PDB                                                        │
│ TSC                                                        │
│ Descheduler                                                │
│                                                            │
└────────────────────────────────────────────────────────────┘
~~~

---

## 8. OpsLab 的准确角色

传统 OpsLab 从 Hermes Phase 开始不再无限增加基础设施功能。

OpsLab 的角色正式冻结为：

> Hermes 智能运维闭环的真实 Kubernetes / SRE 实验底座。

它负责提供：

~~~text
Managed Application

Real Kubernetes State

Metrics

Alerts

Events

Health Signals

Failure Conditions

Recovery Results

High Availability Constraints
~~~

Hermes AIOps 层不是重新建设 OpsLab。

而是在：

~~~text
Existing SRE System
        |
        v
Observation
        |
        v
AI Diagnosis
        |
        v
Controlled Remediation
        |
        v
Verification
~~~

的基础上构建智能闭环。

---

## 9. Hermes 的准确角色

Hermes 在本系统中定义为：

> Diagnosis and Reasoning Engine

即：

> 智能诊断与推理引擎。

主要职责：

~~~text
Incident Context
        |
        v
Evidence Understanding
        |
        v
Incident Classification
        |
        v
Root Cause Hypothesis
        |
        v
Confidence Assessment
        |
        v
Risk Assessment
        |
        v
Recommended Action
        |
        v
Verification Recommendation
~~~

Hermes 是整个系统中的：

~~~text
Brain
~~~

但不是：

~~~text
Executor
~~~

Hermes 可以表达：

~~~text
发生了什么

为什么发生

哪些证据支持这个判断

根因是什么

置信度是多少

风险是多少

建议采取什么动作

应该如何验证
~~~

但 Hermes 本身不具有最终执行授权。

---

## 10. Hermes 明确不负责什么

Hermes 不直接拥有：

~~~text
cluster-admin

Kubernetes admin.conf

sudo

SSH Worker Access

containerd socket

arbitrary shell execution

arbitrary kubectl execution

Secret read access

MySQL administrative access

Redis administrative access
~~~

Hermes 也不得输出任意 Shell 命令后由系统无条件执行。

正式架构采用：

~~~text
LLM Intent
    |
    v
Structured Diagnosis
    |
    v
Deterministic Safety Logic
    |
    v
Controlled Executor
    |
    v
Kubernetes API
~~~

而不是：

~~~text
LLM
 |
 v
Shell
 |
 v
kubectl
 |
 v
Kubernetes
~~~

核心原则：

> Hermes capability 不等于 Kubernetes authority。

---

## 11. 核心模块边界

课程设计 MVP 固定为七个核心模块：

~~~text
1. Observation Layer

2. Incident Context Builder

3. Hermes Agent Adapter

4. Safety Gate

5. Controlled Executor

6. Verification Engine

7. Experience Store
~~~

其中真正涉及 AI 推理的是：

~~~text
Hermes Agent Adapter
        |
        v
Hermes Agent
~~~

其余：

~~~text
Observation

Safety

Execution

Verification

Audit
~~~

都由我们自己实现的确定性程序控制。

---

## 12. 系统完整数据流

完整闭环：

~~~text
Prometheus
Kubernetes
Alertmanager
     |
     v
Observation Layer
     |
     v
Incident Context Builder
     |
     v
Structured IncidentContext
     |
     v
Hermes Agent
     |
     v
DiagnosisResult
     |
     v
Safety Gate
     |
     +----------------------+
     |                      |
     v                      v
    DENY                   ALLOW
     |                      |
     v                      v
Audit Record          Human Approval
                            |
                            v
                    Controlled Executor
                            |
                            v
                     Kubernetes API
                            |
                            v
                       Verification
                            |
                  +---------+---------+
                  |                   |
                  v                   v
                 PASS                FAIL
                  |                   |
                  +---------+---------+
                            |
                            v
                     Experience Store
~~~

Level 3 与 Level 2 的主要差异只在于：

~~~text
Human Approval
~~~

对于严格白名单、低风险场景可以由 Auto Policy 替代。

Safety Gate 不允许省略。

Verification 不允许省略。

---

## 13. Observation Layer

Observation Layer 第一阶段完全只读。

它负责把原始系统状态转换成：

> 可审计、有限、结构化的 Observation。

### 13.1 Kubernetes 输入

第一版计划读取：

~~~text
Deployment

ReplicaSet

Pod

Pod Status

Pod Conditions

Events

Service

EndpointSlice

PDB

HPA

Node Status
~~~

第一阶段主要限定：

~~~text
namespace:
opslab

workload:
opslab-api
~~~

第一阶段禁止读取：

~~~text
Secret values

ServiceAccount Token

Kubernetes private credentials

Unrelated namespaces application data
~~~

---

### 13.2 Prometheus 输入

Prometheus 数据必须按 Incident 按需查询。

第一版可能使用：

~~~text
up

FastAPI request rate

FastAPI 5xx rate

FastAPI latency

FastAPI CPU

FastAPI memory

Pod restart state

Pod readiness

Deployment replica state
~~~

设计原则：

> Context Builder 不把整个 Prometheus TSDB 或大量无关指标直接发送给 Hermes。

---

### 13.3 HTTP Probe

保留：

~~~text
/healthz

/readyz
~~~

必要时增加：

~~~text
Ingress application path
~~~

用于独立验证应用可访问性。

---

### 13.4 Alertmanager 输入

第一版允许读取：

~~~text
alert name

status

labels

annotations

startsAt

endsAt
~~~

Alertmanager 只作为 Incident Trigger Source 和 Observation Source。

不得成为直接执行 Kubernetes Action 的入口。

---

## 14. Incident Context Builder

Context Builder 位于：

~~~text
Raw Observation
       |
       v
Hermes
~~~

之间。

它负责：

- 聚合 Kubernetes 状态
- 聚合 Prometheus 指标
- 聚合健康检查
- 聚合告警信息
- 去除无关数据
- 避免泄露凭据
- 建立 Evidence
- 输出结构化 Incident Snapshot

核心原则：

> Hermes 看到的是 Incident Snapshot，而不是 Kubernetes Shell。

---

## 15. Incident Context Schema

第一版概念 Schema：

~~~text
{
  "schema_version": "1.0",

  "incident": {
    "incident_id": "inc-xxxx",
    "observed_at": "ISO8601",
    "trigger_source": "kubernetes|prometheus|alertmanager|manual",
    "trigger_name": "FastAPIInstanceUnhealthy"
  },

  "target": {
    "cluster": "opslab",
    "namespace": "opslab",
    "kind": "Deployment",
    "name": "opslab-api"
  },

  "kubernetes": {
    "deployment": {},
    "replicaset": {},
    "pods": [],
    "events": [],
    "pdb": {},
    "hpa": {},
    "endpointslices": []
  },

  "prometheus": {
    "observations": []
  },

  "probes": {
    "healthz": {},
    "readyz": {}
  },

  "evidence": [],

  "collection": {
    "started_at": "",
    "completed_at": "",
    "partial": false,
    "errors": []
  }
}
~~~

该 Schema 后续将在：

~~~text
applications/aiops-controller/schemas/
~~~

中形成正式 JSON Schema。

Phase 0 目前冻结概念模型，不提前创建实现。

---

## 16. Evidence Model

Incident Context 中每一条重要证据必须具有来源。

Kubernetes 示例：

~~~text
{
  "evidence_id": "E01",
  "source": "kubernetes",
  "resource": "Pod/opslab-api-xxxxx",
  "observation": "Ready=False",
  "timestamp": "..."
}
~~~

Prometheus 示例：

~~~text
{
  "evidence_id": "E02",
  "source": "prometheus",
  "metric": "up",
  "observation": 0,
  "timestamp": "..."
}
~~~

Agent 诊断应通过：

~~~text
evidence_refs
~~~

引用支持其判断的证据。

目标是实现：

~~~text
Explainability

Auditability

Traceability

Reproducibility

Verification
~~~

禁止只让 Agent 给出：

~~~text
Pod 应该坏了
~~~

这样的无证据结论。

---

## 17. Hermes Agent 输入

Hermes 正式输入必须是结构化数据。

概念模型：

~~~text
{
  "task": "diagnose_incident",

  "context": {},

  "allowed_actions": [
    "NO_ACTION",
    "EVICT_POD"
  ]
}
~~~

需要注意：

`allowed_actions` 表示：

> 当前系统实现支持哪些动作类型。

它并不表示：

> Safety Gate 已经允许执行该动作。

Hermes 只能进行动作建议。

---

## 18. Hermes Agent 输出 Schema

Hermes 第一版正式输出定义为：

> DiagnosisResult

概念结构：

~~~text
{
  "schema_version": "1.0",

  "incident_type": "FASTAPI_UNHEALTHY_INSTANCE",

  "summary": "...",

  "evidence_refs": [
    "E01",
    "E02"
  ],

  "root_cause": {
    "hypothesis": "...",
    "confidence": 0.91
  },

  "risk": {
    "level": "LOW",
    "reasons": []
  },

  "recommended_action": {
    "type": "EVICT_POD",
    "target": {
      "namespace": "opslab",
      "pod": "opslab-api-xxxxx"
    },
    "reason": "..."
  },

  "preconditions": [],

  "verification_plan": [],

  "missing_evidence": [],

  "abstain": false
}
~~~

Agent 输出中明确不存在：

~~~text
command

shell

kubectl
~~~

字段。

最终执行链：

~~~text
Hermes Intent
      |
      v
DiagnosisResult
      |
      v
Safety Gate
      |
      v
ActionRequest
      |
      v
Controlled Executor
~~~

---

## 19. Safety Gate

Safety Gate 是独立于 Hermes 的确定性安全组件。

它不是 Prompt。

不是让 LLM 自己判断：

> 我是不是安全。

而是由 Python 程序基于真实 Kubernetes 状态做硬性判断。

第一版针对：

~~~text
EVICT_POD
~~~

至少检查以下条件。

### Target Boundary

~~~text
namespace == opslab

workload == opslab-api
~~~

---

### Ownership

必须确认：

~~~text
Pod
 |
 v
ReplicaSet
 |
 v
Deployment
 |
 v
opslab-api
~~~

目标不能是其他 Deployment。

---

### Availability

必须满足：

~~~text
Deployment.spec.replicas >= 2
~~~

并至少存在：

~~~text
one other healthy FastAPI Pod
~~~

---

### PDB

必须满足：

~~~text
PDB exists

disruptionsAllowed >= 1
~~~

---

### Target State

目标 Pod 必须确实存在异常证据。

例如：

~~~text
Ready=False
~~~

不能因为 AI 随机选择一个健康 Pod 就允许驱逐。

---

### Stateful Protection

必须确保目标不是：

~~~text
MySQL

Redis

StatefulSet workload

Local PV protected workload
~~~

---

### Action Allowlist

必须满足：

~~~text
action == EVICT_POD
~~~

第一版不支持其他写动作。

---

### Concurrency Protection

必须满足：

~~~text
no other remediation in progress
~~~

并限制：

~~~text
max_actions_per_incident = 1
~~~

后续加入 remediation cooldown。

---

### Safety Decision

任意条件失败：

~~~text
SafetyDecision = DENY
~~~

Safety Gate 的结果不可由 Hermes 覆盖。

---

## 20. Human-in-the-loop

Level 2 必须实现人工批准。

完整路径：

~~~text
Hermes Diagnosis
       |
       v
Safety Gate
       |
       v
ALLOW
       |
       v
Action Proposal
       |
       v
Human Approval
       |
       v
Controlled Executor
       |
       v
Verification
       |
       v
Experience Store
~~~

人工批准的对象必须是：

~~~text
Action ID
~~~

例如：

~~~text
action-20260814-001
~~~

而不是让用户复制执行：

~~~text
kubectl ...
~~~

这样的任意 Shell 命令。

---

## 21. Controlled Executor

MVP 第一版真正实现的 Kubernetes 写动作只有：

~~~text
EVICT_POD
~~~

Executor 接收：

~~~text
Validated ActionRequest
~~~

然后执行：

~~~text
Kubernetes policy/v1 Eviction API
~~~

完整流程：

~~~text
Validated ActionRequest
        |
        v
Controlled Executor
        |
        v
policy/v1 Eviction API
        |
        v
kube-apiserver
        |
        v
PDB Evaluation
        |
        v
Pod Eviction
~~~

Executor 明确禁止：

~~~text
arbitrary kubectl

arbitrary shell

os.system()

shell=True

exec into Pod

direct Pod delete

StatefulSet delete

Node reboot

OS remediation

arbitrary Deployment patch
~~~

Executor 必须使用：

> 独立、最小权限 Kubernetes Credential。

---

## 22. AI 与 Kubernetes Credential 隔离

Hermes 本身不持有 Kubernetes 写 Credential。

后续建议拆分：

~~~text
Observer Credential
        |
        +-- read-only Kubernetes APIs

Executor Credential
        |
        +-- tightly scoped pods/eviction
~~~

Hermes 与 Kubernetes 的关系：

~~~text
Hermes
   |
   | DiagnosisResult
   v
Safety Gate
   |
   | Validated ActionRequest
   v
Controlled Executor
   |
   | Kubernetes Credential
   v
Kubernetes API
~~~

因此：

> Hermes 与 Kubernetes 写权限之间不存在直接信任关系。

---

## 23. Verification Engine

Executor API 返回成功不等于 Incident 已恢复。

必须执行独立 Verification。

第一个 FastAPI 场景至少验证：

~~~text
Deployment desired replicas

Deployment available replicas

old unhealthy Pod disappeared

replacement Pod created

replacement Pod Ready=True

Service EndpointSlice healthy

/healthz == 200

/readyz == 200

MySQL dependency == OK

Redis dependency == OK

Prometheus target == UP

PDB returned to healthy state
~~~

辅助记录：

~~~text
HPA current replicas

HPA desired replicas

Pod node distribution
~~~

但：

~~~text
worker1=1
worker2=1
~~~

不作为所有 remediation 的绝对成功条件。

节点分布属于附加 SRE 状态。

---

## 24. Verification Result Schema

Verification 第一版概念结构：

~~~text
{
  "action_id": "action-xxxx",

  "status": "PASS",

  "started_at": "...",

  "finished_at": "...",

  "checks": [
    {
      "name": "deployment_ready",
      "status": "PASS",
      "evidence": {}
    }
  ],

  "summary": "..."
}
~~~

最终状态只允许：

~~~text
PASS

FAIL

TIMEOUT
~~~

禁止出现：

~~~text
probably recovered

looks good

likely fixed
~~~

等模糊结果。

---

## 25. Experience Store

课程设计需要建立可审计的经验存储。

Canonical Experience Store 保存：

~~~text
Incident

Diagnosis

Safety Decision

Action

Verification

Experience
~~~

MVP 第一版计划使用：

~~~text
SQLite
~~~

而不使用：

~~~text
Elasticsearch

Kafka

Milvus

Qdrant

Large Vector Database
~~~

原因：

~~~text
single controller

small-scale course project

easy audit

easy SQL query

easy backup

minimal operational overhead

no unnecessary infrastructure
~~~

---

## 26. Experience Store 与 Hermes Memory 的关系

如果后续启用 Hermes Memory / Skill，它不能直接取代 Experience Store。

正式关系：

~~~text
Canonical Experience Store
        |
        +-- Incident
        |
        +-- Diagnosis
        |
        +-- Safety Decision
        |
        +-- Action
        |
        +-- Verification
        |
        v
Experience Distillation
        |
        v
Hermes Memory / Skill
~~~

因此：

> 结构化数据库记录是真实事实 Source of Truth。

Hermes Memory / Skill 是从真实 Incident 结果中提炼出的派生经验。

---

## 27. Experience Evolution

成功或者失败的 Incident 完成后，可以形成结构化经验。

例如：

~~~text
{
  "incident_type": "FASTAPI_UNHEALTHY_INSTANCE",

  "conditions": [],

  "action": "EVICT_POD",

  "outcome": "PASS",

  "lesson": "..."
}
~~~

下一次类似 Incident 可以获取：

~~~text
Current Incident
       +
Related Historical Experience
       |
       v
Hermes Diagnosis
~~~

但历史经验：

> 永远不能绕过 Safety Gate。

---

## 28. Automation Level

系统自动化等级冻结为三个阶段。

### Level 1：只读诊断

~~~text
Observation
     |
     v
Context
     |
     v
Hermes
     |
     v
Diagnosis
     |
     v
STOP
~~~

特点：

~~~text
READ ONLY

NO Kubernetes mutation
~~~

---

### Level 2：Human-in-the-loop

~~~text
Observation
     |
     v
Context
     |
     v
Hermes
     |
     v
Safety Gate
     |
     v
Human Approval
     |
     v
Executor
     |
     v
Verification
     |
     v
Experience
~~~

Level 2：

> 课程设计必须完整实现。

---

### Level 3：Controlled Self-Healing

~~~text
Observation
     |
     v
Context
     |
     v
Hermes
     |
     v
Safety Gate
     |
     v
Allowlisted Low-risk Policy
     |
     v
Executor
     |
     v
Verification
     |
     v
Experience
~~~

Level 3 至少实现：

> 一个真实、自洽、可验证的自动自愈场景。

---

## 29. 第一个 MVP Incident

第一个正式 Incident 冻结为：

~~~text
FASTAPI_UNHEALTHY_INSTANCE
~~~

目标：

~~~text
namespace:
opslab

Deployment:
opslab-api

replicas:
>= 2
~~~

典型状态：

~~~text
Pod A
Ready=False

Pod B
Ready=True
~~~

同时要求：

~~~text
PDB exists

disruptionsAllowed >= 1
~~~

Hermes 可以给出：

~~~text
incident_type:
FASTAPI_UNHEALTHY_INSTANCE

risk:
LOW

recommended_action:
EVICT_POD
~~~

但最终动作必须经过 Safety Gate。

---

## 30. 第一个闭环目标

完整 MVP 闭环：

~~~text
FastAPI abnormal instance
        |
        v
Observation
        |
        v
Incident Context
        |
        v
Hermes Diagnosis
        |
        v
Safety Gate
        |
        v
EVICT_POD Proposal
        |
        v
Human Approval / Auto Policy
        |
        v
Eviction API
        |
        v
Deployment Replacement
        |
        v
New Pod Ready
        |
        v
Health Verification
        |
        v
PASS / FAIL
        |
        v
Experience Store
~~~

---

## 31. 为什么第一场景不操作 MySQL / Redis

当前：

~~~text
MySQL
worker1
Local PV

Redis
worker2
Local PV
~~~

它们属于 Stateful Workload。

如果第一版直接允许 AI 自动操作 Stateful workload，会同时引入：

~~~text
storage locality

data safety

state consistency

data recovery

persistent volume scheduling
~~~

等额外问题。

这些问题不是当前 Hermes MVP 的核心研究目标。

因此正式冻结：

~~~text
Level 3 Automatic Remediation
=
Stateless FastAPI only
~~~

第一阶段不自动操作：

~~~text
MySQL

Redis

StatefulSet

Local PV workload
~~~

---

## 32. Non-Goals

本课程设计 MVP 明确不做：

~~~text
Arbitrary AI shell execution

Arbitrary AI kubectl

cluster-admin Agent

Automatic MySQL remediation

Automatic Redis remediation

MySQL HA cluster

Redis HA cluster

Local PV cross-node HA

Ceph

Distributed Storage

Kafka

Multi-cluster AIOps

Large multi-agent orchestration

LLM training

LLM fine-tuning

General-purpose RAG platform

Large-scale vector database

Automatic Node reboot

Automatic OS repair

General-purpose autonomous AIOps

Hermes HA cluster
~~~

这些内容不属于当前课程设计 MVP。

---

## 33. 高可用在课程设计中的含义

课程设计中的：

> 高可用验证

主要指：

利用当前 OpsLab 已经具备的：

~~~text
FastAPI Multi-Replica

Readiness

PDB

HPA

TSC

Descheduler

Kubernetes Self-Healing
~~~

验证：

> 智能运维闭环不会破坏应用已有高可用约束，并能够在受控故障状态下帮助恢复 Python Web 应用。

课程设计不宣称：

~~~text
MySQL Cross-node HA

Redis Cross-node HA

Distributed Storage HA

Hermes Cluster HA
~~~

---

## 34. 仓库目录规划

后续仓库计划扩展为：

~~~text
opslab-k8s/
|
├── applications/
│   ├── fastapi/
│   │
│   └── aiops-controller/
│       ├── pyproject.toml
│       ├── README.md
│       │
│       ├── src/
│       │   └── opslab_aiops/
│       │       ├── observation/
│       │       ├── context/
│       │       ├── agent/
│       │       ├── safety/
│       │       ├── executor/
│       │       ├── verification/
│       │       └── experience/
│       │
│       ├── schemas/
│       │   ├── incident-context.schema.json
│       │   ├── diagnosis-result.schema.json
│       │   └── action-request.schema.json
│       │
│       └── tests/
│
├── kubernetes/
│   └── aiops/
│       ├── observer-rbac/
│       └── executor-rbac/
│
├── config/
│   └── hermes/
│
├── scripts/
│   └── aiops/
│
└── docs/
    └── hermes/
        ├── phase0-architecture-mvp-freeze.md
        ├── architecture/
        ├── validation/
        └── evidence/
~~~

Phase 0 不提前创建无内容的目录。

所有目录都应随真实功能逐步进入 Git。

---

## 35. 为什么使用 aiops-controller 命名

不建议将我们自己开发的系统目录命名为：

~~~text
applications/hermes/
~~~

因为：

> 我们开发的不是 Hermes 本身。

Hermes 是外部 Agent / Reasoning Engine。

我们自己开发的是：

> OpsLab AIOps Controller。

因此：

~~~text
applications/aiops-controller/
~~~

能够更准确体现系统边界。

---

## 36. Git 策略

当前传统 SRE 稳定边界：

~~~text
traditional-sre-enhanced-v1.1.0
~~~

Hermes 主开发分支：

~~~text
hermes-mvp
~~~

个人课程设计不建立大量没有实际价值的 feature branch。

Phase 0 完成以后计划创建：

~~~text
hermes-phase0-v0.1.0
~~~

后续 Tag 根据真实完成情况创建。

可能包括：

~~~text
hermes-diagnosis-v0.2.0

hermes-hitl-v0.3.0

hermes-self-healing-v0.4.0

hermes-aiops-mvp-v1.0.0
~~~

这些 Tag 不提前创建。

---

## 37. Phase 0 完成标准

只有满足全部条件后，Phase 0 才允许正式 PASS。

~~~text
Architecture frozen

Physical deployment boundary frozen

MVP scope frozen

Hermes role frozen

Hermes non-responsibilities frozen

System module boundary frozen

Data flow frozen

Observation input frozen

Incident Context model frozen

Evidence model frozen

Diagnosis Result model frozen

Safety Gate rules frozen

Executor action allowlist frozen

Verification contract frozen

Experience Store design frozen

First MVP Incident frozen

Non-Goals frozen

Repository layout frozen

Git boundary verified

Phase 0 document committed

Git clean

origin synchronized
~~~

最终才允许记录：

~~~text
HERMES_PHASE0_ARCHITECTURE=FROZEN

HERMES_MVP_SCOPE=FROZEN

HERMES_PHASE0=PASS
~~~

在 Git Evidence 完整前不得提前标记 PASS。

---

## 38. Phase 1 入口

Phase 1 正式目标：

> 建立只读 Kubernetes Observation Layer。

第一个真实程序：

~~~text
WSL Python
    |
    v
Kubernetes API
    |
    v
Read-only Observation Snapshot
~~~

第一版只读取：

~~~text
Deployment/opslab-api

ReplicaSet

Pods

PDB

HPA

EndpointSlice

Events
~~~

第一阶段暂时：

~~~text
不调用 Hermes

不调用 LLM

不调用 Executor

不执行 remediation

不配置 Kubernetes 写权限
~~~

Phase 1 首先需要证明：

> WSL External AIOps Control Plane 能够使用最小只读权限，通过程序化、结构化和可审计的方式获取 Kubernetes Managed Plane 的真实运行状态。

---

## 39. Phase 0 核心设计原则

本课程设计正式坚持以下原则。

### 原则一

> AI 负责推理，但不直接掌握生产执行权限。

### 原则二

> Incident Context 是 Kubernetes 真实状态与 Hermes 之间的数据边界。

### 原则三

> Hermes 的输出是 Diagnosis 和 Action Intent，而不是任意 Shell Command。

### 原则四

> Safety Gate 是 AI 与 Kubernetes 写操作之间不可绕过的安全边界。

### 原则五

> Controlled Executor 只理解白名单结构化 Action。

### 原则六

> Kubernetes 写 Credential 只属于受控 Executor，不属于 Hermes。

### 原则七

> Executor API 返回成功不等于 Incident 已恢复。

### 原则八

> 所有执行动作必须经过独立 Verification。

### 原则九

> Incident、Diagnosis、Safety Decision、Action 和 Verification 必须完整记录并能够审计。

### 原则十

> Experience 必须来自真实验证结果，而不是来自 LLM 自己声称“已经修复”。

---

## 40. Phase 0 最终架构结论

OpsLab Hermes 智能运维闭环最终采用：

~~~text
Kubernetes / Prometheus / Alertmanager
                |
                v
        Observation Layer
                |
                v
      Incident Context Builder
                |
                v
           Hermes Agent
                |
                v
       Structured Diagnosis
                |
                v
           Safety Gate
                |
                v
 Human Approval / Auto Policy
                |
                v
      Controlled Executor
                |
                v
          Verification
                |
                v
        Experience Store
                |
                +-------------------+
                                    |
                                    v
                         Future Diagnosis Context
~~~

实现目标：

~~~text
可观测
  |
  v
可诊断
  |
  v
可解释
  |
  v
有安全边界
  |
  v
可执行
  |
  v
可验证
  |
  v
可沉淀经验
~~~

这就是 Hermes 课程设计阶段后续所有实现工作的架构基线。
