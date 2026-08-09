# Metrics Server 因 kubelet Serving Certificate 缺少 IP SAN 导致指标采集失败事件报告

> 文档类型：Incident  
> 项目：基于 kubeadm 的 Kubernetes 云原生应用部署与 SRE 稳定性实践  
> Kubernetes：v1.36.3  
> Metrics Server：v0.9.0  
> 影响范围：Metrics API / `kubectl top` / 后续 HPA  
> 最终状态：已恢复，三节点指标采集正常  
> 严重级别：项目实验环境中的功能阻断，无业务数据损坏

---

## 1. 事件摘要

在 Kubernetes v1.36.3 三节点集群中部署 Metrics Server v0.9.0 后，所有 Kubernetes 对象均成功创建，但 `metrics-server` Deployment 在 120 秒内始终无法形成可用副本：

```text
Waiting for deployment "metrics-server" rollout to finish: 0 of 1 updated replicas are available...
error: timed out waiting for the condition
```

进一步检查发现：

- Metrics Server Pod 已成功调度；
- 容器镜像正常；
- Metrics Server 进程正常启动；
- 容器没有发生重启；
- Pod 为 `Running`，但 `READY=0/1`；
- readinessProbe 持续返回 HTTP 500；
- Metrics Server 无法从三台 kubelet 的 `10250/metrics/resource` 采集指标；
- 根本错误为 kubelet Serving Certificate 缺少对应 Node InternalIP 的 IP SAN。

典型错误：

```text
Failed to scrape node
Get "https://192.168.8.11:10250/metrics/resource":
tls: failed to verify certificate:
x509: cannot validate certificate for 192.168.8.11
because it doesn't contain any IP SANs
```

最终采用正式证书链修复：

```text
启用 serverTLSBootstrap
→ kubelet 创建 kubernetes.io/kubelet-serving CSR
→ 人工审核 CSR 身份、SAN、用途
→ 批准 CSR
→ Kubernetes CA 签发新的 kubelet serving certificate
→ kubelet 使用带 Node IP SAN 的正式证书
→ Metrics Server TLS 校验成功
→ Metrics API 恢复
```

整个过程中**没有使用 `--kubelet-insecure-tls` 绕过证书验证**。

---

## 2. 背景与部署目标

Metrics Server 是当前项目从“应用部署”进入“资源指标驱动自动伸缩”的基础设施组件。

这一阶段的目标不是单纯让 Metrics Server Pod Running，而是打通：

```text
kubelet
  ↓
Metrics Server
  ↓
metrics.k8s.io
  ↓
kubectl top
  ↓
HPA
```

最终验收要求：

```bash
kubectl top nodes
kubectl top pods -n opslab
```

必须能够返回三台 Node 以及 OpsLab 业务 Pod 的真实 CPU / Memory 指标。

---

## 3. 环境信息

### 3.1 Kubernetes 集群

```text
k8s-control-plane  192.168.8.10
k8s-worker1        192.168.8.11
k8s-worker2        192.168.8.12
```

```text
Kubernetes:  v1.36.3
containerd:  2.2.1
```

### 3.2 Metrics Server

```text
Metrics Server: v0.9.0
```

官方镜像：

```text
registry.k8s.io/metrics-server/metrics-server:v0.9.0
```

正式部署使用 ACR RepoDigest：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/metrics-server@sha256:55bf0a082bb585d74b5126af930179a70d084f62a8b363997f4c64ad7a997748
```

三节点均提前完成镜像预拉取。

---

## 4. 部署前校验

官方 `components.yaml` 完整性：

```text
SHA256:
1cec29a5267809306a2c6ec74a3e449abbb705b4a8beed0c8a1963910f72c79b

Lines: 202
Size:  4330 bytes
```

官方参数保持不变：

```text
--secure-port=10250
--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname
--kubelet-use-node-status-port
--metric-resolution=15s
```

没有预先加入：

```text
--kubelet-insecure-tls
```

本地清单相对 upstream 唯一修改：

```diff
- image: registry.k8s.io/metrics-server/metrics-server:v0.9.0
+ image: crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/metrics-server@sha256:55bf0a082bb585d74b5126af930179a70d084f62a8b363997f4c64ad7a997748
```

`kubectl apply --dry-run=server` 全部通过。

---

## 5. 初始故障现象

正式部署后所有资源成功创建，但：

```text
Waiting for deployment "metrics-server" rollout to finish: 0 of 1 updated replicas are available...
error: timed out waiting for the condition
```

第一轮状态：

```text
READY   STATUS    RESTARTS
0/1     Running   0
```

由此可以立即得出：

```text
STATUS=Running
RESTARTS=0
READY=0/1
```

先排除：

- FailedScheduling；
- Pending；
- ErrImagePull / ImagePullBackOff；
- CrashLoopBackOff；
- 主进程启动即退出。

排障方向转向：

```text
容器已经运行
→ 为什么 Readiness 仍然失败？
```

---

## 6. Deployment / ReplicaSet 判断

Deployment：

```text
READY       0/1
UP-TO-DATE  1
AVAILABLE   0
```

ReplicaSet：

```text
DESIRED   1
CURRENT   1
READY     0
```

说明：

```text
Deployment Controller 正常
→ ReplicaSet Controller 正常
→ Pod 已经创建
→ 故障在 Pod readiness 之后
```

---

## 7. describe：Readiness Probe 失败

Pod Event：

```text
Warning  Unhealthy
Readiness probe failed:
HTTP probe failed with statuscode: 500
```

形成第一段因果链：

```text
rollout timeout
→ Deployment Available=0
→ Pod Ready=False
→ readinessProbe HTTP 500
```

但 HTTP 500 仍然只是症状，需要继续看日志。

---

## 8. Metrics Server 日志定位根因

Metrics Server 自身启动正常：

```text
Generated self-signed cert
Adding GroupVersion metrics.k8s.io v1beta1
Serving securely on [::]:10250
Caches are synced
```

说明不是 Metrics Server 主进程启动失败。

真正的错误：

```text
Failed to scrape node
Get "https://192.168.8.12:10250/metrics/resource":
tls: failed to verify certificate:
x509: cannot validate certificate for 192.168.8.12
because it doesn't contain any IP SANs
```

三台节点均出现相同类型错误：

```text
192.168.8.10
192.168.8.11
192.168.8.12
```

同时出现：

```text
Failed probe
probe="metric-storage-ready"
err="no metrics to serve"
```

### Primary Error

```text
kubelet serving certificate 缺少 IP SAN
→ TLS verification failed
```

### Cascading Error

```text
所有 Node scrape 失败
→ 没有 metrics 数据
→ no metrics to serve
→ /readyz HTTP 500
→ Pod NotReady
→ Service 无 Ready Endpoint
→ APIService MissingEndpoints
→ Deployment rollout timeout
```

这里最重要的排障经验是：

> `no metrics to serve` 是连锁症状，不是最初根因。

---

## 9. 为什么 Metrics Server 使用 IP 访问 kubelet

参数：

```text
--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname
```

因此优先级为：

```text
InternalIP
→ ExternalIP
→ Hostname
```

三个 Node 都存在 InternalIP，所以 Metrics Server 实际访问：

```text
https://192.168.8.x:10250/metrics/resource
```

这要求 kubelet HTTPS serving certificate 的 SAN 中包含对应：

```text
IP Address:192.168.8.x
```

否则 TLS 客户端无法验证“这张证书是否真的属于这个 IP”。

---

## 10. 直接检查真实 kubelet Serving Certificate

通过 OpenSSL 直接连接三台 kubelet 10250。

### control-plane

```text
subject=CN = k8s-control-plane@...
issuer=CN = k8s-control-plane-ca@...

Subject Alternative Name:
DNS:k8s-control-plane
```

### worker1

```text
subject=CN = k8s-worker1@...
issuer=CN = k8s-worker1-ca@...

Subject Alternative Name:
DNS:k8s-worker1
```

### worker2

```text
subject=CN = k8s-worker2@...
issuer=CN = k8s-worker2-ca@...

Subject Alternative Name:
DNS:k8s-worker2
```

三台证书共同特征：

```text
有 DNS SAN
没有 IP SAN
```

这与 Metrics Server 日志完全吻合。

---

## 11. 检查 kubelet TLS 配置

三节点 `/var/lib/kubelet/config.yaml` 都只有：

```text
rotateCertificates: true
```

没有：

```text
serverTLSBootstrap: true
```

集群级 `kube-system/kubelet-config` 同样如此。

同时：

```bash
kubectl get csr
```

初始返回：

```text
No resources found
```

形成完整证据链：

```text
serverTLSBootstrap 未启用
→ kubelet 没有申请正式 serving certificate
→ 使用原有 serving certificate
→ 只有 hostname SAN
→ Metrics Server 使用 InternalIP
→ IP 身份验证失败
```

---

## 12. 为什么没有使用 --kubelet-insecure-tls

一种快速做法是：

```text
--kubelet-insecure-tls
```

这样可以绕过 kubelet serving certificate 验证。

本项目没有采用它，原因：

1. 本质是关闭身份校验，而不是修复证书问题；
2. 无法解决 kubelet serving certificate 本身不规范的问题；
3. 项目目标是运维/SRE 实践，而不是只追求 Pod Running；
4. 当前集群可以通过标准 Serving TLS Bootstrap 正式修复；
5. 正式证书链的工程价值明显更高。

因此选择：

```text
修复证书体系
而不是
绕过证书验证
```

---

## 13. 修复策略与风险控制

修复顺序：

```text
集群级 kubelet-config
serverTLSBootstrap=true
        ↓
worker1 单节点实验
        ↓
worker2 复现
        ↓
control-plane 最后处理
```

没有三台一起修改、一起重启 kubelet。

原因：

- 先限制影响面；
- 用 worker1 建立成功模板；
- worker2 验证可重复性；
- control-plane 承载控制面 Static Pod，最后处理并增加健康检查。

---

## 14. 集群级 kubelet-config 修改

先备份：

```text
/tmp/kubelet-config-before-server-tls-bootstrap.yaml
```

候选配置相对原配置只有：

```diff
 rotateCertificates: true
+serverTLSBootstrap: true
```

经过 server-side dry-run 后正式 patch。

最终：

```text
rotateCertificates: true
serverTLSBootstrap: true
```

三节点仍保持 `Ready`。

---

## 15. worker1 单节点实验

worker1 本地先备份：

```text
/var/lib/kubelet/config.yaml.before-server-tls-bootstrap
```

修改：

```text
rotateCertificates: true
serverTLSBootstrap: true
```

重启 kubelet 后：

```text
systemctl is-active kubelet
→ active
```

worker1 上原业务 Pod 均继续 Running，包括：

- kube-flannel；
- kube-proxy；
- nginx-ingress；
- opslab-api；
- opslab-mysql。

说明 kubelet 重启并没有造成业务 Pod 重建或数据影响。

---

## 16. kubelet-serving CSR 审核

worker1 出现：

```text
Signer:
kubernetes.io/kubelet-serving

Requesting User:
system:node:k8s-worker1

Status:
Pending
```

Subject：

```text
O  = system:nodes
CN = system:node:k8s-worker1
```

SAN：

```text
DNS:k8s-worker1
IP Address:192.168.8.11
```

Usages：

```text
digital signature
server auth
```

Groups：

```text
system:nodes
system:authenticated
```

没有：

- 其他 Node DNS；
- 其他 Node IP；
- client auth；
- 异常 URI / Email SAN。

人工审核后批准，最终：

```text
Approved,Issued
```

---

## 17. worker1 新证书验证

再次直接读取 10250 serving certificate：

```text
subject=O = system:nodes, CN = system:node:k8s-worker1
issuer=CN = kubernetes
```

SAN：

```text
DNS:k8s-worker1
IP Address:192.168.8.11
```

前后对比：

```text
旧证书：
issuer = k8s-worker1-ca
SAN    = DNS:k8s-worker1

新证书：
issuer = kubernetes
SAN    = DNS:k8s-worker1
         IP:192.168.8.11
```

证明 kubelet 已真正切换到 Kubernetes 签发的 serving certificate。

---

## 18. A/B 对照实验

worker1 修复后，另外两台保持原状态：

```text
worker1:
serverTLSBootstrap=true
CSR=Approved,Issued
新证书带 IP SAN

worker2/control-plane:
仍是旧证书
无 IP SAN
```

Metrics Server 日志从“三台都失败”变成：

```text
worker1       → 错误消失
worker2       → 仍 missing IP SAN
control-plane → 仍 missing IP SAN
```

同时：

```bash
kubectl top node k8s-worker1
```

已经返回真实 CPU / Memory。

Raw Metrics API 中也出现 `k8s-worker1`。

这个 A/B 对照实验直接证明：

```text
kubelet serving certificate 的 IP SAN
```

就是本次 Metrics Server scrape 故障的决定因素。

---

## 19. worker2 修复

worker2 按已经验证成功的流程：

```text
备份 config.yaml
→ serverTLSBootstrap=true
→ restart kubelet
→ kubelet-serving CSR
→ 审核 Requestor / Signer / Subject / SAN / Usages
→ approve
→ Approved,Issued
→ openssl 验证新证书
→ kubectl top node k8s-worker2
```

CSR 核心身份：

```text
O  = system:nodes
CN = system:node:k8s-worker2

SAN:
DNS:k8s-worker2
IP Address:192.168.8.12
```

修复后：

```text
worker1 ✓
worker2 ✓
control-plane ✗
```

日志只剩 `192.168.8.10` 的 IP SAN 错误。

---

## 20. control-plane 修复

control-plane 最后处理，因为 kubelet 同时监督控制面 Static Pod。

操作前额外确认：

```bash
kubectl get nodes -o wide
kubectl get pods -n kube-system -o wide
kubectl get --raw='/readyz?verbose'
```

确认：

```text
control-plane Ready
API Server Ready
控制面组件 Running
```

然后：

```text
备份 /var/lib/kubelet/config.yaml
→ serverTLSBootstrap=true
→ restart kubelet
→ 控制面健康检查
→ 审核 kubelet-serving CSR
→ approve
→ Approved,Issued
→ openssl 验证新证书
```

目标证书：

```text
CN = system:node:k8s-control-plane
O  = system:nodes

SAN:
DNS:k8s-control-plane
IP Address:192.168.8.10
```

最终 control-plane scrape 错误消失。

---

## 21. 最终恢复验收

### 21.1 Metrics Server

```text
NAME             READY   UP-TO-DATE   AVAILABLE
metrics-server   1/1     1            1
```

Pod：

```text
metrics-server-6f99857f8c-tblkp
1/1 Running
RESTARTS=0
```

### 21.2 APIService

```text
v1beta1.metrics.k8s.io
Available=True
```

### 21.3 Node Metrics

```text
NAME                CPU(cores)   CPU(%)   MEMORY(bytes)   MEMORY(%)
k8s-control-plane   86m          4%       1340Mi          35%
k8s-worker1         114m         2%       1331Mi          17%
k8s-worker2         112m         2%       931Mi           11%
```

三节点全部成功采集。

### 21.4 OpsLab Pod Metrics

```text
NAME                         CPU(cores)   MEMORY(bytes)
opslab-api-d86cc8bb5-cch5r   6m           42Mi
opslab-api-d86cc8bb5-hmlpx   9m           42Mi
opslab-mysql-0               26m          434Mi
opslab-redis-0               21m          4Mi
```

### 21.5 Metrics Server 日志

最近日志无新的 scrape error。

已经不存在：

```text
tls: failed to verify certificate
x509: cannot validate certificate
doesn't contain any IP SANs
```

---

## 22. 最终根因

### Direct Cause

Metrics Server 优先使用 Node `InternalIP` 访问 kubelet：

```text
https://<Node InternalIP>:10250/metrics/resource
```

但三台 kubelet 原 serving certificate：

```text
只有 DNS SAN
没有 IP SAN
```

因此 TLS 无法验证 Node IP 身份。

### Root Cause

三节点 kubelet 未启用：

```text
serverTLSBootstrap: true
```

导致 kubelet 没有通过 Kubernetes CSR API 获取由集群 CA 签发、包含 Node IP SAN 的正式 serving certificate。

---

## 23. 完整故障因果链

```text
serverTLSBootstrap 未启用
        ↓
kubelet 使用原 serving certificate
        ↓
证书只有 DNS SAN
        ↓
没有 Node InternalIP SAN
        ↓
Metrics Server 优先通过 InternalIP
连接 kubelet:10250
        ↓
TLS identity verification failed
        ↓
三节点 /metrics/resource scrape 失败
        ↓
metric storage 无数据
        ↓
no metrics to serve
        ↓
readinessProbe HTTP 500
        ↓
Metrics Server Pod Running / NotReady
        ↓
Service 无 Ready Endpoint
        ↓
APIService MissingEndpoints
        ↓
Deployment rollout timeout
```

修复链：

```text
serverTLSBootstrap=true
        ↓
kubelet-serving CSR
        ↓
人工安全审核
        ↓
Approved,Issued
        ↓
Kubernetes CA 签发
        ↓
证书包含 DNS + Node IP SAN
        ↓
Metrics Server TLS 验证成功
        ↓
scrape 成功
        ↓
Metrics API 可用
        ↓
kubectl top nodes PASS
        ↓
kubectl top pods PASS
```

---

## 24. 本次排除的错误方向

### 不是调度问题

Pod 已被成功调度。

### 不是镜像问题

镜像已存在，Container 正常 Created / Started。

### 不是 CrashLoop

```text
STATUS=Running
RESTARTS=0
```

### 不是 Metrics Server 进程启动失败

```text
Serving securely on [::]:10250
```

### 不是单个 Node 局部问题

三台节点初始均出现同类型 TLS 错误。

### 不是 CNI 故障

故障已经进入 x509 证书校验阶段，说明 Metrics Server 至少已经连接到 kubelet TLS 服务并获得证书。

如果基础 TCP 网络不可达，更常见的应该是：

```text
timeout
connection refused
no route to host
```

而不是：

```text
x509 certificate verification
```

---

## 25. 排障方法总结

本次 Incident 形成了一套可复用的 Kubernetes 排障阅读顺序：

```text
kubectl get
    ↓
判断生命周期阶段
    ↓
Pending / Running / CrashLoop / 0/1
    ↓
kubectl describe
    ↓
State
Ready
Restart Count
Probe
Events
    ↓
kubectl logs
    ↓
找到最靠前、有因果意义的错误
    ↓
把错误拆成：
谁
→ 对谁
→ 做什么
→ 在哪里失败
→ 为什么失败
    ↓
区分：
Primary Error
vs
Cascading Error
    ↓
用独立手段验证根因
    ↓
设计最小修改
    ↓
逐步恢复
    ↓
功能验收
```

本次最关键的思维不是：

```text
看到 x509
→ 搜错误
→ 加 --kubelet-insecure-tls
```

而是：

```text
日志提示 IP SAN
→ openssl 看真实证书
→ kubelet config 验证 TLS Bootstrap
→ CSR 状态验证
→ 单节点实验
→ A/B 对照
→ 根因确认
→ 正式修复
```

---

## 26. CSR 审核方法总结

kubelet-serving CSR 不能只看 `Pending` 就批准。

应该审核五层：

### 1. 谁提交？

```text
spec.username
spec.groups
```

预期：

```text
system:node:<node-name>
system:nodes
```

### 2. 谁签发？

```text
spec.signerName
```

预期：

```text
kubernetes.io/kubelet-serving
```

### 3. 证书主体是谁？

```text
CN=system:node:<node-name>
O=system:nodes
```

### 4. 它还能证明哪些身份？

检查：

```text
DNS SAN
IP SAN
URI
Email
```

SAN 必须只属于本节点。

### 5. 用途是什么？

预期：

```text
digital signature
server auth
```

---

## 27. SRE / 运维项目价值

本次事件真正完成了：

1. 使用不可变镜像 Digest 部署集群组件；
2. 对 upstream YAML 进行最小修改；
3. 使用 server-side dry-run；
4. 根据 rollout timeout 保存现场；
5. 使用 `get / describe / logs / events` 分层定位；
6. 区分根因和连锁症状；
7. 理解 Metrics Server → kubelet 指标采集链路；
8. 理解 TLS SAN 与服务身份验证；
9. 使用 OpenSSL 检查真实服务端证书；
10. 区分 kubelet client certificate 与 serving certificate；
11. 实际启用 `serverTLSBootstrap`；
12. 实际处理 Kubernetes CSR；
13. 人工审核 CSR 身份、SAN 和 Usages；
14. 通过 Kubernetes CA 正式签发 kubelet serving certificate；
15. 使用单节点实验控制影响面；
16. 使用 worker1 / worker2 / control-plane 渐进式恢复；
17. 通过 A/B 对照证明根因；
18. 最终通过 Metrics API 和 `kubectl top` 完成真实功能验收。

这已经不是“安装 Metrics Server”，而是一段完整的 SRE 故障处置案例。

---

## 28. 面试表达参考

> 在 kubeadm 三节点 Kubernetes 集群部署 Metrics Server 时，我遇到 Deployment 一直无法 Ready。通过 Pod 状态、describe、events 和 Metrics Server 日志逐层定位，发现 Metrics Server 使用 Node InternalIP 访问 kubelet 10250 时，因 kubelet serving certificate 不包含 IP SAN 导致 TLS 校验失败。
>
> 我没有直接使用 `--kubelet-insecure-tls` 绕过验证，而是通过 OpenSSL 检查三台 kubelet 实际 serving certificate，并检查 KubeletConfiguration，确认集群未启用 `serverTLSBootstrap`。
>
> 随后我先在 worker1 单节点启用 Serving TLS Bootstrap，审核 `kubernetes.io/kubelet-serving` CSR 中的 Requestor、Signer、CN、Organization、DNS/IP SAN 和 Usages，再人工批准证书。新证书由 Kubernetes CA 签发，并包含对应 Node IP SAN。
>
> worker1 的 Metrics scrape 随即恢复，而另外两台未修改节点继续报相同 x509 错误，形成明确 A/B 对照，进一步证明根因判断正确。
>
> 最后按照 worker1 → worker2 → control-plane 的顺序逐节点完成修复，最终 Metrics API、`kubectl top nodes` 和业务 Pod Metrics 全部恢复，同时没有关闭 kubelet TLS 证书验证。

---

## 29. 文档归类与后续文档建议

本文件建议保存为：

```text
docs/incidents/metrics-server-kubelet-serving-certificate-incident.md
```

理由：

```text
Incident
→ 为什么失败
→ 如何定位
→ 根因是什么
→ 为什么选择这个修复方案
→ 如何控制风险
→ 如何恢复
→ 如何验证恢复
```

未来 HPA 阶段完成后，再单独创建：

```text
docs/validation/metrics-server-and-hpa-validation.md
```

Validation 重点应是：

```text
Metrics API 是否正常
→ kubectl top 是否完整
→ HPA 是否获取 CPU 指标
→ 压测后是否扩容
→ 停止压力后是否缩容
```

不要把这次 Incident 与未来 HPA Validation 机械合并。

---

## 30. 最终结论

本次 Metrics Server 首次部署失败并不是镜像、Deployment、CNI 或主进程启动故障。

根因是：

```text
kubelet serving certificate 缺少 Node InternalIP SAN
```

根本配置原因：

```text
serverTLSBootstrap 未启用
```

通过：

```text
Serving TLS Bootstrap
+
kubelet-serving CSR
+
人工身份审核
+
Kubernetes CA 签发
+
逐节点恢复
```

最终三台 kubelet 均建立了可被 Metrics Server 正常验证的 TLS 服务身份。

最终状态：

```text
Metrics Server      1/1 Ready
APIService          Available=True

k8s-control-plane   Metrics ✓
k8s-worker1         Metrics ✓
k8s-worker2         Metrics ✓

FastAPI             Metrics ✓
MySQL               Metrics ✓
Redis               Metrics ✓

Metrics Server
TLS scrape errors   0
```

**Incident CLOSED。**
