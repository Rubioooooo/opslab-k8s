# Redis Exporter / Prometheus 基础监控验收报告

## 1. 文档目的

本文记录 OpsLab Kubernetes 项目中 Redis 基础监控从现状取证、ACL 最小权限设计、真实 redis_exporter 行为验证，到 Prometheus / Grafana 最终接入的完整过程。

本阶段的目标不是简单完成 `redis_exporter` 部署，而是回答以下工程问题：

1. 当前 Redis 实际采用什么认证模型？
2. 是否应该复用现有业务 Redis 身份？
3. 是否有必要为监控建立独立 ACL 用户？
4. redis_exporter 为 OpsLab 第一版基础监控真正需要哪些权限？
5. exporter 的 requests / limits 应该如何确定？
6. 严格 SecurityContext 是否会影响 exporter 正常运行？
7. ACL 持久化后 Redis StatefulSet 重建是否会影响已有数据和业务？
8. Prometheus 是否能够真正采集 exporter，并且 exporter 是否能够真正访问 Redis？
9. 哪些 Redis 指标值得进入第一版 SRE Dashboard？

---

## 2. Redis 当前基线

Redis 基础信息：

```text
Redis Version:       8.2.8
Namespace:           opslab
StatefulSet:         opslab-redis
Service:             opslab-redis:6379
Mode:                standalone
AOF:                 enabled
appendfsync:          everysec
maxmemory:            384MiB
maxmemory-policy:     noeviction

Redis 使用 Local PV 持久化，已有验证数据：

sre:persistence:test
sre:statefulset:recovery

Redis StatefulSet 持久化和自愈能力已经在此前阶段完成验收，本阶段不重新设计 Redis 数据持久化。

3. Redis 认证模型取证
3.1 Kubernetes 配置

Redis 容器通过：

redis-server /etc/redis/redis.conf

启动。

StatefulSet 中存在：

REDIS_PASSWORD
REDISCLI_AUTH

两者均引用：

Secret:
opslab-redis-secret

Secret 中包含：

REDIS_PASSWORD
users.acl

实际挂载关系：

redis-config
→ /etc/redis/redis.conf

redis-acl
→ /run/secrets/redis/users.acl

redis-data
→ /data

Redis 配置中明确：

aclfile /run/secrets/redis/users.acl

运行中的 Redis：

CONFIG GET aclfile
→ /run/secrets/redis/users.acl

因此可以确认外部 ACL 文件确实被 Redis 加载。

4. 真正的未认证访问验证

最初直接执行：

redis-cli PING

返回：

PONG

但这不能证明 Redis 允许匿名访问。

原因是 Redis Pod 中存在：

REDISCLI_AUTH

redis-cli 会隐式使用该环境变量完成认证。

因此重新显式清除 REDISCLI_AUTH 后进行真实未认证验证：

PING
→ NOAUTH Authentication required.

ACL WHOAMI
→ NOAUTH Authentication required.

INFO
→ NOAUTH Authentication required.

认证后的身份：

ACL WHOAMI
→ default

由此确认：

Redis Authentication = ENABLED

当前业务认证主体为：

default
5. default 用户权限基线

ACL 用户：

default

脱敏后的 ACL：

user default on sanitize-payload #<PASSWORD_HASH_REDACTED> ~* &* +@all

即认证后的 default 用户拥有：

所有 Key
所有 Pub/Sub Channel
全部 Redis 命令

因此虽然 Redis 已启用密码认证，但 default 用户权限边界较宽。

如果 redis_exporter 继续复用 default 身份，则监控组件同样获得 +@all 权限。

本阶段因此决定：

不复用 default
→ 创建独立 opslab_exporter ACL 用户
→ 从最小权限开始实测

这并不是机械复制 MySQL exporter 的账号方案，而是根据 Redis ACL 实际能力和当前权限边界做出的设计。

6. 第一版 Redis 基础监控目标

OpsLab 第一版只关注：

Redis Up
used_memory
maxmemory
Memory Usage %
connected_clients
commands/sec
keyspace hits
keyspace misses
evicted_keys
expired_keys
DB keys

不为了指标数量开启：

Key 扫描
Key 内容读取
Slow Log 分析
CONFIG 指标
客户端列表
复杂 Redis 内部指标

原则：

先明确需要哪些指标
→ 再验证 exporter 真正需要哪些 Redis 权限
7. 专用监控 ACL 用户实验

创建临时运行时用户：

opslab_exporter

第一版 ACL 故意只赋予：

-@all
+info

不允许任何 Key pattern，也不授予其他 Redis 命令。

权限验证：

INFO ALL
→ ALLOW

CONFIG GET
→ DENY

CLIENT SETNAME
→ DENY

GET sre:persistence:test
→ DENY

SET sre:exporter:probe 1
→ DENY

FLUSHALL
→ DENY

由此形成初步权限边界：

监控 INFO           ALLOW
业务数据读取         DENY
业务数据修改         DENY
CONFIG              DENY
CLIENT              DENY
危险 FLUSHALL       DENY
8. redis_exporter 镜像

版本：

redis_exporter v1.89.0

ACR RepoDigest：

crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/redis-exporter@sha256:0a43aadebfc704952d487b0af63c63f8a3b5e7d64f30a0b97d28b15be2501820

镜像已经分别在：

k8s-worker1
k8s-worker2

完成拉取验证。

两节点 RepoDigest 完全一致。

对应 Git commit：

4d29ea3 build(monitoring): add redis exporter mirror image

正式 Kubernetes 配置固定使用 RepoDigest，而不是 mutable tag。

9. 真实 redis_exporter 最小权限验证

使用：

redis_exporter v1.89.0

真实连接：

redis://opslab-redis:6379

认证：

REDIS_USER=opslab_exporter
REDIS_PASSWORD=<Secret>

为了控制第一版 exporter 行为，同时配置：

REDIS_EXPORTER_CONFIG_COMMAND=-
REDIS_EXPORTER_SET_CLIENT_NAME=false
REDIS_EXPORTER_EXCLUDE_LATENCY_HISTOGRAM_METRICS=true
REDIS_EXPORTER_EXPORT_CLIENT_LIST=false
REDIS_EXPORTER_PING_ON_CONNECT=false

真实 /metrics scrape 成功：

curl_rc=0

核心指标：

redis_up 1

redis_commands_processed_total
redis_connected_clients
redis_db_keys
redis_evicted_keys_total
redis_expired_keys_total
redis_keyspace_hits_total
redis_keyspace_misses_total
redis_memory_max_bytes
redis_memory_used_bytes

全部成功产生。

Exporter 日志：

NO_MATCHED_PERMISSION_ERRORS

由此证明：

+info only

已经足以支撑 OpsLab 第一版 Redis 基础监控。

10. ACL LOG 观察

真实 scrape 后 Redis ACL LOG 出现：

command|info
slowlog|get
slowlog|len

连续 60 次 scrape 后，拒绝对象仍然只有上述三类，没有出现新的权限需求。

需要特别区分：

INFO

与：

COMMAND INFO

是两个不同 Redis 命令。

当前 opslab_exporter 拥有 +info，因此基础 INFO 采集成功，但没有 COMMAND INFO 权限。

虽然 exporter 还会尝试：

COMMAND INFO
SLOWLOG GET
SLOWLOG LEN

但实际结果证明：

redis_up = 1
核心指标完整
Exporter 无错误

这些拒绝不会破坏当前基础监控。

因此没有为了“清空 ACL LOG”而扩大权限。

最终决定：

不增加 COMMAND INFO
不增加 SLOWLOG GET
不增加 SLOWLOG LEN

尤其不为了当前不需要的附加指标给 exporter 增加更高权限。

该现象属于明确理解后的安全边界行为，不记录为 Incident。

11. Resources 实测

Exporter 空闲状态：

CPU:     0m
Memory:  4Mi

连续 60 次、约每秒一次真实 scrape：

CPU:
1m
1m
1m
5m
5m
5m

Memory:
4Mi
6Mi
6Mi
7Mi
7Mi
7Mi

实测峰值：

CPU peak:     ≈ 5m
Memory peak:  ≈ 7Mi

因此正式 Resources：

requests:
  cpu: 10m
  memory: 16Mi

limits:
  cpu: 50m
  memory: 64Mi

这是一组 measurement-driven 配置，而不是经验估算。

12. SecurityContext 实验

正式配置前先使用测试 Pod 验证：

runAsNonRoot: true
runAsUser: 65534
runAsGroup: 65534

allowPrivilegeEscalation: false
readOnlyRootFilesystem: true

capabilities:
  drop:
    - ALL

seccompProfile:
  type: RuntimeDefault

测试结果：

Pod 1/1 Running
curl_rc=0
redis_up=1
核心 Redis Metrics 正常
NO_MATCHED_ERRORS

因此严格 SecurityContext 不影响 redis_exporter 正常运行。

正式 Deployment 沿用该配置。

13. ACL 持久化设计

前期：

opslab_exporter

只是通过 Redis 运行时 ACL SETUSER 创建的临时用户。

正式化前没有直接修改生产 ACL。

流程：

备份现有 opslab-redis-secret
→ 导出现有 users.acl
→ 导出已实测的 opslab_exporter ACL
→ 生成 users.acl.candidate
→ 独立 Redis Pod 加载验证

候选用户：

default
opslab_exporter

独立 Redis 8.2.8 验证：

Redis started
Ready to accept connections tcp

default:
PING
→ PONG

opslab_exporter:
INFO
→ PASS

GET
→ NOPERM

证明候选 ACL：

语法正确
原 default 身份未破坏
opslab_exporter 最小权限边界保持不变
14. 正式 ACL 切换与 StatefulSet 重建

当前 ACL 文件通过 Kubernetes Secret 挂载：

opslab-redis-secret/users.acl

正式变更前验证：

Redis Pod             1/1 Running
PVC                   Bound
StatefulSet            1/1

loading                0
aof_enabled            1
aof_last_bgrewrite_status=ok
aof_last_write_status=ok

两个持久化验证 Key
→ EXISTS = 2

FastAPI
→ 2/2 Available

随后：

更新 opslab-redis-secret/users.acl
→ 校验 Secret ACL hash
→ 受控删除 opslab-redis-0
→ StatefulSet 创建新 Pod

新 Redis 日志：

Opening AOF incr file appendonly.aof.1.incr.aof on server start
Ready to accept connections tcp

恢复后：

PING
→ PONG

AOF
→ healthy

原有验证数据
→ 保持存在

ACL 用户仍然：

default
opslab_exporter

说明 opslab_exporter 已由 ACL 文件正式加载，不再是临时运行时用户。

15. Redis 变更后的业务恢复验收

Redis Pod 重建后：

FastAPI Pod 1      1/1 Running
FastAPI Pod 2      1/1 Running

Deployment
2/2 Available

证明：

FastAPI
→ Redis Service
→ 新 Redis Pod

业务依赖链重新恢复正常。

此次 ACL 正式化没有破坏 FastAPI Redis 访问。

16. 正式 redis_exporter Deployment

Deployment：

opslab-redis-exporter
namespace=opslab
replicas=1

Service：

opslab-redis-exporter
ClusterIP
port=9121

正式文件：

kubernetes/monitoring/opslab-redis-exporter.yaml

正式状态：

Deployment  1/1 Available
Pod         1/1 Running
Service     9121/TCP
Endpoint    exporter Pod IP:9121

实际资源：

CPU:     ≈ 1m
Memory:  ≈ 4Mi

正式 SecurityContext：

runAsNonRoot=true
runAsUser=65534
runAsGroup=65534
readOnlyRootFilesystem=true
allowPrivilegeEscalation=false
drop ALL capabilities
seccompProfile=RuntimeDefault
17. Service 级指标验收

通过正式 Service：

opslab-redis-exporter:9121

访问 /metrics。

结果：

redis_up 1

基础指标：

redis_commands_processed_total
redis_connected_clients
redis_evicted_keys_total
redis_expired_keys_total
redis_keyspace_hits_total
redis_keyspace_misses_total
redis_memory_max_bytes
redis_memory_used_bytes

全部正常。

配置：

REDIS_EXPORTER_INCL_METRICS_FOR_EMPTY_DATABASES=false

已经实际生效。

数据库指标只保留真实存在的：

redis_db_keys{db="db0"} 2

不再输出大量空的：

db1 ... db15

时间序列噪声得到控制。

18. ServiceMonitor

正式文件：

kubernetes/monitoring/opslab-redis-exporter-servicemonitor.yaml

ServiceMonitor：

name:
opslab-redis-exporter

namespace:
monitoring

关键 label：

release=opslab-monitoring

发现：

namespace=opslab
service=opslab-redis-exporter
port=metrics
path=/metrics
interval=15s
scrapeTimeout=5s

Prometheus 当前 ServiceMonitor selector：

release=opslab-monitoring

因此该 ServiceMonitor 正常进入 Prometheus 发现范围。

19. Prometheus Target 验收

Prometheus Target：

job:
opslab-redis-exporter

namespace:
opslab

service:
opslab-redis-exporter

scrapeUrl:
http://10.244.1.43:9121/metrics

health:
up

lastError:
""

这里需要区分两层可用性：

Target health=up

证明：

Prometheus
→ redis_exporter

正常。

redis_up=1

证明：

redis_exporter
→ Redis

认证和采集正常。

不能用其中任意一个单独替代整条链路验收。

20. Redis PromQL 最终验收

最终实测：

Redis Up                 1
Memory Used Bytes        1257928
Maxmemory Bytes          402653184
Memory Usage %           0.31240979830423987
Connected Clients        1
Commands / sec           2.0584157647058823
Keyspace Hits / sec      0
Keyspace Misses / sec    0
Evicted Keys / sec       0
Expired Keys / sec       0
DB0 Keys                 2
Redis Up
redis_up{
  job="opslab-redis-exporter"
}
Memory Used
redis_memory_used_bytes{
  job="opslab-redis-exporter"
}
Maxmemory
redis_memory_max_bytes{
  job="opslab-redis-exporter"
}
Memory Usage %
redis_memory_used_bytes{
  job="opslab-redis-exporter"
}
/
redis_memory_max_bytes{
  job="opslab-redis-exporter"
}
* 100

当前：

≈ 0.31%

当前距离：

maxmemory=384MiB

仍然很远，没有内存容量压力。

Connected Clients
redis_connected_clients{
  job="opslab-redis-exporter"
}
Commands / sec
rate(
  redis_commands_processed_total{
    job="opslab-redis-exporter"
  }[5m]
)
Keyspace Hits / sec
rate(
  redis_keyspace_hits_total{
    job="opslab-redis-exporter"
  }[5m]
)
Keyspace Misses / sec
rate(
  redis_keyspace_misses_total{
    job="opslab-redis-exporter"
  }[5m]
)
Evicted Keys / sec
rate(
  redis_evicted_keys_total{
    job="opslab-redis-exporter"
  }[5m]
)
Expired Keys / sec
rate(
  redis_expired_keys_total{
    job="opslab-redis-exporter"
  }[5m]
)
DB0 Keys
redis_db_keys{
  job="opslab-redis-exporter",
  db="db0"
}
21. Cache Hit Ratio 的解释边界

当前：

Keyspace Hits / sec   0
Keyspace Misses / sec 0

这表示当前 Prometheus 观察窗口内没有足够的缓存查询流量。

因此不能把：

hits=0
misses=0

解释成：

缓存命中率为 0%

因为此时本质上没有有效样本。

因此第一版 Dashboard 不直接展示一个容易误导的 Cache Hit Ratio Stat，而是展示：

Hits / sec
Misses / sec

两条时间序列。

22. Data Services Overview Dashboard

Redis Monitoring 完成后，没有创建独立大型 Redis Dashboard。

而是与 MySQL 统一组成：

OpsLab / Data Services Overview

UID：

opslab-data-services-overview

正式文件：

kubernetes/monitoring/dashboards/opslab-data-services-dashboard.yaml

Grafana sidecar label：

grafana_dashboard=1

实际 sidecar 日志：

Writing /tmp/dashboards/opslab-data-services-dashboard.json

Grafana reload：

200 OK
Dashboards config reloaded

Grafana UI 已成功显示 Dashboard。

23. Dashboard 指标设计
MySQL
MySQL Up
Threads Connected
Threads Running
InnoDB Buffer Pool Usage
MySQL QPS
Redis
Redis Up
Connected Clients
Redis Memory Usage
Redis Commands / sec
Redis Cache Hits / Misses
Redis Evicted / Expired Keys

设计原则：

不展示 exporter 能提供的所有指标

而是只保留：

健康
容量
连接
吞吐
缓存行为
淘汰 / 过期行为

这些真正具有日常运维/SRE判断价值的指标。

24. mem_fragmentation_ratio 观察

取证阶段曾观察到：

used_memory ≈ 1.18MiB
used_memory_rss ≈ 20.20MiB
mem_fragmentation_ratio ≈ 17.34

虽然 ratio 数值较高，但当前 Redis 数据集极小。

因此暂时只作为：

Engineering Observation

保留。

不直接判断为 Redis 内存碎片故障，也不建立 Incident。

后续应在更真实的数据量和稳定负载下观察趋势。

25. 本阶段没有建立 Incident 的原因

Redis Monitoring 阶段没有产生需要单独记录的真实故障。

出现过的：

COMMAND INFO ACL DENIED
SLOWLOG GET ACL DENIED
SLOWLOG LEN ACL DENIED

属于刻意缩小 ACL 权限之后观察到的预期行为。

实际：

redis_up=1
核心指标完整
Exporter 无错误
Prometheus Target up

因此这些现象属于：

安全边界验证结果

而不是 Incident。

26. Redis Monitoring 真正完成的内容

本阶段不是简单：

部署 redis_exporter

而是完成：

Redis 认证模型取证
→ 发现 default +@all
→ 不复用高权限业务身份
→ 创建专用 opslab_exporter
→ 从 -@all +info 开始
→ ACL DRYRUN 验证
→ 真实 redis_exporter 验证
→ 基础指标 PASS
→ 业务 GET/SET DENIED
→ CONFIG/CLIENT/FLUSHALL DENIED
→ 不为 SLOWLOG/COMMAND INFO 无意义扩大权限
→ Resources 实测
→ SecurityContext 实测
→ ACL candidate 隔离 Redis 验证
→ 正式 Secret 持久化
→ Redis StatefulSet 受控重建
→ AOF/PVC 数据恢复
→ FastAPI 业务恢复
→ Deployment
→ Service
→ ServiceMonitor
→ Prometheus Target
→ redis_up
→ PromQL
→ Data Services Overview
27. SRE / 面试价值
27.1 最小权限不是照抄文档

没有直接复制 redis_exporter 通用 ACL 示例。

而是：

明确当前指标需求
→ +info only
→ 真实 exporter scrape
→ Redis ACL LOG
→ 验证缺失权限影响
→ 保持最小边界
27.2 认证和授权必须分开理解

Redis：

有密码认证

并不代表：

账号权限合理

原 default：

+@all

因此又通过 ACL 建立监控职责隔离。

27.3 不能只看 Pod Running

完整验收链：

Deployment
→ Service
→ EndpointSlice
→ /metrics
→ ServiceMonitor
→ Prometheus Target health=up
→ redis_up=1
→ PromQL
27.4 Persistence 与配置变更恢复

正式 ACL 持久化需要 Redis Pod 重建。

因此同时验证：

ACL 生效
+
AOF/PVC 数据恢复
+
FastAPI 业务恢复

而不是只检查新用户是否存在。

27.5 Measurement-driven Resources

真实连续 scrape 后测量：

CPU peak ≈ 5m
Memory peak ≈ 7Mi

再设计：

requests 10m / 16Mi
limits   50m / 64Mi
28. 最终验收结论

Redis 基础监控阶段：

PASS

最终状态：

Redis Authentication          PASS
Dedicated Exporter User       PASS
Minimal ACL +info only        PASS
Business Key Access Denied    PASS
RepoDigest                    PASS
Resources Measurement         PASS
SecurityContext               PASS
ACL Persistence               PASS
AOF/PVC Recovery              PASS
FastAPI Recovery              PASS
Exporter Deployment           PASS
Service                       PASS
ServiceMonitor                PASS
Prometheus Target             UP
redis_up                      1
PromQL                        PASS
Grafana Data Services         PASS

Redis Monitoring 已达到 OpsLab SRE Baseline 第一版要求。
