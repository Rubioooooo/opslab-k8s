# MySQL Exporter + Prometheus 基础监控验收

## 1. Validation Goal

本阶段目标是在现有 OpsLab Kubernetes / SRE 项目中，为 MySQL 建立一条可验证、可解释、最小权限的基础监控链：

```text
MySQL
→ mysqld_exporter
→ Kubernetes Service
→ ServiceMonitor
→ Prometheus
→ PromQL
```

本阶段不追求堆叠大量指标，而是覆盖最有价值的基础监控：

- MySQL up
- connections
- threads
- QPS
- connection errors
- slow queries
- InnoDB buffer pool

重点不只是“部署 exporter”，而是证明：

1. exporter 账号为什么必须与业务账号隔离；
2. 当前 collector 范围究竟需要什么权限；
3. 最小权限如何通过真实实验形成证据链；
4. requests / limits 如何依据实测资源消耗确定；
5. Prometheus Target、`mysql_up` 与 PromQL 如何完成端到端验收。

---

## 2. Environment Baseline

### 2.1 MySQL

```text
Version:             MySQL 8.4.10
Namespace:           opslab
StatefulSet:         opslab-mysql
Pod:                 opslab-mysql-0
Service:             opslab-mysql:3306
performance_schema:  ON
max_connections:     200
slow_query_log:      OFF
long_query_time:     10s
```

当前业务账号：

```text
opslab_app@%
```

权限：

```sql
GRANT USAGE ON *.* TO `opslab_app`@`%`;
GRANT ALL PRIVILEGES ON `opslab`.* TO `opslab_app`@`%`;
```

这说明 `opslab_app` 是业务账号，具备 `opslab.*` 上的业务读写/DDL 权限，不适合直接复用为监控账号。

---

## 3. Exporter Account Design

专用监控账号：

```text
opslab_exporter
```

来源限制：

```text
10.244.0.0/255.255.0.0
```

即限制为 Kubernetes Pod CIDR：

```text
10.244.0.0/16
```

并设置：

```text
MAX_USER_CONNECTIONS 3
```

最终保留的权限：

```sql
GRANT USAGE ON *.* TO
`opslab_exporter`@`10.244.0.0/255.255.0.0`;
```

账号设计目标：

- 不复用 `opslab_app`；
- 不允许 exporter 读写业务表；
- 只允许 Pod 网络来源连接；
- 限制 exporter 最大并发连接数；
- 从最小权限开始，通过真实 collector 行为逐步验证，而不是机械复制通用授权方案。

---

## 4. Minimum Privilege Evidence Chain

### 4.1 PodCIDR Host 限制验证

从另一个 Kubernetes Pod：

```text
Pod IP: 10.244.2.39
```

连接：

```text
opslab-mysql:3306
```

结果：

```text
CURRENT_USER = opslab_exporter@10.244.0.0/255.255.0.0
MYSQL_HOSTNAME = opslab-mysql-0
CONNECTION_TEST=PASS
```

说明：

```text
10.244.2.39
→ 命中 10.244.0.0/16 Host 限制
→ opslab_exporter 登录成功
```

---

### 4.2 `GLOBAL STATUS` 验证

USAGE-only 账号成功读取：

```text
Aborted_connects
Connections
Queries
Questions
Slow_queries
Threads_connected
Threads_running

Innodb_buffer_pool_pages_data
Innodb_buffer_pool_pages_free
Innodb_buffer_pool_pages_total
Innodb_buffer_pool_read_requests
Innodb_buffer_pool_reads
```

结果：

```text
GLOBAL_STATUS_TEST=PASS
```

---

### 4.3 `GLOBAL VARIABLES` 验证

USAGE-only 账号成功读取：

```text
innodb_buffer_pool_size
long_query_time
max_connections
performance_schema
slow_query_log
```

结果：

```text
GLOBAL_VARIABLES_TEST=PASS
```

---

### 4.4 业务数据隔离验证

使用同一个 exporter 账号尝试：

```sql
SELECT COUNT(*)
FROM opslab.opslab_events;
```

结果：

```text
BUSINESS_TABLE_ACCESS=DENIED
ERROR_TYPE = OperationalError
```

这证明 exporter 能读取所需运行状态，但不能读取业务表数据。

---

## 5. Real `mysqld_exporter` Validation

### 5.1 Version and Image

版本：

```text
mysqld_exporter v0.19.0
```

正式镜像固定为不可变 RepoDigest：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/mysqld-exporter@sha256:eadbe14b5e0f39fa1ec479c8083d6153fbc907da054fe36c07f0f64d2819fb28
```

正式配置仅保留当前阶段需要的 collector：

```text
global_status
global_variables
```

显式关闭当前项目不需要的 collector：

```text
slave_status
info_schema.innodb_cmp
info_schema.innodb_cmpmem
info_schema.query_response_time
```

Exporter 启动日志：

```text
Scraper enabled scraper=global_status
Scraper enabled scraper=global_variables
Listening on address=[::]:9104
TLS is disabled.
```

错误过滤：

```text
NO_MATCHED_ERRORS
```

真实 exporter 验证结果：

```text
mysql_up 1
```

因此可以确认：对当前 OpsLab 第一版基础监控范围，USAGE-only 账号能够满足真实 mysqld_exporter 的采集需求。

---

## 6. Resource Measurement

### 6.1 Idle

实测：

```text
CPU:     0-1m
Memory:  3-5Mi
```

### 6.2 Continuous Scrape

连续 scrape 后实测：

```text
CPU Peak:     approximately 6m
Memory Peak:  approximately 7Mi
```

因此正式资源配置为：

```yaml
resources:
  requests:
    cpu: 10m
    memory: 16Mi
  limits:
    cpu: 50m
    memory: 64Mi
```

该配置不是直接采用经验默认值，而是基于真实 exporter 在当前项目中的实际资源消耗设置，并保留合理余量。

---

## 7. SecurityContext

正式容器启用：

```yaml
securityContext:
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
```

实际运行身份：

```text
uid=65534(nobody) gid=65534(nobody) groups=65534(nobody)
```

正式 Deployment：

```text
deployment "opslab-mysqld-exporter" successfully rolled out
```

Pod：

```text
READY   STATUS    RESTARTS
1/1     Running   0
```

`runAsNonRoot` 相关启动故障单独保存在 Incident 文档中，本 Validation 只保留最终安全基线。

---

## 8. Kubernetes Service and ServiceMonitor

正式 Service：

```text
opslab-mysqld-exporter
port: 9104
```

ServiceMonitor：

```text
name:      opslab-mysqld-exporter
namespace: monitoring
```

关键 selector：

```yaml
metadata:
  labels:
    release: opslab-monitoring
```

监控目标：

```text
namespace:      opslab
service:        opslab-mysqld-exporter
port:           metrics
path:           /metrics
interval:       15s
scrapeTimeout:  5s
```

该设计复用了项目中已经验证成功的 kube-prometheus-stack ServiceMonitor 发现机制。

---

## 9. Prometheus Target Validation

Prometheus Active Target：

```text
scrapeUrl: http://10.244.1.39:9104/metrics
health:    up
lastError: ""
```

Labels：

```text
job:       opslab-mysqld-exporter
namespace: opslab
service:   opslab-mysqld-exporter
```

这证明：

```text
Prometheus
→ ServiceMonitor
→ Service
→ mysqld_exporter
```

链路正常。

同时：

```text
mysql_up = 1
```

证明：

```text
mysqld_exporter
→ MySQL
```

连接正常。

因此 `Target health=up` 与 `mysql_up=1` 分别完成了两层独立验收。

---

## 10. PromQL Baseline

最终实测基线：

```text
MySQL Up:
1

Threads Connected:
1

Threads Running:
2

QPS:
1.1263157894736842

Aborted Connections / sec:
0.04912280701754385

Slow Queries / sec:
0

InnoDB Buffer Pool Usage:
12.39013671875%
```

### 10.1 MySQL Up

```promql
mysql_up{job="opslab-mysqld-exporter"}
```

### 10.2 Connections

```promql
mysql_global_status_threads_connected{
  job="opslab-mysqld-exporter"
}
```

### 10.3 Running Threads

```promql
mysql_global_status_threads_running{
  job="opslab-mysqld-exporter"
}
```

### 10.4 QPS

```promql
rate(
  mysql_global_status_questions{
    job="opslab-mysqld-exporter"
  }[5m]
)
```

### 10.5 Connection Errors

```promql
rate(
  mysql_global_status_aborted_connects{
    job="opslab-mysqld-exporter"
  }[5m]
)
```

### 10.6 Slow Queries

```promql
rate(
  mysql_global_status_slow_queries{
    job="opslab-mysqld-exporter"
  }[5m]
)
```

### 10.7 InnoDB Buffer Pool Usage

```promql
mysql_global_status_innodb_buffer_pool_bytes_data{
  job="opslab-mysqld-exporter"
}
/
mysql_global_variables_innodb_buffer_pool_size{
  job="opslab-mysqld-exporter"
}
* 100
```

---

## 11. Observation: `Aborted_connects`

当前验收阶段：

```text
rate(mysql_global_status_aborted_connects[5m])
≈ 0.049 / second
```

暂不判定为 Incident。

本阶段进行了大量：

```text
账号验证
Pod 网络连接测试
exporter 测试
MySQL 连接实验
```

这些实验可能影响连接失败累计值。

后续 Manual SRE Baseline 阶段应在系统稳定运行期间重新观察其正常范围，再决定是否建立告警阈值。

---

## 12. What Is Actually Valuable

这一阶段真正有项目和面试价值的不是“部署了 mysqld_exporter”，而是：

### 12.1 最小权限设计

没有复用具备 `opslab.*` 全权限的业务账号，也没有直接套用通用授权，而是：

```text
明确 collector 范围
→ 从 USAGE-only 开始
→ Pod 内真实连接
→ 验证 GLOBAL STATUS
→ 验证 GLOBAL VARIABLES
→ 验证业务表访问被拒绝
→ 再用真实 mysqld_exporter 二次验证
```

形成了可解释的最小权限证据链。

### 12.2 账号来源和资源边界

额外限制：

```text
Pod CIDR Host Restriction: 10.244.0.0/16
MAX_USER_CONNECTIONS:      3
```

体现监控账号不是“能连上即可”，而是有明确攻击面和资源边界。

### 12.3 Measurement-driven Resources

通过实际运行和连续 scrape 测量 CPU/内存，再确定 requests / limits。

这使资源配置具备证据，而不是经验值或模板值。

### 12.4 End-to-End Validation

没有把 `Pod Running` 当成完成，而是分别验证：

```text
Deployment rollout
→ exporter logs
→ Service
→ ServiceMonitor
→ Prometheus Target health
→ mysql_up
→ PromQL
```

形成端到端验收闭环。

---

## 13. Interview Talking Points

面试时可以将该阶段概括为：

> 在给 MySQL 接入 mysqld_exporter 时，我没有复用业务账号，也没有直接套用通用权限。先把 collector 收窄到当前项目真正需要的 `global_status` 和 `global_variables`，然后从 USAGE-only 账号开始，通过独立 Pod 和真实 mysqld_exporter 两层实验验证：它可以读取所需运行状态，同时无法读取业务表。账号来源进一步限制在 Kubernetes Pod CIDR，并设置最大连接数。之后通过真实 scrape 测量 exporter 的 CPU/内存，再确定 requests/limits，最后分别验证 Prometheus Target health、`mysql_up` 和 QPS、连接错误、Buffer Pool 等 PromQL，完成端到端监控验收。

可继续深入追问的点：

- 为什么不能复用 `opslab_app`？
- 为什么要从 USAGE-only 开始验证？
- 为什么关闭不需要的 collector？
- 为什么 Host 不直接使用 `%`？
- 为什么设置 `MAX_USER_CONNECTIONS`？
- requests / limits 如何确定？
- `Target health=up` 与 `mysql_up=1` 分别证明什么？
- 为什么 `Aborted_connects` 更适合观察 rate，而不是只看累计值？

---

## 14. Final Result

```text
Dedicated Exporter Account:      PASS
Pod CIDR Host Restriction:       PASS
Minimum Privilege Validation:    PASS
Business Data Isolation:         PASS
Real mysqld_exporter Validation: PASS
Resource Measurement:            PASS
SecurityContext:                 PASS
Service:                         PASS
ServiceMonitor:                  PASS
Prometheus Target:               PASS
mysql_up:                        PASS
PromQL Basic Metrics:            PASS
```

最终结论：

```text
MySQL Basic Monitoring = PASS
```
