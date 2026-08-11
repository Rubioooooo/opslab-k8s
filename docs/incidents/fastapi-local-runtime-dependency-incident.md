# FastAPI 本地运行时依赖缺失故障复盘

> 类型：Incident  
> 组件：FastAPI v0.3.0 本地验证环境  
> 建议仓库路径：`docs/incidents/fastapi-local-runtime-dependency-incident.md`

---

# 1. Incident 摘要

FastAPI v0.3.0 增加 Prometheus metrics 后，本地进行启动验证时出现：

```text
ModuleNotFoundError: No module named 'redis'
```

初看容易误判为：

```text
metrics.py 新代码导致启动失败
```

但真实 traceback 链路显示：

```text
app.main
→ app.cache
→ import redis.asyncio
→ ModuleNotFoundError
```

最终确认根因：

> **本地 `.venv` 没有同步当前 `requirements.lock.txt`，导致运行时依赖缺失。**

同步依赖后 Uvicorn 正常启动，`/healthz`、404 instrumentation 与 `/metrics` 均通过验证。

---

# 2. 发生背景

FastAPI v0.3.0 新增：

```text
prometheus-client==0.25.0
```

新增：

```text
applications/fastapi/app/metrics.py
```

并增加：

```text
/metrics
```

在正式镜像构建前进行了本地验证。

---

# 3. 现象

使用类似：

```bash
APP_VERSION=v0.3.0 python -m uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 18000
```

启动时出现：

```text
ModuleNotFoundError: No module named 'redis'
```

---

# 4. 为什么容易误判

因为故障发生在“新增 metrics”之后，时间上与新代码高度相关。

但：

```text
发生在修改之后
≠
修改本身一定是根因
```

需要看 traceback 的真实 import chain。

---

# 5. Primary Error

Traceback 指向：

```text
app.cache
→ import redis.asyncio
```

失败。

因此 Primary Error 是：

```text
Python runtime environment 缺少 redis package
```

而不是：

```text
Prometheus metrics middleware 语法错误
```

---

# 6. Root Cause

本地虚拟环境：

```text
.venv
```

没有同步仓库当前锁定依赖：

```text
requirements.lock.txt
```

因此代码仓库与本地 runtime environment 发生 drift。

---

# 7. 一个重要误区：compileall

此前：

```text
python -m compileall
```

能够通过。

但这只能证明：

```text
Python source 可以编译
```

不能证明：

```text
所有 runtime dependency 都存在
```

所以：

```text
compileall PASS
≠
runtime dependency PASS
```

---

# 8. 修复

同步：

```text
requirements.lock.txt
```

对应依赖到本地 `.venv`。

随后重新启动 Uvicorn。

没有修改 metrics 逻辑来“绕过”错误。

---

# 9. 恢复验证

实际验证：

```text
/healthz
→ HTTP 200
```

不存在路由：

```text
/not-found
→ HTTP 404
```

Prometheus Counter 出现：

```text
opslab_http_requests_total
```

Histogram 出现：

```text
opslab_http_request_duration_seconds_bucket
opslab_http_request_duration_seconds_count
opslab_http_request_duration_seconds_sum
```

因此 instrumentation 本身 PASS。

---

# 10. SRE / 工程价值

本次 Incident 说明测试需要分层：

```text
Static / Syntax Validation
↓
Import / Runtime Validation
↓
Functional Validation
↓
Integration Validation
```

单独完成其中一层，不能替代后续层次。

对于 Python 应用尤其应该区分：

```text
Source correctness
vs
Environment correctness
```

---

# 11. 后续预防

建议保持：

1. 依赖文件与虚拟环境同步；
2. 新增 dependency 后执行真实 runtime 启动；
3. 不把 `compileall` 当作完整启动验收；
4. CI 中可增加 dependency install + application import/startup test；
5. traceback 优先沿真实 import chain 定位。

---

# 12. Incident 结论

Root Cause：

```text
Local .venv dependency drift
```

不是：

```text
FastAPI Prometheus instrumentation bug
```

同步锁定依赖后恢复。

Incident：

```text
RESOLVED
```
