# Kubernetes Static Local PV 持久化存储基础层设计、部署与可靠性验收报告

## 1. 摘要

本阶段围绕 MySQL、Redis 后续有状态服务部署，完成了一套从虚拟磁盘、Linux 文件系统一直贯通到 Kubernetes PersistentVolumeClaim 的本地持久化存储基础层。

本阶段不是简单创建两个 PVC，而是完整完成并实际验证了以下链路：

```text
VMware 独立数据盘
        ↓
GPT 分区表
        ↓
/dev/sdb1
        ↓
XFS 文件系统
        ↓
UUID 持久化挂载
        ↓
/data/mysql / /data/redis
        ↓
StorageClass: local-storage
        ↓
Static Local PersistentVolume
        ↓
PersistentVolumeClaim
        ↓
Consumer Pod
        ↓
真实文件写入
        ↓
删除 Pod
        ↓
重新创建 Consumer Pod
        ↓
重新读取原数据
```

最终形成两条独立数据链路：

```text
k8s-worker1
/dev/sdb 20GB
→ /dev/sdb1
→ XFS
→ UUID=b41b131b-6fae-438e-9754-f30fb2a7ec37
→ /data/mysql
→ opslab-mysql-local-pv
→ opslab-mysql-data
```

以及：

```text
k8s-worker2
/dev/sdb 20GB
→ /dev/sdb1
→ XFS
→ UUID=c95feff7-f3a6-4e4e-96b4-e902b5bc44cf
→ /data/redis
→ opslab-redis-local-pv
→ opslab-redis-data
```

两块底层虚拟数据盘均为 20GB；Kubernetes 中两个 PV/PVC 的声明容量均为 18Gi。

需要特别说明：

> **18Gi 是 Kubernetes 存储对象中的容量声明和绑定条件，并没有把约 20GB 的 XFS 文件系统实际限制为只能使用 18Gi。**

本项目没有配置 XFS quota，也没有重新把底层文件系统裁切成 18Gi，因此不能把这 2Gi 差额解释为 Kubernetes 强制保留空间。

本阶段还完成了两类非常重要的生命周期验收：

```text
节点生命周期：
drain → reboot → Node Ready → 数据盘自动挂载 → uncordon
```

以及：

```text
Pod 生命周期：
写入数据 → 删除 Pod → 创建新 Pod → 重新挂载 PVC → 原数据仍可读取
```

因此最终证明的不只是 Kubernetes 对象“创建成功”，而是：

> Linux 数据盘、启动时挂载、Local PV 调度、PVC 绑定以及跨 Pod 生命周期的数据持久化都真实工作。

---

# 2. 阶段开始时的真实基础状态

进入本阶段之前，集群已经完成 kubeadm 三节点 Kubernetes 基础建设。

节点：

```text
k8s-control-plane
192.168.8.10

k8s-worker1
192.168.8.11

k8s-worker2
192.168.8.12
```

Kubernetes：

```text
v1.36.3
```

容器运行时：

```text
containerd 2.2.1
```

后续计划部署：

```text
MySQL
Redis
```

但存储检查发现，集群当时没有动态存储基础设施。

实际检查结果包括：

```text
kubectl get storageclass

No resources found
```

同时：

```text
kubectl get csidriver
```

没有 CSI Driver。

`CSINode` 检查中三个节点也没有注册可用 CSI Driver。

因此当时真实状态可以概括为：

```text
Kubernetes 集群已经可以运行无状态应用
但没有可供 MySQL / Redis 使用的持久化存储层
```

这就是本阶段需要解决的问题。

---

# 3. 为什么不能直接依赖容器文件系统

MySQL 和 Redis 都属于有状态服务。

如果数据只存在于：

```text
容器 writable layer
```

那么其生命周期与容器绑定。

容器被删除、Pod 被重新创建之后，不能把这一层作为数据库长期数据存储。

因此项目必须先建立：

```text
独立于 Pod 生命周期的数据层
```

再进入 MySQL 和 Redis 正式部署。

本阶段最终选择 Kubernetes：

```text
PersistentVolume
+
PersistentVolumeClaim
```

作为应用和底层存储之间的抽象边界。

---

# 4. 为什么给数据库增加独立数据盘

两个 Worker 原本已经存在 Kubernetes 节点系统盘：

```text
/dev/sda
```

后续分别新增：

```text
/dev/sdb 20GB
```

作为数据库专用数据盘。

这属于一个明确的基础设施设计选择：

```text
系统盘
和
数据库数据盘
分离
```

这样设计的工程意义主要有三个。

## 4.1 系统数据与业务数据分离

系统盘承担：

```text
Ubuntu
kubelet
containerd
系统日志
Kubernetes Node 运行数据
```

独立数据盘则承担：

```text
MySQL 数据
或
Redis 数据
```

由此形成更清晰的数据职责边界。

---

## 4.2 降低容量互相影响

如果数据库数据与系统全部共享根文件系统，那么数据库持续增长会直接消耗系统盘可用空间。

独立数据盘并不能消除磁盘写满问题，但它可以避免数据库数据直接消耗 Worker 根文件系统的同一容量池。

因此故障影响范围更加清晰：

```text
数据库数据盘容量问题
```

不会天然等价于：

```text
Worker 根分区容量问题
```

---

## 4.3 提高运维可观察性

独立挂载：

```text
/data/mysql
/data/redis
```

使后续可以分别观察：

```text
容量
文件系统
挂载状态
数据库数据
```

而不需要从系统盘大量目录中判断数据库究竟占用了多少空间。

---

# 5. 这个隔离不代表什么

必须避免夸大这一设计。

这里的独立数据盘仍然属于：

```text
同一台 VMware 虚拟机
```

因此它没有提供：

```text
跨主机数据复制
磁盘级高可用
跨节点自动迁移
灾难恢复副本
```

它提供的是：

```text
文件系统和容量职责隔离
+
更清晰的数据生命周期管理
```

而不是完整的存储高可用。

---

# 6. 为什么 MySQL 和 Redis 分别放在两个 Worker

最终真实映射为：

```text
k8s-worker1
→ MySQL 数据
→ /data/mysql
```

以及：

```text
k8s-worker2
→ Redis 数据
→ /data/redis
```

这是本项目已经实际落地的拓扑。

从工程效果看，这样可以避免 MySQL 与 Redis：

```text
共享同一块本地数据库磁盘
共享同一个数据库数据目录
全部绑定同一个 Worker
```

同时也把两种有状态服务的本地存储位置明确区分开来。

但是不能因此写成：

```text
MySQL / Redis 已实现高可用
```

因为当前仍然是：

```text
MySQL 单 Local PV → worker1
Redis 单 Local PV → worker2
```

worker1 如果不可访问：

```text
/data/mysql
```

也无法自动在 worker2 上出现。

Kubernetes 官方对 Local Persistent Volume 的限制同样明确：Local PV 将存储与特定节点绑定；如果该节点或本地卷不可访问，使用该数据的 Pod 也无法直接正常恢复到其他节点，需要应用层复制、人工介入或额外控制器解决。

---

# 7. 为什么选择 VMware 数据盘 → GPT → XFS → UUID/fstab → Local PV

最终基础链路为：

```text
VMware Virtual Disk
↓
GPT
↓
单分区
↓
XFS
↓
UUID
↓
/etc/fstab
↓
固定挂载目录
↓
Kubernetes Local PV
↓
PVC
```

这一设计让每一层只负责一件事情：

```text
VMware
→ 提供虚拟块设备

GPT
→ 描述磁盘分区

XFS
→ 提供 Linux 文件系统

UUID/fstab
→ 保证启动时重新定位并挂载文件系统

Local PV
→ 把指定节点上的本地存储纳入 Kubernetes 存储模型

PVC
→ 给应用提供 Kubernetes 层的存储申请接口
```

因此可以从最底层一直追踪到 Pod。

---

# 8. 为什么使用 GPT

两块新增磁盘实际都初始化为：

```text
GPT
```

并建立一个主要数据分区：

```text
/dev/sdb1
```

需要准确说明：

> 当前 20GB 磁盘并不存在“必须使用 GPT、MBR 无法支持”的容量原因。

因此不应把 GPT 写成容量限制导致的必选方案。

这里更准确的表述是：

> 项目采用 GPT 作为新数据盘的标准分区表格式，并将数据盘主要空间交给单一数据库分区使用。

---

# 9. 为什么使用 XFS

两块：

```text
/dev/sdb1
```

均实际格式化为：

```text
XFS
```

这与项目节点已有的 Linux 文件系统使用方式保持一致，也使两个数据节点采用相同的数据库数据盘格式。

需要避免没有基准测试支持的表述，例如：

```text
XFS 一定比 ext4 更快
```

本项目没有执行 XFS 与 ext4 的数据库性能对比，因此不应得出这种结论。

本阶段能够确认的事实只是：

```text
最终选择 XFS
并经过格式化、挂载、重启以及真实读写验收
```

---

# 10. 磁盘初始化前的安全措施

这是本阶段风险最高的部分之一。

因为：

```text
创建分区表
格式化文件系统
```

属于破坏性操作。

如果目标设备识别错误，可能直接破坏节点系统盘。

因此在操作前先建立恢复和设备身份确认机制。

---

# 11. VMware 快照

两台 Worker 在真正格式化数据盘之前均创建了快照：

```text
storage-disk-attached-pre-format
```

此时已经完成：

```text
新增数据盘
```

但还没有进行后续破坏性文件系统初始化。

这个快照形成了一个明确恢复边界：

```text
数据盘已连接
↓
快照
↓
再执行分区 / 格式化
```

这比“先操作，出问题再想怎么回退”更加可控。

---

# 12. hostname 与节点身份确认

破坏性磁盘操作之前，首先需要确认：

```text
当前到底位于 worker1 还是 worker2
```

这是因为两个节点都存在：

```text
/dev/sda
/dev/sdb
```

设备名称相似。

结合 hostname 与块设备信息进行确认，可以减少：

```text
在错误节点执行正确命令
```

这一类人为事故。

---

# 13. lsblk 与目标盘身份确认

实际磁盘规划中：

```text
/dev/sda
```

为节点原有系统磁盘。

新增数据库盘：

```text
/dev/sdb
```

大小为：

```text
20GB
```

操作前必须确认新盘：

```text
不是当前根文件系统所在设备
没有已有业务文件系统
没有已有数据挂载
```

之后才进入分区和格式化阶段。

这套顺序的价值在于：

```text
先证明目标盘是谁
再修改目标盘
```

而不是根据：

```text
“新盘应该就是 /dev/sdb”
```

直接执行破坏性命令。

---

# 14. worker1 数据盘初始化结果

worker1：

```text
k8s-worker1
```

新增：

```text
/dev/sdb 20GB
```

最终建立：

```text
/dev/sdb1
```

文件系统：

```text
XFS
```

实际文件系统 UUID：

```text
b41b131b-6fae-438e-9754-f30fb2a7ec37
```

最终挂载点：

```text
/data/mysql
```

因此形成：

```text
/dev/sdb
↓
GPT
↓
/dev/sdb1
↓
XFS
↓
UUID=b41b131b-6fae-438e-9754-f30fb2a7ec37
↓
/data/mysql
```

---

# 15. worker2 数据盘初始化结果

worker2：

```text
k8s-worker2
```

同样新增：

```text
/dev/sdb 20GB
```

最终：

```text
/dev/sdb1
```

文件系统：

```text
XFS
```

真实 UUID：

```text
c95feff7-f3a6-4e4e-96b4-e902b5bc44cf
```

挂载：

```text
/data/redis
```

形成：

```text
/dev/sdb
↓
GPT
↓
/dev/sdb1
↓
XFS
↓
UUID=c95feff7-f3a6-4e4e-96b4-e902b5bc44cf
↓
/data/redis
```

---

# 16. 为什么 /etc/fstab 使用 UUID

最终没有把持久挂载单纯依赖：

```text
/dev/sdb1
```

而是写入文件系统 UUID。

worker1：

```text
UUID=b41b131b-6fae-438e-9754-f30fb2a7ec37 /data/mysql xfs defaults 0 0
```

worker2：

```text
UUID=c95feff7-f3a6-4e4e-96b4-e902b5bc44cf /data/redis xfs defaults 0 0
```

这是一个有明确 Linux 机制依据的设计。

Linux `mount(8)` 文档指出，磁盘分区的 `/dev/sdX` 名称可能随硬件重新配置或设备增删发生变化，因此推荐使用 UUID、LABEL 等文件系统/分区标识符。

因此这里：

```text
UUID
```

承担的是：

```text
识别具体文件系统
```

而不是：

```text
依赖它当前恰好叫 /dev/sdb1
```

---

# 17. 为什么写完 fstab 后先做即时验证

持久化挂载配置完成之后，并没有直接把“配置文件写入成功”当成验收成功。

首先进行当前系统状态下的挂载验证。

核心目标是确认：

```text
fstab 可以被解析
UUID 可以找到正确文件系统
目标目录存在
XFS 可以正确挂载
```

其中：

```text
mount -a
```

可以在不重启节点的情况下先暴露明显的 fstab 或挂载错误。

---

# 18. 为什么使用多条命令交叉验证

本阶段使用的验证视角包括：

```text
lsblk -f
blkid
findmnt
df -hT
```

它们解决的问题不同。

## lsblk -f

用于观察：

```text
块设备
分区
文件系统类型
UUID
挂载关系
```

`lsblk` 本身就是用于列出块设备信息的工具，并可以提供 UUID、文件系统类型和挂载信息。

---

## blkid

用于直接核验：

```text
/dev/sdb1
```

的文件系统身份信息，包括：

```text
UUID
TYPE=xfs
```

---

## findmnt

用于回答：

> 当前 `/data/mysql` 或 `/data/redis` 实际来自哪个文件系统？

`findmnt` 可以查询当前挂载信息，也支持按设备、UUID、挂载点等维度定位文件系统。

---

## df -hT

用于确认：

```text
目标路径已经位于对应数据盘
+
文件系统类型为 XFS
+
能够观察实际文件系统容量/使用情况
```

因此这里不是依靠：

```text
一个目录存在
```

来判断挂载成功。

---

# 19. 为什么 mount -a 成功仍然不能结束验收

`mount -a` 成功只能证明：

```text
当前正在运行的系统
能够根据当前 fstab 执行挂载
```

但数据库节点真正需要保证的是：

> 节点完整重启以后，不依赖人工执行 mount，数据盘仍能回来。

因此本阶段继续进行真实：

```text
Worker reboot
```

验证。

这是本阶段从“配置检查”提升到“生命周期验证”的关键步骤。

---

# 20. 为什么重启前执行 drain

Worker 是 Kubernetes 集群中的正常工作节点。

直接：

```text
reboot
```

虽然可能也能重新加入集群，但这会主动制造不受控的工作负载中断。

因此采用：

```text
kubectl drain
```

先将节点进入维护状态，再重启。

Kubernetes 官方将 `kubectl drain` 定义为节点维护前安全驱逐 Pod 的机制；维护完成后再通过 `kubectl uncordon` 恢复节点可调度状态。

所以这里的思路是：

```text
先处理 Kubernetes 工作负载
↓
再维护操作系统和磁盘
```

---

# 21. worker1 重启验收

worker1 实际完成：

```text
drain
↓
reboot
↓
等待 Node 恢复 Ready
↓
检查 /data/mysql
↓
确认数据盘已经自动挂载
↓
uncordon
```

关键点是：

> 重启以后没有依靠手工重新执行 mount 才恢复数据库数据盘。

因此可以证明：

```text
UUID
+
/etc/fstab
+
系统启动挂载
```

这一链路实际有效。

最终：

```text
/data/mysql
```

仍对应 worker1 的 XFS 数据盘。

---

# 22. worker2 重启验收

worker2 使用相同维护模型：

```text
drain
↓
reboot
↓
Node Ready
↓
/data/redis 自动恢复挂载
↓
uncordon
```

因此：

```text
UUID=c95feff7-f3a6-4e4e-96b4-e902b5bc44cf
```

对应的数据文件系统也通过了实际启动恢复测试。

---

# 23. 为什么还要检查 Kubernetes 集群恢复

数据盘重新挂载只是 Linux 层验收。

Worker 同时还是 Kubernetes Node，因此还必须确认：

```text
Node 恢复 Ready
```

并最终：

```text
uncordon
```

重新允许业务调度。

这证明整个维护流程不是：

```text
磁盘正常
但 Kubernetes Node 留在故障状态
```

而是完整经历：

```text
Kubernetes 维护准备
→ OS 重启
→ 文件系统恢复
→ kubelet / Node 恢复
→ 节点重新投入服务
```

---

# 24. Linux 存储层验收结论

至此两台 Worker 已经分别证明：

```text
独立数据盘存在
PASS

GPT 分区完成
PASS

/dev/sdb1 正确
PASS

XFS 正确
PASS

UUID 已确认
PASS

fstab 持久挂载
PASS

当前挂载
PASS

真实 reboot 后自动挂载
PASS

Node 重启后恢复 Ready
PASS
```

这时才进入 Kubernetes PV/PVC 层。

---

# 25. 为什么不是直接使用 hostPath

本项目最终选择：

```text
local PersistentVolume
```

而不是单纯在 Pod 中写：

```yaml
hostPath:
  path: /data/mysql
```

Local PV 的核心优势之一，是 Kubernetes Scheduler 能理解该卷属于哪个 Node。

Kubernetes 官方文档明确要求 Local PV 配置 `nodeAffinity`；调度器利用该 node affinity 将使用该 PV 的 Pod 调度到正确节点。

官方关于 Local Persistent Volume 的说明也指出，与普通 hostPath 相比，Local PV 能让 Scheduler 理解本地卷的节点约束。

因此：

```text
/data/mysql
```

不只是：

```text
某个 Pod 自己知道的宿主机路径
```

而是被正式建模为：

```text
属于 k8s-worker1 的 Kubernetes PersistentVolume
```

---

# 26. StorageClass 设计

最终创建：

```text
StorageClass:
local-storage
```

主要配置：

```text
provisioner:
kubernetes.io/no-provisioner

volumeBindingMode:
WaitForFirstConsumer
```

当前方案使用：

```text
Static Local PV
```

即 PV 由管理员提前定义，而不是由 CSI 动态创建。

Kubernetes 官方 Local PV 示例同样采用：

```yaml
provisioner: kubernetes.io/no-provisioner
volumeBindingMode: WaitForFirstConsumer
```

用于本地持久卷。

---

# 27. 为什么使用 kubernetes.io/no-provisioner

进入本阶段时集群没有 CSI Driver，也没有可用动态存储 Provisioner。

同时本阶段已经人工完成：

```text
磁盘
→ 分区
→ XFS
→ 挂载点
```

因此 Kubernetes 不需要、也没有组件去：

```text
收到 PVC 后自动创建一块新磁盘
```

而是需要管理员把已有本地存储注册成 PV。

所以：

```text
kubernetes.io/no-provisioner
```

与本阶段：

```text
手工准备底层磁盘
+
手工创建 PersistentVolume
```

的模式一致。

---

# 28. Static Local PV

创建两个本地 PV。

MySQL：

```text
name:
opslab-mysql-local-pv

storageClass:
local-storage

capacity:
18Gi

access mode:
ReadWriteOnce

local.path:
/data/mysql

node:
k8s-worker1
```

Redis：

```text
name:
opslab-redis-local-pv

storageClass:
local-storage

capacity:
18Gi

access mode:
ReadWriteOnce

local.path:
/data/redis

node:
k8s-worker2
```

---

# 29. 18Gi 必须如何正确理解

这里是旧报告最需要纠正的技术点之一。

实际底层磁盘：

```text
20GB
```

实际 XFS 建立在：

```text
/dev/sdb1
```

之上。

Kubernetes PV：

```text
capacity.storage: 18Gi
```

PVC：

```text
requests.storage: 18Gi
```

这个：

```text
18Gi
```

参与 Kubernetes：

```text
PVC 与 PV 是否容量匹配
```

等控制面判断。

但是本项目没有配置：

```text
XFS project quota
文件系统 quota
独立 18Gi 逻辑卷
CSI 容量限制
```

因此不能说：

```text
Pod 最多只能在这个 XFS 中写 18Gi
```

也不能说：

```text
剩余空间被 Kubernetes 自动保留
```

更准确的描述是：

> 底层约 20GB 数据盘被声明为一个 Kubernetes 容量为 18Gi 的 PV，但当前实现没有额外机制将实际文件系统使用量硬限制在 18Gi。

这一区分对于理解 PV `capacity` 与真实存储 enforcement 非常重要。

---

# 30. ReadWriteOnce 也不应被误解

两个 PV/PVC 使用：

```text
ReadWriteOnce
```

这里不能简单翻译成：

```text
只能有一个 Pod 使用
```

更准确的含义是：

```text
该卷可被单个 Node 以读写方式挂载
```

在 Local PV 场景下，卷本身已经固定在一个 Node，因此它与本地卷拓扑天然一致。

本项目并没有把这个实验扩展成多 Pod 并发数据库写入测试，因此报告不对该场景作额外结论。

---

# 31. nodeAffinity 为什么是 Local PV 的核心

MySQL 的：

```text
/data/mysql
```

真实存在于：

```text
k8s-worker1
```

Redis：

```text
/data/redis
```

真实存在于：

```text
k8s-worker2
```

因此：

```text
opslab-mysql-local-pv
```

必须带有指向：

```text
k8s-worker1
```

的 `nodeAffinity`。

Redis PV 则指向：

```text
k8s-worker2
```

Kubernetes 官方明确要求 `local` volume 的 PV 设置 node affinity，并由 Scheduler 根据它把使用该 PV 的 Pod 安排到正确节点。

这意味着本项目不需要再通过业务 Pod：

```text
手工写死一个 nodeName
```

来维持存储正确性。

存储对象本身已经描述：

```text
数据在哪里
```

---

# 32. PersistentVolumeClaim 设计

MySQL PVC：

```text
namespace:
opslab

name:
opslab-mysql-data

storageClass:
local-storage

request:
18Gi

accessMode:
ReadWriteOnce
```

通过 selector 匹配 MySQL 存储角色。

Redis：

```text
namespace:
opslab

name:
opslab-redis-data

storageClass:
local-storage

request:
18Gi

accessMode:
ReadWriteOnce
```

通过 selector 匹配 Redis 存储角色。

因此：

```text
MySQL PVC
```

不会错误绑定：

```text
Redis PV
```

反之亦然。

---

# 33. 为什么 PVC 创建后最初 Pending 是正常的

实际创建 StorageClass、PV、PVC 后观察到：

```text
PV:
Available
```

而：

```text
PVC:
Pending
```

当时这不是故障。

关键原因是：

```text
volumeBindingMode:
WaitForFirstConsumer
```

Kubernetes 官方定义中，`WaitForFirstConsumer` 会延迟卷绑定，直到出现真正使用该 PVC 的 Pod，再结合 Pod 的调度约束和存储拓扑选择合适的 PV。Local volume 明确支持这种预创建 PV 的延迟绑定方式。

因此当时正确的理解是：

```text
没有 Consumer
↓
没有完成最终联合调度判断
↓
PVC 暂时 Pending
```

而不是：

```text
PV/PVC 配置失败
```

---

# 34. 为什么 WaitForFirstConsumer 对 Local PV 特别重要

如果一个卷只能在：

```text
worker1
```

访问，而 Pod 因其他调度约束只能去：

```text
worker2
```

就会产生：

```text
Pod 要去的地方
和
数据所在的地方
不一致
```

`WaitForFirstConsumer` 的作用就是让存储绑定决策与 Pod 调度需求结合起来。

官方文档明确说明：对于受拓扑约束、并非所有 Node 都可访问的存储，Immediate binding 可能在不知道 Pod 调度要求的情况下过早绑定；`WaitForFirstConsumer` 用于延迟绑定，直到有使用该 PVC 的 Pod 出现。

这正适合：

```text
Local PV
```

这样的节点本地存储。

---

# 35. Consumer Pod 触发真实绑定

之后创建 Local PV smoke test Consumer Pod。

MySQL Consumer 使用：

```text
opslab-mysql-data
```

Redis Consumer 使用：

```text
opslab-redis-data
```

Consumer 出现之后，实际观察到：

```text
PVC:
Pending
→ Bound
```

最终：

```text
opslab-mysql-data
→ opslab-mysql-local-pv
```

以及：

```text
opslab-redis-data
→ opslab-redis-local-pv
```

这证明：

```text
StorageClass
PV
PVC
selector
WaitForFirstConsumer
```

之间的绑定逻辑实际工作。

---

# 36. Consumer Pod 的调度结果

MySQL 存储 Consumer 最终调度到：

```text
k8s-worker1
```

Redis Consumer：

```text
k8s-worker2
```

这与两个 Local PV 的：

```text
nodeAffinity
```

一致。

因此形成：

```text
opslab-mysql-data
↓
opslab-mysql-local-pv
↓
k8s-worker1
↓
/data/mysql
```

以及：

```text
opslab-redis-data
↓
opslab-redis-local-pv
↓
k8s-worker2
↓
/data/redis
```

这是调度层的重要验收证据。

---

# 37. 为什么 Bound 仍然不能算最终验收

看到：

```text
PVC Bound
```

只能说明：

```text
Kubernetes 控制面已经建立 PV/PVC 绑定关系
```

它不能独立证明：

```text
底层目录可写
数据确实落盘
Pod 能读取
Pod 删除后数据仍存在
```

因此本项目继续执行真实 I/O 测试。

---

# 38. MySQL Local PV 真实写入测试

MySQL Storage Consumer 通过 PVC 挂载 Local PV，并实际写入：

```text
mysql-local-pv-ok
```

随后可以从挂载卷中读取该数据。

结合：

```text
Pod 调度节点 = k8s-worker1
PV local.path = /data/mysql
```

因此形成：

```text
Pod
↓
opslab-mysql-data
↓
opslab-mysql-local-pv
↓
k8s-worker1
↓
/data/mysql
↓
XFS
```

的实际可读写路径。

---

# 39. Redis Local PV 真实写入测试

Redis Storage Consumer 同样写入：

```text
redis-local-pv-ok
```

并成功读取。

对应：

```text
Pod
↓
opslab-redis-data
↓
opslab-redis-local-pv
↓
k8s-worker2
↓
/data/redis
↓
XFS
```

因此两个卷都不是：

```text
只绑定成功但无法使用
```

而是已经通过真实 I/O。

---

# 40. 为什么还必须删除 Pod

第一次写入成功只能证明：

```text
当前 Pod 可以访问当前挂载
```

还没有证明：

```text
数据真正独立于当前 Pod 生命周期
```

所以本项目继续删除原测试 Consumer。

随后重新创建新的验证 Pod，再挂载相同 PVC。

如果数据写在：

```text
原 Pod 临时文件系统
```

那么这时数据应该消失。

如果数据真正位于：

```text
Local PV
```

则应该继续存在。

---

# 41. Pod 删除重建后的真实结果

新的验证 Pod 重新挂载 MySQL PVC 后仍然读取到：

```text
mysql-local-pv-ok
```

Redis 验证 Pod同样读取到：

```text
redis-local-pv-ok
```

因此真实证明：

```text
Pod A
写数据
↓
Pod A 删除
↓
PV/PVC 保留
↓
底层文件系统数据保留
↓
Pod B
重新挂载同一 PVC
↓
读取到 Pod A 写入的数据
```

这就是本阶段最关键的持久化证据之一。

---

# 42. 持久化测试过程中发现的权限现象

在后续清理测试数据时还出现过一个有价值的现象：

新的验证 Pod 使用非 root 用户运行，在尝试删除之前由 root 身份测试流程产生的测试文件时出现：

```text
Permission denied
```

这个结果不能解释为：

```text
Local PV 损坏
```

因为新 Pod已经能够：

```text
挂载卷
读取原测试数据
```

真正暴露的是：

```text
Kubernetes 存储挂载成功
≠
应用进程必然拥有底层 Unix 文件权限
```

最终这些测试文件通过节点上的 root/Ansible 方式清理。

PV/PVC 本身继续保持：

```text
Bound
```

这一现象后来也为 MySQL 正式部署中的：

```text
UID/GID
initContainer
Local PV 目录权限
```

问题提供了重要前置经验。

---

# 43. Retain 应该怎样准确解释

本项目存储设计采用：

```text
Retain
```

用于避免 PVC 生命周期结束时自动把数据库存储当成可立即销毁的资源处理。

但这里需要比旧版报告更加精确。

对于手工创建的 PersistentVolume，Kubernetes 官方文档明确指出：

> 手工创建并由 StorageClass 管理的 PV，保留其创建时自身指定的 reclaim policy。StorageClass 的 `reclaimPolicy` 主要决定动态创建 PV 的策略。

因此对于本项目的 Static Local PV，真正应该确认的是：

```text
PersistentVolume 自身的
persistentVolumeReclaimPolicy
```

而不能仅仅因为：

```text
StorageClass 写了 Retain
```

就推导所有手工 PV 自动受它控制。

---

# 44. Retain 真正提供什么

对于一个实际设置为：

```text
persistentVolumeReclaimPolicy: Retain
```

的 PV，当对应 PVC 删除后：

```text
PV 不会立即作为全新空卷重新提供给其他 Claim
```

而会进入：

```text
Released
```

并保留原 Claim 的数据，等待管理员人工处理。

Kubernetes 官方文档明确描述了这一行为。

因此 Retain 的核心价值是：

```text
把数据回收决策交给管理员
```

而不是：

```text
自动清空数据库卷
```

---

# 45. Retain 不是什么

Retain 不代表：

```text
数据永远不会丢
```

管理员如果：

```text
手动删除底层文件
重新格式化 /dev/sdb1
删除虚拟磁盘
```

数据当然仍然会丢失。

它也不是：

```text
备份
快照
高可用
跨节点复制
```

所以正确表达应当是：

> Retain 降低 Kubernetes Claim 生命周期直接触发存储回收的风险，但不能替代备份和底层数据保护。

---

# 46. Local PV 能够解决的问题

本阶段已经实际验证 Local PV 能解决：

## 46.1 Pod 生命周期与数据生命周期分离

```text
Pod 删除
≠
底层数据删除
```

---

## 46.2 Pod 重建后重新访问原卷

```text
新 Pod
→ 原 PVC
→ 原 PV
→ 原数据
```

已经经过真实测试。

---

## 46.3 Kubernetes 能理解本地卷所在 Node

通过：

```text
PV nodeAffinity
```

调度器知道：

```text
MySQL 数据位于 worker1
Redis 数据位于 worker2
```

而不是把 Local PV 当成任意节点都可访问的共享存储。

---

## 46.4 为 StatefulSet 提供持久化基础

完成此阶段后：

```text
opslab-mysql-data
opslab-redis-data
```

已经可以直接供后续：

```text
MySQL StatefulSet
Redis StatefulSet
```

使用。

---

# 47. Local PV 不能解决的问题

Local PV 本质上是：

```text
特定节点上的本地存储
```

因此它不提供：

```text
跨节点磁盘复制
节点故障自动数据迁移
存储副本
自动数据库 failover
异机恢复
```

Kubernetes 官方也明确指出，如果 Local PV 所在 Node 或本地磁盘不可访问，使用它的工作负载也会受到影响，可能需要人工操作、外部 Controller 或应用层数据复制恢复。

因此：

```text
StatefulSet 自愈
```

也不能改变这个物理约束。

如果 worker1 完全失效：

```text
opslab-mysql-local-pv
```

的数据仍然留在 worker1 的本地磁盘上。

Scheduler 不能凭空让 worker2 获得：

```text
/data/mysql
```

中的内容。

---

# 48. 为什么这个方案仍适合当前项目

当前阶段的目标并不是直接构建：

```text
Ceph
Longhorn
SAN
云盘 CSI
```

这类更复杂存储平台。

而是先真正理解：

```text
Linux 磁盘
↓
文件系统
↓
持久化挂载
↓
PersistentVolume
↓
PersistentVolumeClaim
↓
Scheduler
↓
Stateful workload
```

的完整工作机制。

Local PV 使每一层都可以被直接观察：

```text
/dev/sdb1
```

在哪里；

```text
/data/mysql
```

为什么存在；

PV 为什么属于 worker1；

PVC 为什么一开始 Pending；

Consumer 为什么最终去 worker1；

Pod 删除后数据为什么还存在。

这种透明度对于当前 Kubernetes/SRE 实践项目具有很高的学习和复盘价值。

---

# 49. 为什么不能把当前方案称为生产级高可用存储

当前方案仍然存在明确限制：

```text
单节点本地卷
无存储复制
无 CSI 动态 provisioning
无跨节点自动迁移
无存储层高可用
```

MySQL/Redis 后续即使运行在 StatefulSet 中：

```text
StatefulSet
```

解决的也是 Kubernetes 工作负载生命周期问题，而不会自动复制 Local PV 中的数据。

因此更准确的项目表述是：

> 在三节点 kubeadm 实验集群中，基于两块独立 VMware XFS 数据盘构建 Static Local PV 存储层，并完成节点重启、调度绑定、实际读写和 Pod 重建后的数据持久化验证。

而不是：

```text
部署了生产级高可用数据库存储
```

---

# 50. 本阶段实际完成的工程能力

本阶段最终完成了五个层次。

## 第一层：块设备管理

```text
识别系统盘和数据盘
→ 独立数据盘初始化
→ GPT
→ /dev/sdb1
```

---

## 第二层：Linux 文件系统管理

```text
XFS
→ UUID
→ /etc/fstab
→ 固定挂载点
```

---

## 第三层：节点维护能力

```text
drain
→ reboot
→ 自动重新挂载
→ Node Ready
→ uncordon
```

---

## 第四层：Kubernetes 存储抽象

```text
StorageClass
→ Static Local PV
→ PVC
```

---

## 第五层：持久化生命周期验证

```text
Consumer Pod
→ 写数据
→ 删除 Pod
→ 新 Pod
→ 原 PVC
→ 原数据仍存在
```

---

# 51. 这一阶段真正体现出的运维/SRE价值

## 51.1 高风险操作前先建立恢复边界

不是拿到：

```text
/dev/sdb
```

以后立即格式化。

而是：

```text
识别设备
→ 建立 storage-disk-attached-pre-format 快照
→ 再进行破坏性操作
```

体现：

```text
先控制风险
再执行变更
```

---

## 51.2 不仅检查配置，还验证生命周期

如果只执行：

```text
mount -a
```

成功就结束，无法证明节点重启以后真的能恢复。

因此继续做：

```text
真实 reboot
```

把配置验证提升成：

```text
恢复能力验证
```

---

## 51.3 Kubernetes 资源 Created/Bound 不等于功能完成

没有停留在：

```text
StorageClass created
PV created
PVC Bound
```

而是继续做：

```text
Consumer 调度
→ 真实写入
→ 删除 Pod
→ 新 Pod
→ 重新读取
```

这才形成真正的端到端验收。

---

## 51.4 能识别“正常 Pending”和真正故障

看到：

```text
PVC Pending
```

没有直接开始修改 YAML。

先结合：

```text
WaitForFirstConsumer
```

理解状态产生原因。

Consumer 出现后：

```text
Pending → Bound
```

证明它本来就是设计行为。

这是 Kubernetes 排错中非常重要的能力：

> 状态异常与状态符合设计，不能只看一个单词判断。

---

## 51.5 能理解调度和存储的耦合关系

Local PV 不是：

```text
所有 Node 都能访问的网络存储
```

因此：

```text
nodeAffinity
+
WaitForFirstConsumer
```

是整个设计的关键。

本阶段通过实际：

```text
MySQL Consumer → worker1
Redis Consumer → worker2
```

证明了这条链路。

---

## 51.6 能区分 Kubernetes 存储与 Linux 文件权限

测试清理阶段出现的：

```text
Permission denied
```

没有误判为 PV 故障。

最终认识到：

```text
PVC 成功挂载
```

与：

```text
容器用户是否拥有 Unix 文件权限
```

是两个不同层级的问题。

这一观察随后直接进入 MySQL StatefulSet 权限设计和 initContainer 故障处理中。

---

# 52. 实际事件时间线

综合本阶段已确认操作，可以按照以下顺序还原。

## 阶段 A：存储现状预检

确认：

```text
无 StorageClass
无 CSI Driver
无现成动态存储
```

并形成存储设计。

对应 Git：

```text
52bf3c4
docs(storage): record local storage design and preflight
```

---

## 阶段 B：新增数据库独立数据盘

worker1：

```text
/dev/sdb 20GB
→ MySQL
```

worker2：

```text
/dev/sdb 20GB
→ Redis
```

在破坏性操作前：

```text
两台 Worker 创建
storage-disk-attached-pre-format
快照
```

---

## 阶段 C：Linux 数据盘初始化

worker1：

```text
/dev/sdb
→ GPT
→ /dev/sdb1
→ XFS
→ UUID=b41b131b-6fae-438e-9754-f30fb2a7ec37
→ /data/mysql
```

worker2：

```text
/dev/sdb
→ GPT
→ /dev/sdb1
→ XFS
→ UUID=c95feff7-f3a6-4e4e-96b4-e902b5bc44cf
→ /data/redis
```

完成：

```text
/etc/fstab
```

持久化挂载。

对应 Git：

```text
f443e80
feat(storage): initialize worker data disks
```

---

## 阶段 D：真实节点重启验证

分别进行：

```text
drain worker
→ reboot
→ Node Ready
→ 验证对应 /data 目录自动挂载
→ uncordon
```

最终：

```text
worker1 /data/mysql
worker2 /data/redis
```

都能够在重启后自行恢复挂载。

---

## 阶段 E：创建 Kubernetes Local PV 层

目录：

```text
kubernetes/storage/local-pv/
```

主要清单：

```text
local-storage.yaml
local-pv-smoke-test.yaml
local-pv-persistence-test.yaml
```

创建：

```text
StorageClass:
local-storage
```

两个 PV：

```text
opslab-mysql-local-pv
opslab-redis-local-pv
```

两个 PVC：

```text
opslab-mysql-data
opslab-redis-data
```

---

## 阶段 F：WaitForFirstConsumer 验证

Consumer 不存在时：

```text
PV = Available
PVC = Pending
```

Consumer 创建后：

```text
PVC = Bound
```

实际调度：

```text
MySQL Consumer → k8s-worker1
Redis Consumer → k8s-worker2
```

证明：

```text
Local PV nodeAffinity
+
WaitForFirstConsumer
```

正常工作。

---

## 阶段 G：真实写入验证

MySQL Local PV 写入：

```text
mysql-local-pv-ok
```

Redis Local PV 写入：

```text
redis-local-pv-ok
```

两个卷均成功读取。

---

## 阶段 H：跨 Pod 生命周期持久化验证

删除原 Consumer Pod。

创建新的验证 Pod。

再次读取：

```text
mysql-local-pv-ok
redis-local-pv-ok
```

仍然存在。

因此正式证明：

```text
数据生命周期独立于测试 Pod 生命周期
```

---

## 阶段 I：测试文件权限现象和清理

新验证 Pod 以非 root 用户运行时：

```text
无法删除此前 root-owned 测试文件
Permission denied
```

但：

```text
数据仍然能够读取
```

所以确认是：

```text
Unix owner / mode 问题
```

而不是：

```text
PV/PVC 故障
```

最终通过节点 root/Ansible 清理测试文件。

PV/PVC 保持 Bound。

---

## 阶段 J：Git 收尾

完成并提交：

```text
491e800
feat(storage): provision and validate local persistent volumes
```

由此 Static Local PV 基础层正式完成。

---

# 53. MySQL 完整证据链

```text
VMware 添加独立 20GB 数据盘
↓
确认目标设备为 /dev/sdb
↓
破坏性操作前创建快照
↓
GPT
↓
/dev/sdb1
↓
XFS
↓
UUID=b41b131b-6fae-438e-9754-f30fb2a7ec37
↓
/etc/fstab
↓
/data/mysql
↓
mount 状态验证
↓
drain k8s-worker1
↓
reboot
↓
无需人工重新挂载
↓
/data/mysql 恢复
↓
Node Ready
↓
uncordon
↓
StorageClass=local-storage
↓
opslab-mysql-local-pv
18Gi declared capacity
↓
nodeAffinity=k8s-worker1
↓
opslab-mysql-data
↓
Consumer 创建
↓
PVC Pending → Bound
↓
Pod 调度至 worker1
↓
写入 mysql-local-pv-ok
↓
删除原 Pod
↓
新 Pod 再次挂载
↓
读取 mysql-local-pv-ok
↓
持久化验收 PASS
```

---

# 54. Redis 完整证据链

```text
VMware 添加独立 20GB 数据盘
↓
确认 /dev/sdb
↓
破坏性操作前创建快照
↓
GPT
↓
/dev/sdb1
↓
XFS
↓
UUID=c95feff7-f3a6-4e4e-96b4-e902b5bc44cf
↓
/etc/fstab
↓
/data/redis
↓
mount 状态验证
↓
drain k8s-worker2
↓
reboot
↓
自动恢复 /data/redis
↓
Node Ready
↓
uncordon
↓
StorageClass=local-storage
↓
opslab-redis-local-pv
18Gi declared capacity
↓
nodeAffinity=k8s-worker2
↓
opslab-redis-data
↓
Consumer 创建
↓
PVC Pending → Bound
↓
Pod 调度至 worker2
↓
写入 redis-local-pv-ok
↓
删除原 Pod
↓
新 Pod 再次挂载
↓
读取 redis-local-pv-ok
↓
持久化验收 PASS
```

---

# 55. 最终资源状态汇总

## worker1 / MySQL

```text
Node:
k8s-worker1

Data Disk:
/dev/sdb
20GB

Partition:
/dev/sdb1

Filesystem:
XFS

UUID:
b41b131b-6fae-438e-9754-f30fb2a7ec37

Mount Point:
/data/mysql

PersistentVolume:
opslab-mysql-local-pv

PersistentVolumeClaim:
opslab-mysql-data

Declared PV/PVC Capacity:
18Gi

StorageClass:
local-storage
```

---

## worker2 / Redis

```text
Node:
k8s-worker2

Data Disk:
/dev/sdb
20GB

Partition:
/dev/sdb1

Filesystem:
XFS

UUID:
c95feff7-f3a6-4e4e-96b4-e902b5bc44cf

Mount Point:
/data/redis

PersistentVolume:
opslab-redis-local-pv

PersistentVolumeClaim:
opslab-redis-data

Declared PV/PVC Capacity:
18Gi

StorageClass:
local-storage
```

---

# 56. 最终验收矩阵

### 独立数据盘

```text
worker1 /dev/sdb 20GB
PASS

worker2 /dev/sdb 20GB
PASS
```

### GPT / 分区

```text
/dev/sdb1
PASS
```

### XFS

```text
worker1
PASS

worker2
PASS
```

### UUID

```text
worker1:
b41b131b-6fae-438e-9754-f30fb2a7ec37
PASS

worker2:
c95feff7-f3a6-4e4e-96b4-e902b5bc44cf
PASS
```

### 持久化挂载

```text
/data/mysql
PASS

/data/redis
PASS
```

### 节点 reboot 后自动挂载

```text
worker1
PASS

worker2
PASS
```

### Kubernetes Node 恢复

```text
drain / reboot / Ready / uncordon
PASS
```

### StorageClass

```text
local-storage
PASS
```

### Static Local PV

```text
opslab-mysql-local-pv
PASS

opslab-redis-local-pv
PASS
```

### PVC

```text
opslab-mysql-data
Bound

opslab-redis-data
Bound
```

### WaitForFirstConsumer

```text
Consumer 前 Pending
Consumer 后 Bound
PASS
```

### nodeAffinity

```text
MySQL Consumer → worker1
Redis Consumer → worker2
PASS
```

### 实际数据写入

```text
mysql-local-pv-ok
PASS

redis-local-pv-ok
PASS
```

### Pod 删除重建后的数据保留

```text
MySQL Local PV
PASS

Redis Local PV
PASS
```

---

# 57. 最终结论

本阶段已经建立了一套经过真实验证的 Kubernetes Static Local PV 持久化存储基础层。

最底层：

```text
VMware 独立数据盘
```

经过：

```text
GPT
→ XFS
→ UUID
→ /etc/fstab
```

成为 Linux 可持续使用的数据文件系统。

随后通过：

```text
StorageClass
→ Static Local PV
→ PVC
```

进入 Kubernetes 存储抽象体系。

再通过：

```text
WaitForFirstConsumer
+
PV nodeAffinity
```

使：

```text
MySQL 数据卷
```

正确绑定：

```text
k8s-worker1
```

并使：

```text
Redis 数据卷
```

正确绑定：

```text
k8s-worker2
```

最终又通过两类真实生命周期实验：

```text
节点 reboot
```

和：

```text
Pod 删除重建
```

证明：

```text
数据盘能够在节点重启后恢复
+
数据能够跨 Pod 生命周期继续存在
```

因此这一阶段真正完成的是：

> **从底层块设备、Linux 文件系统、系统启动挂载，一直到 Kubernetes PV/PVC、拓扑调度和 Pod 数据生命周期的完整存储路径建设与可靠性验收。**

它的项目价值不在于：

```text
“会写 PV/PVC YAML”
```

而在于已经实际理解并验证了：

```text
数据真正存在哪里
↓
节点为什么必须去那里
↓
PVC 为什么会 Pending
↓
什么时候会 Bound
↓
Node 重启以后磁盘会不会回来
↓
Pod 删除以后数据会不会消失
↓
文件系统权限问题和 Kubernetes 卷问题如何区分
↓
Local PV 能解决什么
↓
Local PV 无法解决什么
```

同时，本阶段也明确没有把当前方案夸大成：

```text
生产级 HA Storage
```

因为它仍然具有：

```text
节点本地依赖
无数据复制
无跨节点自动迁移
```

等 Local PV 固有边界。

更准确的最终项目结论是：

> **在三节点 kubeadm Kubernetes 实验集群中，为 MySQL 与 Redis 分别构建了基于独立 VMware XFS 数据盘的 Static Local PV 持久化存储，完成 UUID/fstab 启动恢复、Local PV 拓扑调度、真实读写以及 Pod 重建数据保留验收，为后续 MySQL/Redis StatefulSet 部署建立了经过实际验证的数据基础层。**

本阶段验收结论：

```text
Kubernetes Static Local PV 持久化存储基础层
PASS
```

