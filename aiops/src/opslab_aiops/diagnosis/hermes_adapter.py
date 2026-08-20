from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from opslab_aiops.context.models import IncidentContext

from .models import DiagnosisResult
from .validator import parse_and_validate_diagnosis


class HermesAdapterError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HermesDiagnosisRun:
    diagnosis: DiagnosisResult

    model: str
    provider: str

    tool_count: int
    raw_response: str


Runner = Callable[[str], dict]


def build_diagnosis_prompt(
    context: IncidentContext,
) -> str:
    return f"""
You are the diagnosis engine inside a safety-bounded Kubernetes AIOps system.

You are NOT an executor.

Your task is to diagnose the supplied IncidentContext using ONLY the evidence
contained in that context.

MANDATORY RULES:

1. Return exactly one JSON object.
2. Do not use Markdown or code fences.
3. Do not invent Evidence IDs.
4. evidence_refs must contain only IDs present in IncidentContext.evidence.
5. At least one CURRENT_STATE evidence item must support the diagnosis.
6. Historical Kubernetes Events are historical evidence, not current state.
7. If the underlying root cause is not directly proven, explicitly describe it
   as a bounded hypothesis rather than a proven fact.
8. Do not assert a Kubernetes condition, status, probe result, policy state,
   or resource fact unless that exact fact is present in cited Evidence.
9. Do not use absolute causal language such as "ruled out", "proves the root
   cause", or "definitely caused by" when the Evidence only reduces or increases
   the likelihood of a hypothesis. For example, a healthy sibling on the same
   node makes a node-wide outage less likely but does not prove that every
   possible node-level fault is absent.
10. affected_resource must exactly match IncidentContext.trigger target.
9. confidence must be exactly one of:
   LOW, MEDIUM, HIGH
10. recommended_action must be exactly one of:
    NO_ACTION, EVICT_POD, ESCALATE
11. EVICT_POD and ESCALATE require requires_human_approval=true.
12. NO_ACTION requires requires_human_approval=false.
13. Do not output shell commands, command pipelines, SSH instructions,
    Kubernetes CLI commands, scripts, or arbitrary execution instructions.
14. EVICT_POD is only a recommendation for a later Safety Gate. It is not
    authorization and must not be represented as execution.
15. Prefer ESCALATE when evidence is insufficient for a bounded remediation
    recommendation.

PDB / EVICTION SAFETY BOUNDARY:

- PodDisruptionBudget status is contextual diagnosis evidence only.
- Do NOT infer that an unhealthy Pod eviction is allowed or blocked solely
  from disruptions_allowed, current_healthy, desired_healthy, or replica
  counts.
- unhealthyPodEvictionPolicy is not present in this Phase 2 IncidentContext.
- Therefore final PDB eviction eligibility is UNKNOWN in Phase 2 and must be
  evaluated later by the Safety Gate.
- Do NOT claim "below minimum availability" merely from ready/available
  replica counts unless an explicit supplied Evidence fact proves that exact
  policy or condition.
- You may recommend EVICT_POD when the evidence supports it, but state that
  PDB and disruption eligibility must be independently evaluated by the
  later Safety Gate.

TEMPORAL AND AVAILABILITY GROUNDING:

- Replica counts such as ready_replicas, available_replicas, and
  unavailable_replicas describe observed state only.
- Do NOT describe the Deployment as "below minimum availability",
  "losing minimum availability", or violating an availability policy unless
  an explicit supplied Evidence fact proves that condition or policy.
- restart_count is a cumulative counter only.
- Do NOT infer that the container restarted during this incident, that the
  current probe failure persisted across those restarts, or that the instance
  is unlikely to recover on its own solely from restart_count.
- Do NOT infer a crash-loop from restart_count without explicit current
  container-state or Event evidence supporting that claim.

Return this exact schema:

{{
  "schema_version": "v1alpha1",
  "diagnosis_id": "diag-...",
  "incident_id": "{context.incident_id}",
  "summary": "...",
  "root_cause": "...",
  "confidence": "LOW|MEDIUM|HIGH",
  "evidence_refs": ["EV-0001"],
  "affected_resource": {{
    "kind": "{context.trigger.target_kind}",
    "name": "{context.trigger.target_name}",
    "uid": "{context.trigger.target_uid}"
  }},
  "recommended_action": "NO_ACTION|EVICT_POD|ESCALATE",
  "rationale": "...",
  "risk_notes": ["..."],
  "requires_human_approval": true
}}

IncidentContext:

{context.to_json(indent=2)}
""".strip()


class HermesAdapter:
    def __init__(
        self,
        *,
        hermes_python: str | None = None,
        runner: Runner | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        self.hermes_python = (
            hermes_python
            or os.environ.get("OPSLAB_HERMES_PYTHON")
            or str(
                Path(
                    "~/.hermes/hermes-agent/venv/bin/python"
                ).expanduser()
            )
        )

        self.worker_path = Path(
            __file__
        ).with_name(
            "hermes_runtime_worker.py"
        )

        self.runner = runner
        self.timeout_seconds = timeout_seconds

    def _run_worker(
        self,
        prompt: str,
    ) -> dict:
        command = [
            self.hermes_python,
            str(self.worker_path),
        ]

        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )

        if completed.returncode != 0:
            raise HermesAdapterError(
                "Hermes runtime worker failed: "
                + (
                    completed.stderr.strip()
                    or completed.stdout.strip()
                    or f"rc={completed.returncode}"
                )
            )

        try:
            envelope = json.loads(
                completed.stdout
            )
        except json.JSONDecodeError as exc:
            raise HermesAdapterError(
                "Hermes runtime worker returned invalid IPC JSON"
            ) from exc

        return envelope

    def preflight(self) -> dict:
        completed = subprocess.run(
            [
                self.hermes_python,
                str(self.worker_path),
                "--preflight",
            ],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

        if completed.returncode != 0:
            raise HermesAdapterError(
                "Hermes no-tool preflight failed: "
                + (
                    completed.stderr.strip()
                    or completed.stdout.strip()
                )
            )

        try:
            envelope = json.loads(
                completed.stdout
            )
        except json.JSONDecodeError as exc:
            raise HermesAdapterError(
                "Hermes preflight returned invalid IPC JSON"
            ) from exc

        if envelope.get("status") != "ok":
            raise HermesAdapterError(
                "Hermes preflight status is not ok"
            )

        if envelope.get("tool_count") != 0:
            raise HermesAdapterError(
                "Hermes diagnosis runtime is not tool-free"
            )

        return envelope

    def diagnose(
        self,
        context: IncidentContext,
    ) -> HermesDiagnosisRun:
        prompt = build_diagnosis_prompt(
            context
        )

        runner = self.runner or self._run_worker
        envelope = runner(prompt)

        if envelope.get("status") != "ok":
            raise HermesAdapterError(
                "Hermes runtime returned non-ok status"
            )

        tool_count = envelope.get("tool_count")

        if tool_count != 0:
            raise HermesAdapterError(
                "Hermes diagnosis runtime exposed tools"
            )

        raw_response = envelope.get("response")

        if not isinstance(raw_response, str):
            raise HermesAdapterError(
                "Hermes runtime response is missing"
            )

        diagnosis = parse_and_validate_diagnosis(
            raw_response,
            context,
        )

        return HermesDiagnosisRun(
            diagnosis=diagnosis,
            model=str(envelope.get("model") or ""),
            provider=str(envelope.get("provider") or ""),
            tool_count=tool_count,
            raw_response=raw_response,
        )
