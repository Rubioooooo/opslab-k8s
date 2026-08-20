# Hermes Phase 2 Incident Context Contract

## 1. Phase 2 Goal

Phase 2 provides the Diagnosable and Explainable layers of the OpsLab
AIOps control plane.

The target diagnostic flow is:

ObservationSnapshot
→ Candidate Detector
→ Incident Context Builder
→ IncidentContext
→ Hermes Adapter
→ DiagnosisResult

Phase 2 MUST NOT execute remediation actions.

EVICT_POD, Safety Gate write decisions, automatic remediation,
Controlled Executor and Experience Store are outside the Phase 2 scope.

---

## 2. Architectural Boundaries

The following boundaries are mandatory:

- Observation Layer produces facts.
- Candidate Detector performs deterministic incident candidate detection.
- Incident Context Builder performs deterministic correlation, filtering and evidence construction.
- Hermes performs diagnosis and reasoning.
- Diagnosis does not grant execution permission.
- Hermes MUST NOT receive arbitrary shell capability.
- Hermes MUST NOT receive arbitrary kubectl capability.
- Hermes MUST NOT receive Kubernetes administrator credentials.
- Phase 2 MUST NOT execute EVICT_POD.

Detection, diagnosis, authorization and execution are separate concerns.

---

## 3. Observation Contract Evolution

Phase 1 ObservationSnapshot v1alpha1 is frozen by tag:

hermes-phase1-v0.2.0

Phase 2 may evolve the observation schema to v1alpha2 without changing
the Phase 1 security boundary.

The v1alpha2 evolution is limited to identity and relationship metadata:

ReplicaSetObservation:
- owner_kind
- owner_uid

PodObservation:
- owner_kind
- owner_uid

EndpointObservation:
- pod_uid

ObservationSnapshot:
- service_name

No additional Kubernetes API permissions are introduced by this change.

---

## 4. Kubernetes Relationship Model

The deterministic resource relationship is:

Deployment
→ ReplicaSet
→ Pod

Service
→ EndpointSlice
→ Pod

PDB
→ selector matching target workload Pod labels

HPA
→ scaleTargetRef Deployment

Event
→ involvedObject UID/name

Relationship reconstruction MUST occur before Hermes receives the
incident context.

Hermes MUST NOT discover Kubernetes relationships by itself.

---

## 5. Current ReplicaSet Definition

IncidentContext MUST use:

current_replica_sets[]

and MUST NOT assume that a Deployment always has exactly one active
ReplicaSet.

A ReplicaSet is considered current when it belongs to the target
Deployment and at least one of the following is true:

1. replicas > 0
2. it owns a current non-terminating Pod

This definition allows rolling updates to contain multiple current
ReplicaSets.

ReplicaSets with zero replicas and no current Pod are historical and
must not pollute the current workload context.

---

## 6. FASTAPI_UNHEALTHY_INSTANCE Candidate Rule v1

Rule ID:

fastapi_unhealthy_instance.v1

The candidate target MUST be a current Pod.

A Pod may become a FASTAPI_UNHEALTHY_INSTANCE candidate only when:

- the Pod belongs to a current ReplicaSet
- deletion_timestamp is null
- ready is false
- phase is Running
- Deployment observed_generation matches generation
- Deployment updated_replicas equals replicas
- Deployment ready_replicas is less than replicas

The first version intentionally uses a narrow deterministic rule.

Candidate detection indicates that diagnosis is warranted.

Candidate detection does NOT authorize remediation.

---

## 7. Negative Trigger Rules

The following conditions MUST NOT independently trigger
FASTAPI_UNHEALTHY_INSTANCE.

### Historical Event

Historical Warning/Unhealthy Event with a currently healthy Pod:

NO CANDIDATE

Historical Event is diagnostic evidence, not an independent trigger.

### Topology Drift

Multiple healthy FastAPI Pods located on the same Kubernetes node:

NO FASTAPI_UNHEALTHY_INSTANCE

Topology drift may become a separate future incident type.

### HPA State

HPA desired/current replica differences do not independently trigger
FASTAPI_UNHEALTHY_INSTANCE.

### PDB State

PDB disruptions_allowed does not independently trigger
FASTAPI_UNHEALTHY_INSTANCE.

PDB state is retained for later Safety Gate reasoning.

### EndpointSlice State

Endpoint readiness is supporting evidence in v1 and is not the sole
incident trigger.

---

## 8. IncidentContext v1alpha1

IncidentContext contains:

- schema_version
- incident_id
- incident_type
- created_at
- source
- trigger
- current_state
- historical_evidence
- evidence

### source

The source section contains:

- snapshot_id
- collected_at
- namespace
- workload_name
- service_name

### trigger

The trigger section contains:

- rule_id
- target_kind
- target_name
- target_uid
- evidence_refs

### current_state

The current_state section contains:

- deployment
- current_replica_sets
- current_pods
- endpoint_slices
- pdbs
- hpas
- topology

current_state represents facts that are true at snapshot collection time.

### historical_evidence

historical_evidence contains relevant historical Kubernetes Events.

Historical evidence MUST be distinguishable from current state.

---

## 9. Evidence Model v1alpha1

Every evidence record contains:

- evidence_id
- category
- source_kind
- source_name
- source_uid
- fact_type
- fact
- observed_at

Evidence IDs use deterministic context-local numbering:

EV-0001
EV-0002
EV-0003
...

Initial categories:

- CURRENT_STATE
- HISTORICAL_EVENT

Hermes MUST only cite evidence IDs that exist in the supplied
IncidentContext.

Hermes MUST NOT invent evidence.

---

## 10. Evidence Traceability

The required traceability chain is:

DiagnosisResult
→ IncidentContext
→ ObservationSnapshot
→ Kubernetes observed state

DiagnosisResult MUST contain:

- incident_id
- evidence_refs

IncidentContext MUST contain:

- source.snapshot_id

This allows every diagnosis to be traced to the exact observation from
which it was produced.

---

## 11. Current State vs Historical Evidence

The following principle is mandatory:

historical evidence != current state

For example:

Historical Event:
reason=Unhealthy
Readiness probe failed

Current Pod:
Ready=True

Result:

NO FASTAPI_UNHEALTHY_INSTANCE candidate.

Historical evidence may increase or reduce confidence in an already
detected candidate, but it cannot override healthy current state.

---

## 12. Hermes Boundary

Hermes receives IncidentContext rather than unrestricted Kubernetes
access.

Hermes may produce:

- diagnosis summary
- root cause hypothesis
- confidence
- evidence references
- affected resource identity
- recommended action type
- rationale
- risk notes

Hermes MUST NOT produce executable shell instructions as an execution
interface.

Hermes MUST NOT directly execute:

- kubectl
- shell
- SSH
- Eviction API
- Deployment patch
- Pod deletion

A recommendation such as EVICT_POD is only a diagnosis output and does
not constitute authorization.

---

## 13. Phase 2 Validation Direction

Phase 2 must eventually validate at least:

1. healthy baseline with historical Warning does not create a candidate
2. topology drift with healthy Pods does not create FASTAPI_UNHEALTHY_INSTANCE
3. a real unhealthy FastAPI instance creates a deterministic candidate
4. IncidentContext separates current state from historical evidence
5. evidence IDs are traceable
6. Hermes only references supplied evidence
7. Hermes output conforms to DiagnosisResult schema
8. no remediation action is executed during Phase 2

---

## 14. Phase 2 Non-Goals

Phase 2 does not implement:

- Safety Gate write authorization
- EVICT_POD execution
- automatic remediation
- Controlled Executor
- Experience Store
- arbitrary Kubernetes administration
- cross-node storage HA

These belong to later phases.
