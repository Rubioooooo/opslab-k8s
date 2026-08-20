# Hermes Phase 2 Deterministic Context Pipeline Validation

## 1. Scope

This validation covers the deterministic half of Hermes Phase 2:

ObservationSnapshot v1alpha2
→ Candidate Detector
→ Incident Context Builder
→ IncidentContext v1alpha1
→ Evidence traceability

Hermes diagnosis, DiagnosisResult, Safety Gate, Controlled Executor,
automatic remediation and Experience Store are outside this validation scope.

---

## 2. Observation v1alpha2 Identity Chain

ObservationSnapshot was evolved from v1alpha1 to v1alpha2 with identity
relationship metadata only.

Added fields:

- ReplicaSetObservation.owner_kind
- ReplicaSetObservation.owner_uid
- PodObservation.owner_kind
- PodObservation.owner_uid
- EndpointObservation.pod_uid
- ObservationSnapshot.service_name

No additional Kubernetes RBAC permissions were introduced.

Real Kubernetes validation confirmed:

Deployment UID
→ ReplicaSet owner UID
→ ReplicaSet UID
→ Pod owner UID
→ Pod UID
→ Endpoint targetRef UID

Validation:

OBSERVATION_V1ALPHA2_SCHEMA=PASS
OBSERVATION_V1ALPHA2_SERVICE_IDENTITY=PASS
OBSERVATION_V1ALPHA2_DEPLOYMENT_RS_CHAIN=PASS
OBSERVATION_V1ALPHA2_RS_POD_CHAIN=PASS
OBSERVATION_V1ALPHA2_ENDPOINT_POD_CHAIN=PASS
OBSERVATION_V1ALPHA2_IDENTITY_CHAIN=PASS

---

## 3. Candidate Detector Unit Validation

The FASTAPI_UNHEALTHY_INSTANCE v1 detector is deterministic.

Unit scenarios validated:

- healthy baseline does not create a candidate
- historical Warning does not create a candidate
- topology drift does not create FASTAPI_UNHEALTHY_INSTANCE
- terminating NotReady Pod is ignored
- Pending Pod is outside v1 detection scope
- Deployment generation mismatch suppresses detection
- rollout in progress suppresses detection
- Running + NotReady Pod creates a candidate
- historical zero-replica ReplicaSet is excluded from current workload context

Result:

9 detector tests passed.

---

## 4. Incident Context Builder Unit Validation

The Incident Context Builder was validated for:

- historical ReplicaSet filtering
- stale Event filtering
- current state / historical evidence separation
- sequential Evidence IDs
- trigger Evidence reference integrity
- topology preservation
- source Snapshot traceability
- JSON serialization

Combined Phase 2 deterministic unit validation:

17 tests passed.

---

## 5. Real Healthy Cluster Negative Validation

Real cluster state:

- Deployment opslab-api: 2/2 Ready
- both FastAPI Pods: Running and Ready
- both FastAPI Pods located on k8s-worker1
- historical Unhealthy Warning Events existed

Observed result:

candidate_count=0

Therefore the real cluster validated:

PHASE2_REAL_HEALTHY_NO_CANDIDATE=PASS
PHASE2_REAL_HISTORICAL_WARNING_FALSE_POSITIVE_GUARD=PASS
PHASE2_REAL_TOPOLOGY_FALSE_POSITIVE_GUARD=PASS

Historical evidence did not override healthy current state.

Topology drift did not become FASTAPI_UNHEALTHY_INSTANCE.

---

## 6. Fault Injection RCA

The first fault injection attempts used:

kubectl exec
→ kill -STOP 1

The container PID 1 remained:

State: S (sleeping)

Kubernetes therefore continued to observe:

Pod Ready=True
Deployment 2/2
candidate=0

This was not a Candidate Detector false negative.

The failure was in the fault injection mechanism.

The final injection was performed from the worker node host PID namespace.

The container runtime host PID corresponding to the Uvicorn process was
identified through CRI inspection.

Host-side SIGSTOP produced:

State: T (stopped)

Host-side SIGCONT restored:

State: S (sleeping)

Therefore:

HOST_NAMESPACE_SIGSTOP=PASS
HOST_NAMESPACE_SIGCONT=PASS
FAULT_INJECTION_RCA=PASS

---

## 7. Real Positive Incident Validation

The host-side SIGSTOP created a real service impairment window.

Observed Kubernetes state:

- target Pod phase=Running
- target Pod ready=False
- sibling Pod ready=True
- Deployment ready replicas changed from 2/2 to 1/2
- container restart count remained unchanged

The Candidate Detector changed from:

candidate_count=0

to:

candidate_count=1

Detected incident:

FASTAPI_UNHEALTHY_INSTANCE

Rule:

fastapi_unhealthy_instance.v1

Trigger evidence:

EV-0001
EV-0002

The real IncidentContext contained 11 Evidence records.

Evidence included:

- target Pod status
- Deployment availability
- container status
- target EndpointSlice state
- current ReplicaSet state
- sibling Pod state
- PDB state
- HPA state
- topology state
- historical Kubernetes Events

Validation:

REAL_RUNNING_NOTREADY_WINDOW=PASS
REAL_FASTAPI_UNHEALTHY_CANDIDATE=PASS
REAL_TRIGGER_EVIDENCE_TRACEABILITY=PASS
REAL_INCIDENT_CONTEXT_BUILD=PASS
REAL_INCIDENT_CONTEXT_JSON=PASS

---

## 8. Recovery Validation

After host-side SIGCONT:

- target Pod returned to 1/1 Running
- Deployment returned to 2/2
- both EndpointSlice endpoints returned to ready=true
- target container restart count did not change

Observed restart count:

RESTARTS_BEFORE=2
RESTARTS_AFTER=2

Validation:

PHASE2_INCIDENT_RECOVERY=PASS
PHASE2_NO_CONTAINER_RESTART=PASS

This proves that the test represented a temporary Running + NotReady
service impairment rather than a container crash/restart scenario.

---

## 9. Deterministic Pipeline Result

The following real pipeline has been validated:

Kubernetes observed state
→ ObservationSnapshot v1alpha2
→ deterministic Candidate Detector
→ FASTAPI_UNHEALTHY_INSTANCE
→ Incident Context Builder
→ IncidentContext v1alpha1
→ Evidence IDs

Final deterministic pipeline status:

PHASE2_OBSERVATION_V1ALPHA2=PASS
PHASE2_CANDIDATE_DETECTOR_UNIT=PASS
PHASE2_CANDIDATE_DETECTOR_REAL_NEGATIVE=PASS
PHASE2_CANDIDATE_DETECTOR_REAL_POSITIVE=PASS
PHASE2_CURRENT_HISTORICAL_SEPARATION=PASS
PHASE2_EVIDENCE_TRACEABILITY=PASS
PHASE2_INCIDENT_CONTEXT_BUILDER=PASS
PHASE2_REAL_INCIDENT_DETECTION=PASS
PHASE2_REAL_INCIDENT_CONTEXT=PASS
PHASE2_INCIDENT_RECOVERY=PASS
PHASE2_NO_CONTAINER_RESTART=PASS
PHASE2_DETERMINISTIC_CONTEXT_PIPELINE=PASS

---

## 10. Security Boundary

This stage did not grant Hermes additional Kubernetes permissions.

The Observer remains read-only and least-privileged.

Short-lived ServiceAccount tokens were used for external observation and
were removed from the WSL shell environment after validation.

Runtime Snapshot and IncidentContext JSON files remain under aiops/runtime/
and are excluded from Git.

No token, kubeconfig, Secret, shell credential or administrator credential
is intentionally stored in the repository.

---

## 11. Next Phase

The next Phase 2 step is:

IncidentContext v1alpha1
→ Hermes Adapter
→ Hermes
→ DiagnosisResult v1alpha1

Before connecting Hermes, DiagnosisResult must be frozen as a structured
contract.

Diagnosis remains separate from authorization and execution.

Phase 2 still does not authorize or execute EVICT_POD.
