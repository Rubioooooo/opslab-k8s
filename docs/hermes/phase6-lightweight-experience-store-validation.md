# Hermes Phase 6 — Lightweight Experience Store 验证报告

## 1. 阶段结论

Phase 6 最终状态：**PASS**。

本阶段完成链路：

    Incident
    → Diagnosis
    → Safety Decision
    → Execution
    → Verification
    → Experience Record
    → Append-only JSONL Store

Experience Store 不承担诊断、授权、执行或恢复判定职责。

## 2. 设计边界

本阶段实现的是轻量、可追踪、可审计、可重放的经验记录。

明确未实现：

- RAG
- 向量数据库
- Kafka
- Elasticsearch
- 自动微调
- 模型在线学习
- 复杂多 Agent memory
- Experience 驱动的自动 Kubernetes 写操作

Experience 不能改变 Safety Gate 的授权权威。

## 3. ExperienceRecord Contract

Schema：`v1alpha1`

最终 outcome：

    VERIFIED
    NOT_RECOVERED
    INCONCLUSIVE
    NO_EXECUTION

其中：

- VERIFIED / NOT_RECOVERED / INCONCLUSIVE 继承 VerificationResult 的恢复事实。
- NO_EXECUTION 表示没有 ACCEPTED 的受控 Kubernetes mutation。
- ExecutionResult(ACCEPTED) 本身不等于恢复成功。
- ACCEPTED execution 缺少 VerificationResult 时必须 fail closed。

## 4. Phase 6B — Models

实现：

    aiops/src/opslab_aiops/experience/models.py

模型定向测试：**20 PASS**。

## 5. Phase 6C — JSONL Store

实现：

    aiops/src/opslab_aiops/experience/store.py

API：

    append
    load_all
    get
    find_by_incident_id

运行期存储：

    aiops/runtime/experiences.jsonl

该路径受 Git ignore 保护。

Store 定向测试：**23 PASS**。

## 6. Phase 6D — Adversarial Validation

Adversarial tests：**18 PASS**。

并发验证：

    12 independent processes
    → same JSONL store
    → all records preserved
    → valid final JSONL

## 7. Phase 6E — Real Attempt5 Replay

- experience_id: `exp-verify-f1d42eab-0818-4f28-8697-04dd3172754a`
- incident_id: `inc-obs-20260822T154701Z-38c35073-87f81972`
- diagnosis_action: `EVICT_POD`
- diagnosis_confidence: `MEDIUM`
- safety_outcome: `ALLOW`
- execution_status: `ACCEPTED`
- verification_outcome: `VERIFIED`
- final_outcome: `VERIFIED`
- target_uid: `87f81972-4ad6-4851-926c-310060df64c6`
- replacement_uid: `0840c6a9-c605-4ed9-8ccc-3190b2ceb271`

Runtime record count：**1**。

Runtime SHA256：`56475250feb8a4f35dafeaa6eda009a38b4d0da81a4309e2f7ea62e8a870a4b5`

Replay 过程中：

    Kubernetes access = false
    Hermes call = false
    Observer token required = false
    Executor token required = false

因此 Phase 6E 是 SEALED Phase5G Attempt5 的历史证据重放，
不是再次执行 remediation。

## 8. Phase 6F Attempt1 RCA

Attempt1 的 runtime integrity audit 出现 false mismatch。

根因：

- ExperienceRecord.to_dict() 保留 tuple。
- JSON array 读取后成为 list。
- Python 直接比较 dict 时 tuple != list。
- 因此产生审计脚本假失败。

最小修复：

- 先将 ExperienceRecord JSON 序列化再读取为标准 JSON 数据结构。
- 再与 evidence JSON 比较。
- 未修改任何 Phase 6 产品代码。

Attempt2 runtime integrity：**PASS**。

## 9. Evidence References

- `docs/hermes/evidence/phase5/phase5g/attempt5/02-incident-context.json`
- `docs/hermes/evidence/phase5/phase5g/attempt5/05-validated-diagnosis.json`
- `docs/hermes/evidence/phase5/phase5g/attempt5/06-require-human.json`
- `docs/hermes/evidence/phase5/phase5g/attempt5/08-allow-decision.json`
- `docs/hermes/evidence/phase5/phase5g/attempt5/09-execution-result.json`
- `docs/hermes/evidence/phase5/phase5g/attempt5/14-verification-result.json`
- `docs/hermes/evidence/phase5/phase5g/attempt5/15-phase5g-summary.json`

Phase 6 自身 evidence：

- `docs/hermes/evidence/phase6/phase6e/attempt1/01-attempt5-experience.json`
- `docs/hermes/evidence/phase6/phase6e/attempt1/02-replay-summary.json`
- `docs/hermes/evidence/phase6/phase6f/attempt1-rca.json`
- `docs/hermes/evidence/phase6/phase6f/final-summary.json`

## 10. 最终回归

最终 AIOps regression：**169 PASS**。

Phase 2～5 既有行为未发生回归。

## 11. 安全边界

Experience 产品模块没有：

- Kubernetes client
- kubectl
- shell/subprocess execution
- Hermes invocation
- HTTP mutation
- Observer token
- Executor token
- Secret/API key storage

Experience Store 不能调用 Controlled Executor，
也不能绕过 Safety Gate。

## 12. 最终能力状态

    Observable      PASS
    Diagnosable     PASS
    Explainable     PASS
    Safety-bounded  PASS
    Executable      PASS
    Verifiable      PASS
    Experience      PASS

完整链路：

    Kubernetes
    → Observation
    → Incident
    → Hermes Diagnosis
    → Safety Gate
    → Human Approval
    → Controlled Executor
    → Independent Verification
    → Experience Store

Phase 6 至此完成 Lightweight Experience Store MVP。

这代表项目已经具备“经验演进”的可信数据基础，
但**不等于已经实现模型在线学习或自动经验驱动执行**。
