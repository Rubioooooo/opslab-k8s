# Hermes Phase 2 DiagnosisResult Contract

## 1. Purpose

DiagnosisResult v1alpha1 is the structured output contract produced by the
Hermes diagnosis layer.

The Phase 2 diagnostic flow is:

IncidentContext v1alpha1
→ Hermes Adapter
→ Hermes
→ DiagnosisResult v1alpha1

DiagnosisResult represents diagnosis and recommendation only.

It does not authorize or execute remediation.

---

## 2. Architectural Boundary

The following concerns remain separate:

Detection
→ Diagnosis
→ Authorization
→ Human Approval
→ Execution
→ Verification

Hermes participates only in diagnosis.

Hermes MUST NOT become:

- Kubernetes Executor
- kubectl shell
- SSH executor
- cluster-admin
- arbitrary command runner

---

## 3. DiagnosisResult v1alpha1

DiagnosisResult contains:

- schema_version
- diagnosis_id
- incident_id
- summary
- root_cause
- confidence
- evidence_refs
- affected_resource
- recommended_action
- rationale
- risk_notes
- requires_human_approval

---

## 4. Incident Binding

DiagnosisResult.incident_id MUST exactly match:

IncidentContext.incident_id

A diagnosis MUST NOT be accepted if it refers to another or unknown incident.

---

## 5. Evidence Grounding

DiagnosisResult.evidence_refs MUST contain only Evidence IDs present in the
supplied IncidentContext.

Examples of valid IDs:

EV-0001
EV-0002

Hermes MUST NOT invent Evidence IDs.

A diagnosis with unknown Evidence IDs MUST be rejected by the Adapter.

Diagnosis claims must be grounded in supplied Evidence rather than unrestricted
model assumptions.

---

## 6. Confidence

confidence uses a bounded enum:

- LOW
- MEDIUM
- HIGH

Free-form confidence values are not accepted.

Confidence does not override evidence requirements.

---

## 7. Affected Resource

affected_resource contains:

- kind
- name
- uid

The affected resource SHOULD normally correspond to the incident target.

For FASTAPI_UNHEALTHY_INSTANCE v1, the expected affected resource is the
candidate Pod.

---

## 8. Recommended Action

recommended_action is a structured enum.

Phase 2 v1 allows only:

- NO_ACTION
- EVICT_POD
- ESCALATE

### NO_ACTION

No remediation action is recommended from the available evidence.

### EVICT_POD

Hermes recommends that a later Safety Gate evaluate whether the affected Pod
may be evicted.

EVICT_POD is not authorization.

EVICT_POD is not execution.

### ESCALATE

The available evidence is insufficient, ambiguous, or too risky for a bounded
remediation recommendation.

Human investigation is required.

---

## 9. Prohibited Output

DiagnosisResult MUST NOT contain executable interfaces such as:

- shell commands
- kubectl commands
- SSH commands
- arbitrary URLs used as execution endpoints
- arbitrary Kubernetes API requests
- scripts
- command pipelines

The Adapter must reject malformed or unsafe structured output.

---

## 10. Human Approval

requires_human_approval is a boolean.

For Phase 2:

- EVICT_POD MUST require human approval.
- ESCALATE inherently requires human handling.
- NO_ACTION does not authorize any operation.

Phase 2 itself performs no write action regardless of this field.

---

## 11. Root Cause Semantics

root_cause is a diagnosis or bounded hypothesis derived from supplied evidence.

Hermes MUST distinguish:

current state

from

historical evidence

Historical Warning Events may support a root cause hypothesis, but must not be
represented as current state unless current Evidence also supports the claim.

---

## 12. FASTAPI_UNHEALTHY_INSTANCE Expectations

For FASTAPI_UNHEALTHY_INSTANCE, DiagnosisResult should explain:

- which Pod is affected
- what current Evidence proves the instance is unhealthy
- what historical Evidence supports the root cause hypothesis
- whether sibling replicas remain healthy
- whether the diagnosis is sufficiently grounded for a bounded recommendation

Hermes must not infer an incident solely from historical Warning Events because
candidate detection has already occurred before diagnosis.

---

## 13. Adapter Validation

The Hermes Adapter must validate at least:

1. valid JSON / structured response
2. schema_version
3. incident_id binding
4. confidence enum
5. evidence_refs existence
6. affected resource identity
7. recommended_action enum
8. EVICT_POD requires human approval
9. prohibited executable content is absent

Invalid results must be rejected rather than silently repaired into an accepted
diagnosis.

---

## 14. Phase 2 Non-Goals

DiagnosisResult does not implement:

- Safety Gate authorization
- Eviction API execution
- Pod deletion
- Deployment patching
- automatic remediation
- Verification Engine
- Experience Store

These belong to later phases.

---

## 15. PDB and Eviction Eligibility Boundary

PodDisruptionBudget information in Phase 2 is diagnostic context only.

DiagnosisResult MUST NOT determine that eviction of an unhealthy Pod is
allowed or denied solely from:

- disruptions_allowed
- current_healthy
- desired_healthy
- expected_pods
- Deployment replica counts

Eviction behavior for an unhealthy Running Pod also depends on Kubernetes PDB
policy semantics, including unhealthyPodEvictionPolicy.

Phase 2 IncidentContext does not currently contain sufficient policy information
to make a final eviction-admissibility decision.

Therefore:

- Hermes may recommend EVICT_POD.
- Hermes must not claim the Eviction API will accept or reject that operation.
- Final PDB and disruption eligibility belongs to the later deterministic
  Safety Gate.
- Safety Gate must observe the effective unhealthyPodEvictionPolicy before
  authorizing an eviction.

---

## 16. Temporal and Availability Grounding

Replica availability counters represent observed state.

DiagnosisResult must not infer an unavailable Deployment policy condition,
minimum-availability violation, or similar policy conclusion unless that exact
condition is present in Evidence.

Container restart_count is cumulative historical state.

restart_count alone must not be interpreted as proof that:

- the container restarted during the current incident
- the current failure persisted across those restarts
- the Pod will not recover without remediation
- the container is crash-looping

Temporal or causal conclusions require explicit supporting Evidence.
