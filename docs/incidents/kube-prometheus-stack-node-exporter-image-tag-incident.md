# kube-prometheus-stack node-exporter 镜像 Tag 故障复盘

> 类型：Incident  
> 组件：kube-prometheus-stack / prometheus-node-exporter  
> 建议仓库路径：`docs/incidents/kube-prometheus-stack-node-exporter-image-tag-incident.md`

---

# 1. Incident 摘要

首次部署 kube-prometheus-stack 时，Helm release 最终显示：

```text
REVISION 1
STATUS failed
DESCRIPTION context canceled
```

但实际现场并不是整个 monitoring stack 全部失败。

主要异常集中在：

```text
node-exporter ×3
ImagePullBackOff
```

最终定位到 node-exporter 镜像 Tag 被重复追加：

```text
v1.12.1-distroless-distroless
```

Root Cause 是 values 中已经填写：

```yaml
tag: v1.12.1-distroless
```

同时 node-exporter child chart 又根据：

```yaml
distroless: true
```

自动追加 `-distroless`。

通过最小修改 values 并执行：

```text
helm upgrade
```

最终恢复：

```text
REVISION 2
STATUS deployed
```

node-exporter：

```text
DESIRED=3
CURRENT=3
READY=3
AVAILABLE=3
```

---

# 2. 影响范围

受影响：

```text
prometheus-node-exporter DaemonSet
→ 3 Pods ImagePullBackOff
```

未证明故障的组件不能被一并视为失败。

实际已有多个组件正常运行，包括：

```text
Grafana
kube-state-metrics
Prometheus Operator
Prometheus
Alertmanager
```

因此故障域应缩小为：

```text
node-exporter 镜像拉取
```

而不是：

```text
整个 monitoring stack
```

---

# 3. 初始现象

错误配置：

```yaml
prometheus-node-exporter:
  image:
    tag: v1.12.1-distroless
```

最终渲染镜像却成为：

```text
v1.12.1-distroless-distroless
```

节点不存在该镜像，因此：

```text
ImagePullBackOff
```

---

# 4. 为什么没有立即 uninstall

如果只看到：

```text
helm status = failed
```

就执行：

```text
helm uninstall
```

会有三个问题：

1. 丢失现场证据；
2. 删除已经正常创建的资源；
3. 把局部镜像错误扩大为整个监控栈重建。

因此实际排障采用：

```text
Helm failed
↓
get
↓
Pods / Deployment / StatefulSet
↓
Events
↓
确认异常集中于 node-exporter
↓
检查最终镜像
```

而不是直接重装。

---

# 5. Primary Error

真正的 Primary Error：

```text
node-exporter image reference invalid
```

更具体：

```text
v1.12.1-distroless
+
child chart automatic distroless suffix
=
v1.12.1-distroless-distroless
```

Helm release 的：

```text
failed
context canceled
```

不能直接当成根因。

它只是上层结果。

---

# 6. Root Cause

根因不是：

- Kubernetes 节点网络；
- containerd；
- ACR；
- Prometheus Operator；
- RBAC。

而是：

> **父级 values 与 node-exporter child chart 的 tag 组装逻辑叠加，导致 distroless 后缀重复。**

---

# 7. 最小修复

最终改为：

```yaml
prometheus-node-exporter:
  image:
    tag: v1.12.1
    distroless: true
```

由 child chart 负责生成：

```text
v1.12.1-distroless
```

随后执行：

```text
helm upgrade
```

没有：

```text
helm uninstall
```

---

# 8. 恢复验证

Helm：

```text
REVISION 2
STATUS deployed
```

node-exporter：

```text
DESIRED      3
CURRENT      3
READY        3
UP-TO-DATE   3
AVAILABLE    3
```

后续 Prometheus Target 总体验收：

```text
22 Targets
22 UP
0 DOWN
```

node-exporter 已进入正常监控链。

---

# 9. 本 Incident 的 SRE 价值

最重要的经验：

```text
Release Failed
≠
所有组件失败
```

应该区分：

```text
Primary Error
vs
Cascading / Summary Error
```

本次正确思路：

```text
失败
→ 保存现场
→ 缩小故障域
→ 找 Primary Error
→ 最小修改
→ 原地恢复
→ 再验收
```

---

# 10. 后续预防

对于 Helm child chart 的镜像字段：

1. 不只看 values 文件；
2. 应检查最终渲染结果；
3. 关注 child chart 是否自动拼接 suffix；
4. 镜像正式部署前尽量提前验证 tag / digest 是否存在；
5. Helm failed 时先确认实际失败资源，不立即 uninstall。

---

# 11. Incident 结论

本次故障不是 kube-prometheus-stack 架构问题，而是：

> **node-exporter child chart 镜像 Tag 组合方式与自定义 values 重复表达 distroless，最终产生无效镜像引用。**

通过最小化修改并原地 `helm upgrade` 恢复，完整保留了现场与已正常运行资源。

Incident：

```text
RESOLVED
```
