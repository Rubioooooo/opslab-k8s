# Prometheus Local PV 持久化验证报告

## 1. 验证目标

本次验证的目标，是确认 OpsLab 项目中的 Prometheus TSDB 已经不再依赖 Prometheus Pod 的生命周期，而是迁移到独立的持久化存储中。

需要证明以下目标成立：

- Prometheus 使用独立 PersistentVolume 持久化保存 TSDB 数据。
- Prometheus 数据盘与现有 MySQL、Redis 数据盘相互独立。
- Prometheus Pod 被删除并重新创建后，PVC / PV 仍保持绑定。
- Prometheus Pod 重建后，删除前采集的历史时间序列数据仍然可以查询。
- 持久化配置由 Helm → Prometheus CR → Prometheus Operator → StatefulSet/PVC 的标准控制链管理，而不是手工修改 Operator 生成的 StatefulSet。

最终验证目标不是简单得到：

```text
PVC Bound
```

而是完整证明：

```text
Pod 删除
    ↓
Pod 自动重建
    ↓
重新挂载同一 PVC
    ↓
删除前历史时间序列仍然存在
```

---

## 2. 环境信息

### Kubernetes

- Kubernetes：`v1.36.3`
- Prometheus Namespace：`monitoring`

### Helm

- Release：`opslab-monitoring`
- Chart：`kube-prometheus-stack-88.2.0`
- Helm 升级后 Revision：`3`
- 状态：`deployed`

### Prometheus

- Prometheus CR：`opslab-monitoring-kube-pro-prometheus`
- Prometheus 镜像：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/monitoring-prometheus:v3.13.2-distroless
```

- 副本数：`1`
- TSDB Retention：`3d`

Prometheus Pod SecurityContext：

```yaml
fsGroup: 2000
runAsGroup: 2000
runAsNonRoot: true
runAsUser: 1000
seccompProfile:
  type: RuntimeDefault
```

Prometheus 容器 SecurityContext：

```yaml
allowPrivilegeEscalation: false
capabilities:
  drop:
    - ALL
readOnlyRootFilesystem: true
```

---

## 3. 改造前状态

持久化改造前，Prometheus CR 中没有配置存储：

```text
spec.storage = empty
```

Prometheus StatefulSet 的数据库卷实际使用：

```text
emptyDir
```

也就是说：

```text
Prometheus Pod
    ↓
emptyDir
    ↓
本地临时 TSDB
```

这种设计能够满足基础监控功能，但存在一个明显问题：

> Prometheus Pod 一旦被删除、重建或迁移，其 Pod 生命周期对应的 `emptyDir` 数据就会丢失。

因此，这种状态不适合作为完整 SRE 项目的最终监控存储方案。

---

## 4. Prometheus 独立磁盘设计

为了避免与 MySQL、Redis 数据混用，本次为 Prometheus 单独增加了一块 VMware 虚拟磁盘。

### worker2 原有磁盘

```text
/dev/sda
→ 60G
→ Kubernetes / Ubuntu 系统盘

/dev/sdb1
→ 20G
→ XFS
→ /data/redis
→ Redis Local PV
```

### 新增 Prometheus 数据盘

新增：

```text
/dev/sdc
→ 30G
```

最终划分：

```text
/dev/sdc1
```

文件系统：

```text
XFS
```

Label：

```text
prom-data
```

UUID：

```text
e4ac233b-864b-4c06-b599-9dd26f214786
```

挂载目录：

```text
/data/prometheus
```

实际验证结果：

```text
NAME   FSTYPE FSVER LABEL     UUID                                 FSAVAIL FSUSE% MOUNTPOINTS
sdc
└─sdc1 xfs          prom-data e4ac233b-864b-4c06-b599-9dd26f214786   29.3G     2% /data/prometheus
```

写入测试结果：

```text
PROMETHEUS_DISK_WRITE=PASS
```

因此可以确认：

> `/data/prometheus` 已经成功建立在新的独立 XFS 数据盘上。

---

## 5. `/etc/fstab` 持久挂载

为了保证 worker2 重启后 Prometheus 数据盘能够自动重新挂载，使用 UUID 写入 `/etc/fstab`：

```text
UUID=e4ac233b-864b-4c06-b599-9dd26f214786 /data/prometheus xfs defaults,nofail 0 2
```

之后执行：

```bash
sudo systemctl daemon-reload
sudo mount -a
```

并通过：

```bash
findmnt /data/prometheus
df -hT /data/prometheus
lsblk -f /dev/sdc
```

确认挂载正常。

这里没有依赖 `/dev/sdc1` 这种可能变化的设备名称，而是使用 UUID 建立持久挂载关系。

---

## 6. Prometheus 运行身份与目录权限验证

Prometheus 实际运行身份为：

```text
UID = 1000
GID = 2000
fsGroup = 2000
```

而新建 `/data/prometheus` 后，目录初始状态为：

```text
owner=root:root
uid=0
gid=0
mode=755
```

这个状态下，UID 1000 的 Prometheus 进程无法直接写入目录。

因此目录权限调整为：

```text
owner = root
group = 2000
mode = 2770
```

对应操作：

```bash
sudo chown root:2000 /data/prometheus
sudo chmod 2770 /data/prometheus
```

没有使用：

```bash
chmod 777
```

而是根据 Prometheus 的实际运行组建立最小必要写权限。

其中 `2` 表示设置 setgid：

```text
2770
```

这样目录中新创建的文件和子目录可以继续继承 GID 2000。

### 使用真实 UID/GID 验证写权限

使用：

```text
UID 1000
GID 2000
```

模拟 Prometheus 实际运行身份进行文件和目录创建。

验证结果：

```text
PROMETHEUS_UID_GID_WRITE=PASS
```

因此可以确认：

> Prometheus 可以在不开放 world-writable 权限的前提下正常写入持久化目录。

---

## 7. StorageClass

项目此前已经建立 StorageClass：

```text
local-storage
```

配置：

```yaml
provisioner: kubernetes.io/no-provisioner
reclaimPolicy: Retain
volumeBindingMode: WaitForFirstConsumer
```

其设计含义为：

### `kubernetes.io/no-provisioner`

表示没有动态 provisioner。

因此 Local PV 必须由管理员提前创建。

### `Retain`

PVC 删除后，不自动删除底层存储数据。

对于 Prometheus 监控历史数据而言，这比自动清理更加安全。

### `WaitForFirstConsumer`

PV 不会仅仅因为创建 PVC 就立即完成调度决策，而是结合实际 Pod 调度约束进行绑定。

这对于带有 NodeAffinity 的 Local PV 非常重要。

---

## 8. Prometheus Local PV

创建的 PV：

```text
opslab-prometheus-local-pv
```

核心配置：

```yaml
spec:
  capacity:
    storage: 28Gi

  volumeMode: Filesystem

  accessModes:
    - ReadWriteOnce

  persistentVolumeReclaimPolicy: Retain

  storageClassName: local-storage

  local:
    path: /data/prometheus

  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values:
                - k8s-worker2
```

PV Label：

```text
app.kubernetes.io/name=opslab-prometheus-storage
app.kubernetes.io/part-of=opslab-k8s
```

PV 创建后初始状态：

```text
opslab-prometheus-local-pv   28Gi   RWO   Retain   Available   local-storage
```

这个阶段出现：

```text
Available
```

是正常状态。

因为 Prometheus Operator 此时还没有创建对应 PVC。

---

## 9. 为什么 PV 声明 28Gi，而磁盘是 30Gi

底层 VMware 虚拟磁盘为：

```text
30 GiB
```

格式化为 XFS 后实际可用空间约：

```text
29.3 GiB
```

因此 Kubernetes PV 没有机械声明为完整 30Gi，而是定义为：

```text
28Gi
```

这样可以为文件系统自身和后续运行留出一定余量。

设计关系：

```text
物理虚拟磁盘：30Gi
        ↓
XFS 实际可用：约 29.3Gi
        ↓
Kubernetes PV：28Gi
```

---

## 10. Helm 配置基线确认

项目中的 kube-prometheus-stack values 文件：

```text
kubernetes/monitoring/values.yaml
```

当前 Helm Release 中保存的 values 也进行了备份：

```text
~/.config/opslab/helm-backups/
```

随后使用 YAML 语义比较，而不是单纯使用文本 `diff`。

最终结果：

```text
VALUES_SEMANTIC_MATCH=PASS
```

这说明：

> Git 仓库中的 `kubernetes/monitoring/values.yaml` 与 Helm Revision 2 实际使用的 user-supplied values 在语义上完全一致。

因此可以安全地继续以 Git 中的 values 文件作为长期配置入口。

---

## 11. 固定使用原 Chart 版本

本地已经保存原始 Chart：

```text
/home/rubio/downloads/kube-prometheus-stack-88.2.0.tgz
```

验证：

```text
name: kube-prometheus-stack
version: 88.2.0
appVersion: v0.93.0
```

因此本次持久化变更没有：

- 升级 Helm Chart；
- 更换 kube-prometheus-stack 版本；
- 添加新的 Helm Repo；
- 引入新的 Prometheus Operator 版本。

这样保证本次变更变量只有：

```text
Prometheus Storage
```

避免一次修改中同时引入版本升级风险。

---

## 12. Helm `storageSpec` 配置

在：

```text
kubernetes/monitoring/values.yaml
```

中的：

```yaml
prometheus:
  prometheusSpec:
```

下面增加：

```yaml
storageSpec:
  volumeClaimTemplate:
    spec:
      storageClassName: local-storage

      selector:
        matchLabels:
          app.kubernetes.io/name: opslab-prometheus-storage

      accessModes:
        - ReadWriteOnce

      resources:
        requests:
          storage: 28Gi
```

其中 selector：

```yaml
selector:
  matchLabels:
    app.kubernetes.io/name: opslab-prometheus-storage
```

会明确要求 PVC 绑定带有对应 Label 的 PV。

因此不会误绑定到：

- MySQL Local PV；
- Redis Local PV；
- 其他未来可能存在的 Local PV。

---

## 13. 正确的 Operator 控制链

Prometheus 的持久化没有通过直接修改 StatefulSet 实现。

实际控制链为：

```text
kubernetes/monitoring/values.yaml
                ↓
Helm
                ↓
prometheus.prometheusSpec.storageSpec
                ↓
Prometheus CR
spec.storage
                ↓
Prometheus Operator
                ↓
StatefulSet volumeClaimTemplate
                ↓
PVC
                ↓
Local PV
                ↓
worker2:/data/prometheus
```

这是本次设计中非常重要的一点。

如果直接修改：

```text
StatefulSet
```

Prometheus Operator 下一次 reconcile 时可能会把手工修改覆盖。

因此：

> Operator 管理的资源应该尽量通过 CR / Helm values 修改，而不是直接修改 Operator 生成物。

---

## 14. Helm 渲染验证

正式升级前，通过固定版本 Chart 进行离线渲染。

验证结果：

```text
PROMETHEUS_CR_RENDER=PASS
```

渲染后的 Prometheus CR：

```text
name = opslab-monitoring-kube-pro-prometheus
replicas = 1
retention = 3d
```

镜像：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/monitoring-prometheus:v3.13.2-distroless
```

资源限制保持：

```yaml
limits:
  cpu: "1"
  memory: 1Gi

requests:
  cpu: 200m
  memory: 512Mi
```

最终生成的 Storage：

```text
{'volumeClaimTemplate':
    {'spec':
        {'accessModes': ['ReadWriteOnce'],
         'resources':
             {'requests':
                 {'storage': '28Gi'}},
         'selector':
             {'matchLabels':
                 {'app.kubernetes.io/name':
                     'opslab-prometheus-storage'}},
         'storageClassName':
             'local-storage'}}}
```

证明：

```text
values.yaml 中 storageSpec
            ↓
正确转换为
            ↓
Prometheus CR spec.storage
```

---

## 15. Helm Upgrade Dry Run

正式变更前执行 Helm Upgrade Dry Run。

验证结果：

```text
HELM_UPGRADE_DRY_RUN=PASS
```

说明：

- Chart 可以正常解析；
- values 可以正常解析；
- 渲染后的 Kubernetes Resource 合法；
- `storageSpec` 不会阻塞 Helm Upgrade。

---

## 16. 正式 Helm Upgrade

正式执行：

```bash
helm upgrade \
  opslab-monitoring \
  /home/rubio/downloads/kube-prometheus-stack-88.2.0.tgz \
  -n monitoring \
  -f kubernetes/monitoring/values.yaml \
  --wait \
  --timeout 10m
```

结果：

```text
NAME              NAMESPACE   REVISION   STATUS     CHART
opslab-monitoring monitoring  3          deployed   kube-prometheus-stack-88.2.0
```

因此：

```text
Helm Revision 2
        ↓
Helm Revision 3
```

升级成功。

---

## 17. PV / PVC 绑定验证

升级完成后：

### PersistentVolume

```text
NAME                         CAPACITY ACCESS MODES RECLAIM POLICY STATUS STORAGECLASS
opslab-prometheus-local-pv   28Gi     RWO          Retain         Bound  local-storage
```

PV 最终绑定：

```text
monitoring/prometheus-opslab-monitoring-kube-pro-prometheus-db-prometheus-opslab-monitoring-kube-pro-prometheus-0
```

### PersistentVolumeClaim

Operator 自动创建：

```text
prometheus-opslab-monitoring-kube-pro-prometheus-db-prometheus-opslab-monitoring-kube-pro-prometheus-0
```

状态：

```text
STATUS: Bound
VOLUME: opslab-prometheus-local-pv
CAPACITY: 28Gi
ACCESS MODES: RWO
STORAGECLASS: local-storage
```

最终关系：

```text
Prometheus PVC
        ↓
opslab-prometheus-local-pv
        ↓
worker2
        ↓
/data/prometheus
```

---

## 18. Prometheus Pod 调度验证

Prometheus Pod：

```text
prometheus-opslab-monitoring-kube-pro-prometheus-0
```

升级后状态：

```text
2/2 Running
```

节点：

```text
k8s-worker2
```

示例：

```text
prometheus-opslab-monitoring-kube-pro-prometheus-0
2/2
Running
10.244.2.61
k8s-worker2
```

这与 PV 中：

```yaml
nodeAffinity:
  kubernetes.io/hostname:
    - k8s-worker2
```

保持一致。

---

## 19. `/prometheus` 已从 emptyDir 切换为 PVC

升级后的 Prometheus Pod Volume：

```text
volume=prometheus-opslab-monitoring-kube-pro-prometheus-db
pvc=prometheus-opslab-monitoring-kube-pro-prometheus-db-prometheus-opslab-monitoring-kube-pro-prometheus-0
```

说明 Prometheus DB Volume 已经引用 PVC。

StatefulSet 的 VolumeClaimTemplate：

```text
claimTemplate=prometheus-opslab-monitoring-kube-pro-prometheus-db
storageClass=local-storage
storage=28Gi
```

因此原来的：

```text
emptyDir
```

已经替换为：

```text
PersistentVolumeClaim
```

---

## 20. 物理 TSDB 写入验证

在 worker2 上检查：

```bash
sudo du -sh /data/prometheus
sudo find /data/prometheus -maxdepth 2 -type f | head
```

观察到：

```text
4.0M /data/prometheus
```

并出现：

```text
/data/prometheus/prometheus-db/queries.active
/data/prometheus/prometheus-db/lock
```

这说明：

> Prometheus 已经真正开始向 `/data/prometheus` 所在独立磁盘写入 TSDB 数据。

并不是只完成了 Kubernetes PV/PVC 对象层面的配置。

---

# 21. Pod 重建前历史数据基线

真正验证 Prometheus 持久化时，不能只看：

```text
PVC Bound
```

因为 PVC Bound 只能证明卷绑定成功。

因此本次验证在 Prometheus 使用新 PVC 正常运行一段时间后，记录一个固定历史时间窗口。

测试指标选择：

```promql
up{job="opslab-redis-exporter"}
```

选择该指标的原因：

- Redis Exporter 已经完成独立监控验收；
- Target 长期处于 UP；
- 指标稳定存在；
- 非临时测试指标；
- 能够作为稳定历史时间序列样本。

测试前查询固定时间范围。

结果：

```text
PRE_RESTART_HISTORY=PASS
```

这说明：

> Prometheus Pod 删除前，固定时间窗口中确实已经存在可查询历史样本。

这一点非常重要。

如果删除前根本没有样本，那么删除后即使查不到数据，也无法判断究竟是持久化失败还是测试数据本来就不存在。

---

# 22. 主动删除 Prometheus Pod

随后主动执行：

```bash
kubectl delete pod \
  prometheus-opslab-monitoring-kube-pro-prometheus-0 \
  -n monitoring
```

这个操作模拟：

```text
Prometheus Pod 故障
/
Pod 被 Kubernetes 重建
/
Operator / StatefulSet 重新创建实例
```

因为 Prometheus 是 StatefulSet / Operator 管理的，所以 Pod 删除后 Kubernetes 会自动重新创建。

---

## 23. Prometheus 自愈结果

Pod 重建后：

```text
prometheus-opslab-monitoring-kube-pro-prometheus-0
2/2 Running
```

新 Pod IP：

```text
10.244.2.62
```

节点：

```text
k8s-worker2
```

说明：

```text
旧 Pod
10.244.2.61
        ↓ 删除
新 Pod
10.244.2.62
```

Pod 已经发生真实重建。

与此同时 PVC：

```text
Bound
```

PV：

```text
Bound
```

仍然保持不变。

因此形成：

```text
旧 Prometheus Pod
        ↓
删除
        ↓
StatefulSet / Operator
        ↓
创建新 Prometheus Pod
        ↓
重新挂载原 PVC
        ↓
重新访问同一 Local PV
```

---

# 24. Pod 重建后历史数据验证

新 Pod Ready 后，再查询**完全相同的旧时间窗口**。

而不是查询重启后新产生的数据。

最终结果：

```text
PROMETHEUS_PERSISTENCE_HISTORY=PASS
```

这证明：

> Prometheus Pod 删除前已经采集到的历史时间序列，在 Pod 删除并重新创建后仍然存在。

这是本次 Prometheus 持久化改造最关键的验收证据。

---

# 25. 为什么这个验证比 `PVC Bound` 更有价值

仅仅看到：

```text
PVC Bound
PV Bound
```

只能证明 Kubernetes 存储对象成功绑定。

它不能证明：

- Prometheus 是否真的往卷中写数据；
- TSDB 是否真的存储在 PVC 中；
- Pod 重建后是否能重新读取；
- 历史数据是否能够跨 Pod 生命周期保存。

本次验证进一步完成：

```text
Prometheus 写入真实 TSDB 数据
            ↓
查询并确认旧历史样本存在
            ↓
删除 Prometheus Pod
            ↓
Pod 自动重建
            ↓
重新挂载同一 PVC
            ↓
查询相同旧时间窗口
            ↓
历史数据仍然存在
```

因此可以真正证明：

```text
Prometheus Persistence = PASS
```

---

# 26. 最终证据链

本阶段最终证据如下。

## Linux 存储层

```text
独立 VMware 磁盘                     PASS
/dev/sdc1                           PASS
XFS                                 PASS
/data/prometheus                    PASS
/etc/fstab UUID 持久挂载            PASS
Prometheus UID/GID 写权限            PASS
```

## Kubernetes 存储层

```text
StorageClass local-storage          PASS
PV 28Gi                             PASS
PV Retain                           PASS
PV nodeAffinity=k8s-worker2         PASS
PVC 自动创建                         PASS
PV/PVC Bound                        PASS
```

## Helm / Operator 层

```text
Chart 固定 88.2.0                   PASS
VALUES_SEMANTIC_MATCH              PASS
PROMETHEUS_CR_RENDER               PASS
HELM_UPGRADE_DRY_RUN               PASS
Helm Revision 3 deployed           PASS
Prometheus spec.storage            PASS
StatefulSet volumeClaimTemplate    PASS
```

## Prometheus 数据层

```text
/prometheus 使用 PVC                PASS
TSDB 写入 /data/prometheus          PASS
PRE_RESTART_HISTORY                PASS
Prometheus Pod 自动重建             PASS
重建后 PV/PVC 保持 Bound            PASS
PROMETHEUS_PERSISTENCE_HISTORY     PASS
```

最终：

```text
Prometheus Local PV Persistence = PASS
```

---

# 27. 本阶段真正完成了什么

本阶段并不是简单“给 Prometheus 加了一个 PVC”。

真正完成的是：

### 1. 从临时存储迁移到持久化存储

改造前：

```text
Prometheus
    ↓
emptyDir
```

改造后：

```text
Prometheus
    ↓
PVC
    ↓
Local PV
    ↓
独立 XFS 磁盘
```

### 2. 建立了明确的存储边界

MySQL：

```text
worker1
/data/mysql
```

Redis：

```text
worker2
/data/redis
```

Prometheus：

```text
worker2
/data/prometheus
```

三者不共享同一数据目录。

### 3. 使用 Operator 正确管理方式

没有直接修改 Operator 生成的 StatefulSet。

而是：

```text
Helm values
→ Prometheus CR
→ Operator
→ StatefulSet/PVC
```

### 4. 验证了 Pod 生命周期与数据生命周期解耦

Pod 可以被删除：

```text
Pod 生命周期结束
```

但数据仍然保留：

```text
TSDB 生命周期继续存在
```

这是容器化有状态服务设计中非常重要的基础原则。

---

# 28. SRE 项目价值

这部分具有较明显的 SRE 项目价值。

因为它体现的不只是 Kubernetes YAML 编写，而是完整的：

```text
发现风险
→ 设计存储方案
→ 隔离数据盘
→ 权限设计
→ PV/PVC 设计
→ Operator 配置
→ 变更前 Dry Run
→ 正式变更
→ 主动故障验证
→ 历史数据恢复验证
→ 明确可用性边界
```

简历或答辩中可以描述为：

> 针对 Prometheus 默认临时 TSDB 存储可能随 Pod 重建丢失历史监控数据的问题，为监控系统设计独立 XFS 数据盘与 Kubernetes Local PV，通过 kube-prometheus-stack `storageSpec` 和 Prometheus Operator 管理 PVC 生命周期，并通过主动删除 Prometheus Pod 后查询删除前固定时间窗口历史指标，验证 TSDB 数据可跨 Pod 生命周期持续保留。

---

# 29. 可用性边界

虽然本次已经实现 Prometheus 数据持久化，但必须明确：

> Local PV 持久化 ≠ Prometheus 存储高可用。

目前结构：

```text
Prometheus
    ↓
PVC
    ↓
Local PV
    ↓
k8s-worker2
    ↓
/data/prometheus
    ↓
worker2 独立 VMware 虚拟磁盘
```

因此能够处理：

### Pod 删除

```text
支持
```

Pod 重建后重新挂载原 PVC，数据仍存在。

### Prometheus 容器崩溃

```text
支持
```

容器重新启动后仍使用相同持久化目录。

### worker2 正常重启

磁盘通过 `/etc/fstab` 持久挂载，Local PV 数据本身不会因为操作系统重启而主动删除。

### worker2 永久损坏

```text
不具备自动跨节点存储恢复能力
```

因为 Local PV：

```text
nodeAffinity = k8s-worker2
```

如果 worker2 永久丢失，Prometheus 无法直接把 Local PV 自动迁移到 worker1。

### Prometheus 数据盘永久损坏

Local PV 本身没有副本。

因此：

```text
磁盘故障
```

仍可能造成历史监控数据丢失。

---

# 30. 当前阶段应如何定义

本次验证证明的是：

```text
Pod 级 Prometheus 数据持久化
+
Pod 重建后的历史数据恢复
```

可以正式声明：

```text
Prometheus Persistence = PASS
```

但不能宣称：

```text
Prometheus Storage HA = PASS
```

这两个概念必须明确区分。

对于当前 OpsLab Kubernetes / SRE 实习项目而言，这个设计已经足以证明：

- 理解 Kubernetes 有状态存储；
- 理解 PV / PVC；
- 理解 Local PV NodeAffinity；
- 理解 Operator 控制边界；
- 理解数据生命周期与 Pod 生命周期解耦；
- 能够通过主动故障实验验证设计，而不是只依赖配置文件判断。

---

# 31. 最终结论

本次 Prometheus 持久化改造最终结果：

```text
Prometheus 独立数据盘                PASS
XFS 文件系统                        PASS
UUID / fstab 持久挂载               PASS
Prometheus UID/GID 权限             PASS
Local PV                            PASS
PVC                                 PASS
PV/PVC Bound                        PASS
Helm Revision 3                     PASS
Prometheus CR storage               PASS
Operator volumeClaimTemplate        PASS
TSDB 实际写盘                        PASS
Pod 主动删除与自动重建               PASS
删除前历史数据基线                   PASS
重建后旧历史数据仍然可查询            PASS
```

最终判定：

```text
Prometheus Local PV 持久化验证：PASS
```

至此，OpsLab 项目的 Prometheus 已从：

```text
临时 emptyDir TSDB
```

正式升级为：

```text
独立磁盘
    ↓
XFS
    ↓
Local PV
    ↓
PVC
    ↓
Prometheus TSDB
```

并完成了真实 Pod 重建后的历史数据保留验证。

本阶段可以正式封存。
