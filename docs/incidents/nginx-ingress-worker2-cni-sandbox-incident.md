# NGINX Ingress Pod 本地 CNI Sandbox 异常事故报告

## 1. 事故概要

在正式部署 NGINX Ingress Controller 5.5.4 时，DaemonSet 分别在 `k8s-worker1` 和 `k8s-worker2` 创建一个 Controller Pod。

其中：

* `k8s-worker1` 上的 Pod 正常就绪；
* `k8s-worker2` 上的 Pod 无法通过就绪探针；
* `192.168.8.11:80` 可以访问；
* `192.168.8.12:80` 无法建立连接。

最终仅删除 `k8s-worker2` 上的异常 Pod，DaemonSet 自动创建替代 Pod后，Controller 恢复为 `2/2 Ready`，两个 Worker 的 80 端口均可访问。

## 2. 环境信息

```text
Kubernetes：v1.36.3
containerd：2.2.1
Flannel：v0.28.8
NGINX Ingress Controller：5.5.4
NGINX Open Source：1.31.3
部署方式：DaemonSet
暴露方式：hostPort 80/443
异常节点：k8s-worker2
节点 PodCIDR：10.244.2.0/24
异常 Pod IP：10.244.2.4
替代 Pod IP：10.244.2.5
```

## 3. 故障现象

异常 Pod：

```text
nginx-ingress-7p98n
节点：k8s-worker2
Pod IP：10.244.2.4
```

就绪探针先后出现：

```text
connect: connection refused
HTTP probe failed with statuscode: 503
context deadline exceeded
no route to host
```

控制节点访问两个入口时：

```text
192.168.8.11:80：HTTP 404，正常连接到 NGINX
192.168.8.12:80：无法连接
```

启动初期出现短暂的 `connection refused` 或 `503` 可能属于初始化过程，但后续持续出现 `no route to host`，并且 Worker2 的 hostPort 无法访问，说明故障没有自动恢复。

## 4. 现场证据

在 `k8s-worker2` 上检查发现：

```text
cni0 DOWN
NO-CARRIER
10.244.2.0/24 dev cni0 ... linkdown
10.244.2.4 dev cni0 INCOMPLETE
bridge link 没有有效端口
```

Worker2 本机访问异常 Pod 的就绪端口：

```text
curl http://10.244.2.4:8081/nginx-ready
No route to host
```

与此同时，containerd 中的 Pod Sandbox 显示：

```text
STATE：Ready
```

这说明 Sandbox 对象虽然存在，但 Pod 的本地网络交付链路没有正常工作。

## 5. 原因判断

故障范围可以确定为：

> `k8s-worker2` 上异常 NGINX Ingress Pod 的本地 CNI 网桥、veth、网络命名空间或 Pod Sandbox 接入链路异常。

可以排除的整体性问题包括：

* NGINX Ingress Controller 镜像整体不可用；
* Controller 启动参数整体错误；
* RBAC 整体错误；
* DaemonSet 调度规则错误；
* 两个 Worker 的底层节点网络整体中断；
* Flannel DaemonSet 整体不可用；
* Controller 的 80/443 配置整体错误。

由于删除旧 Pod 后，其网络命名空间和 veth 接口随之销毁，无法确认具体是 CNI ADD、veth 创建、网桥接入还是后续接口状态维护中的哪一步失败。

因此不能把根因进一步写成百分之百确定的某个底层组件缺陷。

## 6. 修复措施

本次没有执行：

* `kubeadm reset`；
* 重装 Flannel；
* 重启 containerd；
* 重启 Worker 节点；
* 清空 iptables；
* 删除整个 DaemonSet；
* 重装 NGINX Ingress Controller。

只删除异常 Pod：

```bash
kubectl delete pod \
  -n nginx-ingress \
  nginx-ingress-7p98n
```

DaemonSet 自动在 `k8s-worker2` 创建新 Pod：

```text
nginx-ingress-x4smd
Pod IP：10.244.2.5
```

## 7. 修复结果

修复后：

```text
DaemonSet DESIRED：2
DaemonSet CURRENT：2
DaemonSet READY：2
DaemonSet AVAILABLE：2
```

两个 Pod 分别位于：

```text
nginx-ingress-lhlhz    k8s-worker1    10.244.1.5
nginx-ingress-x4smd    k8s-worker2    10.244.2.5
```

访问两个节点入口均返回：

```text
HTTP/1.1 404 Not Found
Server: nginx/1.31.3
```

当前尚未创建业务 Ingress，因此默认 404 是正常结果，证明：

* 两个节点的 hostPort 80 均已监听；
* 请求已经进入 NGINX；
* Controller 已正常工作；
* 只是没有匹配的业务路由。

## 8. 与历史故障的关系

该故障与此前发生在 `k8s-worker2` 上的跨节点 Nginx 测试 Pod 网络异常具有相似特征：

* 均发生在 `k8s-worker2`；
* 均表现为本地 `cni0` 或 Pod veth/Sandbox 链路异常；
* 均通过只重建异常 Pod 恢复；
* 均未发现节点底层网络和 Flannel VXLAN 整体故障。

目前只能确认这是第二次相似现象，尚不足以直接判定为持续性系统故障。

如果同一节点再次出现类似问题，应暂停继续删除 Pod，并重点保存和检查：

```text
kubelet 日志
containerd 日志
Flannel 日志
CNI 插件执行结果
Sandbox inspect 信息
网络命名空间
veth 对端关系
cni0 网桥端口
内核网络事件
```

## 9. 经验总结

### 9.1 Pod Sandbox 为 Ready 不等于 Pod 网络正常

containerd 中 Sandbox 对象存在并显示 Ready，仍可能出现：

* veth 未接入网桥；
* cni0 没有有效端口；
* 邻居解析失败；
* Pod IP 无法到达。

### 9.2 启动期探针失败和持续网络故障需要区分

Controller 启动初期短暂出现：

```text
connection refused
503
```

可能属于正常初始化。

但如果后续出现：

```text
no route to host
cni0 linkdown
neighbor INCOMPLETE
```

则应转向排查 Pod 本地 CNI 链路。

### 9.3 最小影响修复有效

在另一副本正常、故障集中于单个 Pod 时，只重建异常 Pod 可以恢复服务，同时避免对整个集群和网络插件造成额外影响。
