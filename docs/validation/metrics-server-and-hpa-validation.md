# Metrics Server 与 HPA 自动扩缩容验证报告

## 1. 验证目标

本次验证用于确认 OpsLab Kubernetes 项目已经具备基于资源指标进行自动扩缩容的能力。

验证范围包括：

* Metrics Server 能否持续提供 Pod CPU / Memory 指标；
* HPA 能否读取 FastAPI Deployment 的 CPU Resource Metric；
* CPU `requests` 能否作为 HPA Utilization 计算基准；
* FastAPI 在持续 HTTP 压力下能否触发自动扩容；
* HPA 是否遵守 `maxReplicas`；
* HPA 扩容后 Pod 是否继续满足跨节点拓扑分布约束；
* 压力解除后 CPU 是否正常下降；
* HPA 是否经过稳定窗口后自动缩容；
* HPA 是否遵守 `minReplicas`；
* 整个扩缩容过程是否能够由 Metrics、HPA Conditions、Events 和 Pod 调度结果形成完整证据链。

本次验证不是简单确认 HPA YAML 能够创建，而是验证：

```text
Metrics
→ HPA Decision
→ Deployment Scaling
→ Scheduler Placement
→ Load Removal
→ Scale Down
```

完整控制闭环。

---

# 2. 验证环境

Kubernetes 集群：

```text
k8s-control-plane   192.168.8.10
k8s-worker1         192.168.8.11
k8s-worker2         192.168.8.12
```

Kubernetes：

```text
v1.36.3
```

Metrics Server：

```text
v0.9.0
```

业务 Deployment：

```text
Namespace: opslab
Deployment: opslab-api
```

FastAPI 初始副本数：

```text
2
```

正常分布：

```text
k8s-worker1
└── FastAPI ×1

k8s-worker2
└── FastAPI ×1
```

FastAPI CPU Resource：

```yaml
requests:
  cpu: 50m

limits:
  cpu: 250m
```

HPA：

```text
API: autoscaling/v2
Metric: CPU Resource Utilization
minReplicas: 2
maxReplicas: 4
targetAverageUtilization: 60%
```

---

# 3. HPA CPU Utilization 计算基础

FastAPI 每个 Pod：

```text
CPU request = 50m
```

HPA 使用的 CPU Utilization 可以理解为：

```text
CPU Utilization
=
实际 CPU Usage / CPU Request × 100%
```

例如一个 FastAPI Pod：

```text
实际 CPU = 30m
CPU request = 50m
```

则：

```text
30m / 50m
= 60%
```

因此本项目：

```text
averageUtilization = 60%
```

对应的目标并不是：

```text
限制 FastAPI 只能使用 60% CPU
```

而是：

```text
HPA 根据 Pod 平均 CPU Usage / CPU Request
动态调整副本数量，
尝试使平均利用率接近 60%。
```

CPU request 同时还参与 Kubernetes Scheduler 的节点资源规划。

---

# 4. 空闲基线验证

HPA 创建后观察：

```text
NAME         REFERENCE               TARGETS        MINPODS   MAXPODS   REPLICAS
opslab-api   Deployment/opslab-api   cpu: 12%/60%   2         4         2
```

当时两个 FastAPI Pod CPU：

```text
4m
8m
```

平均：

```text
(4m + 8m) / 2
= 6m
```

FastAPI CPU request：

```text
50m
```

因此：

```text
6m / 50m
= 12%
```

与 HPA：

```text
12% / 60%
```

完全一致。

HPA Condition：

```text
ScalingActive=True
Reason=ValidMetricFound
```

说明 HPA 已经成功获取 CPU Resource Metric，并可以正常计算期望副本数。

空闲状态下：

```text
CPU Utilization ≈ 12%
```

明显低于：

```text
Target = 60%
```

但由于：

```text
minReplicas=2
```

Deployment 保持：

```text
2 replicas
```

空闲基线验证：

```text
PASS
```

---

# 5. HTTP 压测方法

为了尽可能隔离 FastAPI CPU 变量，本次 HPA 压测使用：

```text
GET /healthz
```

而没有使用 MySQL CRUD 或 Redis Cache-Aside 接口。

这样可以减少：

```text
MySQL
Redis
数据持久化
缓存行为
```

对 HPA CPU 实验的干扰。

请求路径：

```text
Load Generator
      │
      ▼
192.168.8.11:80
      │
Host: api.opslab.local
      │
      ▼
NGINX Ingress
      │
      ▼
opslab-api Service
      │
      ▼
FastAPI Pods
```

第一轮真实实验使用持续 HTTP 并发请求，使 FastAPI CPU 明显上升。

---

# 6. HPA Scale Up 验证

压测开始后，HPA CPU 指标依次观察到：

```text
122% / 60%
305% / 60%
280% / 60%
242% / 60%
```

说明 FastAPI CPU Utilization 已经明显超过 HPA Target。

其中：

```text
122% / 60%
```

时，按照 HPA 的基本比例关系：

```text
desiredReplicas
≈ ceil(
currentReplicas
× currentUtilization
/ targetUtilization
)
```

即：

```text
ceil(
2 × 122 / 60
)

≈ 5
```

但本项目配置：

```text
maxReplicas=4
```

因此实际最大副本数受到 HPA 上限控制。

最终：

```text
2 replicas
↓
4 replicas
```

HPA Event：

```text
SuccessfulRescale
New size: 4;
reason: cpu resource utilization
(percentage of request) above target
```

Scale Up：

```text
PASS
```

---

# 7. maxReplicas 限制验证

持续压测期间，即使 Deployment 已经扩容到：

```text
4 Pods
```

CPU Utilization 仍观察到：

```text
305%
280%
242%
```

此时 HPA Condition：

```text
ScalingLimited=True
Reason=TooManyReplicas
```

Message：

```text
the desired replica count is more than
the maximum replica count
```

说明：

```text
当前 CPU 压力
↓
HPA 理论上希望继续增加副本
↓
但 maxReplicas=4
↓
实际保持 4 Pods
```

因此：

```text
maxReplicas enforcement: PASS
```

该现象同时说明：

```text
ScalingLimited=True
```

并不一定代表 HPA 故障。

在本次场景中，它代表 HPA 计算出的 desired replicas 已经超过配置允许的最大副本数。

---

# 8. 扩容后的 Pod 调度验证

HPA 扩容到 4 个 FastAPI Pod 后：

```text
opslab-api-d86cc8bb5-cch5r
→ k8s-worker2

opslab-api-d86cc8bb5-hmlpx
→ k8s-worker1

opslab-api-d86cc8bb5-q4j8x
→ k8s-worker2

opslab-api-d86cc8bb5-ww7qb
→ k8s-worker1
```

最终分布：

```text
k8s-worker1
├── FastAPI
└── FastAPI

k8s-worker2
├── FastAPI
└── FastAPI
```

即：

```text
2 + 2
```

FastAPI Deployment 已配置：

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: DoNotSchedule
```

因此本次实验同时验证：

```text
HPA 创建新副本
        │
        ▼
ReplicaSet 创建 Pod
        │
        ▼
Scheduler 调度
        │
        ▼
topologySpreadConstraints 生效
        │
        ▼
两个 Worker 保持均衡分布
```

验证结果：

```text
Topology Spread after HPA Scale Up: PASS
```

---

# 9. 压测解除后的 CPU 回落

停止 HTTP 压测后，4 个 FastAPI Pod CPU：

```text
6m
6m
8m
5m
```

HPA：

```text
cpu: 11% / 60%
```

说明业务压力解除后：

```text
CPU Utilization
300% 级别
↓
约 11%
```

Metrics Server 能够正常反映压力解除后的资源变化。

验证结果：

```text
Metrics Recovery after Load Removal: PASS
```

---

# 10. Scale Down Stabilization 验证

压力解除后，FastAPI CPU 已经明显低于：

```text
60%
```

但是 Deployment 没有立即：

```text
4 → 2
```

HPA Condition 曾显示：

```text
AbleToScale=True
Reason=ScaleDownStabilized
```

Message：

```text
recent recommendations were higher than
current one, applying the highest recent
recommendation
```

说明 HPA 并不会因为一个瞬时低负载采样立即删除 Pod，而是通过 Scale Down Stabilization 机制减少副本数量快速上下波动。

因此：

```text
负载下降
≠
立即缩容
```

该机制可以降低：

```text
扩容
↓
立即缩容
↓
再次扩容
```

造成的副本抖动。

验证结果：

```text
Scale Down Stabilization: PASS
```

---

# 11. HPA Scale Down 验证

经过稳定阶段后，HPA 最终自动执行：

```text
4 replicas
↓
2 replicas
```

最终：

```text
NAME         TARGETS        MINPODS   MAXPODS   REPLICAS
opslab-api   cpu: 11%/60%   2         4         2
```

Deployment：

```text
2/2 READY
2 UP-TO-DATE
2 AVAILABLE
```

最终 FastAPI：

```text
k8s-worker1 → 1 Pod
k8s-worker2 → 1 Pod
```

HPA Event：

```text
SuccessfulRescale
New size: 2;
reason: All metrics below target
```

因此：

```text
Scale Down: PASS
```

---

# 12. minReplicas 限制验证

缩容完成后：

```text
CPU ≈ 11%
Target = 60%
Current replicas = 2
```

按照 CPU 指标计算，HPA 理论上可能希望进一步降低副本数。

但：

```text
minReplicas=2
```

最终 HPA Condition：

```text
ScalingLimited=True
Reason=TooFewReplicas
```

Message：

```text
the desired replica count is less than
the minimum replica count
```

因此：

```text
CPU 压力很低
↓
理论 desired replicas < 2
↓
minReplicas=2
↓
保持 2 Pods
```

验证：

```text
minReplicas enforcement: PASS
```

这同时保证 FastAPI 即使在低负载状态下，也继续维持：

```text
worker1 ×1
worker2 ×1
```

基础跨节点冗余。

---

# 13. HPA 完整控制链

本次实验最终形成以下真实控制闭环：

```text
HTTP Load
    │
    ▼
FastAPI CPU 上升
    │
    ▼
kubelet 暴露资源指标
    │
    ▼
Metrics Server
    │
    ▼
metrics.k8s.io
    │
    ▼
HPA
CPU Usage / CPU Request
    │
    ▼
CPU 超过 60%
    │
    ▼
desired replicas 增加
    │
    ▼
Deployment / ReplicaSet
    │
    ▼
创建新 Pod
    │
    ▼
Scheduler
    │
    ▼
topologySpreadConstraints
    │
    ▼
worker1 / worker2 = 2 + 2
```

压力解除：

```text
HTTP Load Stop
    │
    ▼
FastAPI CPU 下降
    │
    ▼
Metrics Server 更新指标
    │
    ▼
HPA CPU ≈ 11%
    │
    ▼
Scale Down Stabilization
    │
    ▼
4 → 2
    │
    ▼
minReplicas=2
    │
    ▼
worker1 / worker2 = 1 + 1
```

---

# 14. 压测脚本工程化

为了避免使用永久运行的后台 `curl` 循环，本项目新增：

```text
scripts/hpa-load-test.sh
```

脚本支持：

```text
TARGET_URL
HOST_HEADER
CONCURRENCY
DURATION
```

默认：

```text
TARGET_URL=http://192.168.8.11/healthz
HOST_HEADER=api.opslab.local
CONCURRENCY=20
DURATION=180
```

脚本提供：

```text
目标可达性检查
并发控制
持续时间控制
Ctrl+C 中断
trap 自动 cleanup
后台 PID 精确回收
```

避免采用：

```text
pkill curl
```

等可能误伤其他进程的宽范围清理方式。

Bash 语法验证：

```bash
bash -n scripts/hpa-load-test.sh
```

结果：

```text
exit code = 0
```

Smoke Test：

```bash
CONCURRENCY=2 DURATION=5 \
./scripts/hpa-load-test.sh
```

结果：

```text
Target check: PASS
Load test completed.
```

SIGINT 验证：

```bash
CONCURRENCY=5 DURATION=60 \
./scripts/hpa-load-test.sh
```

运行期间：

```text
Ctrl+C
```

退出状态：

```text
130
```

说明脚本 SIGINT 处理路径正常。

---

# 15. 最终验证结果

| 验证项                                  | 结果   |
| ------------------------------------ | ---- |
| Metrics Server 提供 FastAPI CPU Metric | PASS |
| HPA 获取 CPU Resource Metric           | PASS |
| CPU Request 参与 Utilization 计算        | PASS |
| 空闲状态维持 2 Pods                        | PASS |
| HTTP 压力导致 CPU 上升                     | PASS |
| CPU 超过 60% Target                    | PASS |
| HPA 自动 Scale Up                      | PASS |
| Deployment 2 → 4                     | PASS |
| maxReplicas=4 生效                     | PASS |
| TooManyReplicas Condition 验证         | PASS |
| 新 Pod 全部 Ready                       | PASS |
| topologySpreadConstraints 继续生效       | PASS |
| worker1 / worker2 达到 2 + 2           | PASS |
| 停止压力后 CPU 回落                         | PASS |
| Scale Down Stabilization 生效          | PASS |
| HPA 自动 Scale Down                    | PASS |
| Deployment 4 → 2                     | PASS |
| minReplicas=2 生效                     | PASS |
| TooFewReplicas Condition 验证          | PASS |
| 最终恢复 worker1 / worker2 各 1 Pod       | PASS |
| HPA 压测脚本语法检查                         | PASS |
| HPA 压测脚本 Smoke Test                  | PASS |
| HPA 压测脚本 SIGINT 处理                   | PASS |

---

# 16. 最终结论

本次验证证明 OpsLab Kubernetes 项目已经具备完整的 CPU Resource Metric 自动扩缩容能力。

不是仅完成：

```text
创建 HPA YAML
```

而是实际完成并验证：

```text
Metrics Server
+
CPU Resource Request
+
HPA CPU Utilization
+
HTTP Load Test
+
Scale Up
+
maxReplicas
+
Scheduler
+
Topology Spread
+
Load Removal
+
Scale Down Stabilization
+
Scale Down
+
minReplicas
```

完整控制闭环。

最终实际状态：

```text
Idle:
FastAPI ×2
worker1 ×1
worker2 ×1

High Load:
FastAPI ×4
worker1 ×2
worker2 ×2

Load Removed:
FastAPI ×2
worker1 ×1
worker2 ×1
```

因此：

```text
Metrics Server Validation: PASS
HPA Scale Up Validation: PASS
HPA Scale Down Validation: PASS
Topology Scheduling Validation: PASS
HPA End-to-End Validation: PASS
```

Metrics Server + HPA 阶段可以正式封板。

