# MySQL StatefulSet 持久化重建权限故障排查与修复报告

## 1. 故障概述

在 Kubernetes 项目中部署 MySQL 8.4.10 StatefulSet，并使用 Static Local PV 挂载 `/data/mysql` 作为数据库持久化目录。

部署目标链路：

```text
MySQL StatefulSet
→ opslab-mysql-data PVC
→ opslab-mysql-local-pv
→ k8s-worker1
→ /data/mysql
→ XFS 独立数据盘
```

为了让 MySQL 主容器以非 root 用户运行，同时解决宿主机数据目录初始为 `root:root` 的问题，在 StatefulSet 中设计了一个 initContainer：

```text
initContainer
→ 以 UID 0 启动
→ 调整 /var/lib/mysql 权限
→ 主容器以 mysql 用户 999:999 启动
```

整个过程中先后暴露出两个不同但相关的权限问题：

1. 第一次部署时，initContainer 的 `chmod` 因 Linux Capability 不足失败；
2. 第一次修复后 MySQL 可以正常初始化，但删除 Pod 验证持久化恢复时，新的 initContainer 又因递归 `chown -R` 无法读取已有数据库目录而失败。

最终通过：

```text
补充 CAP_FOWNER
+
将递归 chown -R 改成只修改挂载根目录
```

解决问题。

最终完成了：

```text
MySQL 初始化
→ 写入业务数据
→ 删除 Pod
→ StatefulSet 自动重建
→ 原 Local PV 重新挂载
→ MySQL 使用已有数据启动
→ 原业务数据完整保留
```

---

# 2. 基础环境

MySQL：

```text
MySQL 8.4.10
```

固定镜像：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/mysql@sha256:3a299410177c29055174e5439d7ba2ad4377f20bd716ace29f67c42497012916
```

镜像运行身份预检：

```text
CURRENT USER:
uid=0(root) gid=0(root)

MYSQL USER:
uid=999(mysql) gid=999(mysql)

mysql:x:999:999::/var/lib/mysql:/bin/bash
```

因此确定：

```text
MySQL UID = 999
MySQL GID = 999
```

存储：

```text
PVC:
opslab-mysql-data

PV:
opslab-mysql-local-pv

Local Path:
/data/mysql

Node:
k8s-worker1

Filesystem:
XFS

PV/PVC:
Bound
```

宿主机首次部署前：

```text
/data/mysql
owner = root
group = root
mode  = 755
```

并且目录为空。

---

# 3. 初始 StatefulSet 权限设计

为避免直接让正式 MySQL 容器长期以 root 身份运行，主容器采用：

```yaml
securityContext:
  runAsUser: 999
  runAsGroup: 999
  runAsNonRoot: true
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL
```

而 initContainer 用于准备 Local PV：

```yaml
securityContext:
  runAsUser: 0
  runAsGroup: 0
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL
    add:
      - CHOWN
```

初始命令为：

```bash
chown -R 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

设计思想是：

```text
宿主机 /data/mysql = root:root
        ↓
Local PV 挂载到 /var/lib/mysql
        ↓
initContainer 修改成 mysql:mysql
        ↓
正式 MySQL 使用 UID/GID 999:999
```

思路本身没有问题，但 Linux Capability 裁剪得过于严格。

---

# 4. 第一次故障：initContainer CrashLoopBackOff

## 4.1 故障现象

正式创建 MySQL 后：

```bash
kubectl rollout status \
  statefulset/opslab-mysql \
  -n opslab \
  --timeout=180s
```

一直无法完成，最终：

```text
Waiting for 1 pods to be ready...
error: timed out waiting for the condition
```

进一步检查：

```bash
kubectl get pod opslab-mysql-0 -n opslab -o wide
```

结果：

```text
NAME             READY   STATUS                  RESTARTS
opslab-mysql-0   0/1     Init:CrashLoopBackOff
```

这说明问题发生在：

```text
initContainer
```

而不是 MySQL 主容器。

此时 MySQL 主容器状态仍然是：

```text
PodInitializing
```

说明 mysqld 根本还没有真正启动。

---

# 5. 第一次故障排查

## 5.1 检查 Pod Describe

执行：

```bash
kubectl describe pod opslab-mysql-0 -n opslab
```

发现 initContainer：

```text
prepare-data-permissions
```

不断：

```text
Created
Started
BackOff
```

并且：

```text
Exit Code: 1
Reason: Error
```

说明容器命令执行失败。

---

## 5.2 获取 initContainer 日志

执行：

```bash
kubectl logs opslab-mysql-0 \
  -n opslab \
  -c prepare-data-permissions
```

得到关键错误：

```text
chmod: changing permissions of '/var/lib/mysql': Operation not permitted
```

这条日志非常重要。

因为 initContainer 执行的是：

```bash
chown -R 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

报错发生在第二条：

```text
chmod
```

而不是：

```text
chown
```

因此可以推断：

```text
chown 已经成功
chmod 失败
```

---

# 6. 第一次故障根因分析

initContainer 虽然：

```yaml
runAsUser: 0
```

也就是 UID 0，但同时配置：

```yaml
capabilities:
  drop:
    - ALL
  add:
    - CHOWN
```

这意味着：

> 容器中的 UID 0 并不再拥有传统 root 的完整权限。

Linux 容器权限判断不仅看：

```text
UID = 0
```

还要看进程实际拥有的 Linux Capabilities。

当前只保留：

```text
CAP_CHOWN
```

因此：

```bash
chown -R 999:999 /var/lib/mysql
```

可以执行。

执行后：

```text
/var/lib/mysql
```

已经变成：

```text
owner = 999
group = 999
```

随后继续执行：

```bash
chmod 750 /var/lib/mysql
```

这时 initContainer 虽然 UID 仍然为 0，但：

```text
文件 owner = 999
进程 UID   = 0
```

同时已经没有完整 root 权限。

修改“不属于自己”的文件权限需要：

```text
CAP_FOWNER
```

但 initContainer 没有该 Capability。

因此内核拒绝：

```text
Operation not permitted
```

---

# 7. 第一次修复

最小影响方案不是：

```text
取消全部安全限制
```

也不是：

```text
privileged: true
```

更不是：

```text
chmod 777
```

而是只增加实际需要的 Capability：

```yaml
capabilities:
  drop:
    - ALL
  add:
    - CHOWN
    - FOWNER
```

即：

```text
CAP_CHOWN
→ 允许修改 owner/group

CAP_FOWNER
→ 允许修改非当前进程所有文件的权限位
```

这样仍然符合：

```text
最小权限原则
```

而不是给予 initContainer 完整 root 能力。

---

# 8. StatefulSet 更新后的第二个现象

修改 StatefulSet 后执行：

```bash
kubectl apply -f kubernetes/mysql/mysql.yaml
```

StatefulSet 显示：

```text
configured
```

但是原 Pod 仍然持续：

```text
Init:CrashLoopBackOff
```

日志仍然是之前的：

```text
chmod: changing permissions of '/var/lib/mysql': Operation not permitted
```

进一步发现，当前失败 Pod 仍然是原来的 Pod，并没有真正使用新的 initContainer 配置。

因此采取最小影响操作：

```bash
kubectl delete pod opslab-mysql-0 -n opslab
```

只删除失败的 Pod。

没有删除：

```text
StatefulSet
PVC
PV
Secret
ConfigMap
/data/mysql
```

StatefulSet 随后自动创建新的：

```text
opslab-mysql-0
```

检查新的 Pod：

```text
capabilities:
CHOWN
FOWNER
```

确认新模板已经实际生效。

---

# 9. 第一次修复后的结果

新 Pod initContainer：

```text
Completed
exit=0
```

说明：

```text
CHOWN + FOWNER
```

修复成功。

随后 MySQL 正式开始初始化。

日志显示：

```text
Initializing database files
```

然后：

```text
Database files initialized
```

创建项目数据库：

```text
Creating database opslab
```

创建业务用户：

```text
Creating user opslab_app
```

完成授权：

```text
Giving user opslab_app access to schema opslab
```

最后：

```text
MySQL init process done. Ready for start up.
```

正式 mysqld：

```text
ready for connections
Version: '8.4.10'
port: 3306
```

此时：

```text
opslab-mysql-0
1/1 Running
```

MySQL 第一次部署正式成功。

---

# 10. MySQL 数据持久化测试

为了验证 StatefulSet + Local PV 是否真正实现数据持久化，使用业务用户：

```text
opslab_app
```

在：

```text
opslab
```

数据库中创建测试表：

```sql
CREATE TABLE IF NOT EXISTS sre_persistence_test (
    id INT PRIMARY KEY,
    message VARCHAR(100) NOT NULL
);
```

写入：

```sql
INSERT INTO sre_persistence_test (id, message)
VALUES (1, 'mysql-local-pv-persistence-ok');
```

查询结果：

```text
id    message
1     mysql-local-pv-persistence-ok
```

至此已经证明：

```text
MySQL
→ PVC
→ Local PV
→ /data/mysql
```

可以正常写入真实数据。

---

# 11. 删除 MySQL Pod 验证 StatefulSet 自愈

记录当前 Pod UID 后，执行：

```bash
kubectl delete pod opslab-mysql-0 -n opslab
```

目的：

```text
主动删除 MySQL Pod
        ↓
StatefulSet 自动创建新 Pod
        ↓
新 Pod 重新挂载原 PVC
        ↓
MySQL 使用原 /data/mysql
        ↓
检查之前写入的数据是否仍存在
```

这是一次真实的：

```text
Pod 重建 + 数据持久化恢复测试
```

---

# 12. 第二次故障：新 Pod Init:Error

删除 Pod 后，StatefulSet 成功创建新的：

```text
opslab-mysql-0
```

但：

```bash
kubectl wait \
  --for=condition=Ready \
  pod/opslab-mysql-0 \
  -n opslab \
  --timeout=180s
```

再次长时间无法 Ready。

检查：

```text
NAME             READY   STATUS
opslab-mysql-0   0/1     Init:Error
```

Pod Conditions：

```text
Initialized=False
Ready=False
ContainersReady=False
PodScheduled=True
```

说明：

```text
调度正常
PVC 挂载正常
Pod 已经启动
但是 initContainer 再次失败
```

---

# 13. 第二次故障现场日志

执行：

```bash
kubectl logs opslab-mysql-0 \
  -n opslab \
  -c prepare-data-permissions
```

得到：

```text
chown: cannot read directory '/var/lib/mysql': Permission denied
```

这次错误已经不是：

```text
chmod
```

而是：

```text
chown -R
```

---

# 14. 为什么第一次可以 chown，第二次却失败

这是整个故障中最值得理解的部分。

## 第一次启动

最开始：

```text
/data/mysql
```

是一个几乎为空的目录：

```text
root:root
755
```

initContainer 执行：

```bash
chown -R 999:999 /var/lib/mysql
```

目录为空，因此递归操作非常简单。

随后 MySQL 初始化完成，在 `/var/lib/mysql` 下产生了大量真实数据库文件：

```text
ibdata
undo log
redo log
mysql system tables
opslab database
certificate files
configuration metadata
...
```

并且数据库目录现在已经属于：

```text
mysql:mysql
999:999
```

目录模式：

```text
750
```

---

# 15. 第二次 Pod 重建时发生了什么

新的 initContainer 再次启动：

```text
UID = 0
```

但为了安全，我们已经：

```yaml
capabilities:
  drop:
    - ALL
  add:
    - CHOWN
    - FOWNER
```

注意：

```text
UID 0 ≠ 完整 root 权限
```

因为大量 DAC 权限绕过能力已经被删除。

现在：

```text
/var/lib/mysql
owner = 999
group = 999
mode  = 750
```

initContainer 的进程：

```text
uid = 0
gid = 0
```

同时没有：

```text
CAP_DAC_OVERRIDE
CAP_DAC_READ_SEARCH
```

而：

```bash
chown -R
```

不是简单修改根目录 inode。

`-R` 代表：

```text
递归
```

它需要进入：

```text
/var/lib/mysql
```

读取目录项，并继续遍历整个数据库目录。

但是当前：

```text
750 mysql:mysql
```

对于已经失去 DAC bypass Capability 的 UID 0 进程而言，并没有足够权限遍历整个目录。

因此：

```text
chown -R
```

在读取目录时直接失败：

```text
cannot read directory '/var/lib/mysql': Permission denied
```

---

# 16. 为什么不直接继续增加 Capability

理论上，可以继续增加例如：

```text
CAP_DAC_OVERRIDE
```

或者：

```text
CAP_DAC_READ_SEARCH
```

让 initContainer 能够递归进入整个 MySQL 数据目录。

但是这不是最佳方案。

因为真正的问题并不是：

> “initContainer 权限还不够。”

而是：

> “我们为什么每次 Pod 启动都要递归修改整个数据库目录？”

MySQL 初始化以后，数据库文件本身已经由：

```text
mysql:999
```

创建和管理。

后续 Pod 重建时没有必要再：

```bash
chown -R
```

遍历并修改：

```text
整个 MySQL 数据目录
```

继续增加 Capability 只能掩盖设计问题。

---

# 17. 第二次故障真正根因

根因是 initContainer 的权限初始化逻辑：

```bash
chown -R 999:999 /var/lib/mysql
```

**不是幂等的合理设计。**

它把两种完全不同的生命周期混在了一起：

### 首次启动

```text
空数据盘
→ 需要准备挂载根目录权限
→ MySQL 初始化
```

### 后续启动

```text
已有完整数据库
→ 不应该再递归修改数据库所有文件
→ 应直接使用现有数据
```

对于数据库持久化卷而言，initContainer 应该只确保：

```text
挂载根目录
```

具有正确 owner/mode。

而不应该每次启动都：

```text
递归处理整个数据库数据树
```

---

# 18. 最终修复方案

把：

```bash
chown -R 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

修改为：

```bash
chown 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

也就是去掉：

```text
-R
```

最终 initContainer：

```yaml
initContainers:
  - name: prepare-data-permissions
    image: crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/mysql@sha256:3a299410177c29055174e5439d7ba2ad4377f20bd716ace29f67c42497012916

    command:
      - /bin/sh
      - -ec
      - |
        chown 999:999 /var/lib/mysql
        chmod 750 /var/lib/mysql

    securityContext:
      runAsUser: 0
      runAsGroup: 0
      allowPrivilegeEscalation: false
      capabilities:
        drop:
          - ALL
        add:
          - CHOWN
          - FOWNER

    volumeMounts:
      - name: mysql-data
        mountPath: /var/lib/mysql
```

---

# 19. 为什么最终方案是正确的

现在整个逻辑变为：

## 首次启动

宿主机：

```text
/data/mysql
root:root
```

挂载后：

```text
/var/lib/mysql
root:root
```

initContainer：

```text
chown 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

得到：

```text
mysql:mysql
750
```

正式 MySQL：

```text
UID=999
GID=999
```

可以正常初始化数据目录。

---

## 后续 Pod 重建

原 Local PV：

```text
/data/mysql
```

已经包含真实 MySQL 数据。

initContainer 只执行：

```text
确认挂载根目录 owner
确认挂载根目录 mode
```

不会递归遍历：

```text
InnoDB
系统库
业务库
redo/undo
证书
其他 MySQL 数据文件
```

然后 MySQL 直接使用已有数据启动。

因此它具备：

```text
可重复执行
幂等
最小权限
不破坏数据库内容
```

---

# 20. 最终恢复验证

修改 StatefulSet 后，再次删除失败 Pod：

```bash
kubectl delete pod opslab-mysql-0 -n opslab
```

StatefulSet 自动创建新 Pod。

initContainer 最终状态：

```text
Completed exit=0
```

Pod：

```text
NAME             READY   STATUS    RESTARTS
opslab-mysql-0   1/1     Running   0
```

节点：

```text
k8s-worker1
```

MySQL 日志：

```text
MySQL Server - start
InnoDB initialization has started
InnoDB initialization has ended
ready for connections
Version: '8.4.10'
port: 3306
```

特别值得注意的是：

这一次日志中没有重新出现：

```text
Initializing database files
Creating database opslab
Creating user opslab_app
```

说明 MySQL 识别到了：

```text
已有数据库目录
```

并直接使用原数据启动，而没有重新初始化数据库。

---

# 21. 持久化数据最终验收

新 Pod 创建成功后，再次执行：

```sql
SELECT * FROM sre_persistence_test;
```

结果：

```text
id    message
1     mysql-local-pv-persistence-ok
```

原数据完整存在。

因此可以确定：

```text
旧 MySQL Pod
        ↓ 删除
StatefulSet
        ↓
创建全新 Pod
        ↓
重新挂载 opslab-mysql-data
        ↓
Local PV
        ↓
/data/mysql
        ↓
使用已有数据库文件启动
        ↓
业务数据完整恢复
```

最终 Pod UID 也发生变化，例如：

```text
18f75e22-5fc7-4585-bc7f-705b6a5eefab
```

证明这是一个真正重新创建的新 Pod，而不是原容器简单重启。

---

# 22. 故障期间明确没有执行的高风险操作

整个排障过程中没有：

```text
删除 PVC
删除 PV
格式化 /dev/sdb1
清空 /data/mysql
重新创建 XFS
重新部署 Local PV
重启 Worker
重置 Kubernetes
删除 StatefulSet 数据
chmod 777
使用 privileged 容器
```

始终采用：

```text
保存现场
→ 查看 Pod 状态
→ describe
→ 查看 initContainer 日志
→ 确认故障层级
→ 最小修改 StatefulSet
→ 只删除失败 Pod
→ 由 StatefulSet 自动重建
→ 验证原数据
```

这是本次排障过程中非常重要的处理原则。

---

# 23. 故障排查方法总结

本次问题可以总结成下面的排错路径：

```text
kubectl rollout status 超时
        ↓
不要立即删资源
        ↓
kubectl get pod
        ↓
发现 Init:CrashLoopBackOff
        ↓
说明主容器尚未启动
        ↓
kubectl describe pod
        ↓
确认失败容器 = initContainer
        ↓
kubectl logs -c prepare-data-permissions
        ↓
得到真实 Linux 权限错误
        ↓
结合 securityContext / capabilities 分析
        ↓
进行最小修复
        ↓
重新创建失败 Pod
        ↓
继续观察
```

第二次也是相同原则：

```text
Pod 重建后无法 Ready
        ↓
发现 Init:Error
        ↓
不是怀疑 PV 损坏
不是怀疑 MySQL 数据损坏
        ↓
先看 initContainer log
        ↓
chown: cannot read directory
        ↓
结合已有数据目录权限和 Capability 推理
        ↓
发现 -R 设计不合理
        ↓
取消递归 chown
        ↓
Pod 成功恢复
```

---

# 24. 本次故障中最重要的三个知识点

## 24.1 UID 0 不等于拥有所有权限

在传统 Linux 主机中容易形成：

```text
root 什么都能做
```

的直觉。

但容器中如果：

```yaml
capabilities:
  drop:
    - ALL
```

即使：

```text
runAsUser: 0
```

UID 0 进程也可能没有：

```text
CAP_FOWNER
CAP_DAC_OVERRIDE
CAP_DAC_READ_SEARCH
```

因此：

```text
root 容器进程
```

也可能得到：

```text
Permission denied
Operation not permitted
```

判断容器权限必须同时看：

```text
UID/GID
+
Linux Capabilities
+
文件 owner/group/mode
```

---

## 24.2 数据库 initContainer 必须考虑幂等性

第一次启动面对的是：

```text
空目录
```

第二次启动面对的是：

```text
完整数据库
```

所以初始化脚本不能只考虑：

> “第一次能不能成功？”

还必须考虑：

> “Pod 删除重建后，再运行一次会发生什么？”

最终正确原则：

```text
只准备必须准备的目录
不要每次启动递归修改整个数据库数据树
```

---

## 24.3 StatefulSet 自愈和数据持久化是两个不同能力

StatefulSet 能保证：

```text
Pod 被删除
→ 自动创建新的同名 Pod
```

Local PV/PVC 保证：

```text
新 Pod
→ 重新挂载原来的持久化数据
```

真正完整的恢复链是：

```text
StatefulSet 自愈
+
PVC/PV 生命周期
+
Local PV nodeAffinity
+
文件系统持久化
+
应用本身能够使用已有数据重新启动
```

本次测试完整覆盖了这条链路。

---

# 25. 最终结论

本次 MySQL 持久化阶段先后出现两次真实权限故障。

第一次：

```text
chmod: Operation not permitted
```

根因：

```text
initContainer drop ALL capabilities
+
仅保留 CAP_CHOWN
+
缺少 CAP_FOWNER
```

修复：

```text
增加 CAP_FOWNER
```

第二次：

```text
chown: cannot read directory '/var/lib/mysql': Permission denied
```

根因：

```text
MySQL 初始化后目录变为 mysql:999、mode 750
+
initContainer 已删除 DAC bypass capabilities
+
chown -R 需要遍历完整数据库目录
+
每次启动递归修改数据库数据本身也不是合理的幂等设计
```

最终修复：

```text
chown -R 999:999 /var/lib/mysql
```

改为：

```text
chown 999:999 /var/lib/mysql
```

并保留：

```text
CAP_CHOWN
CAP_FOWNER
```

最终验证结果：

```text
initContainer = Completed exit=0

MySQL Pod = 1/1 Running

MySQL = 8.4.10

Node = k8s-worker1

PVC = Bound

Local PV = 正常挂载

MySQL StatefulSet 自动重建 = 成功

已有数据库直接恢复启动 = 成功

业务数据：
1 mysql-local-pv-persistence-ok

数据持久化 = 验证通过
```

因此，本次故障最终形成了一套经过实际验证的 MySQL StatefulSet + Local PV 权限处理方案：

```text
独立 XFS 数据盘
→ Static Local PV
→ PVC
→ StatefulSet
→ 最小权限 initContainer
→ MySQL 非 root 运行
→ Pod 自愈
→ 数据持久化恢复
```

本次事故没有造成数据丢失，也没有通过高风险操作绕过问题，而是基于真实日志逐层定位并完成最小影响修复。

