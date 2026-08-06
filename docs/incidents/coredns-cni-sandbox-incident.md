# CoreDNS 第二副本网络异常故障排查报告

## 一、故障概述

### 1. 故障名称

**Flannel 安装后 CoreDNS 第二副本因 Pod 网络沙箱异常无法就绪**

### 2. 故障环境

* Kubernetes：v1.36.3
* 容器运行时：containerd 2.2.1
* CNI：Flannel v0.28.8
* Pod 网段：`10.244.0.0/16`
* Service 网段：`10.96.0.0/12`
* 控制节点：`k8s-control-plane`
* 节点地址：`192.168.8.10`
* CoreDNS 镜像：`coredns:v1.14.2`

### 3. 故障影响

Flannel 安装后，控制节点已经从 `NotReady` 变为 `Ready`，但 CoreDNS Deployment 只有一个副本可用：

```text
coredns-69f5d4fb89-bzk8r   1/1   Running
coredns-69f5d4fb89-qn67r   0/1   Running
```

异常 Pod 多次被 kubelet 重启，导致：

```text
CoreDNS Deployment：1/2 Available
kubectl rollout status deployment/coredns：超时
```

由于另一个 CoreDNS 副本仍正常运行，本次故障没有导致集群 DNS 完全不可用，但失去了 DNS 服务的副本冗余能力。

---

## 二、现象说明

需要特别说明：异常 CoreDNS 容器并不是完全“没有启动”。

容器实际状态为：

```text
State: Running
Ready: False
Restart Count: 3
```

上一次容器退出状态为：

```text
Reason: Completed
Exit Code: 0
```

这说明 CoreDNS 进程能够启动，但由于其健康检查持续失败，kubelet主动终止并重新创建容器。因此，更准确的故障描述是：

> CoreDNS 第二副本已经启动，但 Pod 网络异常，导致其无法完成 Kubernetes API 同步，也无法通过存活和就绪探针，最终被 kubelet反复重启。

---

## 三、关键故障日志

### 1. CoreDNS 无法访问 Kubernetes API Service

异常 Pod 的日志持续出现：

```text
Failed to list *v1.Namespace:
Get "https://10.96.0.1:443/api/v1/namespaces":
dial tcp 10.96.0.1:443: connect: no route to host
```

类似错误还出现在：

```text
Service
EndpointSlice
Namespace
```

说明 CoreDNS 无法通过 Kubernetes Service IP：

```text
10.96.0.1:443
```

访问 kube-apiserver。

CoreDNS 依赖 Kubernetes API 获取 Service、Namespace 和 EndpointSlice 信息。无法访问 API 时，CoreDNS 的 `kubernetes` 插件无法完成数据同步，Readiness Probe 无法通过。

### 2. kubelet无法访问 Pod 健康检查端口

Pod Events 中出现：

```text
Readiness probe failed:
Get "http://10.244.0.3:8181/ready":
connect: no route to host
```

以及：

```text
Liveness probe failed:
Get "http://10.244.0.3:8080/health":
context deadline exceeded
```

最终 kubelet采取了重启操作：

```text
Container coredns failed liveness probe, will be restarted
```

这说明故障不仅是 CoreDNS 容器无法访问外部网络，宿主机上的 kubelet也无法正常访问该 Pod 的 IP。

---

## 四、排查思路

本次排查没有直接重装 Flannel、修改 CoreDNS 配置或清空 iptables，而是按照以下层次逐步缩小故障范围：

```text
控制平面状态
→ Flannel DaemonSet
→ CoreDNS 进程状态
→ Pod 健康检查
→ Pod 到 Kubernetes API 的连接
→ 宿主机到 Pod IP 的连接
→ cni0 和 veth 状态
→ Pod 出站 NAT 与外部 DNS
```

核心思路是：

> 先判断故障属于应用进程、Pod 网络、Service 网络还是外部网络，再采取最小影响的修复操作。

---

## 五、排查过程

### 1. 验证 Flannel 是否整体正常

首先确认 Flannel DaemonSet 已经成功运行：

```text
DESIRED：1
CURRENT：1
READY：1
AVAILABLE：1
```

Flannel Pod 状态为：

```text
1/1 Running
```

控制节点也已经变为：

```text
k8s-control-plane   Ready
```

这说明：

* Flannel 清单已经成功安装；
* 节点已经获得 Pod CIDR；
* kubelet已经识别 CNI；
* 不能简单判断为“Flannel 安装失败”。

### 2. 对比两个 CoreDNS 副本

正常 Pod：

```text
Pod IP：10.244.0.2
Ready：True
```

异常 Pod：

```text
Pod IP：10.244.0.3
Ready：False
```

因为两个 Pod：

* 使用相同镜像；
* 使用相同 ConfigMap；
* 属于同一个 Deployment；
* 运行在同一个节点；
* 只有其中一个异常；

所以可以基本排除：

* CoreDNS 镜像错误；
* CoreDNS Corefile 整体配置错误；
* ServiceAccount 或 RBAC 整体错误；
* 节点上的 Flannel 整体不可用。

故障范围因此缩小到：

> `10.244.0.3` 对应的单个 Pod 网络沙箱。

### 3. 检查宿主机邻居表

执行：

```bash
ip neigh show dev cni0
```

结果为：

```text
10.244.0.2 lladdr 7a:3d:78:86:10:bb REACHABLE
10.244.0.3 FAILED
```

正常 Pod 的邻居状态为：

```text
REACHABLE
```

异常 Pod 的邻居状态为：

```text
FAILED
```

这说明节点无法通过 `cni0` 找到 `10.244.0.3` 对应的二层网络接口。

### 4. 检查 cni0 下的 veth 接口

执行：

```bash
sudo bridge link show master cni0
```

当时只看到一个正常接入 `cni0` 的 veth：

```text
vethe9dc21bf ... master cni0 state forwarding
```

该 veth 对应正常运行的 `10.244.0.2` Pod。

异常 Pod `10.244.0.3` 没有对应的有效 veth 接口接入 `cni0`，或者其 veth 已经处于异常、丢失状态。

### 5. 对比两个 Pod 的 Readiness 地址

执行：

```bash
curl http://10.244.0.2:8181/ready
curl http://10.244.0.3:8181/ready
```

结果为：

```text
10.244.0.2：返回 OK
10.244.0.3：连接超时
```

至此可以确认：

* CoreDNS 正常副本的网络路径完整；
* 异常副本的 Pod IP 已经分配，但实际网络接口或网络沙箱没有正常工作；
* kubelet对 `10.244.0.3` 的探针失败并不是 CoreDNS 应用逻辑造成的，而是 Pod 网络不可达造成的。

---

## 六、根本原因

### 1. 直接原因

异常 CoreDNS Pod 的网络沙箱没有正确接入节点的 CNI 网桥。

具体表现为：

```text
Pod IP：10.244.0.3
邻居项：FAILED
对应 veth：缺失或不可用
宿主机无法访问 Pod 探针端口
Pod 无法访问 10.96.0.1:443
```

因此形成以下故障链：

```text
Pod veth/CNI 网络异常
        ↓
CoreDNS 无法访问 Kubernetes API
        ↓
kubernetes 插件无法同步资源
        ↓
Readiness Probe 失败
        ↓
kubelet也无法访问 Pod 的健康端口
        ↓
Liveness Probe 连续失败
        ↓
kubelet终止并重启 CoreDNS 容器
```

### 2. 根因判断

本次故障最符合以下情况：

> Flannel 刚安装、CoreDNS Pod 从 Pending 转为调度运行时，`10.244.0.3` 对应的 Pod Sandbox 或 veth 接口创建过程出现了一次性异常，导致 Pod 获得了 IP，但网络接口没有保持正常连接。

需要说明的是，本次没有在删除异常 Pod 前保存其完整网络命名空间和 containerd CNI 调用日志，因此不能进一步证明具体失败发生在：

* veth 创建；
* veth 加入 `cni0`；
* CNI ADD 返回后的接口保留；
* Pod Sandbox 生命周期；
* containerd 清理旧网络接口；

中的哪一个精确步骤。

因此，严谨的根因表述应为：

> 已通过网络证据确认故障位于单个 Pod 的 CNI 网络沙箱或 veth 链路，但由于异常 Pod 删除后原网络命名空间消失，无法进一步还原最底层的单一失败步骤。

---

## 七、解决方法

### 1. 删除异常 Pod

执行：

```bash
kubectl delete pod \
  -n kube-system \
  coredns-69f5d4fb89-qn67r
```

该 Pod 由 CoreDNS Deployment 和 ReplicaSet 管理，删除后控制器自动创建新的副本。

这种操作的特点是：

* 不修改 CoreDNS Deployment；
* 不修改 CoreDNS ConfigMap；
* 不重装 Flannel；
* 不清空 iptables；
* 不影响正常运行的另一个 CoreDNS 副本；
* 只重建异常 Pod 的 Sandbox、veth 和 CNI 网络。

### 2. 新 Pod 自动创建

新建 Pod 为：

```text
coredns-69f5d4fb89-4bdxj
```

分配的新 IP：

```text
10.244.0.4
```

新 Pod 很快达到：

```text
1/1 Running
RESTARTS=0
```

CoreDNS Deployment 恢复为：

```text
READY：2/2
AVAILABLE：2
```

### 3. 验证新 Pod 网络

邻居表显示：

```text
10.244.0.4 lladdr ea:0b:b6:be:46:9b REACHABLE
10.244.0.2 lladdr 7a:3d:78:86:10:bb REACHABLE
```

`cni0` 下出现新的 veth：

```text
veth1395fb9e ... master cni0 state forwarding
```

访问新 Pod 的 Readiness 地址：

```bash
curl http://10.244.0.4:8181/ready
```

返回：

```text
OK
```

说明新 Pod 的以下链路均已恢复：

```text
宿主机
→ cni0
→ veth
→ Pod 网络命名空间
→ CoreDNS 健康检查端口
```

---

## 八、外部 DNS 问题说明

在解决 Pod Sandbox 问题后，新 CoreDNS Pod 可以正常访问 Kubernetes API，两个副本均为 Ready，但日志中仍出现部分外部 DNS 超时：

```text
223.5.5.5:53: i/o timeout
192.168.8.2:53: i/o timeout
```

这属于另一个独立问题，不能与最初的 Pod 网络故障混为一谈。

进一步测试发现：

```text
192.168.8.2 UDP DNS：约 2.03 秒后成功
192.168.8.2 TCP DNS：约 0.06 秒成功
223.5.5.5 UDP/TCP：均超时
```

宿主机和 Pod 网络命名空间中的测试结果一致，说明：

* `223.5.5.5` 在当前网络环境中不可达；
* 这不是 Kubernetes 或 Flannel 的故障；
* VMware NAT 提供的 `192.168.8.2` 可以正常解析，但 UDP 响应较慢；
* 使用 TCP 查询更稳定。

抓包还确认 DNS 请求从 Pod 经 `veth → cni0 → ens33` 完成 MASQUERADE，`192.168.8.2` 的响应也能够经过反向 NAT 返回 Pod，证明 Flannel 转发和 NAT 链路正常。

最终将 CoreDNS 上游固定为：

```text
192.168.8.2
```

并启用：

```text
force_tcp
```

避免继续使用当前环境中不可达的 `223.5.5.5`，同时绕开 `192.168.8.2` UDP 查询延迟较高的问题。

该调整解决的是外部 DNS 稳定性问题，并不是修复 `10.244.0.3` Pod 网络沙箱的主要手段。

---

## 九、最终验收结果

故障处理完成后：

```text
k8s-control-plane：Ready
Flannel DaemonSet：1/1 Ready
CoreDNS Deployment：2/2 Available
两个 CoreDNS Pod：1/1 Running
新 CoreDNS Pod：RESTARTS=0
Kubernetes API：可访问
Pod 到 Service 网络：正常
Pod 到外部 TCP 网络：正常
Pod DNS：可用
```

新 CoreDNS Pod 日志中不再出现：

```text
10.96.0.1:443: no route to host
```

说明 CoreDNS 已经能够正常同步 Kubernetes Service、Namespace 和 EndpointSlice 信息。

---

## 十、排障经验总结

### 1. `Running` 不等于健康

异常 Pod 显示：

```text
STATUS=Running
```

但：

```text
READY=0/1
```

真正判断 Pod 是否可用，需要同时查看：

* Ready 状态；
* Restart Count；
* Readiness Probe；
* Liveness Probe；
* Events；
* 当前和上一次容器日志。

### 2. 同一 Deployment 中正常与异常副本的对比非常重要

两个 CoreDNS Pod 使用完全相同的配置，但一个正常、一个异常，这可以快速排除公共配置问题，将故障范围缩小到单个 Pod 实例。

### 3. `no route to host` 不一定是宿主机路由表错误

本次错误中的：

```text
dial tcp 10.96.0.1:443: no route to host
```

并不是默认路由或节点 IP 配置错误，而是 Pod 自身的 veth/CNI 网络链路异常。

必须结合：

```bash
ip neigh
bridge link
curl PodIP
```

判断故障是在三层路由、二层邻居还是 Pod 网络接口。

### 4. 不要看到 CNI 故障就直接重装 Flannel

Flannel DaemonSet、正常 CoreDNS Pod 和控制节点状态都表明 Flannel 整体正常。此时重装 Flannel 会扩大变更范围，还可能破坏已经正常的网络。

本次采用的最小修复是：

```text
只删除异常 Pod
→ 由 Deployment 自动重建
→ 重新创建网络沙箱和 veth
```

### 5. 应区分集群内部网络与外部 DNS 问题

本次同时存在两个现象：

```text
10.244.0.3 Pod 网络沙箱异常
223.5.5.5 外部 DNS 不可达
```

第一个问题导致 CoreDNS 无法就绪，是主要故障。

第二个问题只影响外部域名转发稳定性，是当前网络环境限制。

如果不分层排查，很容易错误地把外部 DNS 超时判断为 Flannel 故障。

---

## 十一、故障结论

本次 CoreDNS 第二副本异常不是镜像、CoreDNS 配置或 Flannel 整体部署失败造成的。

直接原因是：

> CoreDNS Pod `10.244.0.3` 的 CNI 网络沙箱或 veth 链路异常，导致宿主机无法访问该 Pod，Pod 也无法访问 Kubernetes API Service。健康检查连续失败后，kubelet反复重启容器。

最终通过删除异常 Pod，使 Deployment 重新创建 Pod Sandbox 和网络接口，新 Pod `10.244.0.4` 正常接入 `cni0`，CoreDNS Deployment 恢复为 `2/2 Available`。

随后针对当前网络环境中外部 DNS 不稳定的问题，将 CoreDNS 上游固定为可达的 `192.168.8.2`，并使用 TCP 转发，进一步提高了 DNS 服务稳定性。
`@4a>5`>5
