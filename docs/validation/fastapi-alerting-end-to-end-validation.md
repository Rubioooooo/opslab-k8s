# FastAPI 告警端到端验收报告

> 项目：基于 kubeadm 的 Kubernetes 云原生应用部署与 SRE 稳定性实践  
> 验收范围：FastAPI Target Down → PrometheusRule → Alertmanager → QQ Email → Resolved  
> 建议仓库路径：`docs/validation/fastapi-alerting-end-to-end-validation.md`

---

# 1. 验收目的

本报告专门保存本次 Alerting 阶段的最终验收证据。

它不承担整个可观测性阶段的设计叙述，也不把计划内故障注入写成 Incident。

本次需要证明的是：

```text
FastAPI 正常
↓
Prometheus target up=1
↓
故障注入
↓
up=0
↓
PrometheusRule Pending
↓
Firing
↓
Alertmanager Active
↓
QQ 邮箱收到 Firing
↓
恢复
↓
up=1
↓
Rule Inactive
↓
Alertmanager Resolved
↓
QQ 邮箱收到 Resolved
```

只有这条链路真实完成，才能说明告警体系不是“YAML 已创建”，而是端到端可用。

---

# 2. 验收前基线

## 2.1 FastAPI

Deployment：

```text
opslab-api
```

Namespace：

```text
opslab
```

验收时两个 Pod：

```text
opslab-api-74cc9b59dd-cdpg2
→ 10.244.1.33
→ k8s-worker1

opslab-api-74cc9b59dd-zh54w
→ 10.244.2.35
→ k8s-worker2
```

---

## 2.2 Prometheus target

Prometheus 查询：

```promql
up{
  namespace="opslab",
  job="opslab-api",
  service="opslab-api"
}
```

故障前实际：

```text
opslab-api-74cc9b59dd-cdpg2 up=1
opslab-api-74cc9b59dd-zh54w up=1
```

因此健康基线 PASS。

---

# 3. PrometheusRule

资源：

```text
PrometheusRule/opslab-api-alerts
namespace=monitoring
```

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

持续条件：

```text
for: 2m
```

labels：

```text
severity=warning
service=opslab-api
```

annotation summary：

```text
FastAPI Prometheus target is down
```

Prometheus 实际加载后：

```text
state=inactive
health=ok
duration=120
alerts=[]
```

因此：

```text
PrometheusRule CR
→ Operator
→ Prometheus loaded rule
```

PASS。

对应 Git：

```text
e58dcdd feat(alerting): add fastapi target down alert
```

---

# 4. 为什么使用 scrape fault injection

本次验收明确禁止通过以下方法制造告警：

```text
停止 MySQL
删除 MySQL/Redis 数据
删除 PVC/PV
格式化数据盘
删除整个 Deployment
重装组件
```

实际采用：

```text
ServiceMonitor endpoint path
/metrics
→ /metrics-fault-injection
```

这样：

```text
FastAPI 业务
→ 继续存在

Prometheus scrape
→ HTTP 抓取失败

up
→ 0
```

故障边界非常清晰。

因此该操作属于：

```text
Planned Fault Injection
```

而不是 Incident。

---

# 5. Inactive → Pending 验收

修改 ServiceMonitor 后，实际观察：

```text
opslab-api-74cc9b59dd-cdpg2 up=0
opslab-api-74cc9b59dd-zh54w up=0
```

随后规则状态：

```text
rule_state = pending
```

两个实例分别：

```text
alert_state = pending
pod = opslab-api-74cc9b59dd-cdpg2
instance = 10.244.1.33:8000

alert_state = pending
pod = opslab-api-74cc9b59dd-zh54w
instance = 10.244.2.35:8000
```

结论：

```text
Scrape Failure
→ up=0
→ expr=true
→ Pending
```

PASS。

---

# 6. Pending → Firing 验收

实际输出中先观察到：

```text
rule_state = pending
```

随后：

```text
rule_state = firing
```

两个 Pod 均：

```text
alert_state = firing
```

说明 `for: 2m` 生效。

因此状态机：

```text
Inactive
→ Pending
→ Firing
```

PASS。

---

# 7. Alertmanager Active 验收

Alertmanager API 中实际出现两个 `FastAPITargetDown`。

实例 A：

```text
alertname = FastAPITargetDown
instance = 10.244.1.33:8000
pod = opslab-api-74cc9b59dd-cdpg2
namespace = opslab
service = opslab-api
severity = warning
status.state = active
```

annotation：

```text
Prometheus has failed to scrape FastAPI pod
opslab-api-74cc9b59dd-cdpg2
at 10.244.1.33:8000
for more than 2 minutes.
```

实例 B：

```text
instance = 10.244.2.35:8000
pod = opslab-api-74cc9b59dd-zh54w
status.state = active
```

因此：

```text
Prometheus Firing
→ Alertmanager Active
```

PASS。

---

# 8. AlertmanagerConfig / Email Receiver

AlertmanagerConfig：

```text
name=opslab-email-alerts
namespace=opslab
```

实际非敏感配置：

```text
receiver=qq-email
groupWait=10s
groupInterval=1m
repeatInterval=4h
sendResolved=true
smarthost=smtp.qq.com:465
```

SMTP 授权码保存在：

```text
Secret/opslab-alertmanager-email
namespace=opslab
key=smtp-password
```

真实授权码：

```text
没有发送到聊天
没有写入 Git
没有写入 ConfigMap
```

本地环境文件：

```text
~/.config/opslab/alertmanager-email.env
```

用于部署时注入非 Git 配置。

Alertmanager 加载检查实际确认：

```text
qq-email receiver loaded = True
smtp.qq.com:465 loaded   = True
FastAPITargetDown loaded = True
```

PASS。

---

# 9. Firing Email 验收

FastAPITargetDown 进入 Firing 后，QQ 邮箱真实收到告警邮件。

邮件内容包含：

```text
[1] Firing
```

以及真实 labels：

```text
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

以及对应 Pod、instance 的 scrape failure 描述。

这证明：

```text
Alertmanager
→ qq-email receiver
→ smtp.qq.com:465
→ 外部 QQ 邮箱
```

真实可用。

PASS。

---

# 10. 恢复验收

将 ServiceMonitor endpoint path 恢复：

```text
/metrics
```

恢复过程中曾短时间继续观察到：

```text
up=0
```

随后无需额外修改，最终自动恢复：

```text
opslab-api-74cc9b59dd-cdpg2 up=1
opslab-api-74cc9b59dd-zh54w up=1
```

因此没有进行重启或其他破坏性操作。

结合最终结果，可判断此次短暂滞后更符合配置传播 / 后续 scrape 周期带来的暂时状态，而不是持续故障。

---

# 11. Rule 恢复验收

Prometheus Rule 最终：

```text
rule_state = inactive
alerts = 0
```

因此：

```text
Firing
→ condition=false
→ Inactive
```

PASS。

---

# 12. Alertmanager Resolved 验收

恢复后 Alertmanager：

```text
FastAPITargetDown: no active alerts
```

同时 QQ 邮箱真实收到：

```text
Resolved
```

邮件。

因为：

```text
sendResolved=true
```

所以恢复事件也进入通知链。

最终：

```text
Firing Email
→ Recovery
→ Resolved Email
```

PASS。

---

# 13. 最终验收矩阵

| 验收项 | 实际结果 | 结论 |
|---|---|---|
| FastAPI targets 健康基线 | 2 × `up=1` | PASS |
| PrometheusRule 加载 | `health=ok` | PASS |
| 初始规则状态 | `inactive` | PASS |
| scrape fault injection | 2 × `up=0` | PASS |
| Pending | 2 个 pending alert | PASS |
| Firing | 2 个 firing alert | PASS |
| Alertmanager 接收 | 2 个 active alert | PASS |
| QQ Firing 邮件 | 实际收到 | PASS |
| `/metrics` 恢复 | 2 × `up=1` | PASS |
| Rule 恢复 | `inactive`, `alerts=0` | PASS |
| Alertmanager active 清除 | 无 active FastAPITargetDown | PASS |
| QQ Resolved 邮件 | 实际收到 | PASS |

---

# 14. 验收结论

本次验收完整证明：

```text
FastAPI /metrics
→ ServiceMonitor
→ Prometheus
→ up metric
→ PrometheusRule
→ Pending
→ Firing
→ Alertmanager
→ AlertmanagerConfig
→ QQ SMTP
→ Firing Email
→ Recovery
→ Resolved Email
```

端到端链路真实可用。

本次验收价值不在于“创建了一条告警规则”，而在于：

> **告警条件、状态机、Alertmanager 接收、真实外部通知以及恢复通知全部通过实际故障注入完成验证。**

因此：

```text
FastAPI Alerting End-to-End Validation
= PASS
```
