# Hermes Phase 2 Diagnosis and Explainability Validation

## 1. Scope

Hermes Phase 2 establishes the Diagnosable and Explainable layers of the
OpsLab AIOps architecture.

Validated pipeline:

Kubernetes
→ ObservationSnapshot v1alpha2
→ Candidate Detector
→ IncidentContext v1alpha1
→ Hermes Adapter
→ Hermes Agent
→ DiagnosisResult v1alpha1
→ deterministic validation

Phase 2 performs diagnosis only.

It does not authorize or execute remediation.

---

## 2. Deterministic Incident Detection

FASTAPI_UNHEALTHY_INSTANCE uses deterministic current-state rules.

A real fault-injection experiment produced:

- target Pod phase=Running
- target Pod Ready=False
- sibling Pod Ready=True
- Deployment ready replicas=1/2
- target EndpointSlice endpoint not ready/not serving
- no container restart during the controlled incident window

The Candidate Detector changed from zero candidates under healthy current state
to one FASTAPI_UNHEALTHY_INSTANCE candidate during the real incident.

Historical Warning Events alone did not trigger an incident.

Topology drift alone did not trigger FASTAPI_UNHEALTHY_INSTANCE.

Result:

PHASE2_DETERMINISTIC_CONTEXT_PIPELINE=PASS

---

## 3. Incident Context and Evidence

The real IncidentContext contained deterministic current-state and historical
Evidence records with stable EV-xxxx identifiers.

The evidence chain included:

- target Pod status
- Deployment replica state
- target container status
- EndpointSlice target state
- current ReplicaSet state
- sibling Pod status
- PodDisruptionBudget status
- HorizontalPodAutoscaler status
- Pod topology
- historical kubelet Events

Trigger Evidence references were traceable to existing Evidence records.

Current state and historical Events remained explicitly separated.

Result:

PHASE2_EVIDENCE_TRACEABILITY=PASS
PHASE2_CURRENT_HISTORICAL_SEPARATION=PASS

---

## 4. DiagnosisResult v1alpha1

Hermes output is not accepted as arbitrary text.

DiagnosisResult v1alpha1 validates:

- schema version
- incident binding
- affected-resource identity
- confidence enum
- Evidence ID references
- recommendation enum
- human-approval requirements
- prohibited executable content
- required CURRENT_STATE grounding

Allowed recommendation values:

- NO_ACTION
- EVICT_POD
- ESCALATE

EVICT_POD is a recommendation only.

It is not authorization or execution.

---

## 5. Negative Validation

The deterministic validator rejects at least:

- malformed JSON
- unsupported schema version
- incorrect incident ID
- unknown Evidence IDs
- historical-only grounding
- invalid confidence
- invalid action
- EVICT_POD without human approval
- ESCALATE without human approval
- NO_ACTION requiring human approval
- affected-resource mismatch
- kubectl content
- shell command content
- unknown top-level fields
- duplicate Evidence references

The complete Phase 2 unit suite contains 33 tests.

---

## 6. Hermes Runtime Isolation

The production diagnosis path does not use normal Hermes one-shot CLI tool
execution.

The Phase 2 Hermes runtime is instantiated with:

- enabled_toolsets=[]
- skip_context_files=True
- skip_memory=True
- max_iterations=1

A runtime preflight validates the actual exposed tool surface.

Real validation:

tool_count=0
tool_names=[]

The model used during real Phase 2 validation was:

- provider: deepseek
- model: deepseek-v4-flash

Result:

HERMES_PHASE2_ZERO_TOOL_RUNTIME=PASS

---

## 7. Real Hermes Diagnosis

A real IncidentContext captured during the controlled
FASTAPI_UNHEALTHY_INSTANCE experiment was replayed into Hermes.

Hermes returned a structured DiagnosisResult that passed:

- schema validation
- incident binding
- affected-resource binding
- Evidence reference validation
- action enum validation
- human approval constraints

Hermes was not given the hidden fault-injection mechanism as diagnosis
Evidence.

Therefore it had to reason only from observable IncidentContext evidence.

The diagnosis correctly treated the underlying application unresponsiveness
cause as a bounded hypothesis rather than claiming knowledge of the hidden
fault injection.

Result:

HERMES_PHASE2_REAL_DIAGNOSIS=PASS

---

## 8. Semantic Grounding Hardening

Real-model validation identified several important semantic risks that cannot
be solved by checking Evidence IDs alone.

The Phase 2 prompt contract was hardened so that Hermes must not:

- treat a healthy sibling as proof that every node-level fault is impossible
- convert replica counters into an unevidenced Deployment policy condition
- infer temporal failure history from cumulative restart_count
- infer a crash-loop from restart_count alone
- infer PDB eviction permission or denial solely from disruptions_allowed or
  health counters

PDB eviction eligibility remains outside Phase 2.

Final eligibility must be independently determined by the deterministic
Safety Gate using the effective Kubernetes policy, including
unhealthyPodEvictionPolicy.

Result:

PHASE2_SEMANTIC_GROUNDING=PASS
PHASE2_PDB_SEMANTIC_GROUNDING=PASS

---

## 9. Architectural Boundary

The final Phase 2 architecture remains:

Detection
→ Diagnosis
→ Authorization
→ Human Approval
→ Execution
→ Verification

Hermes participates only in Diagnosis.

Hermes does not receive:

- cluster-admin
- kubectl execution
- SSH execution
- arbitrary shell execution
- Kubernetes Secret access
- Controlled Executor authority

No Kubernetes write operation is performed by Phase 2.

---

## 10. Phase 2 Result

Final status:

PHASE2_OBSERVATION_V1ALPHA2=PASS
PHASE2_DETERMINISTIC_CONTEXT_PIPELINE=PASS
PHASE2_EVIDENCE_TRACEABILITY=PASS
PHASE2_DIAGNOSIS_RESULT_CONTRACT=PASS
PHASE2_DIAGNOSIS_VALIDATOR=PASS
PHASE2_REAL_CONTEXT_REPLAY=PASS
HERMES_PHASE2_ZERO_TOOL_RUNTIME=PASS
HERMES_PHASE2_REAL_DIAGNOSIS=PASS
PHASE2_SEMANTIC_GROUNDING=PASS
PHASE2_PDB_SEMANTIC_GROUNDING=PASS
HERMES_PHASE2_DIAGNOSABLE=PASS
HERMES_PHASE2_EXPLAINABLE=PASS

Hermes Phase 2 is validated as:

Diagnosable
+
Explainable

---

## 11. Next Phase

Phase 3 will implement the deterministic Safety Gate.

The first bounded action remains:

EVICT_POD

The Safety Gate must independently decide whether a recommendation can proceed.

At minimum, Phase 3 must evaluate:

- diagnosis binding
- approved action enum
- current target identity
- current target health
- workload replica state
- PDB state
- effective unhealthyPodEvictionPolicy
- disruption eligibility
- rollout state
- human approval
- stale-context protection

Hermes recommendations will never directly authorize execution.
