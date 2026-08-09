# MySQL StatefulSet 持久化部署、自愈验收与 initContainer 权限故障排查报告

## 摘要

本阶段在已经完成 Kubernetes Static Local PV 存储基础层建设的前提下，将 MySQL 8.4.10 作为有状态服务正式部署到三节点 kubeadm Kubernetes 集群中，并完成了从镜像身份核验、Secret/ConfigMap 管理、Service 与 StatefulSet 建模、非 root 运行、Local PV 权限初始化，到真实业务数据写入、Pod 删除、自愈重建和数据恢复的完整验收。

本阶段最终形成的运行链路为：

```text
MySQL 8.4.10 固定 Digest 镜像
        ↓
Secret + ConfigMap
        ↓
StatefulSet
        ↓
opslab-mysql-data PVC
        ↓
opslab-mysql-local-pv
        ↓
k8s-worker1
        ↓
/data/mysql
        ↓
独立 20GB XFS 数据盘
```

与普通“部署成功”不同，本阶段在实际运行过程中连续暴露了两次真实的 initContainer 权限故障。

第一次故障：

```text
chmod: changing permissions of '/var/lib/mysql': Operation not permitted
```

根因是 initContainer 虽然以 UID 0 运行，但已经通过：

```yaml
capabilities:
  drop:
    - ALL
  add:
    - CHOWN
```

删除绝大多数传统 root 能力。第一次 `chown` 成功将挂载根目录改为 `999:999` 后，后续 `chmod` 缺少 `CAP_FOWNER`，因此被内核拒绝。

第一次修复后，MySQL 成功完成首次初始化，并创建：

```text
数据库：opslab
业务用户：opslab_app
```

随后写入真实持久化测试数据，并主动删除 `opslab-mysql-0` 验证 StatefulSet 自愈。

第二次故障发生在 Pod 重建阶段：

```text
chown: cannot read directory '/var/lib/mysql': Permission denied
```

此时真正暴露出更深层的设计问题：

```bash
chown -R 999:999 /var/lib/mysql
```

虽然在首次空数据目录上可以运行，但它并不是一个适合数据库生命周期的幂等初始化策略。MySQL 首次运行后，持久卷中已经包含完整数据库文件，每次 Pod 重建都递归遍历、修改整个数据树既没有必要，又引入了额外权限需求和数据操作风险。

最终将其修改为：

```bash
chown 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

即只准备 Local PV 的挂载根目录，不递归处理已经由 MySQL 管理的数据文件。

最终验收结果：

```text
initContainer = Completed / exit 0
MySQL Pod      = 1/1 Running
Node           = k8s-worker1
MySQL          = 8.4.10
PVC            = Bound
PV             = Bound
原业务数据      = 完整保留
```

重新创建的 MySQL Pod 成功直接使用已有数据目录启动，并再次查询得到：

```text
id  message
1   mysql-local-pv-persistence-ok
```

证明以下完整链路已经实际成立：

```text
MySQL 数据写入
→ 删除 Pod
→ StatefulSet 自愈
→ 新 Pod 创建
→ 原 PVC/PV 重新挂载
→ 已有 MySQL 数据目录恢复
→ 原业务数据完整存在
```

因此，本阶段真正完成的不是单纯的 MySQL Kubernetes 部署，而是一次完整的：

**有状态服务部署 → 权限故障定位 → 最小权限修复 → 幂等性问题发现 → 自愈验证 → 数据持久化恢复**

工程实践。

---

# 1. 项目背景

在进入 MySQL 阶段之前，Kubernetes 存储基础层已经完成。

MySQL 对应存储为：

```text
Node:
k8s-worker1

底层磁盘:
/dev/sdb 20GB

分区:
/dev/sdb1

文件系统:
XFS

宿主机挂载点:
/data/mysql

PV:
opslab-mysql-local-pv

PVC:
opslab-mysql-data

PV/PVC 声明容量:
18Gi
```

Local PV 已经过真实测试：

```text
Pod 写入数据
→ 删除 Pod
→ 新 Pod 重新挂载 PVC
→ 原数据仍然存在
```

因此进入 MySQL 阶段时，存储基础设施本身已经有独立证据证明正常。

这一点后来对排错非常重要：

> 当 MySQL Pod 出现 `Init:CrashLoopBackOff` 时，不能因为它是有状态服务就立即怀疑 PV/PVC 或磁盘损坏。

此前已经建立的存储证据链，使排查能够迅速聚焦到真正的失败层级。

---

# 2. 为什么 MySQL 使用 StatefulSet

MySQL 与 FastAPI 的运行模型不同。

FastAPI 是典型无状态应用：

```text
Pod A 删除
→ Deployment 创建 Pod B
→ 只要代码和配置一致即可继续工作
```

MySQL 则不仅包含一个运行进程，还包含持续变化的数据状态：

```text
数据库文件
InnoDB 数据
redo/undo
系统表
业务表
用户及权限
```

因此 MySQL 的生命周期不仅是：

```text
启动一个容器
```

而是：

```text
稳定的工作负载身份
+
固定的数据卷
+
受控的启动/终止过程
+
Pod 重建后重新使用原数据
```

StatefulSet 比普通 Deployment 更适合作为这一类工作负载的控制器。

本项目虽然只有：

```text
replicas=1
```

而且 PVC 是预先创建的，并没有使用 `volumeClaimTemplates`，但 StatefulSet 仍提供了：

```text
稳定 Pod 名称：opslab-mysql-0
稳定 ordinal：0
受控的 Stateful 工作负载生命周期
与 Headless Service 配合的稳定网络身份
```

因此最终采用：

```text
StatefulSet
```

而不是普通 Deployment。

必须指出：

> StatefulSet 并不自动等于数据库高可用。

当前仍然是：

```text
单 MySQL 实例
+
单 Local PV
+
单 worker1 数据副本
```

因此本项目验证的是持久化和 Pod 自愈，不是 MySQL 主从或多副本 HA。

---

# 3. 为什么复用已有 opslab-mysql-data PVC

在 MySQL 部署之前，PVC：

```text
opslab-mysql-data
```

已经完成：

```text
Pending
→ Consumer Pod
→ Bound
```

并绑定：

```text
opslab-mysql-local-pv
```

而 PV 通过 nodeAffinity 指向：

```text
k8s-worker1
```

底层对应：

```text
/data/mysql
```

因此没有必要让 MySQL StatefulSet 再创建一套新的存储对象。

直接复用：

```yaml
persistentVolumeClaim:
  claimName: opslab-mysql-data
```

有两个重要价值。

第一，存储层与数据库工作负载生命周期解耦：

```text
StatefulSet 删除或 Pod 重建
≠
PVC/PV 数据删除
```

第二，可以继续使用已经经过真实读写和 Pod 重建验收的存储链路：

```text
opslab-mysql-data
→ opslab-mysql-local-pv
→ k8s-worker1
→ /data/mysql
```

而不是在部署数据库时同时引入新的存储变量。

---

# 4. 为什么固定 MySQL 镜像 Digest

项目没有直接使用：

```text
mysql:latest
```

也没有只依赖：

```text
mysql:8.4.10
```

最终正式运行镜像固定为：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/mysql@sha256:3a299410177c29055174e5439d7ba2ad4377f20bd716ace29f67c42497012916
```

原因在于：

```text
Tag 是名字
Digest 才是镜像内容身份
```

即使：

```text
:8.4.10
```

理论上已经比 `latest` 稳定，但标签仍然属于可变引用。

Digest：

```text
sha256:3a299410...
```

则固定到了某一份具体 manifest 内容。

因此后续：

```text
重新创建 Pod
节点重新拉取镜像
故障恢复
项目复现
```

都不会因为标签背后的镜像发生变化而引入新的不确定性。

这对于故障分析尤其重要：

> 如果运行镜像本身可能变化，那么两次 Pod 重建之间就多了一个不必要变量。

固定 Digest 可以把这个变量排除。

---

# 5. 为什么先检查镜像，而不是直接部署

数据库镜像有一个很重要的问题：

> 容器内真正运行 MySQL 的 UID/GID 是什么？

不能凭经验假设：

```text
999:999
```

也不能因为网上某个版本这么配置，就认为当前镜像一定相同。

因此正式部署前创建了临时检查 Pod。

实际得到：

```text
===== CURRENT USER =====
uid=0(root) gid=0(root) groups=0(root)

===== MYSQL USER =====
uid=999(mysql) gid=999(mysql) groups=999(mysql)

mysql:x:999:999::/var/lib/mysql:/bin/bash
mysql:x:999:
```

同时检查：

```text
/var/lib/mysql
/var/run/mysqld
```

镜像内部均已经存在，并属于 mysql 用户。

因此真正确认：

```text
mysql UID = 999
mysql GID = 999
```

而不是猜测。

MySQL Official Image 的入口脚本也明确包含“如果入口当前是 root，则切换到专用 mysql 用户”的逻辑；同时会根据数据目录中是否已经存在 `mysql` 系统目录来判断数据库是否已经初始化。

本项目正式 StatefulSet 又进一步显式设置：

```yaml
runAsUser: 999
runAsGroup: 999
```

因此正式 MySQL 容器直接以 mysql 身份运行。

---

# 6. MySQL 正式资源设计

正式清单：

```text
kubernetes/mysql/mysql.yaml
```

包含：

```text
ConfigMap
ClusterIP Service
Headless Service
StatefulSet
```

敏感凭据单独通过 Kubernetes Secret 提供。

---

# 7. Secret 的职责

Kubernetes Secret：

```text
opslab-mysql-secret
```

包含：

```text
MYSQL_ROOT_PASSWORD
MYSQL_DATABASE
MYSQL_USER
MYSQL_PASSWORD
```

其中：

```text
MYSQL_DATABASE=opslab
MYSQL_USER=opslab_app
```

真实密码没有写入 Git。

控制节点本地保存：

```text
~/.config/opslab/mysql.env
```

并设置：

```text
chmod 600
```

这种做法的目标是将：

```text
非敏感部署配置
```

和：

```text
数据库凭据
```

分离。

需要说明的是，Kubernetes 原生 Secret 本身不是完整的生产级秘密管理平台。

实验项目中可以使用，但生产环境还应考虑：

```text
etcd encryption at rest
RBAC
外部 Secret Manager
密钥轮换
审计
```

等能力。

---

# 8. ConfigMap 的职责

ConfigMap：

```text
opslab-mysql-config
```

用于保存非敏感 MySQL 配置。

实际配置包括：

```ini
[mysqld]
character-set-server=utf8mb4
collation-server=utf8mb4_0900_ai_ci
max_connections=200
skip-name-resolve
```

它与 Secret 分开的原因是：

```text
数据库参数 ≠ 数据库密码
```

这样可以做到：

```text
配置可版本化
凭据不进入 Git
```

---

# 9. 为什么同时存在 ClusterIP Service 和 Headless Service

普通 ClusterIP Service：

```text
opslab-mysql
```

用于未来应用访问数据库：

```text
FastAPI
→ opslab-mysql:3306
```

它提供一个稳定的 Kubernetes Service 地址，使客户端不需要直接依赖 Pod IP。

Headless Service：

```text
opslab-mysql-headless
clusterIP: None
```

则作为 StatefulSet：

```yaml
serviceName: opslab-mysql-headless
```

对应的稳定网络身份基础。

当前只有单副本，因此 Headless Service 的优势没有被完全发挥，但它仍然符合 StatefulSet 的标准建模方式，也为未来多副本或更复杂数据库拓扑保留了正确结构。

---

# 10. Probe 的职责

正式 StatefulSet 配置：

```text
startupProbe
readinessProbe
livenessProbe
```

三类 Probe 不承担同一个职责。

### startupProbe

用于回答：

> MySQL 是否已经完成启动阶段？

命令：

```bash
mysqladmin ping -h 127.0.0.1 --silent
```

首次初始化数据库可能明显慢于普通应用启动，因此 startupProbe 给 MySQL 足够时间，避免刚初始化就被 livenessProbe 误杀。

### readinessProbe

用于回答：

> 当前数据库是否已经能够真正处理需要认证的 SQL 请求？

使用 root 凭据执行：

```sql
SELECT 1;
```

只有 readiness 成功，Pod 才应该进入：

```text
Ready
```

### livenessProbe

用于回答：

> mysqld 进程是否仍然处于存活状态？

继续使用：

```text
mysqladmin ping
```

这样把：

```text
启动阶段
可服务状态
进程存活状态
```

进行了区分，而不是只配置一个 TCP 探针。

---

# 11. resources 的职责

MySQL 设置：

```text
requests:
  cpu: 250m
  memory: 512Mi

limits:
  cpu: 1
  memory: 1Gi
```

目的不是宣称这些数值适用于生产，而是在实验集群中建立明确的资源边界。

`requests` 用于 Kubernetes 调度决策。

`limits` 用于限制 MySQL 最大资源消费，避免单个数据库 Pod 无限制抢占节点资源。

这些值属于：

```text
实验环境初始 sizing
```

而不是生产容量规划结果。

真正生产环境应根据：

```text
数据量
QPS
buffer pool
连接数
IOPS
延迟
工作集大小
```

重新测量。

---

# 12. securityContext 设计

MySQL 正式容器：

```yaml
runAsUser: 999
runAsGroup: 999
runAsNonRoot: true
allowPrivilegeEscalation: false
capabilities:
  drop:
    - ALL
```

并使用：

```text
RuntimeDefault seccomp
```

核心目标是：

> mysqld 本身不需要长期拥有 root 权限。

因此正式数据库进程运行身份：

```text
uid=999(mysql)
gid=999(mysql)
```

实际验收也得到：

```text
uid=999(mysql) gid=999(mysql) groups=999(mysql)
```

这比直接让数据库容器长期以 root 身份运行更符合最小权限原则。

---

# 13. 为什么还需要 initContainer

这里存在一个关键矛盾。

Kubernetes 主容器希望：

```text
runAsUser=999
```

但 Local PV 底层目录最初实际是：

```text
/data/mysql
root:root
755
```

挂载到容器后对应：

```text
/var/lib/mysql
```

如果 MySQL 直接以 UID 999 启动，那么它不一定拥有初始化数据库所需的目录写权限。

因此需要一个生命周期很短的 initContainer：

```text
主容器启动前
→ 准备 Local PV 挂载根目录权限
→ 退出
→ 正式 MySQL 以非 root 身份运行
```

也就是说：

```text
高权限只用于“准备目录”
数据库长期运行保持非 root
```

这是比直接把 MySQL 主容器改成 root 更合理的职责分离。

---

# 14. 初始 initContainer 方案

第一版命令：

```bash
chown -R 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

securityContext：

```yaml
runAsUser: 0
runAsGroup: 0
allowPrivilegeEscalation: false
capabilities:
  drop:
    - ALL
  add:
    - CHOWN
```

设计思想是：

```text
Local PV root:root
↓
initContainer 临时取得修改 owner 的能力
↓
改成 mysql:mysql
↓
正式 mysqld 使用 UID/GID 999
```

问题并不在整体方向，而是在对 Linux Capability 的权限需求估计得不够完整。

---

# 15. 第一次真实故障

## 15.1 现象

正式执行：

```bash
kubectl apply -f kubernetes/mysql/mysql.yaml
```

资源全部创建：

```text
configmap/opslab-mysql-config created
service/opslab-mysql-headless created
service/opslab-mysql created
statefulset.apps/opslab-mysql created
```

随后：

```bash
kubectl rollout status \
  statefulset/opslab-mysql \
  -n opslab \
  --timeout=180s
```

最终超时：

```text
Waiting for 1 pods to be ready...
error: timed out waiting for the condition
```

这里没有直接得出：

```text
MySQL 启动失败
```

因为 `rollout status` 只说明：

> StatefulSet 在规定时间内没有达到 Ready。

必须继续确定 Pod 生命周期卡在哪一层。

---

# 16. 第一次故障：根据 Pod 生命周期缩小范围

检查：

```bash
kubectl get pod opslab-mysql-0 -n opslab -o wide
```

得到：

```text
READY   STATUS
0/1     Init:CrashLoopBackOff
```

StatefulSet：

```text
READY
0/1
```

这里已经得到第一条关键结论：

```text
Pod 已调度
但 initContainer 尚未完成
```

因此正式 MySQL 容器实际上还没有启动。

进一步查看主容器：

```text
State:
Waiting

Reason:
PodInitializing
```

这意味着当前故障层级是：

```text
initContainer
```

而不是：

```text
mysqld
readinessProbe
Service
MySQL SQL 配置
```

---

# 17. 第一次故障：describe 建立第二层证据

`kubectl describe pod` 显示：

```text
prepare-data-permissions

Last State:
Terminated

Reason:
Error

Exit Code:
1
```

Events 则不断出现：

```text
Pulled
Created
Started
BackOff
```

说明：

```text
镜像可以正常访问
容器可以成功创建
容器可以正常启动
但容器内部命令执行失败
```

因此又排除了：

```text
ImagePullBackOff
CreateContainerConfigError
PVC Pending
调度失败
```

等方向。

---

# 18. 第一次故障：initContainer 日志锁定问题

执行：

```bash
kubectl logs opslab-mysql-0 \
  -n opslab \
  -c prepare-data-permissions
```

得到关键日志：

```text
chmod: changing permissions of '/var/lib/mysql': Operation not permitted
```

第一版 initContainer 使用：

```bash
/bin/sh -ec
```

执行：

```bash
chown -R 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

因为 shell 开启了 `-e`：

> 如果第一条 `chown` 已经失败，脚本就应在第一条命令处终止，不会继续执行到 `chmod`。

而实际日志来自第二条：

```text
chmod
```

因此可以合理确定：

```text
chown 已经成功执行
chmod 才是失败点
```

这成为后续 Capability 分析的重要证据。

---

# 19. 为什么 UID 0 仍然会 Operation not permitted

这是第一次故障最关键的 Linux 知识点。

initContainer 明明配置：

```yaml
runAsUser: 0
```

为什么 root 还会：

```text
Operation not permitted
```

原因是 Linux 从 2.2 开始已经把传统超级用户权限拆分为多个独立的：

```text
Capabilities
```

这些能力可以独立添加或删除。`CAP_CHOWN` 允许改变文件 UID/GID，而 `CAP_FOWNER` 可以绕过许多通常要求进程 fsuid 与文件 owner 匹配的检查，例如 `chmod(2)`。

当前 initContainer：

```text
UID = 0
```

但：

```yaml
drop:
  - ALL

add:
  - CHOWN
```

即有效权限被主动裁剪。

所以：

```text
UID 0
```

不能简单等价为：

```text
拥有完整传统 root 权限
```

---

# 20. 第一次故障的完整因果链

初始目录：

```text
/var/lib/mysql
对应 /data/mysql

owner=root
group=root
mode=755
```

initContainer 首先执行：

```bash
chown -R 999:999 /var/lib/mysql
```

因为具有：

```text
CAP_CHOWN
```

所以成功。

这一步之后挂载根目录已经变成：

```text
owner=999(mysql)
group=999(mysql)
```

接下来执行：

```bash
chmod 750 /var/lib/mysql
```

此时执行进程 UID 仍为 0，但目录 owner 已是 999。

由于：

```text
drop ALL
```

且没有重新加入：

```text
CAP_FOWNER
```

因此对非自身所有 inode 执行相应 chmod 权限检查时被内核拒绝。

Linux capability 文档明确指出，`CAP_FOWNER` 可以绕过通常要求进程文件系统 UID 与文件 owner 匹配的检查，其中就包括 `chmod(2)`。

最终表现：

```text
chmod: ... Operation not permitted
```

---

# 21. 为什么没有使用粗暴方案

面对这个错误，有很多“让它赶快跑起来”的办法。

例如：

```text
privileged: true
```

或者：

```text
不 drop capabilities
```

甚至：

```bash
chmod 777 /var/lib/mysql
```

这些方法可能让权限错误消失，但会带来更大的问题。

### privileged

意味着容器获得极高宿主机权限，与：

```text
只需要准备一个数据目录
```

的实际需求严重不匹配。

### 保留完整 root capability

会让 initContainer 获得远超需求的权限，违反最小权限原则。

### chmod 777

相当于用扩大所有用户访问权限的方式掩盖 owner/capability 设计问题，也不符合数据库目录的安全要求。

因此最终选择：

> 只补上当前失败操作真正需要的能力。

即：

```yaml
add:
  - CHOWN
  - FOWNER
```

---

# 22. 第一次最小修复

修改后：

```yaml
capabilities:
  drop:
    - ALL
  add:
    - CHOWN
    - FOWNER
```

其中：

```text
CHOWN
→ 修改 owner/group

FOWNER
→ 完成 chmod 所需的 owner 检查绕过
```

其余 Capability 继续全部删除。

因此这不是：

```text
放开安全限制
```

而是：

```text
把权限扩大到“刚好够完成任务”
```

---

# 23. 为什么 apply 后仍然看到旧错误

更新：

```bash
kubectl apply -f kubernetes/mysql/mysql.yaml
```

StatefulSet 返回：

```text
configured
```

但是再次观察发现：

```text
opslab-mysql-0
```

仍然是之前那个已经持续失败的旧 Pod：

```text
AGE 仍然很长
IP 仍是 10.244.1.21
```

initContainer 日志仍显示旧的：

```text
chmod ... Operation not permitted
```

这时没有再次修改 capability，也没有怀疑第一次分析错误，而是比较：

```text
StatefulSet template
```

和：

```text
当前 Pod spec
```

中的 capabilities。

最终新的 Pod 检查结果后来明确显示：

```json
{"add":["CHOWN","FOWNER"],"drop":["ALL"]}
```

从而证明修复后的模板确实已经正确进入 StatefulSet。

这里最重要的经验不是把 StatefulSet 更新机制简单概括成某一个固定原因，而是：

> `kubectl apply` 显示 configured，只证明控制器对象模板更新成功，并不能替代对当前实际 Pod spec 的检查。

当实际 Pod 仍在使用旧模板时，必须根据：

```text
Pod spec
controller revision
Pod 实际生命周期
```

判断，而不能只看 YAML 文件。

---

# 24. 为什么只删除失败 Pod

确认 StatefulSet 新模板正确之后，没有：

```text
删除 StatefulSet
删除 PVC
删除 PV
重建 MySQL
```

只执行：

```bash
kubectl delete pod opslab-mysql-0 -n opslab
```

原因是：

```text
失败对象 = Pod
期望状态管理者 = StatefulSet
```

删除失败 Pod 后：

```text
StatefulSet
→ 自动创建新的 opslab-mysql-0
```

并采用新的 Pod template。

这是非常典型的最小影响修复：

```text
只替换有问题的最小运行单元
不碰数据和持久化对象
```

---

# 25. 第一次修复验证

新 Pod：

```text
INIT STATUS:
Completed
exitCode=0
```

实际 capability：

```text
CHOWN
FOWNER
drop ALL
```

证明第一次权限修复生效。

随后 MySQL 主容器正式开始工作。

---

# 26. MySQL 首次初始化过程

日志明确显示：

```text
Entrypoint script for MySQL Server 8.4.10-1.el9 started.
```

随后：

```text
Initializing database files
```

然后：

```text
InnoDB initialization has started.
InnoDB initialization has ended.
```

数据库文件初始化完成：

```text
Database files initialized
```

官方入口脚本随后启动临时 mysqld：

```text
Starting temporary server
```

实际日志：

```text
Temporary server started.
```

然后创建项目数据库：

```text
Creating database opslab
```

创建业务用户：

```text
Creating user opslab_app
```

授权：

```text
Giving user opslab_app access to schema opslab
```

随后停止临时 server：

```text
Stopping temporary server
Temporary server stopped
```

最后：

```text
MySQL init process done. Ready for start up.
```

正式 MySQL：

```text
mysqld 8.4.10
port: 3306
ready for connections
```

整个过程与 MySQL Official Image 的入口逻辑一致：当数据目录中不存在已有 MySQL system database 时执行首次初始化；存在已有 `$DATADIR/mysql` 时则标记数据库已经存在并跳过这套初始化过程。

---

# 27. 第一次正式运行验收

最终：

```text
opslab-mysql-0
1/1 Running
NODE=k8s-worker1
```

PVC：

```text
opslab-mysql-data
Bound
opslab-mysql-local-pv
18Gi
```

正式容器：

```text
uid=999(mysql)
gid=999(mysql)
```

数据库：

```text
opslab
```

已经存在。

这证明：

```text
StatefulSet
→ PVC
→ Local PV
→ k8s-worker1
→ MySQL
```

的基本运行链路成立。

---

# 28. 一个验收中的非故障现象

曾执行：

```bash
mysql -Nse 'SELECT VERSION();'
```

得到：

```text
ERROR 1045 (28000):
Access denied for user 'mysql'@'localhost'
(using password: NO)
```

这个结果并不是 MySQL 故障。

因为容器当前 Linux 用户是：

```text
mysql
```

而 `mysql` 客户端没有显式指定：

```text
-u
-p
```

时，尝试使用了数据库用户：

```text
mysql
```

而不是 Kubernetes Secret 中配置的 root 或 `opslab_app`。

随后使用真实 root 凭据：

```text
SHOW DATABASES LIKE 'opslab';
```

成功返回：

```text
opslab
```

所以这只是：

```text
验收命令认证参数不完整
```

而不是数据库不可用。

这里也体现了一个容易混淆的知识点：

```text
Linux 用户 mysql
≠
MySQL 数据库用户 mysql
```

二者属于完全不同的身份体系。

---

# 29. 持久化验收设计

仅仅看到：

```text
MySQL Pod Running
```

并不能证明持久化真正有效。

必须产生一个：

```text
只有数据库数据卷才能保存的真实状态
```

然后破坏 Pod 生命周期，再观察它是否回来。

因此使用：

```text
opslab_app
```

在：

```text
opslab
```

数据库中创建：

```text
sre_persistence_test
```

并写入：

```text
id=1
message=mysql-local-pv-persistence-ok
```

真实结果：

```text
id  message
1   mysql-local-pv-persistence-ok
```

此时可以确定：

```text
MySQL
→ /var/lib/mysql
→ PVC
→ Local PV
→ /data/mysql
```

已经产生真实数据库数据。

---

# 30. 为什么使用 opslab_app 而不是只用 root

使用业务用户完成测试非常重要。

如果所有检查都只用：

```text
root
```

只能证明：

```text
root 可以操作数据库
```

而不能证明初始化阶段创建的：

```text
opslab_app
```

以及其对：

```text
opslab
```

数据库的权限配置正确。

因此这一测试同时验证：

```text
Secret
→ MYSQL_USER
→ MYSQL_PASSWORD
→ 数据库创建
→ 用户创建
→ 用户授权
→ 真实业务写入
```

完整成立。

---

# 31. 主动删除 Pod 验证 StatefulSet 自愈

建立测试数据后，主动执行：

```bash
kubectl delete pod opslab-mysql-0 -n opslab
```

这里故意删除的是：

```text
Pod
```

而不是：

```text
StatefulSet
PVC
PV
```

目标是模拟：

```text
数据库 Pod 丢失
```

然后观察：

```text
StatefulSet 是否重新创建 Pod
新 Pod 是否重新使用原 PVC
MySQL 是否能够从原数据库目录启动
原数据是否仍存在
```

这一步才是真正的：

```text
StatefulSet 自愈 + 持久化恢复
```

联合验收。

---

# 32. 第二次真实故障

新的：

```text
opslab-mysql-0
```

创建后没有进入 Ready。

实际状态：

```text
READY   STATUS
0/1     Init:Error
```

或者随后进入 initContainer 重试。

Pod Conditions：

```text
PodReadyToStartContainers=True
Initialized=False
Ready=False
ContainersReady=False
PodScheduled=True
```

从这里立即可以判断：

```text
调度已经成功
Pod sandbox 已经准备
但 initContainer 没完成
```

再次不是 MySQL 主容器故障。

主容器仍然：

```text
waiting:
  reason: PodInitializing
```

---

# 33. 第二次故障日志

initContainer：

```text
exitCode=1
reason=Error
```

日志：

```text
chown: cannot read directory '/var/lib/mysql': Permission denied
```

这一次与第一故障完全不同。

第一次：

```text
chmod failed
```

第二次：

```text
chown -R 在读取目录时失败
```

因此不能机械地认为：

```text
FOWNER 修复没有作用
```

必须重新分析数据目录已经发生的生命周期变化。

---

# 34. 第一次启动与第二次启动的根本区别

## 第一次

Local PV 基本为空：

```text
/data/mysql
root:root
755
```

目录下没有完整 MySQL 数据树。

执行：

```bash
chown -R 999:999 /var/lib/mysql
```

实际需要递归处理的内容极少。

---

## MySQL 初始化完成后

挂载根目录已经经过 initContainer：

```text
owner=999(mysql)
group=999(mysql)
mode=750
```

随后 MySQL 在其中创建完整数据库内容：

```text
mysql system database
InnoDB 数据文件
redo / undo
opslab 数据库
用户授权数据
证书及其他 MySQL 元数据
```

因此第二次 Pod 启动面对的已经不是：

```text
一个空挂载点
```

而是：

```text
一个真实数据库数据目录
```

这是第二次故障分析的关键转折。

---

# 35. 为什么 chown -R 的含义发生了变化

第一版命令：

```bash
chown -R 999:999 /var/lib/mysql
```

其中真正有问题的是：

```text
-R
```

`chown` 根目录本身，只需要定位这个 inode 并修改其 owner。

但：

```text
chown -R
```

意味着需要：

```text
进入目录
枚举目录内容
继续进入子目录
递归访问整个数据树
```

所以它对文件系统访问权限的要求明显更高。

---

# 36. 为什么 UID 0 这次仍不能进入目录

新的 initContainer 仍然：

```text
runAsUser=0
runAsGroup=0
```

但同时：

```text
drop ALL

只 add:
CHOWN
FOWNER
```

Linux 的传统 root 权力已经被拆分为多个 capabilities；`CAP_DAC_OVERRIDE` 用于绕过文件读/写/执行权限检查，而 `CAP_DAC_READ_SEARCH` 可以绕过文件读权限以及目录的 read/search 权限检查。

当前挂载根目录：

```text
mysql:mysql
750
```

而 initContainer 进程：

```text
uid=0
gid=0
```

但它没有保留：

```text
CAP_DAC_OVERRIDE
CAP_DAC_READ_SEARCH
```

因此它不能因为“数字 UID 是 0”就被简单理解成拥有宿主 Linux 中完整传统 root 的文件访问能力。

现场出现：

```text
cannot read directory
Permission denied
```

正与这种受限 capability 模型相符。

需要注意技术表达：

> 本项目没有额外使用 `capsh` 等工具逐位记录当时进程的 Effective capability bitmap，因此报告不应声称“已经通过 capsh 直接证明某一内核检查分支”。但根据 Kubernetes securityContext、目录权限状态、实际错误和 Linux capability 的定义，缺少 DAC bypass 能力与递归目录遍历失败之间具有完整一致的因果解释。

这也是本报告区分：

```text
现场直接证据
```

与：

```text
机制推理
```

的地方。

---

# 37. 为什么继续增加 DAC Capability 不是最佳方案

从纯粹“让 chown -R 成功”的角度，可以考虑继续加入：

```text
CAP_DAC_OVERRIDE
```

或者：

```text
CAP_DAC_READ_SEARCH
```

让 initContainer 获得更强的目录遍历能力。

但这只是在回答：

> 如何让当前脚本继续执行？

而不是回答：

> 当前脚本本身是不是正确设计？

这两者完全不同。

真正应该问：

```text
为什么一个已经正常存在的 MySQL 数据目录，
每次 Pod 重建都需要被递归 chown 一遍？
```

答案是：

```text
并不需要。
```

所以继续增加 capability 虽然可能绕过权限错误，但只是：

```text
用更多权限保留一个不合理的生命周期设计
```

而不是根治问题。

---

# 38. 第二次故障真正暴露的是幂等性问题

initContainer 的特点是：

```text
每次 Pod 创建
都会执行
```

因此其逻辑必须能够安全面对：

```text
第一次执行
第二次执行
第十次执行
故障恢复后再次执行
```

理想结果都应该相同。

这就是：

```text
幂等性
```

第一版：

```bash
chown -R 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

实际上把两类完全不同的对象混在了一起。

### initContainer 真正应该管理的

```text
Local PV 挂载根目录
```

### MySQL 应该自己管理的

```text
数据库数据树
InnoDB 文件
系统数据库
业务数据库
运行生成的文件
```

如果每次 Pod 重建都递归触碰整个数据树，initContainer 的职责就越界了。

---

# 39. 为什么数据库数据目录尤其不应该反复递归修改

数据库数据目录与普通缓存目录不同。

其中包含：

```text
数据库系统文件
事务相关文件
数据页
日志文件
表空间
权限数据
业务数据
```

即使 `chown -R` 理论上不会修改文件内容，它仍然会：

```text
遍历整个数据树
修改 inode metadata
产生额外磁盘 IO
扩大权限脚本影响范围
增加错误发生面
```

当数据库规模从几十 MB 增长到几十 GB、几百 GB 时，这种操作还可能显著拖慢 Pod 恢复。

因此数据库启动路径应该遵循：

> 能不碰已有数据，就不要碰已有数据。

---

# 40. 第二次故障的最终修复

将：

```bash
chown -R 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

修改为：

```bash
chown 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

核心变化只有一个：

```text
删除 -R
```

但设计含义发生了根本变化。

---

# 41. 最终 initContainer 职责

最终逻辑变成：

```text
Local PV 挂载
↓
只确保挂载根目录 owner=999:999
↓
只确保挂载根目录 mode=750
↓
退出
↓
正式 MySQL 自己管理数据内容
```

securityContext 仍然保留：

```yaml
runAsUser: 0
runAsGroup: 0
allowPrivilegeEscalation: false

capabilities:
  drop:
    - ALL
  add:
    - CHOWN
    - FOWNER
```

没有：

```text
privileged
DAC_OVERRIDE
DAC_READ_SEARCH
完整 root capability
```

因此不是扩大权限解决问题，而是缩小操作范围。

---

# 42. 为什么最终方案满足最小权限

initContainer 只获得：

```text
改变 owner 所需的 CHOWN
完成目录 chmod 所需的 FOWNER
```

不赋予：

```text
完整宿主访问能力
任意 mount 能力
网络管理能力
系统管理能力
```

权限范围与任务基本对应。

这体现：

```text
最小权限原则
```

---

# 43. 为什么最终方案满足幂等性

第一次空盘：

```text
root:root /var/lib/mysql
↓
chown 999:999
↓
chmod 750
↓
MySQL 初始化
```

后续已有数据库：

```text
mysql:mysql /var/lib/mysql
↓
chown 999:999
↓
结果仍是 999:999
↓
chmod 750
↓
结果仍是 750
↓
不进入数据树
```

重复执行不会不断产生新的状态变化。

因此：

```text
第一次执行
=
后续执行
```

在根目录权限这一目标上具有幂等性。

---

# 44. 为什么最终方案更保护数据库数据

新的 initContainer 不再：

```text
递归访问 MySQL 数据文件
```

因此把故障影响范围从：

```text
整个数据库目录树
```

缩小到：

```text
挂载根目录 inode
```

即：

```text
管理存储入口
不管理数据库内部内容
```

这比“让 initContainer 能够遍历所有数据库文件”更符合数据保护原则。

---

# 45. 第二次修复后的最终启动

应用修复后，再次只删除失败 Pod。

新：

```text
opslab-mysql-0
```

最终：

```text
===== INIT =====
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

IP：

```text
10.244.1.24
```

说明：

```text
StatefulSet
→ Pod 重建
→ Local PV 调度约束
```

均正常。

---

# 46. 最终 MySQL 日志为什么很关键

重建后的日志直接显示：

```text
Entrypoint script for MySQL Server 8.4.10-1.el9 started.
```

随后马上进入：

```text
MySQL Server - start.
mysqld 8.4.10 starting
InnoDB initialization has started.
InnoDB initialization has ended.
ready for connections.
```

重要的是：

> 这一次没有再次出现首次启动时的数据库初始化序列。

没有：

```text
Initializing database files
```

没有：

```text
Creating database opslab
```

没有：

```text
Creating user opslab_app
```

没有：

```text
MySQL init process done
```

而是直接使用原数据启动。

MySQL Official Image 的入口脚本会检查：

```text
$DATADIR/mysql
```

是否存在；存在时设置 `DATABASE_ALREADY_EXISTS=true`，从而跳过首次数据库初始化逻辑。

因此：

```text
现场日志
+
官方入口逻辑
```

共同支持：

> 新 Pod 使用的是已经存在的 MySQL 数据目录，而不是重新初始化了一个新的数据库。

---

# 47. 最终持久化数据验证

新 Pod 启动后，再次使用：

```text
opslab_app
```

查询：

```sql
SELECT * FROM sre_persistence_test;
```

真实输出：

```text
id  message
1   mysql-local-pv-persistence-ok
```

这条结果是本阶段最重要的最终证据之一。

它证明保存下来的不仅是：

```text
一个普通测试文件
```

而是：

```text
真实 MySQL 业务表
+
真实 SQL 数据
```

---

# 48. Pod UID 为什么也是证据

最终查询：

```bash
kubectl get pod opslab-mysql-0 \
  -n opslab \
  -o jsonpath='{.metadata.uid}'
```

得到新 Pod UID：

```text
18f75e22-5fc7-4585-bc7f-705b6a5eefab
```

Pod 名称仍然是：

```text
opslab-mysql-0
```

因为 StatefulSet 保留稳定身份。

但：

```text
metadata.uid
```

属于具体 Kubernetes 对象实例。

UID 发生变化证明：

> 当前 `opslab-mysql-0` 是 StatefulSet 创建的一个新 Pod 对象，而不是之前那个 Pod 简单恢复运行。

所以：

```text
Pod 名称稳定
+
Pod UID 改变
```

正好体现了 StatefulSet 的两个不同概念：

```text
逻辑身份稳定
对象实例可以重建
```

---

# 49. 最终完整证据链

整个 MySQL 阶段最终形成如下证据链：

```text
固定 MySQL 8.4.10 Digest
↓
实测 mysql UID/GID=999:999
↓
Secret 提供数据库凭据
↓
ConfigMap 提供非敏感配置
↓
StatefulSet 创建 opslab-mysql-0
↓
PVC=opslab-mysql-data
↓
PV=opslab-mysql-local-pv
↓
Local PV nodeAffinity
↓
k8s-worker1
↓
/data/mysql
↓
initContainer 准备挂载根目录
↓
MySQL 以 UID/GID 999 非 root 运行
↓
创建 opslab
↓
创建 opslab_app
↓
写入 sre_persistence_test
↓
id=1
message=mysql-local-pv-persistence-ok
↓
删除 opslab-mysql-0
↓
StatefulSet 创建新 Pod
↓
新 Pod UID 改变
↓
重新挂载原 PVC/PV
↓
不重新初始化数据库
↓
MySQL 直接使用已有数据启动
↓
SELECT 原表
↓
原数据完整存在
```

---

# 50. 最终运行状态

最终确认：

```text
StatefulSet:
opslab-mysql
READY=1/1
```

Pod：

```text
opslab-mysql-0
1/1 Running
RESTARTS=0
NODE=k8s-worker1
```

initContainer：

```text
Completed
exit=0
```

MySQL：

```text
8.4.10
ready for connections
port=3306
```

运行身份：

```text
uid=999(mysql)
gid=999(mysql)
```

PVC：

```text
opslab-mysql-data
Bound
```

PV：

```text
opslab-mysql-local-pv
Bound
```

业务数据：

```text
1 mysql-local-pv-persistence-ok
```

全部通过。

---

# 51. 故障排查过程中明确没有做什么

这部分非常重要。

第一次和第二次故障期间均没有：

```text
删除 PVC
删除 PV
清空 /data/mysql
格式化 /dev/sdb1
重新创建 XFS
重建 Local PV
重启 worker1
kubeadm reset
chmod 777
privileged=true
关闭 seccomp
给数据库主容器 root 权限
```

排查始终保持：

```text
先确定故障层
→ 查看证据
→ 修改最小对象
→ 删除最小运行单元
→ 重新验证
```

这样避免把：

```text
一个 initContainer 权限问题
```

扩大成：

```text
数据丢失事故
```

---

# 52. 第一次故障的排错模型

可以抽象成：

```text
rollout timeout
↓
不能直接判断 MySQL 故障
↓
kubectl get pod
↓
Init:CrashLoopBackOff
↓
主容器没有启动
↓
kubectl describe
↓
initContainer exit 1
↓
kubectl logs -c prepare-data-permissions
↓
chmod Operation not permitted
↓
检查 UID + capabilities
↓
发现缺少 FOWNER
↓
最小增加 CAP_FOWNER
↓
确认新模板
↓
只删除失败 Pod
↓
initContainer Completed
```

---

# 53. 第二次故障的排错模型

```text
删除 MySQL Pod
↓
StatefulSet 创建新 Pod
↓
kubectl wait 长时间不 Ready
↓
get pod
↓
Init:Error
↓
说明仍不是 mysqld 故障
↓
initContainer logs
↓
chown cannot read directory
↓
重新检查生命周期变化
↓
第一次是空目录
第二次是完整数据库
↓
发现 chown -R 每次递归数据库目录
↓
不是单纯“权限不够”
↓
是初始化设计不幂等
↓
取消 -R
↓
只准备挂载根目录
↓
新 Pod 成功
↓
原 SQL 数据仍存在
```

---

# 54. 这次故障真正体现的运维/SRE能力

## 54.1 不因 rollout timeout 直接下结论

```text
rollout status timeout
```

只表示：

```text
期望状态尚未达到
```

不能等同：

```text
MySQL 挂了
```

真正的第一步是：

```text
当前 Pod 生命周期进行到了哪一层？
```

这是整个排错方向能够正确的基础。

---

## 54.2 根据 Pod 生命周期定位故障层级

第一次和第二次都看到：

```text
Init:...
```

所以问题在：

```text
initContainer
```

主 MySQL 容器甚至还没有运行。

这直接排除了大量无关方向，例如：

```text
SQL 配置
readiness 查询
业务用户权限
MySQL 网络连接
Service 后端
```

这体现的是：

> 先确定系统层级，再深入具体组件。

---

## 54.3 使用 describe + logs 建立证据链

不是只看：

```text
STATUS
```

而是继续：

```text
get
→ describe
→ initContainer state
→ Events
→ logs
```

最终从：

```text
Init:CrashLoopBackOff
```

逐步收敛到：

```text
chmod Operation not permitted
```

再从第二次：

```text
Init:Error
```

收敛到：

```text
chown cannot read directory
```

排查过程始终建立在现场证据上。

---

## 54.4 区分 Kubernetes、存储、Linux、应用四个层级

整个系统至少有：

```text
Kubernetes 控制器层
PV/PVC 存储层
Linux 文件权限层
MySQL 应用层
```

第一次故障中：

```text
Pod 能调度
PVC 能挂载
镜像能启动
initContainer 命令失败
```

因此故障属于：

```text
Linux 权限 / securityContext
```

而不是：

```text
Kubernetes 调度
Local PV
MySQL 数据库
```

这种分层能力是运维故障处理最核心的能力之一。

---

# 55. UID/GID 与 Capability 的联合分析

如果只看到：

```text
runAsUser: 0
```

就认为：

```text
root 一定拥有所有权限
```

那么第一次故障很难解释。

本次实际理解了：

```text
进程身份
=
UID/GID
+
Capabilities
+
文件 owner/group/mode
+
其他安全机制
```

Linux capabilities 将传统 root 权限拆分为多个可独立控制的能力；本次实际涉及：

```text
CAP_CHOWN
CAP_FOWNER
CAP_DAC_OVERRIDE
CAP_DAC_READ_SEARCH
```

其职责并不相同。

这使问题从：

```text
为什么 root 也 permission denied？
```

转变成：

```text
这个具体文件操作需要哪一项 capability？
```

这是更精确的权限分析方式。

---

# 56. 最小权限原则

第一次故障没有选择：

```text
privileged
```

而只增加：

```text
FOWNER
```

第二次故障又没有继续增加：

```text
DAC_OVERRIDE
DAC_READ_SEARCH
```

而是反过来减少 initContainer 的操作范围。

最终形成：

```text
权限不够
≠
永远继续加权限
```

而应该问：

```text
当前操作是否真的有必要？
```

这正是最小权限思想比“让它能跑”更进一步的地方。

---

# 57. 最小影响修复

每次修复都没有动：

```text
PVC
PV
数据盘
XFS
Secret
```

只修改：

```text
StatefulSet Pod template
```

并在必要时删除：

```text
失败 Pod
```

由 StatefulSet 自动恢复。

这样把故障处理的 blast radius 控制在最小范围内。

---

# 58. StatefulSet 自愈不是只看 Pod 重建

简单看到：

```text
Pod 被重新创建
```

只能说明：

```text
Controller reconciliation
```

成功。

真正的有状态自愈还必须包括：

```text
原 PVC 重新使用
原数据库可以启动
原数据还存在
```

本次通过 SQL 记录：

```text
mysql-local-pv-persistence-ok
```

把这三件事情一起证明。

---

# 59. initContainer 幂等性

这是第二次故障最有价值的工程结论。

一个 initContainer 不能只问：

```text
第一次运行能不能成功？
```

必须问：

```text
第 N 次运行还能不能安全成功？
```

尤其数据库工作负载：

```text
首次 = 空数据目录
后续 = 完整数据库
```

两者差异非常大。

因此初始化逻辑必须按照：

```text
可重复
最小修改
明确职责
```

设计。

最终：

```bash
chown 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

只操作挂载入口，是比递归操作完整数据库更合理的生命周期设计。

---

# 60. 数据持久化恢复能力

本阶段不是用：

```text
Pod 文件系统中的普通文件
```

做验证，而是：

```text
MySQL 真实表
```

写入：

```text
id=1
mysql-local-pv-persistence-ok
```

Pod 被删除之后：

```text
新 MySQL 进程
+
新 Pod UID
+
原 Local PV
```

再次读到原数据。

所以最终验证的是：

```text
数据库级持久化
```

而不是简单文件存在。

---

# 61. 从“能跑”提升到“可重复运行”

如果项目只追求“让 MySQL 跑起来”，第一次解决：

```text
chmod Operation not permitted
```

以后看到 Pod `1/1 Running` 就可以停止。

但那样第二次：

```text
Pod 删除
→ initContainer 失败
```

永远不会被发现。

本次继续主动进行：

```text
数据写入
→ 删除 Pod
→ StatefulSet 自愈
```

才暴露：

```text
chown -R 非幂等
```

这意味着项目从：

```text
部署成功
```

提高到了：

```text
重复生命周期下仍然能够正确恢复
```

这更接近真实 SRE 验收思维。

---

# 62. 生产环境边界

本阶段已经具有很强的工程实践价值，但不能描述为生产级 MySQL 高可用。

当前仍存在明确边界：

```text
MySQL replicas=1
```

没有：

```text
主从复制
Group Replication
InnoDB Cluster
自动 failover
```

存储使用：

```text
Static Local PV
```

因此数据物理上仍绑定：

```text
k8s-worker1
```

worker1 如果发生不可恢复的节点或磁盘故障：

```text
MySQL 不能仅靠 StatefulSet 自动迁移数据到 worker2
```

此外当前还没有完成：

```text
定期逻辑备份
备份恢复演练
异地副本
生产级密钥管理
TLS 证书治理
数据库监控与告警
容量趋势监控
```

所以准确描述应该是：

> 在三节点 kubeadm 实验集群中，基于 Static Local PV 部署 MySQL StatefulSet，并完成非 root 运行、权限初始化、自愈重建以及数据持久化恢复验证。

而不是：

```text
生产级高可用 MySQL
```

---

# 63. 当前日志 Warning 的处理原则

MySQL 最终日志中还存在：

```text
CA certificate ca.pem is self signed
```

以及：

```text
Insecure configuration for --pid-file
```

还有：

```text
sha256_password is deprecated
```

这些 Warning 当前没有造成：

```text
启动失败
readiness 失败
SQL 查询失败
数据恢复失败
```

因此没有在当前阶段继续扩大范围优化。

这体现另一个运维原则：

> Warning 要记录，但不应该因为看到 Warning 就无边界地修改一个已经通过核心验收的系统。

后续进入安全加固或生产化阶段时再单独处理。

---

# 64. 本阶段真正完成的工程能力

最终完成的不是：

```text
kubectl apply mysql.yaml
```

而是以下完整能力。

### 镜像可重复性

```text
固定 MySQL 8.4.10 Digest
```

### 身份确认

```text
实际检查 mysql UID/GID
```

### 配置与凭据分离

```text
ConfigMap
+
Secret
```

### Kubernetes 有状态建模

```text
StatefulSet
+
Headless Service
+
ClusterIP Service
```

### 资源管理

```text
requests
limits
```

### 健康检查

```text
startup
readiness
liveness
```

### 容器安全

```text
non-root mysql
drop ALL
RuntimeDefault
allowPrivilegeEscalation=false
```

### Local PV 权限衔接

```text
受限 initContainer
```

### 实际故障排查

```text
capability 权限故障
+
initContainer 幂等故障
```

### StatefulSet 自愈

```text
Pod 删除
→ 自动重建
```

### 数据持久化

```text
真实 SQL 数据跨 Pod 生命周期保留
```

---

# 65. 本阶段为什么比“部署 MySQL 成功”更有项目价值

普通实验往往停留在：

```text
kubectl apply
→ Pod Running
→ mysql 登录成功
```

这种结果只能证明：

```text
当前这一刻可以运行
```

而本阶段进一步回答了：

```text
镜像是否可重复？
数据库是否非 root？
Local PV 权限是否正确？
第一次启动能否初始化？
Pod 删除后能否重建？
initContainer 是否可重复执行？
已有数据库是否会被误初始化？
原数据是否仍然存在？
权限故障时如何避免扩大事故？
```

这使项目从：

```text
Kubernetes 使用练习
```

提升到了：

```text
有状态服务生命周期管理实践
```

---

# 66. 最终结论

本阶段最终成功构建并验证：

```text
MySQL 8.4.10
↓
固定不可变 Digest
↓
Secret / ConfigMap
↓
ClusterIP + Headless Service
↓
StatefulSet
↓
非 root UID/GID 999
↓
最小权限 initContainer
↓
opslab-mysql-data PVC
↓
opslab-mysql-local-pv
↓
k8s-worker1
↓
/data/mysql
↓
真实业务数据
```

并通过两次真实故障完成了方案修正。

第一次故障：

```text
chmod: Operation not permitted
```

解决了：

```text
UID 0 与 Linux Capability 的关系
CAP_CHOWN 与 CAP_FOWNER 的职责差异
```

最终：

```text
CHOWN + FOWNER
```

完成最小权限修复。

第二次故障：

```text
chown: cannot read directory '/var/lib/mysql': Permission denied
```

最终发现真正问题不是：

```text
权限还不够多
```

而是：

```text
chown -R 整个数据库目录本身不是合理的幂等初始化设计
```

因此最终从：

```bash
chown -R 999:999 /var/lib/mysql
```

改为：

```bash
chown 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

把 initContainer 的职责严格限制在：

```text
准备 Local PV 挂载根目录
```

而不再递归干预 MySQL 自己管理的数据。

最终：

```text
initContainer Completed exit=0

opslab-mysql-0
1/1 Running
Node=k8s-worker1

MySQL 8.4.10
ready for connections
```

新 Pod 直接使用原数据库目录启动，没有重新执行首次数据库初始化。

最终 SQL：

```text
id  message
1   mysql-local-pv-persistence-ok
```

仍然存在。

因此可以正式判定：

```text
MySQL StatefulSet 部署          PASS
MySQL 非 root 运行              PASS
Secret / ConfigMap             PASS
Probe / resources              PASS
Local PV 挂载                   PASS
initContainer 最小权限          PASS
initContainer 重建幂等性        PASS
StatefulSet Pod 自愈            PASS
MySQL 已有数据恢复              PASS
真实业务数据持久化              PASS
```

本阶段最有价值的结论并不是：

> “MySQL 最后跑起来了。”

而是：

> 在真实权限故障和 Pod 生命周期变化中，通过逐层取证、最小权限分析和最小影响修复，将一个只能首次启动的 MySQL StatefulSet 改造成了能够安全重复创建、自动恢复并继续使用原持久化数据的有状态工作负载。

这正是本阶段最核心的运维/SRE项目价值。

