# OpsLab 可观测性阶段实施与验收报告

> 项目：基于 kubeadm 的 Kubernetes 云原生应用部署与 SRE 稳定性实践  
> 阶段：Prometheus / Grafana 基础监控 → FastAPI 应用级可观测性接入  
> 范围：本次对话窗口中完成和确认的工作  
> 建议仓库路径：`docs/observability/fastapi-prometheus-grafana-observability-stage-report.md`

---

## 1. 报告目的

本报告不是单纯记录“执行过哪些命令”，而是保存本阶段真正值得复盘的工程证据，包括：

1. 为什么选择 kube-prometheus-stack；
2. 为什么第一阶段主动关闭部分控制面监控目标；
3. Prometheus / Grafana 基础监控是如何验收的；
4. kube-prometheus-stack 首次安装失败是如何定位并恢复的；
5. FastAPI 为什么需要从基础资源监控继续升级到应用级指标；
6. HTTP 指标为什么采用 Counter + Histogram；
7. 为什么 route label 必须使用路由模板而不是实际 URL；
8. FastAPI v0.3.0 是如何完成源码、镜像、Digest、Kubernetes、ServiceMonitor 的完整链路；
9. RollingUpdate 后两个 Pod 为什么会临时落在同一 Worker，以及如何处理；
10. 本阶段形成了哪些真正具有运维/SRE价值的能力；
11. 当前仍有哪些不足，以及下一阶段应如何继续。

本报告尽量只写有实际输出、配置或验收结果支撑的内容。对于未在对话中完整贴出的结果，只记录“已确认符合预期”，不虚构具体数值。

---

# 2. 阶段开始前的项目基线

在进入本阶段前，OpsLab 项目已经具备较完整的 Kubernetes 应用运行基础：

- kubeadm 三节点 Kubernetes 集群；
- Flannel 容器网络；
- CoreDNS；
- NGINX Ingress；
- FastAPI 两副本；
- MySQL StatefulSet + Local PV；
- Redis StatefulSet + Local PV；
- FastAPI 已完成 MySQL CRUD；
- Redis Cache-Aside；
- `/healthz` 与 `/readyz` 分离；
- requests / limits；
- SecurityContext；
- RollingUpdate；
- topologySpreadConstraints；
- Metrics Server；
- HPA。

因此，本阶段的目标已经不再是“让应用跑起来”，而是回答更进一步的 SRE 问题：

> 应用当前运行得怎么样？  
> 流量是多少？  
> 请求是否成功？  
> 延迟是多少？  
> 哪个组件出现异常？  
> Prometheus 能不能主动发现并采集业务指标？  
> Grafana 能不能把这些数据转化成可理解的运行状态？

这标志着项目开始从“容器编排实践”向“可观测性与稳定性实践”转变。

---

# 3. kube-prometheus-stack 方案选择

## 3.1 采用方案

本项目选择：

```text
kube-prometheus-stack
+
Helm
+
自定义 values.yaml
```

固定 Chart：

```text
kube-prometheus-stack 88.2.0
```

Prometheus Operator：

```text
v0.93.0
```

Helm：

```text
v4.2.3
```

自定义配置文件：

```text
kubernetes/monitoring/values.yaml
```

对应 Git 提交：

```text
11dd3e6 feat(monitoring): deploy kube prometheus stack
```

## 3.2 为什么不是手工逐个部署 Prometheus / Grafana

本阶段没有自行从零手写：

```text
Prometheus Deployment
Grafana Deployment
Alertmanager Deployment
ServiceAccount
RBAC
ServiceMonitor CRD
PrometheusRule CRD
...
```

而是使用 Prometheus Operator 体系。

原因是项目目标不是证明“能够复制很多 YAML”，而是更接近真实云原生监控体系：

```text
Helm values
    ↓
kube-prometheus-stack
    ↓
Prometheus Operator
    ↓
Prometheus / Alertmanager CR
    ↓
Operator reconciliation
    ↓
实际 StatefulSet / Service / 配置
```

这也解释了一个重要现象：

> `helm template` 中看不到最终 Prometheus StatefulSet，并不代表 Prometheus 没有 StatefulSet。

因为 Helm 主要创建 `Prometheus` CR，真正的 StatefulSet 由 Prometheus Operator 后续根据 CR 自动生成。

---

# 4. 第一版监控为什么主动关闭部分控制面 Targets

部署之前先检查了真实监听配置：

```text
kube-controller-manager:
--bind-address=127.0.0.1

kube-scheduler:
--bind-address=127.0.0.1

etcd:
--listen-metrics-urls=http://127.0.0.1:2381
```

因此，第一版 `values.yaml` 主动关闭：

```yaml
kubeControllerManager:
  enabled: false

kubeScheduler:
  enabled: false

kubeEtcd:
  enabled: false

kubeProxy:
  enabled: false
```

并关闭相应默认规则。

如果不做这一层预检查，可能形成：

```text
Prometheus 安装成功
↓
默认 ServiceMonitor 被创建
↓
目标地址根本不可达
↓
Targets 页面大量 DOWN
↓
再开始怀疑 Prometheus / 网络 / RBAC
```

而实际根因只是目标组件只监听 Loopback。

因此本阶段坚持：

> **先观察真实行为，再设计 Kubernetes 配置。**

第一阶段优先构建“少而准确”的监控目标，而不是追求数量。

---

# 5. kube-prometheus-stack 首次安装故障

这是本阶段最值得保留的 Incident 之一。

## 5.1 现象

最初 node-exporter 镜像配置错误：

```yaml
tag: v1.12.1-distroless
```

但 node-exporter 子 Chart 本身又会自动加入 distroless 后缀，最终渲染得到：

```text
v1.12.1-distroless-distroless
```

导致三个 node-exporter Pod：

```text
ImagePullBackOff
```

首次 Helm release：

```text
REVISION 1
STATUS   failed
DESCRIPTION context canceled
```

但现场并不是“整个 monitoring 都坏了”。实际已经运行的组件包括：

- Grafana；
- kube-state-metrics；
- Prometheus Operator；
- Prometheus；
- Alertmanager。

真正异常的主要是：

```text
node-exporter ×3
```

## 5.2 为什么没有 uninstall 重装

如果只根据 `helm status = failed` 就执行 `helm uninstall`，会丢失大量已经成功创建的资源，也破坏现场证据。

本次正确处理逻辑：

```text
Helm failed
↓
检查 Pods / Deployment / StatefulSet / Event
↓
发现大部分组件正常
↓
定位只有 node-exporter ImagePullBackOff
↓
检查最终渲染镜像
↓
发现 distroless 后缀重复
↓
最小修正 values.yaml
↓
helm upgrade
```

最终改成：

```yaml
prometheus-node-exporter:
  image:
    tag: v1.12.1
    distroless: true
```

最终镜像正确渲染为：

```text
monitoring-node-exporter:v1.12.1-distroless
```

## 5.3 恢复结果

Helm：

```text
REVISION 2
STATUS   deployed
```

node-exporter：

```text
DESIRED      3
CURRENT      3
READY        3
UP-TO-DATE   3
AVAILABLE    3
```

这次故障体现的 SRE 方法：

```text
失败
≠
立即重装

失败
→ 保存现场
→ 缩小故障域
→ Primary Error
→ 最小修改
→ 原地恢复
```

建议后续单独形成：

```text
docs/incidents/kube-prometheus-stack-node-exporter-image-tag-incident.md
```

---

# 6. Prometheus 基础监控验收

完成监控栈修复后，通过 Prometheus `/api/v1/targets` 进行真实验证。

实际得到：

```text
Total: 22
Health: {'up': 22}
```

即：

```text
22 Targets
22 UP
0 DOWN
```

已确认的 Targets 包含：

- kube-apiserver；
- CoreDNS；
- kube-state-metrics；
- kubelet；
- node-exporter；
- Grafana；
- Alertmanager；
- Prometheus Operator；
- Prometheus。

PromQL：

```promql
count(up)
```

结果：

```text
22
```

```promql
count(up == 1)
```

结果：

```text
22
```

```promql
count(up == 0)
```

结果为空数组。

这里空数组不是失败，而是：

> 当前不存在值为 0 的 `up` 时间序列。

---

# 7. Kubernetes 对象指标链路验证

使用：

```promql
kube_deployment_status_replicas_available{
  namespace="opslab",
  deployment="opslab-api"
}
```

实际返回：

```text
2
```

这证明完整数据链：

```text
Kubernetes API
      ↓
kube-state-metrics
      ↓
Prometheus scrape
      ↓
TSDB
      ↓
PromQL
```

因此 Kubernetes 对象状态监控链路正常。

---

# 8. Grafana 访问链路问题与解决

最初在 Windows 浏览器访问：

```text
http://127.0.0.1:3000
```

出现：

```text
ERR_CONNECTION_REFUSED
```

原因不是 Grafana 故障，而是 `127.0.0.1` 所属主机不同。

VM 中执行：

```bash
kubectl port-forward   -n monitoring   svc/opslab-monitoring-grafana   3000:80
```

实际监听的是：

```text
k8s-control-plane 的 127.0.0.1:3000
```

而 Windows 浏览器访问的是 Windows 自己的 `127.0.0.1:3000`。

因此增加 SSH Tunnel：

```bash
ssh -N   -L 3000:127.0.0.1:3000   rubio@192.168.8.10
```

最终链路：

```text
Windows Browser
127.0.0.1:3000
      ↓
SSH Tunnel
      ↓
k8s-control-plane:127.0.0.1:3000
      ↓
kubectl port-forward
      ↓
Grafana Service
      ↓
Grafana Pod
```

随后成功进入 Grafana。

---

# 9. Grafana 基础 Dashboard 验收

实际检查了：

```text
Kubernetes / Compute Resources / Cluster
Kubernetes / Compute Resources / Node
Kubernetes / Compute Resources / Namespace (Pods)
Node Exporter / Nodes
```

已看到：

- 集群 CPU；
- 集群 Memory；
- Namespace 资源；
- Node CPU；
- Node Memory；
- Disk I/O；
- Filesystem；
- Pod CPU；
- Pod Memory；
- Kubernetes 工作负载数据。

其中 Namespace Dashboard 初始显示：

```text
No data
```

检查后发现顶部变量：

```text
namespace=default
```

而业务 namespace：

```text
opslab
```

切换为 `opslab` 后即可看到真实业务数据。

得到的排障认识：

```text
No data
≠
Prometheus 挂了
```

排查顺序应包括：

```text
Dashboard variable
→ PromQL
→ label
→ time range
→ datasource
→ target
```

---

# 10. 为什么基础 Kubernetes 监控还不够

到这一阶段，Prometheus 已经能够知道：

```text
FastAPI Pod CPU
FastAPI Pod Memory
Deployment replicas
Node 状态
```

但仍然无法回答：

```text
FastAPI 一共处理多少 HTTP 请求？
每秒请求数是多少？
200 / 404 / 500 各有多少？
哪个 API 流量最大？
请求 P95 延迟是多少？
```

因此继续增加 Application Metrics。

---

# 11. FastAPI v0.3.0 指标设计

## 11.1 新增依赖

```text
prometheus-client==0.25.0
```

新增：

```text
applications/fastapi/app/metrics.py
```

FastAPI 新增：

```text
/metrics
```

对应提交：

```text
07a8514 feat(observability): expose fastapi prometheus metrics
```

## 11.2 请求总数 Counter

```text
opslab_http_requests_total
```

Labels：

```text
method
route
status_code
```

用途：

- HTTP 请求量；
- QPS；
- route 分布；
- HTTP 状态码统计；
- 5xx 错误率。

## 11.3 请求延迟 Histogram

```text
opslab_http_request_duration_seconds
```

Labels：

```text
method
route
```

自动产生：

```text
_bucket
_count
_sum
_created
```

用途：

- 延迟趋势；
- 延迟分布；
- P50；
- P95；
- P99。

---

# 12. 为什么 route 使用模板而不是实际 URL

错误方式：

```text
/api/v1/events/1
/api/v1/events/2
/api/v1/events/3
...
```

如果把真实 URL 直接作为 label，每个 event ID 都会生成新的 label combination：

```text
高基数
→ 时间序列数量快速增长
→ Prometheus 内存 / 存储压力上升
→ 查询成本上升
```

因此本项目使用：

```text
/api/v1/events/{event_id}
```

不存在的路由统一：

```text
__unmatched__
```

这是应用可观测性设计中的重要实践。

---

# 13. 为什么 /metrics 不统计自身请求

Prometheus 会周期性：

```text
GET /metrics
```

如果 middleware 也把 `/metrics` 算作应用请求：

```text
Prometheus scrape
→ /metrics
→ requests_total +1
```

那么监控系统会自己制造业务流量。

因此本项目明确排除 `/metrics`，避免监控自身污染业务 HTTP 指标。

---

# 14. 本地 metrics 验证与依赖问题

第一次启动 FastAPI v0.3.0：

```bash
APP_VERSION=v0.3.0 python -m uvicorn app.main:app   --host 127.0.0.1   --port 18000
```

出现：

```text
ModuleNotFoundError: No module named 'redis'
```

Traceback：

```text
app.main
→ app.cache
→ import redis.asyncio
→ ModuleNotFoundError
```

Root Cause 不是 metrics.py，而是：

```text
本地 .venv 没有同步当前 requirements.lock.txt
```

同步依赖后恢复。

形成的重要认识：

```text
python -m compileall PASS
≠
Runtime dependency PASS
```

`compileall` 主要验证语法可编译；真正启动 Uvicorn 才会触发完整 import chain。

---

# 15. FastAPI metrics 本地实际输出

实际：

```text
/healthz
→ {"status":"ok"}
```

不存在路由：

```text
not_found_status=404
```

Counter：

```text
opslab_http_requests_total{
  method="GET",
  route="/healthz",
  status_code="200"
} 1
```

404：

```text
opslab_http_requests_total{
  method="GET",
  route="__unmatched__",
  status_code="404"
} 1
```

Histogram：

```text
opslab_http_request_duration_seconds_bucket
opslab_http_request_duration_seconds_count
opslab_http_request_duration_seconds_sum
```

实际 `/healthz` 一次请求的 sum 示例：

```text
0.0017484169802628458
```

因此本地 instrumentation 判定 PASS。

---

# 16. FastAPI v0.3.0 镜像供应链

源码 push 后：

```text
07a8514 feat(observability): expose fastapi prometheus metrics
```

发布：

```text
opslab-api-v0.3.0
```

镜像：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/opslab-api:v0.3.0
```

ACR Digest：

```text
sha256:159496a050ac4c4dacf222b4e071b73ba4b8d7bc9f93436245d5f0dcf1ce1df4
```

containerd 中同样存在：

```text
opslab-api@sha256:159496a050ac...
```

两者完全一致。

正式不可变引用：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/opslab-api@sha256:159496a050ac4c4dacf222b4e071b73ba4b8d7bc9f93436245d5f0dcf1ce1df4
```

实际输出同时存在：

```text
159496a050ac4...
78d4da3ab1b36...
```

确认：

```text
159496...
→ Repository Manifest Digest

78d4da...
→ Local Image ID
```

Kubernetes Deployment 使用 repository digest，而不是本地 Image ID。

---

# 17. 三节点镜像预拉取

三节点均确认存在 v0.3.0：

```text
k8s-control-plane → v0.3.0
k8s-worker1       → v0.3.0
k8s-worker2       → v0.3.0
```

均对应：

```text
159496a050ac4
```

因此正式 RollingUpdate 前，节点镜像可用性已经预先验证。

---

# 18. Kubernetes RollingUpdate

Deployment 更新为：

```text
app.kubernetes.io/version: v0.3.0
```

镜像固定：

```text
@sha256:159496a050ac4c4dacf222b4e071b73ba4b8d7bc9f93436245d5f0dcf1ce1df4
```

上线前现场：

```text
Deployment READY=2/2
HPA CPU=11%/60%
min=2
max=4
replicas=2
```

原 Pod：

```text
worker1 → 10.244.1.26
worker2 → 10.244.2.25
```

原 EndpointSlice：

```text
10.244.1.26
10.244.2.25
```

说明升级前应用处于正常 1+1 分布。

---

# 19. RollingUpdate 后出现的 2+0 调度现象

升级完成后，新 Pod 一度变为：

```text
opslab-api-74cc9b59dd-cdpg2 → worker1
opslab-api-74cc9b59dd-qmfg8 → worker1
```

即：

```text
worker1 = 2
worker2 = 0
```

这引出一个值得复盘的问题：

> 明明配置 topologySpreadConstraints，为什么最终还会出现两个 Pod 在同一节点？

原因是 Deployment RollingUpdate 期间存在“旧 Pod + 新 Pod”同时运行的中间状态。

Scheduler 只在新 Pod 被创建时做 Placement 判断。某个新 Pod 被调度时，结合当时仍存在的旧 Pod，可能仍满足当前 topology spread 条件。随后旧 Pod 被删除，最终可能形成 2+0。

而 Scheduler：

```text
不会主动把已经 Running 的 Pod 从 worker1 搬到 worker2
```

即：

> Scheduler 负责 Placement，不负责持续 Rebalancing。

## 19.1 解决

没有删除 Deployment，也没有重启节点。

只删除其中一个 FastAPI Pod，Deployment 自动补回新副本。

实际恢复为：

```text
opslab-api-74cc9b59dd-cdpg2
→ k8s-worker1

opslab-api-74cc9b59dd-zh54w
→ k8s-worker2
```

最终：

```text
worker1 → 1
worker2 → 1
```

这再次体现：

```text
局部异常
→ 最小动作
→ Controller 自愈
```

---

# 20. ServiceMonitor 接入

新增：

```text
kubernetes/monitoring/opslab-api-servicemonitor.yaml
```

对应 Git：

```text
dcb6a06 feat(observability): scrape opslab api metrics
```

逻辑：

```text
Prometheus
    ↓
ServiceMonitor
namespace=monitoring
    ↓ namespaceSelector
namespace=opslab
    ↓ selector
Service/opslab-api
    ↓ port=http
Pod :8000
    ↓
GET /metrics
```

关键 label：

```text
release=opslab-monitoring
```

用于匹配当前 kube-prometheus-stack Prometheus 实例的 ServiceMonitor selector。

---

# 21. ServiceMonitor / Prometheus 验收

本次对话后续实验中，用户确认：

- ServiceMonitor 工作正常；
- Prometheus application Target 正常；
- FastAPI `/metrics` 成功进入 Prometheus；
- PromQL 查询有数据；
- 所需信息均符合预期。

由于具体后续完整 JSON 未粘贴到当前窗口，本报告不虚构 Target 数量或具体 PromQL 返回数值，只记录：

```text
FastAPI
→ /metrics
→ Service
→ ServiceMonitor
→ Prometheus
→ PromQL
```

链路已经实际验收通过。

---

# 22. 当前可观测性架构

```text
                    ┌───────────────────┐
                    │ Kubernetes API    │
                    └─────────┬─────────┘
                              │
                       kube-state-metrics
                              │
                              ▼
┌─────────────┐       ┌─────────────────┐
│ Linux Nodes │──────▶│   Prometheus    │
└──────┬──────┘       └────────┬────────┘
       │                       ▲
 node-exporter                 │
                               │
┌──────────────────┐           │
│ kubelet/cAdvisor │───────────┤
└──────────────────┘           │
                               │
┌──────────────────┐           │
│ FastAPI v0.3.0   │           │
│ /metrics         │───────────┘
└──────────────────┘

Prometheus
    ↓
PromQL
    ↓
Grafana
```

监控已经从节点/Pod 资源层升级为：

```text
基础设施
+
Kubernetes 对象
+
应用 HTTP 指标
```

---

# 23. 本阶段真正完成了什么

如果只写成“部署 Prometheus 和 Grafana，对 FastAPI 增加 metrics”，会严重低估本阶段价值。

更准确的总结：

## 23.1 Kubernetes 监控基础设施

完成：

```text
Prometheus Operator
Prometheus
Alertmanager
Grafana
kube-state-metrics
node-exporter
```

并完成真实 Targets 验收。

## 23.2 应用级可观测性

FastAPI 主动暴露：

```text
HTTP requests total
HTTP status code
HTTP request latency histogram
```

使 Prometheus 从“只能观察 Pod”升级为“能够观察应用本身”。

## 23.3 声明式服务发现

不是在 `prometheus.yml` 中手写 Pod IP，而是：

```text
ServiceMonitor
→ Service selector
→ Endpoint discovery
→ Pod replacement
→ Prometheus 自动更新 target
```

## 23.4 控制指标高基数

通过：

```text
/api/v1/events/{event_id}
```

代替实际资源 ID，避免 label cardinality 失控。

## 23.5 不可变镜像发布链

完成：

```text
Git source
→ ACR Build
→ image tag
→ RepoDigest verification
→ 3-node pre-pull
→ Kubernetes @sha256
→ RollingUpdate
```

并明确区分：

```text
RepoDigest
≠
Image ID
```

## 23.6 真实 SRE 排障案例

至少形成三个有复盘价值的问题：

### Incident A

```text
node-exporter ImagePullBackOff
```

Root Cause：

```text
-distroless 后缀重复
```

Recovery：

```text
修 values
→ helm upgrade
```

没有 uninstall。

### Incident B

```text
本地 FastAPI ModuleNotFoundError: redis
```

Root Cause：

```text
.venv 未同步 requirements.lock
```

不是 metrics 代码错误。

### Incident C

```text
RollingUpdate 后 FastAPI 两副本同节点
```

原因：

```text
调度时约束成立
+
旧 Pod 后续删除
+
Scheduler 不主动 rebalance
```

Recovery：

```text
只删除一个 Pod
→ Deployment recreate
→ Scheduler 重新 placement
→ 1+1
```

---

# 24. 本阶段值得掌握的知识点

复盘时建议重点回答以下问题，而不是死记命令。

## Prometheus

1. Prometheus 的 Pull 模型是什么？
2. `up` 指标代表什么？
3. 为什么 `count(up == 0)` 可能返回空，而不是 0？
4. Counter 与 Gauge 有什么区别？
5. Histogram 为什么会产生 bucket/count/sum？
6. `histogram_quantile()` 如何得到 P95？
7. 为什么 label cardinality 很重要？

## Prometheus Operator

1. Prometheus Operator 解决什么问题？
2. ServiceMonitor 是什么？
3. ServiceMonitor 如何从 Service 找到 Pods？
4. 为什么 Prometheus Pod IP 变化后不需要手改配置？
5. 为什么 Helm template 不一定直接产生 Prometheus StatefulSet？

## Grafana

1. Grafana 本身保存指标吗？
2. Grafana 与 Prometheus 是什么关系？
3. Dashboard `No data` 应该如何排查？
4. Dashboard Variable 有什么作用？

## Kubernetes

1. ServiceMonitor 为什么通过 Service，而不是直接绑定 Pod IP？
2. RollingUpdate 为什么可能暂时改变 Pod 分布？
3. topologySpreadConstraints 是调度约束还是持续 Rebalance 机制？
4. Deployment 为什么删除一个 Pod 后能够自动补回？

## Image

1. Tag、Digest、Image ID 有什么区别？
2. 为什么 Deployment 更适合固定 `@sha256`？
3. 为什么 ACR Digest 与 containerd RepoDigest 要交叉验证？

## SRE

1. 为什么 Helm failed 不等于整个 monitoring stack 都失败？
2. 如何区分 Primary Error 和 Cascading Error？
3. 为什么先保存现场再修改？
4. 为什么应优先选择最小恢复动作？

---

---

# 25. Grafana 应用级 Dashboard：从“有指标”到“可解释运行状态”

上一版阶段报告形成时，FastAPI 自定义指标已经进入 Prometheus，但还缺少真正属于 OpsLab 的应用级 Dashboard。本窗口继续完成了这一部分。

## 25.1 Dashboard 数据源取证

在设计 Dashboard 之前，没有直接照抄 PromQL，而是先确认真实 metric labels。

`opslab_http_requests_total` 实际包含：

```text
container
endpoint
instance
job
method
namespace
pod
route
service
status_code
```

其中本项目确认：

```text
namespace="opslab"
service="opslab-api"
job="opslab-api"
```

业务路由实际使用模板：

```text
/api/v1/events/{event_id}
```

而不是实际资源 ID。

Histogram：

```text
opslab_http_request_duration_seconds_bucket
```

实际存在标准 `le` bucket，包括：

```text
0.005
0.01
0.025
0.05
0.075
0.1
0.25
0.5
0.75
1.0
2.5
5.0
7.5
10.0
+Inf
```

这里 `le` 表示 bucket 上限（less than or equal），单位为秒；`+Inf` 为最终累计桶。

HPA 指标也实际存在：

```text
kube_horizontalpodautoscaler_status_current_replicas
kube_horizontalpodautoscaler_status_desired_replicas
```

FastAPI 容器资源指标通过 kubelet/cAdvisor 获取：

```text
container_cpu_usage_seconds_total
container_memory_working_set_bytes
```

Deployment available replicas 通过 kube-state-metrics 获取：

```text
kube_deployment_status_replicas_available
```

因此 Dashboard 的数据源不是基于猜测，而是基于真实 Prometheus 时间序列设计。

---

## 25.2 最终 PromQL 验证

Dashboard 创建前，四条代表性 PromQL 已直接通过 Prometheus HTTP API 验证。

### Business QPS

实际返回：

```text
0.04561019422925788 req/s
```

查询逻辑只统计：

```text
route=~"/api/v1/.*"
```

因此主动排除：

```text
/healthz
/readyz
/metrics
__unmatched__
```

避免将 Probe 与监控自身流量误认为业务流量。

### P95 latency

实际返回：

```text
0.03374999999999999 s
```

说明 Histogram bucket 与 `histogram_quantile()` 查询链路正常。

### Pod CPU

两个 FastAPI Pod 均有独立数据：

```text
opslab-api-74cc9b59dd-cdpg2
opslab-api-74cc9b59dd-zh54w
```

### Pod Memory

两个 FastAPI Pod 均有独立 working set 数据，验收时约为：

```text
46 MB 量级
```

因此应用 Dashboard 所需关键 PromQL 在进入 Grafana 前已经先行验证。

---

## 25.3 Dashboard as Code

Grafana Pod 中确认存在：

```text
grafana-sc-dashboard
grafana-sc-datasources
grafana
```

同时 monitoring namespace 中已经存在大量：

```text
grafana_dashboard=1
```

的 Dashboard ConfigMap。

因此本项目没有只在 Grafana UI 中手工创建 Dashboard，而是采用：

```text
Git
↓
ConfigMap
↓
grafana_dashboard=1
↓
Grafana dashboard sidecar
↓
Grafana 自动加载
```

新增：

```text
kubernetes/monitoring/dashboards/opslab-api-dashboard.yaml
```

Dashboard：

```text
OpsLab / FastAPI Application Overview
```

固定 UID：

```text
opslab-fastapi-overview
```

对应 Git 提交：

```text
64ae6fb feat(observability): add fastapi grafana dashboard
```

---

## 25.4 Dashboard 8 个 Panel

最终 Dashboard 包含：

1. Business QPS
2. Request Rate by Route
3. HTTP 2xx / 4xx / 5xx
4. FastAPI P95 Request Latency
5. FastAPI Pod CPU
6. FastAPI Pod Memory
7. Deployment Available Replicas
8. HPA Current / Desired Replicas

其层次可概括为：

```text
业务层
├── QPS
├── Route 请求率
├── HTTP 状态
└── P95

容器资源层
├── Pod CPU
└── Pod Memory

Kubernetes 控制层
├── Deployment Available
└── HPA Current / Desired
```

这使 Dashboard 能够同时回答：

```text
业务有没有流量？
请求是否报错？
请求是否变慢？
Pod 是否存在资源压力？
当前有多少可用副本？
HPA 是否正在尝试扩缩容？
```

---

## 25.5 Dashboard 实际验收

Server-side dry-run：

```text
configmap/opslab-api-dashboard created (server dry run)
```

正式 apply：

```text
configmap/opslab-api-dashboard created
```

ConfigMap 标签：

```text
grafana_dashboard=1
```

Grafana sidecar 日志真实出现：

```text
Writing /tmp/dashboards/opslab-api-dashboard.json (ascii)
```

随后调用 Grafana provisioning reload API：

```text
Response: 200 OK
{"message":"Dashboards config reloaded"}
```

最终用户在 Grafana UI 中确认：

```text
8 个 Panel 的实际情况都与预期完全相符
```

因此：

```text
ConfigMap
→ sidecar discovery
→ Grafana reload
→ 8 Panel data
```

链路 PASS。

---

# 26. PrometheusRule：从“看见异常”升级到“主动发现异常”

Dashboard 解决的是观察问题，但真正的 SRE 监控体系还必须能够主动发现异常。

因此下一步增加自定义 PrometheusRule。

文件：

```text
kubernetes/monitoring/rules/opslab-api-alerts.yaml
```

对应 Git：

```text
e58dcdd feat(alerting): add fastapi target down alert
```

---

## 26.1 为什么第一条规则选择 FastAPITargetDown

规则：

```text
FastAPITargetDown
```

表达式：

```promql
up{
  namespace="opslab",
  job="opslab-api",
  service="opslab-api"
} == 0
```

持续时间：

```text
for: 2m
```

severity：

```text
warning
```

没有优先选择“为了得到告警而故意破坏 MySQL 产生 5xx”。

原因是本项目已有真实业务数据和 Local PV，故障实验必须控制影响范围。

因此选择：

```text
只破坏 Prometheus scrape
而不破坏 FastAPI 业务和数据依赖
```

这符合项目一直采用的原则：

> 为了验证监控，不应制造不必要的数据风险。

---

## 26.2 PrometheusRule selector 取证

Prometheus CR 实际配置：

```text
ruleSelector={"matchLabels":{"release":"opslab-monitoring"}}
```

因此自定义 PrometheusRule 必须包含：

```yaml
release: opslab-monitoring
```

创建后实际检查：

```text
opslab-api-alerts
release=opslab-monitoring
```

Prometheus `/api/v1/rules?type=alert` 随后真实找到：

```text
FastAPITargetDown
```

初始状态：

```text
state: inactive
health: ok
duration: 120
```

这证明：

```text
PrometheusRule CR
→ Prometheus Operator
→ Prometheus rule configuration
→ rule loaded
```

链路 PASS。

---

# 27. 告警状态机与安全故障注入

## 27.1 健康基线

故障注入前两个 FastAPI targets：

```text
opslab-api-74cc9b59dd-cdpg2 up=1
opslab-api-74cc9b59dd-zh54w up=1
```

规则：

```text
FastAPITargetDown = inactive
```

---

## 27.2 故障注入方法

故障实验没有：

- 删除 FastAPI Pod；
- 停止 MySQL；
- 停止 Redis；
- 删除 PVC/PV；
- 修改业务数据。

只临时把 ServiceMonitor 的抓取路径：

```text
/metrics
```

修改为：

```text
/metrics-fault-injection
```

FastAPI 业务本身仍可运行，但 Prometheus 无法正常抓取 metrics。

这是一种：

```text
planned fault injection
```

而不是生产 Incident。

---

## 27.3 Inactive → Pending

Prometheus 很快观察到：

```text
opslab-api-74cc9b59dd-cdpg2 up=0
opslab-api-74cc9b59dd-zh54w up=0
```

规则：

```text
rule_state = pending
```

两个 target 分别出现 pending alert。

这证明：

```text
scrape failure
→ up=0
→ PromQL condition=true
→ alert Pending
```

链路成立。

---

## 27.4 Pending → Firing

在条件持续超过：

```text
for: 2m
```

后，实际观察到：

```text
rule_state = firing
```

两个 Pod 均进入 firing。

Alertmanager API 中出现两条：

```text
alertname=FastAPITargetDown
status.state=active
severity=warning
```

其中一条真实 annotation：

```text
Prometheus has failed to scrape FastAPI pod
opslab-api-74cc9b59dd-cdpg2
at 10.244.1.33:8000
for more than 2 minutes.
```

另一条对应：

```text
opslab-api-74cc9b59dd-zh54w
10.244.2.35:8000
```

因此：

```text
PrometheusRule Firing
→ Alertmanager Active
```

链路 PASS。

---

# 28. Alertmanager QQ 邮件通知

仅仅让 Alertmanager 收到 alert 还不能称为完整通知闭环。

初始 Alertmanager 中实际看到：

```text
receiver: null
```

这证明：

```text
Prometheus → Alertmanager
```

已经工作，但尚无真正外部通知渠道。

因此继续接入 QQ 邮箱 SMTP。

---

## 28.1 敏感信息处理

SMTP 授权码没有：

- 发送到聊天；
- 写入 ConfigMap；
- 写入 Git。

本地敏感配置：

```text
~/.config/opslab/alertmanager-email.env
```

权限：

```text
600
```

Kubernetes Secret：

```text
namespace: opslab
name: opslab-alertmanager-email
key: smtp-password
```

AlertmanagerConfig 中只引用 Secret，不保存授权码明文。

---

## 28.2 AlertmanagerConfig

创建：

```text
AlertmanagerConfig/opslab-email-alerts
namespace=opslab
```

关键非敏感配置：

```text
receiver=qq-email
groupWait=10s
groupInterval=1m
repeatInterval=4h
sendResolved=true
smarthost=smtp.qq.com:465
```

配置通过 server-side dry-run：

```text
alertmanagerconfig.monitoring.coreos.com/opslab-email-alerts created (server dry run)
```

随后正式创建成功。

Alertmanager 实际加载检查确认：

```text
qq-email receiver loaded = True
smtp.qq.com:465 loaded   = True
FastAPITargetDown loaded = True
```

因此：

```text
AlertmanagerConfig
→ Operator merge
→ Alertmanager active config
```

链路 PASS。

---

## 28.3 Firing 邮件验收

再次执行同样的安全 scrape fault injection。

`FastAPITargetDown` 进入 firing 后，QQ 邮箱真实收到邮件。

邮件至少包含：

```text
[1] Firing

alertname = FastAPITargetDown
container = opslab-api
endpoint = http
instance = 10.244.1.33:8000
job = opslab-api
namespace = opslab
pod = opslab-api-74cc9b59dd-cdpg2
service = opslab-api
severity = warning
```

Annotations：

```text
summary = FastAPI Prometheus target is down
```

以及具体 Pod / instance 描述。

这证明邮件不是手工测试邮件，而是：

```text
真实 PrometheusRule
→ 真实 Firing alert
→ Alertmanager routing
→ QQ SMTP
→ 外部邮箱
```

形成的告警通知。

---

## 28.4 Resolved 邮件验收

随后将 ServiceMonitor path 恢复：

```text
/metrics
```

最终：

```text
up=1
up=1
```

PrometheusRule：

```text
state = inactive
alerts = 0
```

Alertmanager：

```text
FastAPITargetDown: no active alerts
```

并且 QQ 邮箱真实收到：

```text
Resolved
```

恢复邮件。

因此最终闭环：

```text
Healthy
→ Inactive
→ scrape fault
→ up=0
→ Pending
→ Firing
→ Alertmanager Active
→ QQ Firing Email
→ restore /metrics
→ up=1
→ Inactive
→ Alertmanager Resolved
→ QQ Resolved Email
```

完整 PASS。

详细验收证据另见：

```text
docs/validation/fastapi-alerting-end-to-end-validation.md
```

---

# 29. 本阶段 Incident 拆分

阶段报告负责保存完整逻辑，但具体故障细节应单独保存，避免主报告被故障命令淹没。

本阶段拆分三个 Incident：

```text
docs/incidents/
├── kube-prometheus-stack-node-exporter-image-tag-incident.md
├── fastapi-local-runtime-dependency-incident.md
└── fastapi-rolling-update-pod-spread-incident.md
```

三者分别对应：

### Incident A

```text
node-exporter ImagePullBackOff
→ distroless tag 重复
→ helm upgrade 原地恢复
```

### Incident B

```text
compileall PASS
→ Uvicorn runtime import failure
→ .venv dependency drift
→ 同步 requirements.lock 恢复
```

### Incident C

```text
RollingUpdate
→ FastAPI replicas 2+0
→ Scheduler placement ≠ continuous rebalance
→ 删除单 Pod
→ Deployment + Scheduler 恢复 1+1
```

需要特别区分：

> ServiceMonitor `/metrics-fault-injection` 是计划内 Validation，不是 Incident。

---

# 30. 当前完整可观测性架构

本阶段结束后，OpsLab 已形成：

```text
Linux Nodes
  │
  └─ node-exporter ─────────────┐
                               │
kubelet / cAdvisor ─────────────┤
                               │
Kubernetes API                 │
  │                            │
  └─ kube-state-metrics ───────┤
                               ▼
                         Prometheus
                               ▲
                               │
FastAPI v0.3.0                 │
  │                            │
  └─ /metrics                  │
       ↓                       │
     Service                   │
       ↓                       │
  ServiceMonitor ──────────────┘

Prometheus
   │
   ├─ PromQL
   │    ↓
   │  Grafana
   │    ↓
   │  OpsLab FastAPI Dashboard
   │
   └─ PrometheusRule
        ↓
      Alertmanager
        ↓
   AlertmanagerConfig
        ↓
      QQ SMTP
        ↓
 Firing / Resolved Email
```

这已经不再只是“部署了一套监控软件”。

而是形成：

```text
采集
+
存储
+
查询
+
可视化
+
规则判断
+
告警状态机
+
通知路由
+
外部通知
+
恢复通知
```

完整链路。

---

# 31. 本阶段真正形成的 SRE 能力

## 31.1 分层可观测性

已经覆盖：

```text
基础设施层
→ Node

容器层
→ Pod CPU / Memory

Kubernetes 对象层
→ Deployment / HPA

应用层
→ HTTP QPS / Route / Status / Latency

告警层
→ Rule / Pending / Firing / Resolved

通知层
→ Alertmanager / SMTP / Email
```

---

## 31.2 Dashboard as Code

Dashboard 不依赖浏览器中的人工配置，而是：

```text
Git
→ ConfigMap
→ Sidecar
→ Grafana
```

可版本化、可复现。

---

## 31.3 Alerting as Code

PrometheusRule 使用 Kubernetes CR 管理。

告警条件、持续时间、标签与 annotation 均进入版本控制。

---

## 31.4 Secret 与配置分离

邮件授权码：

```text
Secret
```

路由逻辑：

```text
AlertmanagerConfig
```

部署辅助：

```text
runtime env file / script
```

避免把真实授权码提交 Git。

---

## 31.5 安全故障注入

为了验证告警：

```text
没有破坏业务数据
没有删除持久卷
没有停止数据库
```

而是最小化修改 scrape path。

这体现：

> 故障演练的目的不是“制造更大的故障”，而是以最小风险证明监控与恢复机制。

---

# 32. Git 里程碑

本阶段的重要提交已经形成连续演进：

```text
11dd3e6 feat(monitoring): deploy kube prometheus stack

07a8514 feat(observability): expose fastapi prometheus metrics

dcb6a06 feat(observability): scrape opslab api metrics

64ae6fb feat(observability): add fastapi grafana dashboard

e58dcdd feat(alerting): add fastapi target down alert
```

其含义依次为：

```text
监控基础设施
↓
应用 instrumentation
↓
声明式采集
↓
应用级可视化
↓
主动告警
```

QQ 邮件 AlertmanagerConfig 已完成实际部署与验收；其敏感信息必须继续保持在本地私有配置与 Kubernetes Secret 中，提交 Git 前需继续检查仓库中不存在真实邮箱授权码。

---

# 33. 当前仍存在的不足

本阶段完成后，以下内容已不再属于“尚缺”：

```text
FastAPI 专属 Grafana Dashboard   ✅
PrometheusRule                  ✅
Alertmanager 真实告警           ✅
外部 Email Firing / Resolved    ✅
```

下一阶段仍需继续：

```text
MySQL 基础监控
Redis 基础监控
Prometheus 独立持久化
更系统的故障演练
MySQL 备份 / 恢复
最终 SRE 验收
README / 架构图 / 测试报告 / 项目总结
```

其中 MySQL / Redis 监控应控制范围，不应为了组件数量无限堆 exporter。

Prometheus persistence 仍应：

```text
新增独立 VMware 数据盘
→ Local PV
→ Prometheus TSDB persistence
→ 重建后历史数据恢复验证
```

禁止复用现有 MySQL / Redis 数据盘。

---

# 34. 阶段结论

如果只把本阶段总结为：

> “安装 Prometheus、Grafana，并配置了邮箱告警。”

会严重低估本阶段的工程价值。

更准确的描述是：

> 在三节点 kubeadm Kubernetes 环境中，以 kube-prometheus-stack 与 Prometheus Operator 建立分层监控基础设施；对 FastAPI v0.3.0 实现低基数 HTTP Counter / Histogram instrumentation，通过 ServiceMonitor 声明式接入 Prometheus；在真实 metric labels 与 PromQL 取证基础上，以 ConfigMap + Grafana sidecar 实现应用 Dashboard as Code；随后以 PrometheusRule 建立 FastAPITargetDown 告警，通过最小风险的 scrape fault injection 完成 Inactive → Pending → Firing → Resolved 状态机验证，并使用 AlertmanagerConfig、Kubernetes Secret 与 QQ SMTP 实际完成 Firing / Resolved 外部邮件通知，从而形成经过真实部署、故障注入、告警、恢复和验收证明的端到端 SRE 可观测性闭环。

本阶段最重要的成果不是组件数量，而是以下证据链：

```text
Application
→ Metrics
→ ServiceMonitor
→ Prometheus
→ PromQL
→ Grafana
```

以及：

```text
Scrape Failure
→ up=0
→ PrometheusRule
→ Pending
→ Firing
→ Alertmanager
→ QQ Email
→ Recovery
→ Resolved Email
```

最终已经能够回答：

```text
系统是否正常？
业务是否有流量？
延迟是否异常？
Pod 是否存在资源压力？
副本是否健康？
Prometheus 是否能发现故障？
告警是否真的送达？
恢复以后是否真的闭环？
```

这标志着 OpsLab 已从“有监控”进入：

> **可观测、可告警、可验证恢复。**

---

# 35. 复盘时推荐的叙述顺序

以后无论自己复盘、写课程设计、制作 PPT，还是准备实习面试，都建议按照：

```text
为什么需要可观测性
↓
原来缺什么
↓
为什么选 kube-prometheus-stack
↓
部署前做了哪些真实环境检查
↓
Prometheus 如何采集集群指标
↓
为什么 FastAPI 还需要应用指标
↓
Counter / Histogram 为什么这样设计
↓
ServiceMonitor 如何完成声明式发现
↓
Dashboard 为什么要 As Code
↓
如何从 metric labels 推导 PromQL
↓
为什么需要 PrometheusRule
↓
为什么选择安全 scrape fault injection
↓
Inactive / Pending / Firing 是什么
↓
Alertmanager 如何路由
↓
Secret 如何保护 SMTP 授权码
↓
如何证明 Firing 邮件真的收到
↓
如何证明 Resolved 也真正完成
↓
过程中发生过哪些真实 Incident
↓
最终形成了什么能力
↓
下一阶段还缺什么
```

不要按照“执行了多少条命令”来衡量项目。

本阶段真正值得复盘的是：

```text
设计理由
+
证据链
+
故障边界
+
最小恢复
+
安全验证
+
版本化配置
+
端到端闭环
```
