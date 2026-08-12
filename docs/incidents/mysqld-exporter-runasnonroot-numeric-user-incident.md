# mysqld-exporter `runAsNonRoot` 非数字用户启动失败 Incident

## 1. Incident Summary

在将 `mysqld_exporter` 从临时测试 Pod 切换为正式 Kubernetes Deployment 时，Deployment 无法完成 rollout：

```text
deployment "opslab-mysqld-exporter" exceeded its progress deadline
```

进一步检查发现正式 Pod 状态为：

```text
CreateContainerConfigError
```

该故障仅影响新部署的 MySQL exporter，未影响 MySQL、FastAPI、Redis、Prometheus、Grafana、Alertmanager 及现有业务流量。

---

## 2. Detection

按照项目既定排障顺序：

```text
get
→ describe
→ logs
→ events
```

执行 `kubectl describe pod` 后，在 Events 中找到 Primary Error：

```text
Error: container has runAsNonRoot and image has non-numeric user (nobody),
cannot verify user is non-root
```

此时 `kubectl logs` 无法获得应用日志，因为容器尚未真正启动。

---

## 3. Root Cause

正式 Deployment 启用了：

```yaml
securityContext:
  runAsNonRoot: true
```

而 mysqld_exporter 镜像声明的运行用户是：

```text
nobody
```

这是用户名，不是数字 UID。

Kubelet 在 `runAsNonRoot: true` 下需要确认容器不会以 UID 0 运行，但无法仅根据非数字用户名 `nobody` 完成验证，因此在容器启动前拒绝创建。

---

## 4. Evidence

此前成功运行的临时测试 Pod 使用同一个镜像。

执行：

```bash
kubectl exec \
  -n opslab \
  opslab-mysqld-exporter-test \
  -- id
```

得到：

```text
uid=65534(nobody) gid=65534(nobody) groups=65534(nobody)
```

因此确认该镜像内 `nobody` 的实际身份为：

```text
UID = 65534
GID = 65534
```

---

## 5. Minimal Fix

没有删除 `runAsNonRoot`，而是在原有 SecurityContext 中显式补充数字 UID/GID：

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

这是最小修复：解决 kubelet 的身份验证问题，同时不降低现有安全约束。

---

## 6. Recovery Verification

重新 apply 后：

```text
deployment "opslab-mysqld-exporter" successfully rolled out
```

正式 Pod：

```text
READY   STATUS    RESTARTS
1/1     Running   0
```

再次验证运行身份：

```text
uid=65534(nobody) gid=65534(nobody) groups=65534(nobody)
```

Exporter 日志正常：

```text
Scraper enabled scraper=global_status
Scraper enabled scraper=global_variables
Listening on address=[::]:9104
```

错误过滤结果：

```text
NO_MATCHED_ERRORS
```

---

## 7. Engineering Lesson

这次故障本身影响较低，但具有典型 Kubernetes 安全配置价值：

1. 镜像声明 `USER nobody`，不等于 kubelet 一定能够验证其为非 root。
2. `CreateContainerConfigError` 发生在容器进程启动前，优先查看 Events，而不是反复看应用日志。
3. 不应为了“让 Pod 跑起来”直接删除 `runAsNonRoot`。
4. 更合理的方法是先确认镜像真实 UID/GID，再通过 `runAsUser` / `runAsGroup` 显式声明。
5. 修复应遵循“保存现场 → 找 Primary Error → 最小修改 → 重新验收”。

---

## 8. Final Result

```text
Root Cause Identified: PASS
Minimal Fix:          PASS
Deployment Recovery:  PASS
Security Preserved:   PASS
```

> Incident 级别：低影响、但有较高 Kubernetes SecurityContext 学习价值，因此保留为精炼 Incident，不作为项目核心故障重点展开。
