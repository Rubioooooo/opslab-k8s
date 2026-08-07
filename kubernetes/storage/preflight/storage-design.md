# Kubernetes 本地持久化存储设计

## 1. 当前集群状态

存储预检确认：

- 集群不存在 StorageClass；
- 集群不存在 CSI Driver；
- 集群不存在 PersistentVolume；
- 集群不存在 PersistentVolumeClaim；
- k8s-worker1 和 k8s-worker2 均只有一块 60GB 系统盘；
- 根文件系统为 XFS；
- 根分区剩余约 51GB；
- containerd、kubelet、系统日志和操作系统均位于根分区；
- 两个 Worker 均没有独立数据磁盘或数据挂载点。

因此，不直接在系统盘上的 `/opt`、`/srv` 或 `/mnt` 中部署数据库持久化数据。

## 2. 最终方案

本项目采用：

```text
VMware 独立虚拟磁盘
→ 节点文件系统
→ Kubernetes 静态 Local PersistentVolume
→ PersistentVolumeClaim
→ StatefulSet
磁盘规划：

节点	磁盘容量	文件系统	挂载点	工作负载
k8s-worker1	20GB	XFS	/data/mysql	MySQL
k8s-worker2	10GB	XFS	/data/redis	Redis
3. Kubernetes 存储对象

创建 StorageClass：

name: local-storage
provisioner: kubernetes.io/no-provisioner
volumeBindingMode: WaitForFirstConsumer
reclaimPolicy: Retain

创建两个静态 Local PersistentVolume：

opslab-mysql-local-pv
opslab-redis-local-pv

每个 PV 必须包含明确的节点亲和性：

MySQL PV → k8s-worker1
Redis PV → k8s-worker2

PVC 将显式绑定到对应 PV，避免数据库卷被错误匹配。

4. 数据保护策略

PersistentVolume 使用：

persistentVolumeReclaimPolicy: Retain

删除 PVC 后：

PV 不自动删除数据；
节点磁盘中的数据目录继续保留；
重新使用前必须进行人工检查和清理；
禁止未经确认直接格式化或删除数据目录。
5. 可用性边界

Local PV 只解决 Pod 重建、容器重启和工作负载重新创建后的本地数据持久化。

它不提供：

跨节点复制；
节点故障后的自动磁盘迁移；
数据库主从高可用；
分布式存储冗余；
自动快照和远程备份。

如果承载数据的 Worker 故障，绑定该 Local PV 的 Pod不能直接在另一个节点恢复。

本项目后续通过以下实践补充稳定性：

MySQL 逻辑备份与恢复；
Redis 持久化配置；
节点故障演练；
PVC 和 StatefulSet 恢复测试；
监控磁盘容量和数据库状态；
在项目报告中说明生产环境应使用云盘、SAN、Ceph、Longhorn 或其他远程/分布式存储。
6. 禁止事项

未经单独验收，禁止：

在 /dev/sda 上重新分区；
格式化现有系统分区；
启用已经禁用的 Swap 分区；
使用系统盘目录替代专用数据盘；
使用未包含 nodeAffinity 的 Local PV；
将 reclaimPolicy 设置为 Delete；
删除 PVC 后直接认为底层数据已经删除；
同时为多个数据库 Pod挂载同一个 ReadWriteOnce Local PV。
