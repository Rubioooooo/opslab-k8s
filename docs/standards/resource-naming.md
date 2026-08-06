# Kubernetes 应用层资源与目录命名规范

## 1. 适用范围

本文档用于规范 `opslab-k8s` 项目后续应用层资源，包括：

* FastAPI 业务应用；
* Nginx Ingress Controller；
* MySQL；
* Redis；
* ConfigMap 和 Secret；
* 持久化存储；
* Prometheus、Grafana 和告警组件。

基础集群组件，例如 kubeadm、Flannel、CoreDNS，不纳入本规范的应用资源命名范围。

## 2. Namespace 规划

| Namespace       | 用途                                    |
| --------------- | ------------------------------------- |
| `opslab`        | FastAPI、MySQL、Redis，以及业务相关配置          |
| `nginx-ingress` | Nginx Ingress Controller              |
| `monitoring`    | Prometheus、Grafana、Alertmanager 等监控组件 |

当前业务环境为开发和课程设计环境，因此暂不拆分 `dev`、`test`、`staging` 和 `production` 等多个 Namespace。

如后续需要演示多环境部署，再增加：

```text
opslab-dev
opslab-test
opslab-prod
```

## 3. Kubernetes 资源命名规则

资源名称统一遵循以下规则：

* 全部使用小写字母；
* 单词之间使用连字符 `-`；
* 不使用下划线；
* 不使用中文；
* 不在名称中添加随机编号；
* 名称应能够直接反映组件用途；
* 同一应用的不同资源可以使用相同基础名称，由资源类型进行区分。

### 3.1 FastAPI

统一基础名称：

```text
opslab-api
```

对应资源：

| 资源类型                    | 资源名称                |
| ----------------------- | ------------------- |
| Deployment              | `opslab-api`        |
| Service                 | `opslab-api`        |
| Ingress                 | `opslab-api`        |
| ConfigMap               | `opslab-api-config` |
| Secret                  | `opslab-api-secret` |
| ServiceAccount          | `opslab-api`        |
| HorizontalPodAutoscaler | `opslab-api`        |
| PodDisruptionBudget     | `opslab-api`        |

### 3.2 MySQL

统一基础名称：

```text
opslab-mysql
```

对应资源：

| 资源类型                  | 资源名称                  |
| --------------------- | --------------------- |
| StatefulSet           | `opslab-mysql`        |
| Service               | `opslab-mysql`        |
| ConfigMap             | `opslab-mysql-config` |
| Secret                | `opslab-mysql-secret` |
| PersistentVolumeClaim | `opslab-mysql-data`   |

### 3.3 Redis

统一基础名称：

```text
opslab-redis
```

对应资源：

| 资源类型                     | 资源名称                  |
| ------------------------ | --------------------- |
| Deployment 或 StatefulSet | `opslab-redis`        |
| Service                  | `opslab-redis`        |
| ConfigMap                | `opslab-redis-config` |
| Secret                   | `opslab-redis-secret` |
| PersistentVolumeClaim    | `opslab-redis-data`   |

## 4. 标签规范

应用资源优先使用 Kubernetes 推荐的标准应用标签。

FastAPI 示例：

```yaml
labels:
  app.kubernetes.io/name: opslab-api
  app.kubernetes.io/instance: opslab
  app.kubernetes.io/component: backend
  app.kubernetes.io/part-of: opslab-k8s
```

MySQL 示例：

```yaml
labels:
  app.kubernetes.io/name: opslab-mysql
  app.kubernetes.io/instance: opslab
  app.kubernetes.io/component: database
  app.kubernetes.io/part-of: opslab-k8s
```

Redis 示例：

```yaml
labels:
  app.kubernetes.io/name: opslab-redis
  app.kubernetes.io/instance: opslab
  app.kubernetes.io/component: cache
  app.kubernetes.io/part-of: opslab-k8s
```

Deployment、StatefulSet 和 Service 的 selector 必须使用稳定标签。

不得将以下容易变化的标签作为唯一 selector：

* 镜像版本；
* Git Commit；
* 构建时间；
* Pod 模板版本。

## 5. 镜像命名规范

业务镜像使用阿里云个人镜像仓库。

镜像名称规划为：

```text
crpi-uxjhyltxd2kcr7f9.cn-hangzhou.personal.cr.aliyuncs.com/k8s-test-111/opslab-api:<version>
```

版本标签使用明确版本，例如：

```text
v0.1.0
v0.2.0
v1.0.0
```

正式 Kubernetes 清单中禁止使用：

```text
latest
```

使用固定版本能够保证：

* 部署结果可复现；
* 回滚时可以找到旧镜像；
* Git 记录与运行版本能够对应；
* 避免节点缓存导致镜像版本不一致。

## 6. 项目目录规划

后续逐步建立以下目录：

```text
opslab-k8s/
├── applications/
│   └── fastapi/
│       ├── app/
│       ├── tests/
│       ├── Dockerfile
│       ├── requirements.txt
│       └── README.md
├── kubernetes/
│   ├── namespaces/
│   │   └── opslab.yaml
│   ├── apps/
│   │   └── opslab-api/
│   ├── data/
│   │   ├── mysql/
│   │   └── redis/
│   ├── addons/
│   │   ├── coredns/
│   │   └── nginx-ingress/
│   └── observability/
├── docs/
│   ├── incidents/
│   ├── standards/
│   └── architecture/
└── ansible/
```

这些目录按项目进度逐步创建，不预先提交无内容的空目录。

## 7. 配置和敏感信息规则

可以提交到 Git 的内容包括：

* ConfigMap 清单；
* Secret 模板；
* 示例环境变量；
* 不包含真实密码的配置文件；
* `.env.example`。

禁止提交到 Git 的内容包括：

* MySQL 实际 root 密码；
* Redis 实际密码；
* 阿里云镜像仓库密码；
* Docker Registry 登录凭据；
* 私钥；
* Kubernetes ServiceAccount Token；
* 完整的生产 kubeconfig。

包含真实凭据的 Secret 文件必须在提交前检查。

后续可以采用以下方式之一管理 Secret：

* 本地创建 Secret，不提交生成后的明文文件；
* 提交只包含占位符的 Secret 模板；
* 使用 Sealed Secrets；
* 使用 External Secrets。

当前课程项目初期优先采用“Secret 模板加本地实际 Secret”的方式。

## 8. YAML 文件命名规则

清单文件统一使用小写字母和连字符，例如：

```text
namespace.yaml
deployment.yaml
service.yaml
configmap.yaml
secret.example.yaml
ingress.yaml
statefulset.yaml
persistent-volume-claim.yaml
horizontal-pod-autoscaler.yaml
```

同一目录内已经能够通过路径识别组件时，不必在每个文件名中重复完整应用名称。

例如：

```text
kubernetes/apps/opslab-api/deployment.yaml
kubernetes/apps/opslab-api/service.yaml
```

优于：

```text
kubernetes/apps/opslab-api/opslab-api-deployment.yaml
kubernetes/apps/opslab-api/opslab-api-service.yaml
```

## 9. Git 提交规范

应用层后续提交示例：

```text
feat(app): add minimal fastapi service
feat(container): add opslab api image
feat(kubernetes): deploy opslab api
feat(ingress): expose opslab api
feat(mysql): deploy persistent mysql
feat(redis): deploy redis cache
feat(observability): add application monitoring
fix(kubernetes): correct readiness probe
docs(project): add application deployment guide
```

每次提交只完成一个清晰阶段，避免在一个提交中混合大量不相关修改。
