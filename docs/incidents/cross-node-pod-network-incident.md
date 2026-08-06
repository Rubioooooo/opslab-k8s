# Flannel 跨节点 Pod 通信异常排查报告

## 一、故障概述

### 1. 故障名称

**Worker1 上的 Pod 无法访问 Worker2 上的 Pod**

### 2. 故障环境

* Kubernetes：v1.36.3
* 容器运行时：containerd 2.2.1
* CNI：Flannel v0.28.8
* Flannel 后端：VXLAN
* Kubernetes 节点：

  * `k8s-control-plane`：`192.168.8.10`
  * `k8s-worker1`：`192.168.8.11`
  * `k8s-worker2`：`192.168.8.12`
* Pod 网段：

  * 控制节点：`10.244.0.0/24`
  * Worker1：`10.244.1.0/24`
  * Worker2：`10.244.2.0/24`

### 3. 故障影响

三节点 Kubernetes 集群已成功建立，所有节点均处于 `Ready` 状态，Flannel、kube-proxy 和 CoreDNS 均正常运行。

在基础集群验收阶段，创建了两个 Nginx Pod，并通过调度约束使其分别运行在两个 Worker 节点：

```text
smoke-nginx-58789989bc-588hf   k8s-worker2   10.244.2.2
smoke-nginx-58789989bc-9qpvp   k8s-worker1   10.244.1.3
```

测试客户端 `smoke-client` 固定运行在 `k8s-worker1`。

从客户端访问两个 Nginx Pod 时：

```text
访问 10.244.1.3：成功
访问 10.244.2.2：No route to host
```

故障说明：

* Worker1 节点内部的 Pod 通信正常；
* Worker1 到 Worker2 的跨节点 Pod 通信异常；
* 集群尚不能通过完整的跨节点网络验收。

---

## 二、故障现象

执行跨节点 Pod IP 访问测试：

```bash
for POD_IP in $(
  kubectl get pods \
    -n cluster-validation \
    -l app=smoke-nginx \
    -o jsonpath='{range .items[*]}{.status.podIP}{"\n"}{end}'
); do
  echo "testing pod_ip=$POD_IP"

  kubectl exec \
    -n cluster-validation \
    smoke-client \
    -- sh -c \
    "wget -q -T 5 -O /dev/null http://${POD_IP} && echo PASS"
done
```

结果：

```text
testing pod_ip=10.244.2.2
wget: can't connect to remote host (10.244.2.2): No route to host

testing pod_ip=10.244.1.3
PASS
```

该结果具有较强的定位价值：

```text
同节点 Pod 通信正常
跨节点 Pod 通信失败
```

因此可以排除 Nginx 服务进程整体异常，也可以暂时排除客户端本身无法发起 HTTP 请求。

---

## 三、排查思路

本次排查采用从外到内、逐层缩小范围的方法：

```text
Pod 调度位置
→ 节点 PodCIDR
→ 节点底层网络
→ Flannel 节点注解
→ 远端 Pod 路由
→ VXLAN 邻居表
→ VXLAN FDB
→ 防火墙和转发策略
→ 目标节点本地 cni0 与 Pod veth
```

核心判断逻辑为：

> 如果节点底层网络、VXLAN 路由和映射都正常，但跨节点流量仍不能到达目标 Pod，应继续检查目标节点上从 `flannel.1` 到 `cni0`、veth 和 Pod 网络命名空间之间的本地交付路径。

---

## 四、排查过程

### 1. 确认 Pod 调度位置

检查 Nginx Pod：

```text
10.244.1.3 → k8s-worker1
10.244.2.2 → k8s-worker2
```

客户端 Pod 位于 `k8s-worker1`。

因此：

* 访问 `10.244.1.3` 属于同节点通信；
* 访问 `10.244.2.2` 必须经过 Flannel VXLAN 跨节点网络。

这证明测试设计有效，确实覆盖了跨节点通信路径。

### 2. 检查节点 IP 和 PodCIDR

检查结果：

```text
k8s-control-plane   192.168.8.10   10.244.0.0/24   Ready
k8s-worker1         192.168.8.11   10.244.1.0/24   Ready
k8s-worker2         192.168.8.12   10.244.2.0/24   Ready
```

节点 IP 和 PodCIDR 分配正确，没有出现：

* PodCIDR 重复；
* Worker PodCIDR 分配错误；
* Flannel 网段与 kubeadm 配置不一致；
* 节点状态异常。

### 3. 检查 Flannel 节点注解

Worker1：

```text
public-ip: 192.168.8.11
backend-type: vxlan
VtepMAC: de:63:13:48:64:88
```

Worker2：

```text
public-ip: 192.168.8.12
backend-type: vxlan
VtepMAC: 22:f1:54:63:65:99
```

说明 Flannel 正确识别了：

* 两个 Worker 的节点 IP；
* VXLAN 后端类型；
* 每个节点的 VTEP MAC 地址。

未发现 Flannel 误选网卡、使用旧 DHCP 地址或节点注解缺失的问题。

### 4. 检查节点底层网络

Worker1 到 Worker2：

```text
192.168.8.11 → 192.168.8.12
0% packet loss
```

Worker2 到 Worker1：

```text
192.168.8.12 → 192.168.8.11
0% packet loss
```

因此 VMware NAT 网络及两个节点之间的底层 IP 通信正常。

这一步排除了：

* Worker 节点彼此不可达；
* IP 地址或掩码错误；
* VMware 虚拟网络隔离；
* 基础网络路由异常。

### 5. 检查跨节点 Flannel 路由

Worker1 到 Worker2 Pod 网段：

```text
10.244.2.0/24 via 10.244.2.0 dev flannel.1 onlink
```

查询目标 Pod 路由：

```text
10.244.2.2 via 10.244.2.0 dev flannel.1
```

Worker2 到 Worker1 Pod 网段：

```text
10.244.1.0/24 via 10.244.1.0 dev flannel.1 onlink
```

查询回程路由：

```text
10.244.1.3 via 10.244.1.0 dev flannel.1
```

说明：

* Worker1 存在到 Worker2 Pod 网段的去程路由；
* Worker2 存在到 Worker1 Pod 网段的回程路由；
* Flannel 已为远端 PodCIDR 创建正确的路由。

因此故障不是远端 Pod 路由缺失造成的。

### 6. 检查 VXLAN 邻居表和 FDB

Worker1 上存在 Worker2 的 VXLAN 邻居：

```text
10.244.2.0 lladdr 22:f1:54:63:65:99 PERMANENT
```

Worker1 FDB 中存在：

```text
22:f1:54:63:65:99 dst 192.168.8.12 self permanent
```

Worker2 上存在 Worker1 的 VXLAN 邻居：

```text
10.244.1.0 lladdr de:63:13:48:64:88 PERMANENT
```

Worker2 FDB 中存在：

```text
de:63:13:48:64:88 dst 192.168.8.11 self permanent
```

这说明 Flannel 已建立：

```text
远端 PodCIDR
→ 远端 VTEP MAC
→ 远端节点 IP
```

之间的映射。

因此不能简单归因于 Flannel VXLAN 邻居或 FDB 缺失。

### 7. 检查防火墙与转发策略

两个 Worker 均显示：

```text
UFW: inactive
INPUT policy: ACCEPT
FORWARD policy: ACCEPT
```

并且存在 Flannel 和 Kubernetes 自动创建的转发规则。

因此未发现：

* UFW 拦截；
* FORWARD 默认 DROP；
* 节点防火墙阻断跨节点通信。

### 8. 检查 Flannel 日志

Flannel 日志显示 Worker 节点已经正常获得本节点 PodCIDR，并接收到其他节点的子网事件。

例如 Worker2 正确接收到：

```text
PublicIP: 192.168.8.10
PublicIP: 192.168.8.11
BackendType: vxlan
```

Worker1 也正确接收到 Worker2：

```text
PublicIP: 192.168.8.12
BackendType: vxlan
VtepMAC: 22:f1:54:63:65:99
```

日志中没有显示 Flannel 初始化失败、VXLAN 设备创建失败或路由安装失败。

### 9. 发现 Worker2 本地 cni0 异常

Worker2 的本地 Pod 网段路由显示：

```text
10.244.2.0/24 dev cni0 proto kernel scope link
src 10.244.2.1 linkdown
```

其中最关键的是：

```text
linkdown
```

这表示 Worker2 上的本地 CNI 网桥 `cni0` 在当时没有正常的活动链路。

跨节点数据包的正常路径应为：

```text
Worker1 上的 Pod
→ Worker1 cni0
→ Worker1 flannel.1
→ UDP 8472 VXLAN
→ Worker2 flannel.1
→ Worker2 cni0
→ 目标 Pod veth
→ 10.244.2.2
```

虽然前面的节点路由、VXLAN 邻居和 FDB 都正确，但 Worker2 本地 `cni0` 显示 `linkdown`，说明数据包在抵达 Worker2 后，无法正常交付给本地 Pod。

---

## 五、原因分析

### 1. 已证实的直接原因

本次故障的直接原因是：

> Worker2 上的本地 CNI 网桥或目标 Pod 对应的 veth 链路没有处于正常工作状态，导致跨节点 VXLAN 流量无法从 `flannel.1` 继续交付到 `10.244.2.2`。

关键证据：

```text
同节点 Pod 10.244.1.3 可访问
跨节点 Pod 10.244.2.2 不可访问
节点底层网络正常
Flannel 路由正常
VXLAN Neighbor 正常
VXLAN FDB 正常
防火墙策略正常
Worker2 本地 cni0 路由显示 linkdown
```

### 2. 推断根因

结合现象，最可能的根因是：

> Worker2 上的 Nginx Pod 在创建网络沙箱时，veth 接口与 `cni0` 网桥之间出现了一次性异常，导致 Pod 虽然获得了 `10.244.2.2` 地址并显示为 Running，但实际网络链路不完整。

该问题与之前 CoreDNS 单个 Pod 网络异常具有相似特征：

* Pod 获得了 IP；
* 容器进程可以启动；
* 但网络接口或 veth 链路异常；
* 删除并重新创建 Pod 后，CNI 会重新建立网络沙箱和 veth。

需要注意：

本次没有在删除原 Pod 前保存其完整网络命名空间、containerd Sandbox 信息和 CNI ADD/DEL 调用日志，因此不能百分之百确认错误发生在以下哪个精确步骤：

* veth Pair 创建；
* veth 接入 `cni0`；
* Pod 网络命名空间配置；
* containerd Sandbox 生命周期；
* CNI 插件返回后接口状态异常。

因此在正式报告中，严谨表述应为：

> 已确认故障位于 Worker2 本地 CNI 网桥或 Pod veth 交付链路；具体底层失败步骤因异常 Pod 被删除后相关网络命名空间消失，无法进一步还原。

---

## 六、解决方法

### 1. 采用最小范围修复

由于以下组件均正常：

* Flannel DaemonSet；
* 节点 PodCIDR；
* VXLAN 路由；
* 邻居表和 FDB；
* Worker 节点底层网络；
* iptables 转发策略；

因此没有采取以下高风险操作：

```text
没有重装 Flannel
没有重启整个集群
没有执行 kubeadm reset
没有清空 iptables
没有修改 PodCIDR
```

而是只删除 Worker2 上的异常 Nginx Pod，由 Deployment 自动重新创建：

```bash
kubectl delete pod \
  -n cluster-validation \
  <Worker2上的smoke-nginx Pod>
```

Deployment 随后重新创建副本，重新执行：

```text
创建 Pod Sandbox
→ 调用 CNI
→ 创建 veth Pair
→ 接入 cni0
→ 分配 Pod IP
→ 启动 Nginx
```

### 2. 重新执行跨节点通信验证

重新创建 Pod 后，再次从 Worker1 上的客户端分别访问两个 Nginx Pod。

验收目标：

```text
访问 Worker1 本地 Pod：PASS
访问 Worker2 远端 Pod：PASS
```

根据后续验收结果，第九步之后的 Service、DNS、自愈及资源清理步骤均已完成，说明跨节点 Pod 通信已经恢复。

---

## 七、后续综合验收

故障修复后，继续完成了以下基础集群验收：

1. 两个 Nginx 副本分别调度到两个 Worker；
2. 跨节点 Pod IP 通信正常；
3. ClusterIP Service 可访问；
4. Service EndpointSlice 包含两个后端 Pod；
5. Kubernetes Service DNS 解析正常；
6. 外部域名解析正常；
7. 删除测试 Pod 后 Deployment 能自动恢复副本；
8. 测试 Namespace 成功删除。

最终删除测试 Namespace：

```text
namespace "cluster-validation" deleted
```

---

## 八、最终集群状态

三个节点均为：

```text
Ready
```

具体状态：

```text
k8s-control-plane   Ready   192.168.8.10
k8s-worker1         Ready   192.168.8.11
k8s-worker2         Ready   192.168.8.12
```

Flannel：

```text
三个 kube-flannel Pod 全部 1/1 Running
```

kube-proxy：

```text
三个 kube-proxy Pod 全部 1/1 Running
```

CoreDNS：

```text
两个 CoreDNS Pod 全部 1/1 Running
RESTARTS=0
```

控制平面组件：

```text
etcd                       Running
kube-apiserver             Running
kube-controller-manager    Running
kube-scheduler             Running
```

说明三节点 Kubernetes 集群当前运行正常。

---

## 九、经验总结

### 1. Node Ready 不代表跨节点网络一定正常

本次三个节点均处于 `Ready`，Flannel Pod 也均为 `Running`，但跨节点 Pod 通信仍然失败。

因此集群验收不能只看：

```bash
kubectl get nodes
kubectl get pods -A
```

还必须验证：

* 同节点 Pod 通信；
* 跨节点 Pod 通信；
* ClusterIP Service；
* CoreDNS；
* EndpointSlice；
* Deployment 自愈。

### 2. 应先区分同节点与跨节点通信

测试结果：

```text
Worker1 → Worker1 Pod：成功
Worker1 → Worker2 Pod：失败
```

这一对比快速排除了：

* 客户端整体网络失败；
* Nginx 镜像整体错误；
* HTTP 请求命令错误；
* Worker1 本地 CNI 整体异常。

故障范围因此被迅速缩小到跨节点或 Worker2 本地交付路径。

### 3. 路由正常不代表最终链路一定正常

本次检查发现：

* 远端 Pod 路由存在；
* VXLAN Neighbor 存在；
* FDB 映射存在；

但 Worker2 本地 `cni0` 显示：

```text
linkdown
```

说明网络排查不能只停留在三层路由，还必须检查：

```text
flannel.1
cni0
veth
Pod 网络命名空间
```

### 4. 优先采用最小影响修复

当故障只影响单个 Pod 网络沙箱时，最合理的处理方式是：

```text
删除异常 Pod
→ 由 Deployment 自动重建
→ 重新创建 CNI 网络
```

而不是重装整个 CNI 或重置节点。

这种方式：

* 变更范围小；
* 风险低；
* 恢复速度快；
* 符合 Kubernetes 声明式控制器和自愈机制。

### 5. 对根因结论应保持证据边界

根据现有证据，可以确认故障位于：

```text
Worker2 本地 cni0 / veth / Pod Sandbox 链路
```

但不能在缺少 CNI 调用日志和旧网络命名空间证据的情况下，断言为某一个精确内核或插件缺陷。

工程报告中应区分：

```text
已确认的直接原因
最可能的底层根因
尚未获得的证据
```

---

## 十、最终结论

本次故障表现为：

> Worker1 上的客户端 Pod 可以访问同节点的 Nginx Pod，但无法访问 Worker2 上的 Nginx Pod，并返回 `No route to host`。

排查确认：

* 节点底层网络正常；
* 节点 IP 和 PodCIDR 正确；
* Flannel VXLAN 注解、路由、Neighbor 和 FDB 均正常；
* 防火墙和转发策略正常；
* Worker2 本地 `cni0` 路由出现 `linkdown`。

因此故障被定位为：

> Worker2 上目标 Pod 的本地 CNI 网桥或 veth 网络链路异常，导致跨节点 VXLAN 流量无法最终交付给 Pod。

通过删除异常 Pod，并由 Deployment 自动重新创建网络沙箱和 veth 接口，跨节点 Pod 通信恢复。随后 Service、DNS、自愈和资源清理等验收均顺利完成。

当前三节点 kubeadm Kubernetes 集群所有节点及核心系统组件均正常运行，基础集群网络验收通过。

