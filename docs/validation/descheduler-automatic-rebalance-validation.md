# OpsLab Descheduler 自动重平衡增强：完整验证、故障排查与 RCA 报告

> 文档类型：SRE / Kubernetes 工程验证报告
> 项目：`opslab-k8s`
> 阶段：Traditional SRE Baseline 之后的 Automatic Rebalance Enhancement
> 核心组件：Kubernetes v1.36.3、Descheduler v0.36.0、TopologySpreadConstraints、PodDisruptionBudget、HPA、RBAC
> 验证结论：**Automatic Rebalance Enhancement = PASS**
> 说明：本文只记录本次实际执行、实际日志和已经得到证据支持的结论。凡是尚未落入 Git 仓库、尚未清理或尚未执行的事项，会明确标记为“待完成”，不会写成既成事实。

---

## 1. 文档目的

这一阶段最初看起来只是“给 FastAPI 增加 Descheduler，让两个副本在发生放置漂移后自动恢复到 1/1”。

实际执行后发现，它远比单纯部署一个组件复杂。

整个过程中连续遇到了多层问题：

1. Descheduler dry-run 单次执行看不到已经存在的 `0/2` 放置漂移；
2. FastAPI 的 `emptyDir` 被 DefaultEvictor 视为 local storage，导致 Pod 被默认保护；
3. 多周期诊断 Job 因 `activeDeadlineSeconds` 和 CronJob ownership 导致证据 Pod 被删除，第一次诊断装置自身失败；
4. `kubectl auth can-i` 对 Pod `eviction` subresource 的检查写法造成一次 RBAC 假阴性；
5. 第一次真实 Descheduler 运行时，明明存在 PDB，`PodsWithoutPDB` 保护却将两个 FastAPI Pod 错误判定为“没有 PDB”；
6. 在保持 Kubernetes PDB 不变的前提下，针对 Descheduler v0.36.0 的已定位问题做最小 workaround；
7. 第二次真实运行最终完成：
   `worker1=0 / worker2=2 → eviction 1 Pod → replacement → worker1=1 / worker2=1`。

因此，这一阶段真正的价值并不是“安装了 Descheduler”，而是形成了一条完整的 SRE 证据链：

```text
观察异常
→ 保护现场
→ 建立可重复实验输入
→ dry-run
→ 失败分类
→ 提升日志等级
→ 提出假设
→ A/B 验证
→ 排除错误假设
→ 定位测试装置问题
→ 定位组件版本行为
→ 最小修改
→ 安全门禁
→ 真实 mutation
→ UID / Placement / PDB / HPA / StatefulSet 多维回归
→ 最终 PASS
```

---

# 2. 阶段进入时的项目基线

## 2.1 Git 分界

在进入自动重平衡增强之前，传统 SRE 基线已经完成并封存：

```text
b5a1222  docs(validation): seal traditional sre baseline
tag: traditional-sre-baseline-v1.0.0
```

随后增加 PDB Safety Guard：

```text
48b269d  feat(kubernetes): add api disruption budget
```

Descheduler 镜像构建上下文已进入仓库：

```text
cf79147  build(descheduler): add v0.36.0 mirror context
```

在本阶段结束时的 Git 基线仍为：

```text
## master...origin/master
cf79147 (HEAD -> master, origin/master) build(descheduler): add v0.36.0 mirror context
48b269d feat(kubernetes): add api disruption budget
b5a1222 (tag: traditional-sre-baseline-v1.0.0) docs(validation): seal traditional sre baseline
```

**非常重要：**

截至本次 inventory audit，运行态 Descheduler 配置已经验证成功，但仓库里还没有正式的 Descheduler manifests：

```text
Existing Descheduler Files In Repository:
<none>
```

当前仓库中与这一阶段直接相关的文件只有：

```text
images/descheduler/Dockerfile
kubernetes/apps/opslab-api/02-deployment.yaml
kubernetes/apps/opslab-api/04-pdb.yaml
kubernetes/apps/opslab-api/deployment-baseline.txt
```

因此：

> “运行态验证成功”已经完成；
> “将最终 Descheduler 配置正式落回 Git 仓库”仍属于本阶段最后的工程收尾任务。

这两件事必须区分。

---

# 3. 集群和业务前置条件

## 3.1 Kubernetes 集群

本阶段基于已有 kubeadm 集群继续：

```text
Kubernetes: v1.36.3
control-plane: k8s-control-plane
worker1: k8s-worker1
worker2: k8s-worker2
```

两个 Worker 都作为 FastAPI 的可调度节点。

control-plane 存在控制平面 taint，FastAPI 不容忍该 taint，因此不会被调度到 control-plane。

这一点后来在 Descheduler V5 日志中得到直接验证：

```text
Pod fits on node ... node="k8s-worker1"
Pod does not fit on node ... node="k8s-control-plane"
err="pod does not tolerate taints on the node"
```

---

## 3.2 FastAPI 工作负载

namespace：

```text
opslab
```

Deployment：

```text
opslab-api
```

正常副本数：

```text
2
```

HPA：

```text
minReplicas = 2
maxReplicas = 4
CPU target = 60%
```

本次真实重平衡前 HPA 稳定在：

```text
CURRENT=2
DESIRED=2
```

这很重要，因为如果 HPA 正在扩缩容，就无法把 Pod 数量变化简单归因于 Descheduler。

---

## 3.3 FastAPI TopologySpreadConstraints

应用原本已经具有调度时拓扑约束，其核心语义为：

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: DoNotSchedule
    nodeAffinityPolicy: Honor
    nodeTaintsPolicy: Honor
    labelSelector:
      matchLabels:
        app.kubernetes.io/instance: opslab
        app.kubernetes.io/name: opslab-api
```

意义：

- `topologyKey: kubernetes.io/hostname`：按节点进行拓扑分散；
- `maxSkew: 1`：节点间匹配 Pod 的数量差不能超过允许偏差；
- `DoNotSchedule`：如果新 Pod 会违反约束，则不允许这样调度；
- `nodeTaintsPolicy: Honor`：不可容忍 taint 的节点不应被当作正常可选域；
- selector 只匹配 `opslab-api`。

### 关键认知

TopologySpreadConstraints 主要解决的是：

> **新 Pod 创建时怎么放。**

它不会主动把“已经存在但后来变得不均衡”的 Pod 自动搬走。

因此之前 Drill 7 中曾出现：

```text
worker1=0
worker2=2
```

即使 TSC 仍然存在，只要两个 Pod 都已经处于 Running 状态，Scheduler 不会为了“美观”主动重排。

这正是引入 Descheduler 的工程原因。

---

# 4. PDB Safety Guard

在进入 Descheduler 真实 eviction 之前，先增加：

```text
kubernetes/apps/opslab-api/04-pdb.yaml
```

核心约束：

```yaml
maxUnavailable: 1
```

selector 只匹配 FastAPI。

正常 2 个健康副本时，PDB 运行态为：

```text
CURRENT_HEALTHY=2
DESIRED_HEALTHY=1
DISRUPTIONS_ALLOWED=1
```

这代表：

- 当前有 2 个健康 FastAPI Pod；
- PDB 至少要求保留 1 个健康；
- 当前允许 1 次 voluntary disruption。

PDB 的作用不是“决定 Descheduler 要不要重平衡”，而是：

> 当 Descheduler 真正通过 Eviction API 请求驱逐 Pod 时，由 Kubernetes API Server 对 disruption budget 做最后约束。

这是后面安全模型的重要一层。

---

# 5. Descheduler 版本、镜像与部署设计

## 5.1 固定版本

本阶段固定：

```text
Descheduler v0.36.0
Helm Chart 0.36.0
```

镜像不是运行时临时从境外拉取，而是已转存到 ACR，并使用 digest 固定：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/
k8s-test-111/descheduler:v0.36.0@
sha256:abb7831250bd342f828ef0e212b44d24987372e3ccfa7efec8aafd4ff8084137
```

两个 Worker 已完成预拉取。

这样做有三个目的：

1. 避免演练过程中受境外镜像可达性影响；
2. 避免 tag 漂移；
3. 让验证结果绑定到确切二进制。

---

## 5.2 为什么没有直接使用 Chart 默认行为

Helm Chart 默认行为对于本实验过于宽泛：

- CronJob 会周期执行；
- 默认并不是 suspend；
- 默认策略比本项目需要的范围更大。

因此最终采用了“先把危险能力锁死，再逐层打开”的思路：

```text
CronJob
schedule: */2 * * * *
suspend: true
--dry-run=true
```

在完成最终真实验收之前，父 CronJob 始终保持：

```text
SUSPEND=true
ARGS=["--policy-config-file=/policy-dir/policy.yaml","--dry-run=true","--v=3"]
```

真实 mutation 不通过解除 CronJob suspend 来做，而是生成**独立 standalone one-shot Job**。

这是非常重要的 blast-radius 控制。

---

# 6. RBAC 最小权限设计

ServiceAccount：

```text
opslab-descheduler
```

运行态 RBAC 对象：

```text
Role/opslab-descheduler-evict
RoleBinding/opslab-descheduler-evict

ClusterRole/opslab-descheduler-observer
ClusterRoleBinding/opslab-descheduler-observer
```

## 6.1 Observer 权限

ClusterRole 只提供读取 Descheduler 判断所需对象的能力，例如：

- Nodes
- Namespaces
- Pods
- PVC
- PriorityClass
- PDB

而不是赋予 cluster-admin。

---

## 6.2 Eviction 权限

`opslab` namespace 内单独使用 Role：

```yaml
rules:
  - apiGroups:
      - ""
    resources:
      - pods/eviction
    verbs:
      - create

  - apiGroups:
      - events.k8s.io
    resources:
      - events
    verbs:
      - create
      - update
```

真实权限边界最终验证为：

```text
OPSLAB_EVICTION_CREATE=yes
MONITORING_EVICTION_CREATE=no
KUBE_SYSTEM_EVICTION_CREATE=no
OPSLAB_DIRECT_POD_DELETE=no
CANONICAL_RBAC_BOUNDARY=PASS
```

这意味着：

- 可以在 `opslab` 发起 Eviction API；
- 不能去 `monitoring` 发 eviction；
- 不能去 `kube-system` 发 eviction；
- 不能绕开 Eviction API 直接 `delete pods`。

这比简单给 `delete pods` 更符合本项目的安全目标。

---

# 7. 初始 Descheduler Policy

最初 Policy 的核心安全设计如下：

```yaml
apiVersion: descheduler/v1alpha2
kind: DeschedulerPolicy

maxNoOfPodsToEvictPerNode: 1
maxNoOfPodsToEvictPerNamespace: 1
maxNoOfPodsToEvictTotal: 1

evictionFailureEventNotification: true

profiles:
  - name: opslab-fastapi-rebalance

    pluginConfig:
      - name: DefaultEvictor
        args:
          nodeFit: true
          minReplicas: 2

          labelSelector:
            matchLabels:
              app.kubernetes.io/instance: opslab
              app.kubernetes.io/name: opslab-api

          podProtections:
            extraEnabled:
              - PodsWithPVC
              - PodsWithoutPDB

      - name: RemovePodsViolatingTopologySpreadConstraint
        args:
          namespaces:
            include:
              - opslab

          labelSelector:
            matchLabels:
              app.kubernetes.io/instance: opslab
              app.kubernetes.io/name: opslab-api

          constraints:
            - DoNotSchedule

          topologyBalanceNodeFit: true

    plugins:
      balance:
        enabled:
          - RemovePodsViolatingTopologySpreadConstraint
```

设计目标非常明确：

- 只处理 `opslab-api`；
- 只处理 namespace `opslab`；
- 只启用 TopologySpreadConstraint rebalance；
- 单次最多 eviction 1；
- 副本控制器少于 2 个副本时不动；
- 候选 Pod 必须能在其他节点 fit；
- PVC Pod 保护；
- 没有 PDB 的 Pod 保护。

理论上是一套非常保守的配置。

后面的困难恰恰来自：

> “安全保护越多越好”并不等于“所有保护都在当前版本中能正确工作”。

---

# 8. 第一阶段：平衡状态 baseline dry-run

在真正制造 drift 之前，先验证 Descheduler 自身能否：

- 启动；
- 加载 Policy；
- 执行目标 plugin；
- 在健康 1/1 状态下保持零 mutation。

当时 FastAPI 为：

```text
worker1 = 1
worker2 = 1
```

PDB：

```text
ALLOWED=1
CURRENT_HEALTHY=2
DESIRED_HEALTHY=1
```

使用从 suspended CronJob 派生的 one-shot Job：

```text
opslab-descheduler-dryrun-baseline
```

安全断言包括：

```text
JOB_SERVICE_ACCOUNT=PASS
JOB_IMMUTABLE_IMAGE=PASS
JOB_DRY_RUN_GUARD=PASS
JOB_POLICY_MOUNT_ARGUMENT=PASS
ONE_SHOT_JOB_PREFLIGHT=PASS
```

Job 正常完成：

```text
Complete 1/1
```

最终：

```text
evictedPods=0
evictionRequests=0
totalEvicted=0
```

且 FastAPI UID / Node 不变化。

这一阶段证明：

```text
DESCHEDULER_BINARY_EXECUTION=PASS
POLICY_RUNTIME_LOAD=PASS
TOPOLOGY_BALANCE_PLUGIN_EXECUTION=PASS
BASELINE_ZERO_MUTATION=PASS
```

---

# 9. 构造受控 0/2 放置漂移

为了验证“已经存在的不均衡”能否被修复，需要构造稳定的实验输入：

```text
worker1=0
worker2=2
```

## 9.1 为什么不能随便 delete Pod

如果直接：

```bash
kubectl delete pod ...
```

存在两个问题：

1. 这绕开 PDB；
2. 无法证明后面受控 eviction 的安全链路。

因此先通过临时调度条件，使 replacement 必然落到 worker2，再通过 Eviction API 驱逐 worker1 上的一个 FastAPI Pod。

---

## 9.2 临时 taint

临时在 worker1 添加：

```text
opslab.io/rebalance-test=drift:NoSchedule
```

已有 Pod 不会因为 NoSchedule taint 自动被赶走，因此初始 1/1 不立即变化。

---

## 9.3 Eviction API 的一次认知修正

最开始尝试将 `policy/v1 Eviction` 当成普通顶级资源直接 `kubectl create`。

Server dry-run 返回无法匹配资源。

这里没有去“装一个 Eviction CRD”，而是及时纠正：

> Eviction 不是一个需要长期保存的顶级资源；它是 Pod 的 subresource。

于是使用：

```text
POST /api/v1/namespaces/opslab/pods/<pod>/eviction
```

并使用 Pod UID precondition 锁定目标。

先：

```text
dryRun=All
```

验证请求可以被接受且 Pod UID 不变。

再做一次真实 eviction。

Deployment replacement 因 worker1 暂时 tainted，被调度到 worker2。

随后移除测试 taint。

最终形成干净实验输入：

```text
worker1=0
worker2=2
```

并且：

- worker1 Ready；
- worker2 Ready；
- worker1 已无临时 taint；
- 两个 Worker 均可调度；
- 两个 FastAPI Pod 均 Ready；
- PDB 正常；
- HPA 稳定；
- Git clean。

这是之后所有 Descheduler RCA 的固定输入。

---

# 10. 第一次 drift dry-run：出现“看起来什么都没发生”

Job：

```text
opslab-descheduler-dryrun-drift
```

输入确认：

```text
WORKER1_BEFORE=0
WORKER2_BEFORE=2
PLACEMENT_DRIFT_INPUT=PASS
```

Job 本身：

```text
Complete 1/1
```

但是日志只看到：

```text
Processing namespaces for topology spread constraints
evictedPods=0
evictionRequests=0
totalEvicted=0
```

并没有：

```text
Evicted pod in dry run mode
```

真实集群继续：

```text
worker1=0
worker2=2
```

因此当时只能得到：

```text
PLACEMENT_DRIFT_INPUT=PASS
DESCHEDULER_DRYRUN_EXECUTION=PASS
TOPOLOGY_PLUGIN_EXECUTION=PASS

EXPECTED_SIMULATED_EVICTION=1
ACTUAL_SIMULATED_EVICTION=0

TOPOLOGY_SPREAD_VIOLATION_DETECTED=FAIL
REAL_CLUSTER_ZERO_MUTATION=PASS
```

### 关键原则

此时不能得出：

> “Descheduler 不支持这个场景”。

也不能得出：

> “TopologySpreadConstraints 配错了”。

只能得出：

> **现象未解释，RCA open。**

---

# 11. 提升到 V5：第一轮 RCA

将诊断日志提升到：

```text
--v=5
```

观察重点：

- 是否进入 namespace；
- DefaultEvictor 是否过滤 Pod；
- nodeFit 是否拒绝；
- topology plugin 是否认为已经平衡。

但结果仍然只看到：

```text
Processing namespaces for topology spread constraints
evictedPods=0
totalEvicted=0
```

没有看到：

```text
Processing namespace ... namespace=opslab
```

也没有：

```text
Pod fails the following checks
```

也没有：

```text
ignoring pod ... does not fit
```

于是开始检查所有可能的“输入条件”。

---

# 12. 排除 selector / ReplicaSet / TSC / node eligibility

对两个 FastAPI Pod 做运行态审计：

- labels 正确；
- 两个 Pod 属于同一个 ReplicaSet；
- ReplicaSet desired/current/ready = 2；
- PDB selector 可以匹配；
- 两个 Pod 都没有 PVC；
- 两个 Pod 都包含一个 `emptyDir` volume；
- worker1 / worker2 Ready；
- 两个 Worker 无 taint；
- FastAPI 没有 toleration；
- TSC 内容与预期一致。

已验证的 Pod label：

```text
app.kubernetes.io/component=backend
app.kubernetes.io/instance=opslab
app.kubernetes.io/name=opslab-api
app.kubernetes.io/part-of=opslab-k8s
app.kubernetes.io/version=v0.3.2
```

TSC selector 和 Descheduler selector 都是：

```yaml
app.kubernetes.io/instance: opslab
app.kubernetes.io/name: opslab-api
```

因此以下原因被排除：

```text
namespace 不匹配       → 排除
strategy selector 不匹配 → 排除
Pod TSC 不存在          → 排除
worker1 不可调度        → 排除
worker2 不健康          → 排除
ReplicaSet < 2          → 排除
```

---

# 13. RCA-2 的最初假设：emptyDir / local storage

审计 FastAPI Pod 时发现：

```text
volume:
  emptyDir:
    sizeLimit: 64Mi
```

Descheduler DefaultEvictor 对本地临时存储 Pod 有保护语义，因此提出假设：

> FastAPI 因 `emptyDir` 被 `PodsWithLocalStorage` 默认保护拦住了。

建立 RCA Policy variant：

```yaml
podProtections:
  defaultDisabled:
    - PodsWithLocalStorage
  extraEnabled:
    - PodsWithPVC
    - PodsWithoutPDB
```

注意：

这一步只关闭 `PodsWithLocalStorage` 保护。

仍然保留：

```text
PodsWithPVC
PodsWithoutPDB
minReplicas=2
nodeFit=true
maxNoOfPodsToEvictTotal=1
FastAPI-only selector
```

---

# 14. 第一次 local-storage A/B 没有成功证明因果

使用：

```text
opslab-descheduler-rca-localstorage
```

作为临时 ConfigMap。

运行 one-shot V5 dry-run：

```text
opslab-descheduler-dryrun-localstorage-regression
```

结果仍然：

```text
Processing namespaces...
evictedPods=0
totalEvicted=0
```

没有：

```text
Processing namespace
```

因此当时正确结论不是“local storage 不是问题”，而是：

```text
RCA_HYPOTHESIS_LOCAL_STORAGE
= FALSIFIED_AS_SOLE_CAUSE

PODS_WITH_LOCAL_STORAGE_PROTECTION
= REAL_BUT_NOT_SUFFICIENT_TO_EXPLAIN_CURRENT_FAILURE

ROOT_CAUSE
= STILL_OPEN
```

为什么要这样表述？

因为 A/B 的自变量虽然改了，但测试装置本身还存在另一个未知问题。

如果直接说：

> “emptyDir 与问题无关”

会过早关闭正确方向。

---

# 15. 多周期 dry-run：诊断装置第一次失败

为了验证“是不是 dry-run 第一个 cycle 太早”，将 Descheduler 改为：

```text
--dry-run=true
--v=5
--descheduling-interval=5s
```

计划让它执行至少 3 个周期。

第一次多周期 Job：

```text
opslab-descheduler-dryrun-multicycle-rca
```

同时设置：

```text
activeDeadlineSeconds=18
```

外部脚本又：

```text
sleep 20
```

结果：

```text
Job Failed
reason=DeadlineExceeded
```

Pod 随后被删除，日志也来不及按预期获取。

这一轮不能用于判断 Descheduler 是否支持重平衡。

### 正确故障分类

```text
DESCHEDULER_FUNCTIONALITY
= INCONCLUSIVE

DIAGNOSTIC_HARNESS
= FAILED
```

---

# 16. 诊断装置 RCA：activeDeadlineSeconds + CronJob ownership

进一步 describe Job 后发现两个关键事实。

## 16.1 DeadlineExceeded

Job：

```text
Active Deadline Seconds: 18s
```

实际外部等待：

```text
sleep 20
```

因此 Job controller 在日志采集之前终止运行 Pod。

事件：

```text
Killing
SuccessfulDelete
DeadlineExceeded
```

这是纯测试装置问题。

---

## 16.2 `kubectl create job --from=cronjob` 保留 ownership

更关键的是：

```text
Controlled By: CronJob/opslab-descheduler
```

事件还出现：

```text
UnexpectedJob
```

并且此前由 CronJob history 管理逻辑删除过老 diagnostic Job。

也就是说：

> “手工从 CronJob 生成一个 Job”并不自动等于“这个 Job 完全脱离 CronJob 生命周期”。

这会威胁诊断证据保留。

---

## 16.3 修正方法

之后所有重要 standalone diagnostic Job 都显式：

1. 删除：

```yaml
metadata.ownerReferences
```

2. 删除：

```text
cronjob.kubernetes.io/instantiate
```

3. 删除：

```yaml
activeDeadlineSeconds
ttlSecondsAfterFinished
```

4. 设置：

```yaml
backoffLimit: 0
```

5. 先采日志和 Job YAML，再显式清理。

这是本阶段非常值得保留的一条工程经验：

> **诊断工具本身也需要被验证。**

---

# 17. RCA-1：dry-run first-cycle sandbox 初始化时序被确认

修正后的 Job：

```text
opslab-descheduler-dryrun-multicycle-standalone
```

安全条件：

```text
CRONJOB_OWNER_REFERENCE=ABSENT
ACTIVE_DEADLINE_SECONDS=ABSENT
TTL_SECONDS_AFTER_FINISHED=ABSENT
BACKOFF_LIMIT=0

--dry-run=true
--v=5
--descheduling-interval=5s
```

真实集群仍保持：

```text
worker1=0
worker2=2
```

---

## 17.1 第一轮

日志明确显示：

```text
Resetting pod evictor counters
Skipping descheduling cycle: requires >=2 nodes
found=0
Restoring evicted pods from cache
totalEvicted=0
```

这比之前“只看到 `evictedPods=0`”的信息量大得多。

根因开始清晰：

> 第一轮 cycle 时，dry-run sandbox 看到的 node 集合为 0。

---

## 17.2 5 秒后的第二轮

第二轮：

```text
Processing namespaces for topology spread constraints
Processing namespace ... namespace="opslab"
```

然后：

```text
Pod fits on node ... worker1
Pod does not fit on node ... control-plane
```

最后：

```text
Evicted pod in dry run mode
evictedPods=1
totalEvicted=1
```

第三轮、第四轮重复相同结果。

真实集群仍：

```text
worker1=0
worker2=2
```

因为是 dry-run。

于是 RCA-1 可以封闭：

```text
RCA-1=CONFIRMED

CAUSE:
Descheduler v0.36.0 dry-run sandbox first cycle
may run before the sandbox node view is ready.

SYMPTOM:
first cycle found=0
→ cycle skipped
→ one-shot dry-run exits
→ false negative

REGRESSION:
multi-cycle dry-run
→ later cycles see nodes and Pods
→ simulated eviction=1
```

这也解释了为什么之前多个 one-shot dry-run 都得到了假阴性。

---

# 18. RCA-2：PodsWithLocalStorage 最终通过真正 A/B 确认

有了“必须等 sandbox 进入稳定 cycle”的认知后，重新做 local-storage A/B。

---

## 18.1 A 组：关闭 `PodsWithLocalStorage`

RCA Policy：

```yaml
podProtections:
  defaultDisabled:
    - PodsWithLocalStorage
  extraEnabled:
    - PodsWithPVC
    - PodsWithoutPDB
```

多周期结果：

```text
first cycle:
found=0

later cycle:
Processing namespace=opslab
Evicted pod in dry run mode
evictedPods=1
```

---

## 18.2 B 组：原始 Policy，保持 local-storage protection

原始 Policy 多周期：

```text
Processing namespaces for topology spread constraints
```

随后 V5 明确打印：

```text
Pod fails the following checks
checks="pod has local storage and is protected against eviction"
```

两个 FastAPI Pod 都被过滤。

最后：

```text
evictedPods=0
totalEvicted=0
```

而相同 `0/2` 输入、相同节点、相同 selector，只关闭 local-storage protection 后就能模拟 eviction。

因此因果关系成立：

```text
RCA-2=CONFIRMED

FastAPI emptyDir
→ DefaultEvictor treats Pod as local-storage Pod
→ PodsWithLocalStorage default protection
→ Pod rejected by Filter
→ no topology rebalance candidate
```

---

# 19. 正式 Policy 第一次修复：放开 FastAPI local storage

将：

```yaml
defaultDisabled:
  - PodsWithLocalStorage
```

升级进入运行态正式 Policy。

保留：

```text
PodsWithPVC
PodsWithoutPDB
minReplicas=2
nodeFit=true
max eviction total=1
FastAPI-only selector
```

Promotion 前后：

```text
FINAL_POLICY_STATIC_AUDIT=PASS
FINAL_POLICY_SERVER_DRYRUN=PASS
FINAL_POLICY_PROMOTED=PASS
```

运行态：

```text
RUNTIME_LOCAL_STORAGE_EXCEPTION=PASS
RUNTIME_PVC_PROTECTION=PASS
RUNTIME_PDB_PROTECTION=PASS
RUNTIME_MIN_REPLICAS_GUARD=PASS
RUNTIME_NODE_FIT_GUARD=PASS
RUNTIME_MAX_EVICTION_TOTAL=1
FINAL_RUNTIME_POLICY=PASS
```

同时：

```text
worker1=0
worker2=2
```

没有被 promotion 本身改变。

说明只修改了 ConfigMap，不进行了 eviction。

---

# 20. 第一次真实运行前 Safety Gate

真实 mutation 前做了独立门禁。

## 20.1 输入

```text
Deployment READY=2/2
worker1=0
worker2=2
```

## 20.2 Worker

```text
worker1 Ready
worker2 Ready
无 taint
均可调度
```

## 20.3 PDB

```text
DISRUPTIONS_ALLOWED=1
CURRENT_HEALTHY=2
DESIRED_HEALTHY=1
```

## 20.4 HPA

```text
CURRENT=2
DESIRED=2
```

## 20.5 Policy

```text
FastAPI-only scope = PASS
topology plugin only = PASS
local storage exception = PASS
PVC protection = PASS
PodsWithoutPDB protection = PASS
minReplicas=2 = PASS
nodeFit=true = PASS
max total eviction=1 = PASS
```

---

# 21. RBAC Gate 的一次假阴性

最开始检查：

```bash
kubectl auth can-i create pods/eviction ...
```

得到：

```text
OPSLAB_EVICTION_CREATE=no
```

看起来像 RBAC 退化。

但此前运行态 RBAC 已经验证过，因此没有直接“修权限”，而是检查测试命令。

正确检查 Pod subresource：

```bash
kubectl auth can-i \
  create pods \
  --subresource=eviction \
  -n opslab \
  --as=system:serviceaccount:opslab:opslab-descheduler
```

结果：

```text
OPSLAB_EVICTION_CREATE=yes
```

进一步：

```text
MONITORING_EVICTION_CREATE=no
KUBE_SYSTEM_EVICTION_CREATE=no
OPSLAB_DIRECT_POD_DELETE=no
```

最终：

```text
CANONICAL_RBAC_BOUNDARY=PASS
```

---

## 21.1 `set -e` 又制造了一次测试脚本早退

RBAC 检查脚本最初：

```bash
set -e
```

而：

```text
MONITORING_EVICTION_CREATE=no
```

本来是“安全边界正确”的期望结果。

但 `kubectl auth can-i` 在返回 `no` 时退出码为非 0，于是 `set -e` 把整个脚本提前终止。

因此这一轮又得到一个重要经验：

> 当“命令返回非 0”本身是预期测试结果时，不能让 `set -e` 把它自动解释成脚本失败。

后来使用：

```bash
... || true
```

或显式收集结果再断言。

---

# 22. 真实 standalone Job 的安全设计

最终真实 Job 不是直接改 CronJob，而是从父 CronJob 生成草案后做以下修正：

```text
ownerReferences = removed
cronjob instantiate annotation = removed

backoffLimit = 0
activeDeadlineSeconds = absent
ttlSecondsAfterFinished = absent

--dry-run=true = removed
--descheduling-interval = absent
--v=5
```

运行账户：

```text
ServiceAccount=opslab-descheduler
```

Policy：

```text
ConfigMap=opslab-descheduler
```

镜像：

```text
immutable digest
```

因为没有 `--descheduling-interval`，本实验把真实 Job限定为一次执行路径，而不是持续反复驱逐。

Server dry-run：

```text
REAL_JOB_SERVER_DRYRUN=PASS
```

并确认：

```text
REAL_JOB_ZERO_MUTATION=PASS
```

之后才真正允许创建。

---

# 23. 第一次真实 Descheduler：Job 成功，但 0 eviction

Job：

```text
opslab-descheduler-real-rebalance
```

执行前：

```text
worker1=0
worker2=2

PDB:
ALLOWED=1
CURRENT=2
DESIRED=1
```

Job：

```text
Complete 1/1
```

但是日志出现了新的、此前 dry-run 没有暴露的问题：

```text
Pod fails the following checks
checks="pod does not have a PodDisruptionBudget and is protected against eviction"
```

两个 FastAPI Pod 都被这样判定。

然后：

```text
evictedPods=0
totalEvicted=0
```

最终 60 次检查仍：

```text
worker1=0
worker2=2
READY=2
BALANCED=0
```

UID：

```text
两个旧 UID 全部保留
EVICTED_OR_REMOVED_UIDS=
REPLACEMENT_UIDS=
```

PDB 此时仍然明确存在：

```text
ALLOWED=1
CURRENT=2
DESIRED=1
```

因此这个失败非常关键：

> 不是因为没有 PDB。

---

# 24. RCA-3：Descheduler v0.36.0 `PodsWithoutPDB` 真实运行路径问题

排查过程对照到了 Descheduler 上游相关问题：

```text
Issue #1882
PR #1883
```

本次现场现象与该问题一致：

```text
Descheduler v0.36.0
PodsWithoutPDB protection enabled
真实 PDB 明确存在
但 real-mode DefaultEvictor 判定：
"pod does not have a PodDisruptionBudget"
```

排查结论是：

```text
RCA-3=CONFIRMED
```

在本次使用的 v0.36.0 行为下，real-mode 的 PDB informer / lister 路径导致 `PodsWithoutPDB` 保护得不到正确 PDB 视图。

结果：

```text
真实有 PDB
→ Descheduler 自己的“是否有 PDB”预过滤误判
→ 两个 FastAPI Pod 全部被保护
→ fail-safe
→ 0 eviction
```

### 为什么说它是 fail-safe failure

因为它没有错误驱逐 Pod。

实际表现是：

```text
自动重平衡目标失败
但业务未发生 mutation
```

这是一个安全方向的失败，而不是危险方向的失败。

---

# 25. 为什么不能直接删除 Kubernetes PDB

这里最容易做错的事情是：

> 既然 `PodsWithoutPDB` 有问题，就把 PDB 删掉。

这是错误方向。

真正的 PDB：

```text
opslab-api
```

是 Kubernetes API Server 对 Eviction API 的安全预算。

我们需要移除的只是：

```text
Descheduler v0.36.0 中有问题的 PodsWithoutPDB “预过滤保护”
```

而不是 Kubernetes 自己的 PDB。

这两个层次必须分开：

```text
Descheduler DefaultEvictor:
“我认为这个 Pod 有没有 PDB？”
        ↓
候选预过滤

Kubernetes API Server:
“当前这次 eviction 是否违反 PDB？”
        ↓
权威 disruption enforcement
```

workaround 的原则是：

> 移除当前版本错误的预过滤，不移除真正的 PDB。

---

# 26. v0.36.0 最小 workaround

Policy 从：

```yaml
podProtections:
  defaultDisabled:
    - PodsWithLocalStorage
  extraEnabled:
    - PodsWithPVC
    - PodsWithoutPDB
```

改为：

```yaml
podProtections:
  defaultDisabled:
    - PodsWithLocalStorage
  extraEnabled:
    - PodsWithPVC
```

仅移除：

```text
PodsWithoutPDB
```

同时明确保留：

```text
Kubernetes PDB
PodsWithPVC
minReplicas=2
nodeFit=true
FastAPI-only selector
maxNoOfPodsToEvictPerNode=1
maxNoOfPodsToEvictPerNamespace=1
maxNoOfPodsToEvictTotal=1
RBAC namespace boundary
direct Pod delete denied
parent CronJob suspended
```

Promotion 结果：

```text
PDB_BUG_WORKAROUND_CREATED=PASS
PDB_BUG_WORKAROUND_STATIC_AUDIT=PASS
PDB_BUG_WORKAROUND_SERVER_DRYRUN=PASS
PDB_BUG_WORKAROUND_PROMOTED=PASS
```

真实 PDB 仍：

```text
NAME=opslab-api
ALLOWED=1
CURRENT=2
DESIRED=1
```

FastAPI 仍：

```text
worker1=0
worker2=2
```

说明 workaround promotion 本身仍是零业务 mutation。

---

# 27. 第二次真实运行：最终成功

使用新 Job：

```text
opslab-descheduler-real-rebalance-pdb-workaround
```

没有覆盖第一次失败 Job。

这是为了保留一组非常有价值的 A/B：

```text
旧 Job:
PodsWithoutPDB enabled
→ false no-PDB
→ 0 eviction

新 Job:
只移除 broken pre-filter
→ real eviction
→ 1/1
```

---

## 27.1 最终 preflight

第二次真实运行前：

```text
WORKER1_FASTAPI=0
WORKER2_FASTAPI=2

PDB_ALLOWED=1
PDB_CURRENT=2
PDB_DESIRED=1

HPA_CURRENT=2
HPA_DESIRED=2

OPSLAB_EVICTION_CREATE=yes
```

结论：

```text
FINAL_WORKAROUND_REAL_RUN_PREFLIGHT=PASS
```

---

## 27.2 Job 安全属性

```text
WORKAROUND_REAL_JOB_OWNER=STANDALONE
WORKAROUND_REAL_JOB_DRY_RUN=DISABLED
WORKAROUND_REAL_JOB_INTERVAL=ONE_SHOT
WORKAROUND_REAL_JOB_BACKOFF_LIMIT=0
WORKAROUND_REAL_JOB_STATIC_AUDIT=PASS
```

Server dry-run：

```text
WORKAROUND_REAL_JOB_SERVER_DRYRUN=PASS
```

最后再次检查 PDB：

```text
ALLOWED=1
CURRENT=2
DESIRED=1
LAST_PDB_GATE=PASS
```

之后才真正：

```text
CREATE REAL WORKAROUND JOB
```

---

# 28. 最关键的真实 eviction 证据

Descheduler V5 日志：

```text
Processing namespaces for topology spread constraints
Processing namespace ... namespace="opslab"
```

候选评估：

```text
Pod fits on node ... k8s-worker1
```

control-plane：

```text
Pod does not fit on node ... k8s-control-plane
err="pod does not tolerate taints on the node"
```

随后出现整个阶段最关键的一行：

```text
Evicted pod
pod="opslab/opslab-api-797c8fdfbf-8bnmj"
node="k8s-worker2"
strategy="RemovePodsViolatingTopologySpreadConstraint"
profile="opslab-fastapi-rebalance"
```

计数：

```text
evictedPods=1
totalEvicted=1
```

这证明：

1. Descheduler 不只是“看到了不均衡”；
2. 它确实选择了 worker2 上的一个 FastAPI Pod；
3. 一次真实 eviction 已成功；
4. eviction limit 没有被突破。

---

# 29. Placement 从 0/2 自动恢复到 1/1

真实 eviction 之后立刻观测：

```text
BALANCE_CHECK=1 WORKER1=1 WORKER2=1 READY=1
BALANCE_CHECK=2 WORKER1=1 WORKER2=1 READY=1
BALANCE_CHECK=3 WORKER1=1 WORKER2=1 READY=2
BALANCED=1
```

最终：

```text
opslab-api-797c8fdfbf-r697x  1/1  Running  k8s-worker1
opslab-api-797c8fdfbf-zwmzk  1/1  Running  k8s-worker2
```

于是核心目标正式成立：

```text
AUTOMATIC_REBALANCE_0_2_TO_1_1=PASS
```

---

# 30. UID 证据：证明恰好替换了一个 Pod

执行前：

```text
c1034838-bab9-45b0-b886-8ca2d31718b8
5c41cccb-2174-4c5a-9d97-8201343949ec
```

执行后：

```text
SURVIVING_UIDS=
5c41cccb-2174-4c5a-9d97-8201343949ec

REMOVED_UIDS=
c1034838-bab9-45b0-b886-8ca2d31718b8

REPLACEMENT_UIDS=
dfdc752d-91fe-443b-9287-9336bfb22a4d
```

被移除：

```text
NAME=opslab-api-797c8fdfbf-8bnmj
NODE=k8s-worker2
```

新 Pod：

```text
NAME=opslab-api-797c8fdfbf-r697x
NODE=k8s-worker1
READY=True
```

最终：

```text
EXACT_ONE_REPLACEMENT=PASS
```

这一证据非常重要。

因为只看“最后 1/1”无法排除：

- 两个 Pod 都重建；
- HPA 改过副本数；
- Deployment 做过 rollout；
- 人工 delete 过 Pod。

UID 集合变化证明：

> 原 2 个 Pod 中恰好消失 1 个，新增恰好 1 个，并且新增 Pod 落到了 worker1。

---

# 31. Kubernetes Events 的时间链

事件进一步补齐完整链路：

```text
Descheduler Job Pod 被调度到 worker1
→ Job Pod 创建、启动

ReplicaSet 创建 replacement：
opslab-api-797c8fdfbf-r697x

replacement 被调度到：
k8s-worker1

旧 Pod：
opslab-api-797c8fdfbf-8bnmj
Killing

replacement container Created / Started

Descheduler Job Completed
```

这条事件链与 Descheduler 日志和 UID 差异互相印证。

---

# 32. Startup Probe 瞬时失败为什么没有判成回归失败

新 Pod 启动期间出现：

```text
Startup probe failed:
Get "http://10.244.1.76:8000/healthz":
dial tcp 10.244.1.76:8000:
connect: connection refused
```

它发生在 Pod 刚创建的短时间窗口。

后续实际结果：

```text
READY=2
replacement 1/1 Running
```

因此本次证据支持的结论是：

> 这是容器启动过程中一次瞬时 startup probe failure，随后启动成功；它没有演化成持续不可用。

不能把它删除或忽略，因为它是现场的一部分；但也不能仅凭一次 startup probe failure 把整个 rebalance 判成失败。

---

# 33. Event broadcaster 的非阻断错误

Descheduler 在成功 eviction 后日志还出现：

```text
Unable to write event (may retry after sleeping)
err="client rate limiter Wait returned an error: context canceled"
```

它出现在：

```text
Evicted pod
totalEvicted=1
```

之后，并且 Job 最终：

```text
Complete 1/1
```

因此本阶段只记录为：

```text
NON_BLOCKING_RUNTIME_LOG
```

没有把它继续扩展成新的 RCA。

原因：

- eviction 已经成功；
- placement 已经恢复；
- Job 正常完成；
- 当前没有证据表明它影响业务或 rebalance 成果。

---

# 34. Stateful workload 回归

这次 Descheduler 必须绝对不能误伤：

- MySQL local-PV StatefulSet
- Redis local-PV StatefulSet

执行前：

```text
opslab-mysql-0
UID=b4b31e01-b32c-4f6c-a5c8-1f9e88acca54
NODE=k8s-worker1

opslab-redis-0
UID=9cc7e201-7711-4edf-abef-30cca3830971
NODE=k8s-worker2
```

执行后完全一致：

```text
STATEFUL_WORKLOAD_UIDS_UNCHANGED=PASS
```

这证明：

- FastAPI selector 作用域有效；
- `PodsWithPVC` 保护没有被破坏；
- 没有发生跨工作负载误驱逐。

---

# 35. PDB / HPA 最终恢复

真实 rebalance 后：

```text
PDB:
ALLOWED=1
CURRENT=2
DESIRED=1
```

HPA：

```text
MIN=2
MAX=4
CURRENT=2
DESIRED=2
```

说明最终系统回到正常稳定基线：

- 两个健康副本；
- PDB disruption budget 恢复；
- HPA 没有被实验扰乱；
- 没有产生额外 replica。

---

# 36. 最终运行态 Policy

截至本次 inventory audit，运行态最终 Policy 为：

```yaml
apiVersion: descheduler/v1alpha2
kind: DeschedulerPolicy

evictionFailureEventNotification: true

maxNoOfPodsToEvictPerNamespace: 1
maxNoOfPodsToEvictPerNode: 1
maxNoOfPodsToEvictTotal: 1

profiles:
  - name: opslab-fastapi-rebalance

    pluginConfig:
      - name: DefaultEvictor
        args:
          labelSelector:
            matchLabels:
              app.kubernetes.io/instance: opslab
              app.kubernetes.io/name: opslab-api

          minReplicas: 2
          nodeFit: true

          podProtections:
            defaultDisabled:
              - PodsWithLocalStorage

            extraEnabled:
              - PodsWithPVC

      - name: RemovePodsViolatingTopologySpreadConstraint
        args:
          constraints:
            - DoNotSchedule

          labelSelector:
            matchLabels:
              app.kubernetes.io/instance: opslab
              app.kubernetes.io/name: opslab-api

          namespaces:
            include:
              - opslab

          topologyBalanceNodeFit: true

    plugins:
      balance:
        enabled:
          - RemovePodsViolatingTopologySpreadConstraint
```

## 36.1 这份 Policy 的工程含义

| 保护 / 约束 | 状态 | 目的 |
|---|---:|---|
| FastAPI labelSelector | 保留 | 不处理其他业务 |
| namespace include `opslab` | 保留 | 缩小范围 |
| `RemovePodsViolatingTopologySpreadConstraint` | 唯一 balance plugin | 不启用无关策略 |
| `maxNoOfPodsToEvictTotal=1` | 保留 | 单次总驱逐上限 |
| `maxNoOfPodsToEvictPerNode=1` | 保留 | 节点侧上限 |
| `maxNoOfPodsToEvictPerNamespace=1` | 保留 | namespace 上限 |
| `minReplicas=2` | 保留 | 避免低副本工作负载 |
| `nodeFit=true` | 保留 | 候选必须可在别处落地 |
| `PodsWithPVC` | 保留 | Stateful/PVC 安全 |
| `PodsWithLocalStorage` | 对匹配 FastAPI 放开 | FastAPI 使用 emptyDir，否则无法 rebalance |
| `PodsWithoutPDB` | v0.36.0 workaround：不启用 | 避开本次已验证的错误 PDB 预过滤 |
| Kubernetes PDB | **仍然存在** | 真正约束 Eviction API |

---

# 37. 当前运行态 inventory

## 37.1 Descheduler objects

```text
serviceaccount/opslab-descheduler

configmap/opslab-descheduler
configmap/opslab-descheduler-rca-localstorage

cronjob.batch/opslab-descheduler
```

父 CronJob：

```text
schedule=*/2 * * * *
suspend=true
```

截至 inventory 时仍存在以下 Jobs：

```text
opslab-descheduler-dryrun-drift
opslab-descheduler-dryrun-drift-rca
opslab-descheduler-dryrun-localstorage-regression
opslab-descheduler-dryrun-multicycle-rca
opslab-descheduler-real-rebalance
opslab-descheduler-real-rebalance-pdb-workaround
```

其中：

```text
opslab-descheduler-dryrun-multicycle-rca
```

为测试装置 DeadlineExceeded 失败证据。

两个最重要的真实 Job：

```text
opslab-descheduler-real-rebalance
```

代表：

```text
v0.36.0 PodsWithoutPDB 问题
→ fail-safe
→ 0 eviction
```

以及：

```text
opslab-descheduler-real-rebalance-pdb-workaround
```

代表：

```text
最小 workaround
→ Evicted pod
→ totalEvicted=1
→ 0/2 → 1/1
```

在文档和 Git 证据落盘之前，这两个 Job 应暂时保留。

---

# 38. 当前最终 FastAPI 状态

inventory audit：

```text
NAME                          UID                                    NODE          READY
opslab-api-797c8fdfbf-r697x   dfdc752d-91fe-443b-9287-9336bfb22a4d   k8s-worker1   true
opslab-api-797c8fdfbf-zwmzk   5c41cccb-2174-4c5a-9d97-8201343949ec   k8s-worker2   true
```

即：

```text
worker1=1
worker2=1
READY=2
```

PDB：

```text
ALLOWED=1
CURRENT=2
DESIRED=1
```

HPA：

```text
MIN=2
MAX=4
CURRENT=2
DESIRED=2
```

这就是本阶段最终健康基线。

---

# 39. 三个正式 RCA 总结

## RCA-1：dry-run first-cycle false negative

### 现象

```text
0/2 明确存在
one-shot dry-run
→ evictedPods=0
```

### 最关键证据

多周期 standalone dry-run：

```text
first cycle:
requires >=2 nodes
found=0

later cycle:
Processing namespace=opslab
Evicted pod in dry run mode
evictedPods=1
```

### 根因

dry-run sandbox 第一轮 node view 尚未准备好。

### 解决

- 不用首轮 one-shot dry-run 的零 eviction 直接否定策略；
- 多周期 diagnostic 观察后续 cycle；
- 真实运行另外单独验证。

### 工程教训

> dry-run 不是现实世界的完美复制。测试模式本身也可能有初始化时序。

---

## RCA-2：FastAPI emptyDir 被 local-storage protection 拦截

### 现象

稳定 cycle 仍：

```text
evictedPods=0
```

### 证据

原 Policy V5：

```text
pod has local storage and is protected against eviction
```

两个 FastAPI Pod 都被 Filter。

### 根因

FastAPI Pod 存在：

```text
emptyDir
sizeLimit=64Mi
```

DefaultEvictor 的 `PodsWithLocalStorage` 保护生效。

### A/B

Protection ON：

```text
evictedPods=0
```

Protection OFF：

```text
Evicted pod in dry run mode
evictedPods=1
```

### 最小修复

```yaml
defaultDisabled:
  - PodsWithLocalStorage
```

但仍通过 labelSelector 把作用域限制到 FastAPI。

---

## RCA-3：v0.36.0 PodsWithoutPDB 真实运行误判

### 现象

PDB 真实状态：

```text
ALLOWED=1
CURRENT=2
DESIRED=1
```

真实 Descheduler：

```text
pod does not have a PodDisruptionBudget
```

### 结果

```text
evictedPods=0
totalEvicted=0
```

### 排查结论

本次对照 Descheduler 上游相关 issue / PR 后，确认与 v0.36.0 真实运行路径的 PDB informer/lister 问题一致。

### 最小 workaround

从：

```yaml
extraEnabled:
  - PodsWithPVC
  - PodsWithoutPDB
```

改为：

```yaml
extraEnabled:
  - PodsWithPVC
```

**Kubernetes PDB 不删除。**

### 回归

workaround 后：

```text
Evicted pod
evictedPods=1
totalEvicted=1
```

并最终：

```text
0/2 → 1/1
```

### 工程教训

> 组件自己实现的“安全预过滤”与 Kubernetes API Server 的权威安全约束不是同一层。
> 遇到版本 bug 时，应尽量移除有问题的额外层，而不是破坏底层真正的安全机制。

---

# 40. 被排除或修正过的错误方向

这一段很重要，因为排错价值不仅在“最后找到什么”，还在“哪些方向被证据否定”。

| 假设 / 操作方向 | 最终结论 | 为什么 |
|---|---|---|
| Descheduler 不支持 TSC drift | 排除 | 后续 cycle 和最终 real run 都成功识别 |
| namespace selector 错 | 排除 | 运行态匹配 2 Pods |
| FastAPI labelSelector 错 | 排除 | selector 精确找到两个 Pod |
| worker1 不可调度 | 排除 | nodeFit 日志明确 `Pod fits on node worker1` |
| control-plane 也应参与 spread | 排除 | FastAPI 不容忍 control-plane taint |
| ReplicaSet 副本数不足 | 排除 | desired/current/ready=2 |
| PDB 不存在 | 排除 | API 中 PDB 明确存在，状态正常 |
| 安装 Eviction CRD | 错误方向 | Eviction 是 Pod subresource |
| local storage 与问题完全无关 | 过早结论 | 初次 A/B 被 first-cycle sandbox 问题污染 |
| RBAC 真坏了 | 排除 | canonical subresource check = yes |
| 看到 `auth can-i no` 就补权限 | 未执行 | 先确认检查命令本身 |
| 第一次 real Job Complete 就算成功 | 错误 | Job Complete 但 0 eviction、0/2 未恢复 |
| 删除 Kubernetes PDB 绕过问题 | 未执行 | 会破坏真正的 disruption safety |
| 为了修 v0.36.0 直接 fork 打 patch | 未采用 | 当前项目用最小、可审计 workaround 更合适 |

---

# 41. 为什么这一阶段的失败反而很有 SRE 价值

如果这一阶段一次成功，最终只能写：

```text
部署 Descheduler
→ 发现 drift
→ 自动恢复
```

但实际过程形成了更完整的能力证明：

### 41.1 能区分“业务失败”和“测试装置失败”

`DeadlineExceeded` 那一轮没有误判成 Descheduler 功能失败。

---

### 41.2 能避免在证据不足时提前下结论

local-storage 第一次 A/B 没起效时，没有立即否定 emptyDir 假设。

---

### 41.3 会做稳定 A/B

变量被控制为：

```text
PodsWithLocalStorage ON / OFF
```

最终得到直接 Filter 日志。

---

### 41.4 知道真实 mutation 前做安全门禁

每次真实运行前都检查：

```text
0/2 输入
Worker eligibility
PDB
HPA
Policy
RBAC
CronJob suspended
immutable image
one-shot
backoffLimit=0
```

---

### 41.5 会保留失败证据

第一次真实 Job 没有被删除，而是与成功 workaround Job 配成 A/B 现场证据。

---

### 41.6 不把“Job Complete”等价为“功能 PASS”

第一次真实 Job：

```text
Complete 1/1
```

但功能判定仍然：

```text
FAIL / BLOCKED
```

因为：

```text
totalEvicted=0
BALANCED=0
```

这是非常典型的 SRE 验收思维：

> 控制器进程成功退出，不代表系统目标达成。

---

# 42. 最终 PASS 判定矩阵

| 验证项 | 结果 |
|---|---|
| Descheduler immutable image | PASS |
| Descheduler Policy 加载 | PASS |
| Topology plugin 执行 | PASS |
| 平衡状态 baseline 零 mutation | PASS |
| 受控制造 0/2 drift | PASS |
| dry-run first-cycle RCA | PASS |
| local-storage protection RCA | PASS |
| standalone diagnostic harness 修复 | PASS |
| RBAC namespace boundary | PASS |
| direct Pod delete denied | PASS |
| PDB pre-run safety | PASS |
| HPA stable-at-two | PASS |
| first real-run fail-safe | PASS（作为安全行为） |
| PodsWithoutPDB version blocker RCA | PASS |
| workaround promotion | PASS |
| real eviction exactly 1 Pod | PASS |
| worker1 0 → 1 | PASS |
| worker2 2 → 1 | PASS |
| replacement Ready | PASS |
| exactly one FastAPI UID replaced | PASS |
| MySQL UID unchanged | PASS |
| Redis UID unchanged | PASS |
| PDB recovered | PASS |
| HPA recovered | PASS |
| parent CronJob still suspended | PASS |
| Git runtime experiment left repo clean | PASS |
| final Descheduler manifests committed to repo | **PENDING** |
| RCA temp resources cleanup | **PENDING** |
| validation document committed | **PENDING** |

综合功能结论：

```text
AUTOMATIC_REBALANCE_ENHANCEMENT=PASS
```

工程收尾状态：

```text
RUNTIME_VALIDATION=PASS
GIT_MANIFEST_SEAL=PENDING
RCA_RESOURCE_CLEANUP=PENDING
DOCUMENT_COMMIT=PENDING
```

---

# 43. 当前还没有完成的收尾事项

根据最终 inventory，以下内容不能写成已经完成。

## 43.1 正式 Descheduler manifests 尚未进入 Git

仓库还没有：

```text
kubernetes/descheduler/
```

建议最终形成类似：

```text
kubernetes/descheduler/
├── 01-serviceaccount.yaml
├── 02-rbac.yaml
├── 03-policy-configmap.yaml
└── 04-cronjob.yaml
```

这里必须以**已经验证通过的最终运行态配置**反向生成，不应再重新设计一套不同版本。

---

## 43.2 临时 RCA ConfigMap 尚未清理

仍存在：

```text
configmap/opslab-descheduler-rca-localstorage
```

在文档和最终配置落盘后，可以清理。

---

## 43.3 多个 diagnostic Job 尚在集群中

仍存在：

```text
opslab-descheduler-dryrun-drift
opslab-descheduler-dryrun-drift-rca
opslab-descheduler-dryrun-localstorage-regression
opslab-descheduler-dryrun-multicycle-rca
opslab-descheduler-real-rebalance
opslab-descheduler-real-rebalance-pdb-workaround
```

建议：

- 先保存必要 YAML / logs；
- 文档落盘；
- Git 完成；
- 再清理临时 diagnostic Jobs。

尤其两个 real Job 在清理前应至少保存：

```text
Job YAML
Pod logs
关键事件
```

---

# 44. 建议进入 Git 的正式配置说明

## 44.1 ServiceAccount

应保存当前已经使用并验证过的：

```text
opslab-descheduler
```

---

## 44.2 RBAC

应保存：

```text
ClusterRole/opslab-descheduler-observer
ClusterRoleBinding/opslab-descheduler-observer
Role/opslab-descheduler-evict
RoleBinding/opslab-descheduler-evict
```

重点保证：

```text
opslab eviction = yes
monitoring eviction = no
kube-system eviction = no
direct delete = no
```

---

## 44.3 Policy ConfigMap

应保存本文第 36 节的**最终运行态 Policy**。

并在注释或 validation 文档中明确：

```text
PodsWithoutPDB 未启用
不是因为不需要 PDB
而是 Descheduler v0.36.0 本次已验证的版本行为 workaround。

Kubernetes PDB 本身继续存在。
```

避免未来维护者误删 PDB。

---

## 44.4 CronJob

目前运行态：

```text
schedule=*/2 * * * *
suspend=true
--dry-run=true
```

在当前阶段收尾时应保持和运行态一致。

是否未来把它切换成：

```text
suspend=false
dry-run=false
```

属于另一个明确的上线决策，不应因为本次 one-shot real validation 成功就自动打开周期真实驱逐。

这份报告不把“定时生产自动 eviction 已启用”写成事实。

---

# 45. 简历 / 答辩中真正值得讲的点

可以概括为：

> 在 kubeadm 三节点集群中，为双副本 FastAPI 构建基于 TopologySpreadConstraints + Descheduler 的运行后自动放置修复机制；通过 namespace-scoped Eviction RBAC、PDB、单次 eviction limit、minReplicas、nodeFit 和 PVC 保护控制风险。构造 `0/2` 放置漂移后完成 dry-run、真实 eviction 与 UID 级回归，最终自动恢复 `1/1`。过程中定位并验证 Descheduler v0.36.0 dry-run 首轮 sandbox 时序、emptyDir local-storage 默认保护以及 PodsWithoutPDB real-mode PDB 识别问题，并通过最小 workaround 完成真实闭环。

如果答辩被问：

### “为什么有 TSC 还需要 Descheduler？”

答：

> TSC 主要在新 Pod 调度时约束放置；已经存在的 Pod 不会因为节点恢复或历史调度状态不均衡就自动迁移。Descheduler 负责识别运行后违反目标拓扑的 Pod，并通过 Eviction API 触发控制器补副本，再让 Scheduler + TSC 完成重新放置。

### “为什么不用 delete pod？”

答：

> 直接 delete 会绕开 Eviction API / PDB 这一层 voluntary disruption 约束。本项目给 Descheduler 的 RBAC 只允许创建 `pods/eviction`，不允许直接 delete pods。

### “为什么关闭 PodsWithLocalStorage？”

答：

> FastAPI 有 `emptyDir`，Descheduler 默认将它视为 local-storage Pod 并保护。通过 V5 日志和 ON/OFF A/B 证明这是实际 blocker。由于 selector 只匹配 FastAPI，且它的 emptyDir 只是临时目录，因此针对该工作负载放开这层保护，同时继续保护 PVC 工作负载。

### “为什么又关闭 PodsWithoutPDB？是不是不重视 PDB？”

答：

> 恰恰相反。Kubernetes PDB 一直保留。关闭的是 Descheduler v0.36.0 的额外 no-PDB 预过滤，因为真实运行中它把已有 PDB 的 FastAPI 错误判成 no PDB。真正的 Eviction API 仍然由 Kubernetes PDB 约束。workaround 前后做了真实 A/B，第一次 0 eviction，workaround 后 exactly one eviction 并恢复 1/1。

---

# 46. 本阶段最重要的工程方法论总结

## 46.1 每个“PASS”必须对应系统目标，而不是命令退出码

```text
Job Complete ≠ Rebalance PASS
```

真正 PASS 是：

```text
Evicted pod
+
totalEvicted=1
+
exactly one UID replaced
+
worker1=1 worker2=1
+
READY=2
+
PDB/HPA recovered
+
StatefulSet unchanged
```

---

## 46.2 排错时不要同时改多个变量

local-storage RCA 最终之所以可信，是因为做了稳定 A/B。

---

## 46.3 测试工具也可能有 bug

本阶段至少有三次“不是业务配置本身”的问题：

```text
dry-run sandbox first cycle
activeDeadlineSeconds / CronJob ownership
kubectl auth can-i subresource syntax + set -e
```

如果不知道先验证测试装置，很容易把问题归错层。

---

## 46.4 安全保护不是越多越好，而是要“可证明地正确”

最初：

```text
PodsWithPVC
PodsWithoutPDB
PodsWithLocalStorage
minReplicas
nodeFit
PDB
RBAC
eviction limit
```

看起来非常安全。

但实际：

- `PodsWithLocalStorage` 与 FastAPI emptyDir 的目标冲突；
- `PodsWithoutPDB` 在 v0.36.0 真实运行路径中发生误判。

最终安全设计不是“删除安全”，而是：

> 去掉已经被证据证明有问题或不适合当前工作负载的一层，同时保留 Kubernetes 原生的权威安全边界。

---

# 47. 最终结论

本阶段最终证明了以下真实链路：

```text
初始健康：
worker1=1
worker2=1

受控制造历史放置漂移：
worker1=0
worker2=2

Descheduler：
识别 TopologySpreadConstraint violation

DefaultEvictor：
候选匹配 FastAPI
nodeFit 验证 worker1 可落地

RBAC：
只允许 opslab pods/eviction

PDB：
真实 Kubernetes disruption budget 保持有效

Eviction：
只驱逐 worker2 上 1 个 FastAPI Pod

Deployment：
自动补 1 个 replacement

Scheduler + TSC：
replacement 调度到 worker1

最终：
worker1=1
worker2=1
READY=2

UID：
exactly one replacement

MySQL / Redis：
UID 不变

PDB / HPA：
恢复正常
```

最终判定：

```text
REAL_WORKAROUND_DESCHEDULER_JOB=PASS
AUTOMATIC_REBALANCE_0_2_TO_1_1=PASS
EXACT_ONE_REPLACEMENT=PASS
STATEFUL_WORKLOAD_UIDS_UNCHANGED=PASS

AUTOMATIC_REBALANCE_ENHANCEMENT=PASS
```

同时必须保留一个现实工程结论：

```text
运行态功能验证 = 已完成
最终 manifests Git 落盘 = 待完成
临时 RCA 资源清理 = 待完成
本报告 commit/push = 待完成
```

---

# 48. 建议的阶段封存语句

后续 Git 和 validation 全部落盘后，可以用下面的语义封闭本阶段：

```text
Automatic Rebalance Enhancement sealed.

Validated:
- topology-spread-aware runtime rebalance
- namespace-scoped eviction RBAC
- PDB-backed voluntary disruption
- one-pod eviction ceiling
- FastAPI-only candidate scope
- PVC workload protection
- 0/2 placement drift → 1/1 automatic recovery
- exactly one FastAPI Pod replacement
- MySQL / Redis zero mutation

RCA:
- Descheduler v0.36.0 dry-run first-cycle sandbox false negative
- emptyDir blocked by PodsWithLocalStorage protection
- v0.36.0 PodsWithoutPDB real-mode PDB recognition blocker

Final result:
AUTOMATIC_REBALANCE_ENHANCEMENT=PASS
```

---

## 附录 A：最重要的最终运行态数据

```text
FastAPI:
opslab-api-797c8fdfbf-r697x
UID=dfdc752d-91fe-443b-9287-9336bfb22a4d
NODE=k8s-worker1
READY=true

opslab-api-797c8fdfbf-zwmzk
UID=5c41cccb-2174-4c5a-9d97-8201343949ec
NODE=k8s-worker2
READY=true
```

```text
PDB:
ALLOWED=1
CURRENT=2
DESIRED=1
```

```text
HPA:
MIN=2
MAX=4
CURRENT=2
DESIRED=2
```

```text
Parent CronJob:
SUSPEND=true
SCHEDULE=*/2 * * * *
ARGS=["--policy-config-file=/policy-dir/policy.yaml","--dry-run=true","--v=3"]
```

```text
Git:
## master...origin/master

HEAD:
cf79147 build(descheduler): add v0.36.0 mirror context
```

---

## 附录 B：当前仍需保留 / 后续再清理的证据对象

```text
configmap/opslab-descheduler-rca-localstorage

job/opslab-descheduler-dryrun-drift
job/opslab-descheduler-dryrun-drift-rca
job/opslab-descheduler-dryrun-localstorage-regression
job/opslab-descheduler-dryrun-multicycle-rca
job/opslab-descheduler-real-rebalance
job/opslab-descheduler-real-rebalance-pdb-workaround
```

建议先完成：

```text
正式 manifests 落盘
→ validation / RCA 文档落盘
→ 保存两次真实 Job YAML / logs
→ Git diff 审计
→ commit/push
```

再清理这些运行态实验对象。

---

## 附录 C：本报告使用的证据类型

本报告中的结论来自本阶段实际输出，主要包括：

- `kubectl get` 的 Pod / Deployment / HPA / PDB / Node / RBAC 状态；
- Descheduler v0.36.0 V3 / V5 运行日志；
- dry-run fake sandbox 日志；
- Job describe / Event；
- FastAPI UID before/after；
- MySQL / Redis StatefulSet UID before/after；
- runtime ConfigMap policy；
- parent CronJob 运行态；
- Git status / log；
- 对 Descheduler 上游 issue / PR 的排查对照。

没有把尚未执行的 Git manifests 落盘、临时资源清理或 commit/push 描述为已完成。
