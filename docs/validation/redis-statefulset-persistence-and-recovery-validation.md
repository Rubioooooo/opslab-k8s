# Redis StatefulSet 持久化、权限模型与自愈恢复验收报告

## 1. 报告背景

在本项目中，MySQL 已经通过 StatefulSet + Local PV 完成持久化部署，并在实际部署过程中经历过两次与 Linux 文件权限、Capability 和 initContainer 幂等性有关的真实故障。

因此，在随后部署 Redis 时，没有直接复制 MySQL 的权限方案，而是重新从 Redis 官方镜像本身出发，逐步验证：

```text
镜像默认用户是谁
→ Redis 用户 UID/GID 是多少
→ 官方 Entrypoint 做了什么
→ /data 默认权限如何
→ Redis Server 最终以什么身份运行
→ Local PV 挂载后权限是否能够自动处理
→ 是否真的需要自定义 initContainer
→ AOF 是否能写入 Local PV
→ Pod 删除重建后数据能否恢复
→ 正式 StatefulSet 是否能够自动自愈
```

最终完成了 Redis 8.2.8 的：

```text
固定镜像
→ ACR 转存
→ Digest 固定
→ 镜像运行时分析
→ Local PV 权限验证
→ Secret
→ ACL 认证
→ ConfigMap
→ Service
→ Headless Service
→ StatefulSet
→ AOF 持久化
→ 非 root 运行
→ Pod 删除
→ StatefulSet 自愈
→ PVC 重新挂载
→ AOF 自动恢复
→ 数据完整性验证
```

这是一次完整的 Kubernetes 有状态应用部署与 SRE 恢复验证。

---

# 2. Redis 存储基础条件

Redis 已提前准备 Local PV：

```text
PVC:
opslab-redis-data

PV:
opslab-redis-local-pv

StorageClass:
local-storage

Capacity:
18Gi

AccessMode:
ReadWriteOnce

Node:
k8s-worker2

Host Path:
/data/redis
```

PV 使用 Local PersistentVolume，因此通过 PV 自身的 `nodeAffinity` 将使用该 PVC 的 Pod 自动约束到：

```text
k8s-worker2
```

正式 Redis StatefulSet 中没有额外写死 `nodeSelector`。

这意味着调度关系是：

```text
PVC
→ PV
→ PV nodeAffinity
→ k8s-worker2
```

而不是：

```text
StatefulSet
→ 手工 nodeSelector
→ k8s-worker2
```

这是 Local PV 正确使用方式的一部分。

---

# 3. Redis 镜像版本与不可变引用

本项目选择：

```text
Redis 8.2.8
Debian Bookworm
```

GitHub 中镜像定义：

```dockerfile
FROM redis:8.2.8-bookworm
```

镜像通过阿里云 ACR 海外构建后转存至：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/redis8.2.8-bookworm:8.2.8-bookworm
```

三节点使用 `crictl pull` 均成功：

```text
Image is up to date for sha256:07931c5cf5d1a8822798cefdbc99b2cf5890fcc81eb2087b502b2e976f1e94e5
```

三节点镜像完全一致。

进一步通过：

```bash
crictl inspecti
```

获取正式 Repository Digest：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/redis8.2.8-bookworm@sha256:93e4bfd32d899cabbc410d74bf70c539327aeb1e81dbf74d621d8da17169dba0
```

正式 Kubernetes StatefulSet 最终固定该 Digest，而不是依赖可变 Tag。

因此：

```text
Tag
8.2.8-bookworm
```

主要用于构建和人工识别。

真正用于正式部署的不可变身份是：

```text
sha256:93e4bfd32d899...
```

---

# 4. 为什么 Redis 不能机械复制 MySQL 的权限方案

## 4.1 MySQL 曾经为什么需要 initContainer

MySQL 使用 Local PV 后，需要让：

```text
/var/lib/mysql
```

能够被：

```text
UID=999
GID=999
```

的非 root MySQL 进程访问。

因此 MySQL 最终设计了一个 root initContainer，只处理挂载根目录：

```bash
chown 999:999 /var/lib/mysql
chmod 750 /var/lib/mysql
```

并通过：

```text
CAP_CHOWN
CAP_FOWNER
```

提供最小必要权限。

之所以形成这个方案，是经过真实故障后得到的。

最初使用：

```bash
chown -R 999:999 /var/lib/mysql
```

在已有数据库数据后会因为目录权限和 Capability 限制导致 Pod 重建失败。

最终才改成：

```text
只修正挂载根目录
不递归修改数据库内部数据
```

---

## 4.2 Redis 表面上看起来很像 MySQL

Redis 同样具有：

```text
StatefulSet
Local PV
非 root 进程
UID/GID 999
持久化目录
```

因此最容易产生的错误思路是：

```text
MySQL:
initContainer
→ chown 999:999
→ chmod

Redis:
照抄一份
```

但这种做法存在一个根本问题：

> Kubernetes 中的权限设计不仅取决于应用需要什么权限，还取决于镜像 Entrypoint 本身已经做了什么。

如果官方 Redis 镜像已经实现：

```text
检查数据目录
→ 调整权限
→ 主动降权
→ 启动 Redis
```

再人为添加 initContainer，就是重复实现镜像已经具备的功能。

更严重的是，如果不了解 Entrypoint 就直接添加：

```yaml
runAsUser: 999
```

或者：

```yaml
capabilities:
  drop:
    - ALL
```

还有可能破坏 Redis 官方镜像原有的初始化流程。

因此 Redis 阶段明确采用：

```text
先检查镜像
→ 再检查 Entrypoint
→ 再检查真实进程
→ 再检查真实 Local PV
→ 最后决定是否需要 initContainer
```

而不是从 MySQL YAML 复制权限配置。

---

# 5. 第一层证据：Redis 镜像内部用户

首先创建不挂载 PVC 的镜像检查 Pod。

真实结果：

```text
===== CURRENT USER =====
uid=0(root) gid=0(root) groups=0(root)
```

说明镜像默认启动身份是：

```text
root
```

但是镜像内部同时存在：

```text
===== REDIS USER =====
uid=999(redis) gid=999(redis) groups=999(redis)
```

对应：

```text
redis:x:999:999::/home/redis:/bin/sh
```

因此可以确认：

```text
Redis 用户 UID=999
Redis 用户 GID=999
```

---

# 6. 第二层证据：镜像原始 /data 权限

检查镜像内部：

```text
/data
```

结果：

```text
drwxr-xr-x redis redis /data
```

数字形式：

```text
UID=999
GID=999
mode=755
```

也就是：

```text
/data = 999:999
```

从镜像本身看，Redis 用户拥有 `/data`。

但是这个结果还不能直接作为 Kubernetes 持久化方案依据。

因为正式部署以后：

```text
Local PV
/data/redis
```

会挂载到：

```text
容器 /data
```

这时镜像层原本的：

```text
/data = 999:999
```

会被宿主机真实目录覆盖。

所以继续检查 Local PV，而不是在这里停止分析。

---

# 7. 第三层证据：Redis 官方 Entrypoint

检查：

```text
/usr/local/bin/docker-entrypoint.sh
```

发现官方镜像已经实现完整的权限处理和降权机制。

核心行为可以概括为：

```text
容器首先以 root 启动
        ↓
判断启动命令是不是 redis-server
        ↓
检查当前是否具有 SETUID / SETGID Capability
        ↓
检查 /data
        ↓
在符合安全条件时修正目录 owner/mode
        ↓
setpriv --reuid redis --regid redis
        ↓
清理 Capability
        ↓
设置 no-new-privileges
        ↓
再次执行 Entrypoint
        ↓
正式启动 redis-server
```

Entrypoint 中明确存在：

```text
setpriv --reuid redis --regid redis --clear-groups
```

同时存在：

```text
fix_data_dir_perms()
```

用于处理 Redis 数据目录权限。

这里出现了第一个非常重要的判断：

> Redis 官方镜像不是单纯“root 运行 Redis”，而是“root 进入 Entrypoint，完成必要准备之后主动降权”。

因此不能仅仅因为：

```text
CURRENT USER=root
```

就判定 Redis 正式进程运行在 root 下。

---

# 8. 第四层证据：Redis Server 真实运行身份

为了避免只根据 Shell 脚本推理，又启动了一个真正的临时 Redis Server。

没有挂载 PVC，只使用镜像默认 `/data`。

Redis 正常启动：

```text
Starting Redis Server
Redis version=8.2.8
Running mode=standalone, port=6379
Ready to accept connections tcp
```

检查：

```text
/proc/1/status
```

真实结果：

```text
Name: redis-server

Uid:
999 999 999 999

Gid:
999 999 999 999

CapInh:
0000000000000000

CapPrm:
0000000000000000

CapEff:
0000000000000000

CapBnd:
0000000000000000

NoNewPrivs:
1
```

同时：

```bash
redis-cli PING
```

返回：

```text
PONG
```

因此真正证明了：

```text
容器最初 root
        ↓
官方 Entrypoint
        ↓
完成用户切换
        ↓
redis-server PID 1
        ↓
UID=999
GID=999
        ↓
无有效 Capability
        ↓
NoNewPrivs=1
```

这比单纯查看 Dockerfile 或 `/etc/passwd` 更具有证明力。

---

# 9. 第五层证据：真实 Local PV 初始权限

随后检查真正用于 Redis 的 worker2：

```text
/data/redis
```

真实结果：

```text
owner=root
group=root
uid=0
gid=0
mode=755
```

目录为空：

```text
drwxr-xr-x root:root /data/redis
```

因此出现真正需要验证的问题：

```text
redis-server UID=999

/data/redis = root:root 755
```

如果直接让 UID 999 进程使用这个目录：

```text
owner rwx = root
group r-x
other r-x
```

Redis 不能向其中写入数据。

但此时没有立刻添加 initContainer。

原因是已经发现 Redis 官方 Entrypoint 自带：

```text
fix_data_dir_perms()
```

所以接下来需要验证的是：

> 官方 Entrypoint 能不能自己处理真实 Local PV。

---

# 10. 关键验证：不使用 initContainer 挂载真实 PVC

创建临时：

```text
redis-pvc-preflight
```

直接挂载：

```text
opslab-redis-data
```

到：

```text
/data
```

并且刻意没有配置：

```text
initContainer
runAsUser
runAsGroup
自定义 chown
nodeSelector
```

Redis 启动参数：

```text
appendonly yes
appendfsync everysec
save ""
```

结果 Pod 成功：

```text
Running
```

PID 1：

```text
UID=999
GID=999
CapEff=0
CapBnd=0
NoNewPrivs=1
```

Redis：

```text
PONG
```

这说明：

```text
root:root Local PV
```

没有阻止 Redis 官方镜像完成初始化。

---

# 11. 官方 Entrypoint 实际修改了什么

再次检查容器：

```text
/data
```

结果：

```text
drwxr-xr-x redis root /data
```

数字形式：

```text
UID=999
GID=0
mode=755
```

对应宿主机：

```text
/data/redis
```

也已经变成：

```text
uid=999
gid=0
mode=755
```

这证明 Redis 官方 Entrypoint 的权限修复确实作用到了：

```text
真实 Local PV
```

而不是只修改了容器镜像层。

这里 `/data` 为：

```text
999:0
```

完全能够满足 Redis 需求。

因为 Redis 用户 UID=999 是该目录 owner，并拥有：

```text
rwx
```

权限。

没有任何必要为了“看起来整齐”强行改成：

```text
999:999
```

---

# 12. GID 999 在宿主机上的显示问题

Redis 创建 AOF 以后，worker2 上看到：

```text
999:systemd-journal
```

但容器内部看到：

```text
redis:redis
```

这不是权限冲突。

Linux 文件系统真正保存的是：

```text
UID
GID
```

而不是用户名和组名。

在 Redis 容器里：

```text
GID 999 = redis
```

在 worker2 宿主机上：

```text
GID 999 = systemd-journal
```

所以：

```text
容器：
redis:redis

宿主机：
999:systemd-journal
```

本质上对应的数字都是：

```text
999:999
```

这也是容器权限排查中必须注意的一点：

> 判断跨容器/宿主机文件权限时优先看数字 UID/GID，而不是用户名。

---

# 13. AOF 持久化真实写入

Redis 启用：

```text
appendonly yes
appendfsync everysec
```

之后，在：

```text
/data/appendonlydir
```

真实生成：

```text
appendonly.aof.1.base.rdb
appendonly.aof.1.incr.aof
appendonly.aof.manifest
```

权限：

```text
UID=999
GID=999
mode=600
```

目录：

```text
appendonlydir
```

为：

```text
UID=999
GID=999
mode=700
```

说明 Redis UID 999 已经能够正确在 Local PV 中创建持久化数据。

AOF 状态：

```text
aof_enabled:1
aof_rewrite_in_progress:0
aof_current_size:175
```

真实写入测试：

```text
SET sre:persistence:test redis-local-pv-persistence-ok
```

随后：

```text
GET sre:persistence:test
```

成功。

---

# 14. 第一次 Pod 重建持久化验证

记录旧临时 Redis Pod UID：

```text
7249a897-0fac-4912-b4bf-a06e4d3a4269
```

删除该 Pod。

随后重新创建新的临时 Pod，并重新挂载：

```text
opslab-redis-data
```

新 Pod UID：

```text
4a699c34-8bda-4e0a-b293-82c3c5ff992a
```

UID 不同，证明是全新的 Pod。

新 Redis 日志明确显示：

```text
Reading RDB base file on AOF loading...
Loading RDB produced by version 8.2.8
RDB is base AOF
DB loaded from base file appendonly.aof.1.base.rdb
DB loaded from incr file appendonly.aof.1.incr.aof
DB loaded from append only file
Opening AOF incr file appendonly.aof.1.incr.aof on server start
Ready to accept connections tcp
```

最终：

```text
EXISTS sre:persistence:test
1
```

```text
TYPE sre:persistence:test
string
```

```text
GET sre:persistence:test
"redis-local-pv-persistence-ok"
```

```text
DBSIZE
1
```

因此完整证明：

```text
Redis 写入数据
→ AOF 写入 Local PV
→ Pod 删除
→ 新 Pod 创建
→ 同一 PVC 重新挂载
→ Redis 读取已有 AOF
→ key 自动恢复
```

---

# 15. 为什么最终确定 Redis 不需要自定义 initContainer

到这里已经形成完整证据链。

## 证据 1

Redis 官方镜像自带：

```text
fix_data_dir_perms()
```

说明镜像作者本身已经考虑数据目录权限初始化。

## 证据 2

官方 Entrypoint 会：

```text
root
→ setpriv
→ redis
```

主动完成降权。

## 证据 3

真实 Redis PID 1：

```text
UID=999
GID=999
```

并且：

```text
CapEff=0
CapBnd=0
NoNewPrivs=1
```

证明最终运行态安全性满足非 root 要求。

## 证据 4

真实 Local PV 初始：

```text
root:root 755
```

并不是预先人为修改好的目录。

## 证据 5

没有 initContainer 的情况下，Redis 官方 Entrypoint 成功将真实 Local PV 根目录调整为：

```text
UID=999
```

使 Redis 可写。

## 证据 6

Redis 成功在 Local PV 创建：

```text
appendonlydir
AOF base
AOF incr
AOF manifest
```

## 证据 7

第二个全新 Pod 再次挂载已有数据目录后仍然启动成功。

说明 Entrypoint 的处理逻辑能够面对：

```text
已有 Redis 数据目录
```

而不只是第一次面对空目录。

## 证据 8

AOF 数据跨 Pod 删除重建成功恢复。

因此最终结论是：

```text
Redis 官方镜像已经正确实现：
权限初始化
+
用户降权
+
数据目录处理
```

如果此时继续添加 MySQL 式 initContainer：

```text
root initContainer
→ chown
→ chmod
```

只是在重复镜像已有逻辑，并增加新的维护面和故障可能。

所以正式设计：

```text
Redis 不使用自定义 initContainer
```

不是为了“省事”，而是由真实实验得出的技术结论。

---

# 16. Redis 与 MySQL 权限方案的本质区别

两者虽然都是：

```text
StatefulSet
+
Local PV
+
UID 999
+
非 root
```

但最终设计完全不同。

## MySQL

```text
Local PV
        ↓
需要显式准备挂载根目录
        ↓
自定义 initContainer
        ↓
CHOWN + FOWNER
        ↓
只修改挂载根目录
        ↓
MySQL UID/GID 999
```

## Redis

```text
Local PV root:root
        ↓
官方 docker-entrypoint.sh
        ↓
fix_data_dir_perms
        ↓
官方自动处理目录 owner
        ↓
setpriv
        ↓
Redis UID/GID 999
        ↓
无需自定义 initContainer
```

因此真正的经验不是：

> MySQL 用 initContainer，所以 Redis 也用。

而是：

> 每个镜像都应该先理解自己的 Entrypoint、运行用户、文件权限和启动生命周期，再决定 Kubernetes securityContext 和 initContainer。

---

# 17. Secret 与认证设计

Redis 持久化基础验证完成后，开始正式安全配置。

本地真实密码保存：

```text
~/.config/opslab/redis.env
```

权限：

```text
600
```

真实密码不进入 Git。

Kubernetes Secret：

```text
opslab-redis-secret
```

包含：

```text
REDIS_PASSWORD
users.acl
```

其中 ACL 使用 Redis 用户认证机制。

Redis 正式配置：

```text
aclfile /run/secrets/redis/users.acl
```

Secret 以文件形式挂载：

```text
/run/secrets/redis/users.acl
```

同时将：

```text
REDIS_PASSWORD
```

注入应用环境变量，供：

```text
REDISCLI_AUTH
```

等内部客户端认证使用。

整个：

```text
kubernetes/
applications/
docs/
```

均执行过明文 Secret 泄漏扫描。

无匹配结果。

---

# 18. ConfigMap 与 Redis 正式配置

正式 Redis ConfigMap：

```text
opslab-redis-config
```

核心配置：

```text
bind 0.0.0.0
protected-mode yes
port 6379

dir /data

appendonly yes
appendfsync everysec
save ""

aclfile /run/secrets/redis/users.acl

maxmemory 384mb
maxmemory-policy noeviction

loglevel notice
```

持久化采用：

```text
AOF
```

策略：

```text
appendfsync everysec
```

关闭周期性 RDB save：

```text
save ""
```

这里仍然可能看到：

```text
appendonly.aof.1.base.rdb
```

因为 Redis 8 的多文件 AOF 机制可以使用 RDB 格式作为 AOF base 文件。

这与：

```text
save ""
```

并不冲突。

---

# 19. Service 与 StatefulSet

正式资源：

```text
ConfigMap:
opslab-redis-config

Service:
opslab-redis

Headless Service:
opslab-redis-headless

StatefulSet:
opslab-redis
```

StatefulSet：

```text
replicas=1
```

正式镜像固定 Digest：

```text
redis8.2.8-bookworm@sha256:93e4bfd...
```

PVC：

```text
opslab-redis-data
```

挂载：

```text
/data
```

没有：

```text
nodeSelector
```

最终 Pod 自动运行在：

```text
k8s-worker2
```

这再次验证 Local PV nodeAffinity 正常生效。

---

# 20. Probe 与资源限制

Redis 配置：

```text
startupProbe
readinessProbe
livenessProbe
```

均使用：

```text
redis-cli ping
```

通过：

```text
REDISCLI_AUTH
```

自动完成认证。

资源：

```text
requests:
  cpu: 100m
  memory: 256Mi

limits:
  cpu: 1
  memory: 512Mi
```

Redis：

```text
maxmemory 384mb
```

为容器本身、Redis Server、模块等留下额外内存空间，而不是直接将：

```text
maxmemory = memory limit
```

设置成完全相同。

---

# 21. 正式 ACL 认证验收

正式 Redis StatefulSet 启动后：

```text
opslab-redis-0
1/1 Running
RESTARTS=0
NODE=k8s-worker2
```

认证访问：

```text
redis-cli PING
```

在 `REDISCLI_AUTH` 存在时：

```text
PONG
```

故意去掉：

```text
REDISCLI_AUTH
```

之后：

```text
NOAUTH Authentication required.
```

这证明：

```text
Redis 正常工作
        +
认证配置生效
        +
无凭据客户端无法访问
```

---

# 22. 正式 StatefulSet 成功接管已有 Local PV

正式 StatefulSet 并不是在空磁盘上重新开始。

此前 preflight Pod 已经向：

```text
opslab-redis-data
```

写入：

```text
sre:persistence:test
=
redis-local-pv-persistence-ok
```

正式 StatefulSet 启动后日志：

```text
Reading RDB base file on AOF loading...
DB loaded from base file ...
DB loaded from incr file ...
DB loaded from append only file ...
Ready to accept connections tcp
```

查询：

```text
GET sre:persistence:test
```

结果：

```text
"redis-local-pv-persistence-ok"
```

因此证明正式：

```text
StatefulSet
```

成功接管了临时验证实例留下的已有 Local PV 数据。

---

# 23. 正式 StatefulSet 自愈实验

为了验证真正的 Kubernetes Controller 自愈，而不只是普通 Pod 手工重建，再进行一次正式 StatefulSet 故障模拟。

旧 Redis Pod UID：

```text
a24634ed-f787-4899-b7a4-9f6ca5f556e6
```

先写入专门用于 StatefulSet 恢复验证的数据：

```text
SET sre:statefulset:recovery redis-statefulset-recovery-ok
```

读取：

```text
"redis-statefulset-recovery-ok"
```

随后主动删除：

```text
opslab-redis-0
```

注意：

```text
没有删除 StatefulSet
没有删除 PVC
没有删除 PV
没有清空 /data/redis
```

StatefulSet Controller 自动重新创建：

```text
opslab-redis-0
```

新 Pod UID：

```text
4bc536a0-955d-4272-8b0f-89d8c2d7573f
```

新旧 UID 不同，证明发生了真正的：

```text
Pod 删除
→ Controller 自愈
→ 新 Pod 创建
```

而不是容器简单 restart。

---

# 24. 正式 StatefulSet 恢复日志

新 Pod：

```text
1/1 Running
RESTARTS=0
NODE=k8s-worker2
```

日志：

```text
Starting Redis Server

Reading RDB base file on AOF loading...

Loading RDB produced by version 8.2.8

DB loaded from base file appendonly.aof.1.base.rdb

DB loaded from incr file appendonly.aof.1.incr.aof

DB loaded from append only file

Opening AOF incr file appendonly.aof.1.incr.aof on server start

Ready to accept connections tcp
```

这说明新 Redis 不是创建了一个新的空数据库。

而是：

```text
重新挂载原 PVC
→ 找到已有 AOF
→ 加载 base
→ 加载 incr
→ 恢复数据库
→ 正常提供服务
```

---

# 25. 最终数据完整性验收

新 StatefulSet Pod 启动后：

认证：

```text
PONG
```

第一代 preflight 数据：

```text
GET sre:persistence:test
```

结果：

```text
"redis-local-pv-persistence-ok"
```

第二代正式 StatefulSet 数据：

```text
GET sre:statefulset:recovery
```

结果：

```text
"redis-statefulset-recovery-ok"
```

数据库：

```text
DBSIZE
2
```

因此两个不同时期写入的数据全部存在。

完整证据链：

```text
第一代临时 Pod
        ↓
写入 persistence:test
        ↓
AOF
        ↓
删除
        ↓
第二代临时 Pod
        ↓
数据恢复成功
        ↓
删除
        ↓
正式 StatefulSet
        ↓
再次读取旧数据
        ↓
写入 statefulset:recovery
        ↓
删除正式 opslab-redis-0
        ↓
StatefulSet Controller 自动重建
        ↓
重新挂载原 PVC
        ↓
自动加载 AOF
        ↓
两个 key 全部恢复
```

---

# 26. 最终完成的 Redis 能力

本阶段已经真实完成：

```text
Redis 8.2.8
```

```text
官方镜像固定版本
```

```text
GitHub → ACR 镜像转存
```

```text
完整 Digest 固定
```

```text
三节点预拉取
```

```text
镜像 UID/GID 实测
```

```text
官方 Entrypoint 分析
```

```text
真实 PID 1 权限验证
```

```text
Local PV 权限验证
```

```text
无自定义 initContainer 持久化验证
```

```text
AOF everysec
```

```text
Secret
```

```text
ACL 认证
```

```text
ConfigMap
```

```text
Service
```

```text
Headless Service
```

```text
StatefulSet
```

```text
startupProbe
```

```text
readinessProbe
```

```text
livenessProbe
```

```text
requests / limits
```

```text
maxmemory
```

```text
非 root Redis
```

```text
Capability 清零
```

```text
NoNewPrivs
```

```text
真实数据写入
```

```text
Pod 删除重建
```

```text
StatefulSet Controller 自愈
```

```text
PVC/PV 重新挂载
```

```text
AOF 自动加载
```

```text
数据完整恢复
```

整个 Redis StatefulSet 生命周期已经形成完整闭环。

---

# 27. 真正具有运维/SRE项目价值的部分

本阶段真正有价值的并不是：

```text
会写一个 Redis YAML
```

因为单纯把 Redis 跑起来非常容易。

真正具有运维/SRE价值的是以下几个方面。

## 27.1 没有机械套用已有方案

MySQL 已经有一套可工作的：

```text
initContainer
+ chown
+ chmod
```

方案。

但 Redis 没有照搬。

而是重新从：

```text
镜像
Entrypoint
UID/GID
Capability
Local PV
```

出发建立自己的技术判断。

这体现的是：

```text
理解系统行为
```

而不是：

```text
复制 YAML
```

---

## 27.2 用实验而不是猜测决定架构

整个 Redis 权限设计不是根据：

```text
“Redis 一般都是 999”
```

或者：

```text
“应该需要 chown”
```

得出的。

而是逐层验证：

```text
镜像 USER
→ redis UID/GID
→ /data
→ Entrypoint
→ PID 1
→ Capability
→ 宿主机 Local PV
→ 真实 PVC 挂载
→ 文件实际 owner
→ AOF 实际写入
```

最终才得出：

```text
无需 initContainer
```

这是非常典型的生产运维思维。

---

## 27.3 权限排查深入到了 Linux Capability 和 PID 1

不是只查看：

```bash
kubectl get pods
```

而是进一步检查：

```text
/proc/1/status
```

确认：

```text
Uid
Gid
CapEff
CapBnd
NoNewPrivs
```

由此证明：

```text
Redis 真正运行身份是非 root
并且已经移除 Capability
```

这比 YAML 上写：

```yaml
runAsNonRoot: true
```

更有实际证明力。

---

## 27.4 验证了镜像 Entrypoint，而不是破坏它

识别出 Redis 官方镜像本身存在：

```text
root
→ 权限准备
→ setpriv
→ redis
```

生命周期。

因此没有盲目加入：

```yaml
runAsUser: 999
```

去绕过这个机制。

也没有过早：

```yaml
capabilities:
  drop:
    - ALL
```

导致 Entrypoint 无法进行必要的用户切换。

真正关注的是：

```text
最终运行态安全
```

而不是：

```text
YAML 看起来最严格
```

---

## 27.5 验证了 Local PV 的实际权限变化

不是只看到：

```text
PVC Bound
```

就认为持久化已经完成。

还真正进入：

```text
worker2 /data/redis
```

检查：

```text
UID
GID
mode
文件
AOF
```

并验证容器内和宿主机之间数字 UID/GID 的映射。

这属于真正的 Kubernetes + Linux 存储权限排查。

---

## 27.6 验证了真正的数据持久化，而不是只验证 PVC 状态

项目没有停留在：

```text
PVC=Bound
PV=Bound
```

而是：

```text
SET key
→ AOF 文件产生
→ 删除 Pod
→ 创建新 Pod
→ AOF 加载
→ GET 原 key
```

这是“资源存在”和“业务数据真的持久化”之间的本质区别。

---

## 27.7 使用 Pod UID 证明真正发生重建

第一次：

```text
7249a897-...
→
4a699c34-...
```

正式 StatefulSet：

```text
a24634ed-...
→
4bc536a0-...
```

通过 Kubernetes Pod UID 而不是仅仅 Pod 名称证明：

```text
这是一个全新的 Kubernetes 对象
```

因为 StatefulSet Pod 名称始终还是：

```text
opslab-redis-0
```

如果只看名字，无法证明发生过真正重建。

---

## 27.8 验证了 Controller 自愈

主动删除：

```text
opslab-redis-0
```

StatefulSet Controller 自动恢复：

```text
opslab-redis-0
```

这不仅验证：

```text
Redis 持久化
```

还同时验证：

```text
Kubernetes Desired State
Controller Reconciliation
StatefulSet Self-Healing
```

---

## 27.9 验证了恢复之后的业务数据完整性

恢复成功标准不是：

```text
Pod Running
```

而是：

```text
Redis Ready
+
认证成功
+
AOF 加载
+
旧 key 存在
+
新 key 存在
+
DBSIZE=2
```

说明验收目标已经从基础设施状态提升到：

```text
服务状态
+
安全状态
+
数据状态
```

---

# 28. 最重要的工程结论

本阶段最大的经验并不是某一条 Redis 命令。

而是：

> 同样是 StatefulSet、Local PV、UID 999 和非 root 容器，不同镜像仍然可能需要完全不同的权限方案。

MySQL：

```text
需要自行设计 initContainer
```

Redis：

```text
官方 Entrypoint 已经提供正确的权限处理
无需额外 initContainer
```

真正合理的方法应该是：

```text
先观察
→ 再理解
→ 再验证
→ 最后设计
```

而不是：

```text
看到相似场景
→ 复制上一份 YAML
```

---

# 29. 最终验收结论

Redis StatefulSet 阶段最终状态：

```text
镜像固定                PASS
Digest 固定              PASS
三节点镜像可用           PASS
Redis 8.2.8              PASS
Local PV                 PASS
PVC                      PASS
自动节点约束             PASS
官方 Entrypoint 权限处理  PASS
非 root                  PASS
UID/GID 999              PASS
Capability 清零          PASS
NoNewPrivs               PASS
Secret                   PASS
ACL                      PASS
无认证访问拒绝           PASS
ConfigMap                PASS
Service                  PASS
Headless Service         PASS
StatefulSet              PASS
AOF                      PASS
appendfsync everysec      PASS
数据写入                 PASS
Pod 删除                 PASS
Controller 自愈          PASS
PVC 重新挂载             PASS
AOF 自动恢复             PASS
数据完整性               PASS
```

最终形成的完整链路：

```text
GitHub Redis 镜像定义
        ↓
ACR 构建与国内镜像
        ↓
Digest 固定
        ↓
镜像行为实测
        ↓
官方 Entrypoint 权限模型确认
        ↓
Local PV 权限验证
        ↓
无需自定义 initContainer
        ↓
Secret + ACL
        ↓
ConfigMap
        ↓
Service / Headless Service
        ↓
StatefulSet
        ↓
Local PV
        ↓
AOF everysec
        ↓
非 root Redis
        ↓
真实数据写入
        ↓
主动删除 Pod
        ↓
StatefulSet Controller 自愈
        ↓
原 PVC/PV 重新挂载
        ↓
AOF 自动加载
        ↓
原业务数据恢复
```

Redis 持久化与 StatefulSet 自愈阶段验收完成。

---

# 30. 简历 / 答辩中可以如何描述

如果在简历中总结，不需要写所有过程，可以压缩为：

```text
基于 Kubernetes StatefulSet + Local PV 部署 Redis 8.2.8，
通过分析官方 Entrypoint、UID/GID 与 Linux Capability，
验证其自动完成持久化目录权限处理及非 root 降权，
避免机械复制 MySQL initContainer 权限方案；
配置 ACL、Secret、AOF everysec、资源限制及健康探针，
并通过主动删除 StatefulSet Pod 验证 Controller 自愈、
PVC 重挂载及 AOF 数据自动恢复。
```

答辩时如果老师问：

> 为什么 Redis 没有像 MySQL 一样使用 initContainer？

可以回答：

```text
我没有直接根据 MySQL 的方案复制。

我先实际检查 Redis 8.2.8 官方镜像，发现镜像虽然默认以 root
进入 Entrypoint，但 Entrypoint 内置数据目录权限修复，并使用
setpriv 切换到 UID/GID 999。

随后我用真实 Local PV 做了验证。Local PV 初始是 root:root 755，
不加任何自定义 initContainer 的情况下，官方 Entrypoint 成功处理
挂载目录，Redis Server 最终以 999:999 运行，Capability 为 0，
并成功向 Local PV 写入 AOF。

之后删除 Pod、重新挂载同一 PVC，Redis 能正常加载已有 AOF 并恢复
业务数据。

所以最终决定不增加重复的 initContainer。这个结论是基于镜像行为、
Linux 权限和真实持久化恢复实验得出的，而不是根据经验猜测。
```

这也是这一阶段最值得在答辩中讲出来的部分。

