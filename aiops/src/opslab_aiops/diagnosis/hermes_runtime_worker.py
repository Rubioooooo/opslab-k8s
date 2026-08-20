from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from typing import Any


def _configured_runtime() -> tuple[str, str | None, dict[str, Any]]:
    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider import resolve_runtime_provider

    config = load_config()
    model_config = config.get("model") or {}

    if isinstance(model_config, str):
        model = model_config.strip()
        requested_provider = None

    elif isinstance(model_config, dict):
        model = str(
            model_config.get("default")
            or model_config.get("model")
            or ""
        ).strip()

        requested_provider = str(
            model_config.get("provider")
            or ""
        ).strip() or None

    else:
        raise RuntimeError(
            "unsupported Hermes model configuration"
        )

    if not model:
        raise RuntimeError(
            "Hermes configured model could not be resolved"
        )

    runtime = resolve_runtime_provider(
        requested=requested_provider,
        target_model=model,
    )

    if not isinstance(runtime, dict):
        raise RuntimeError(
            "Hermes runtime provider resolution returned "
            "an invalid result"
        )

    return model, requested_provider, runtime


def _tool_names(agent: Any) -> set[str]:
    names: set[str] = set()

    for item in getattr(agent, "tools", None) or ():
        if isinstance(item, dict):
            function = item.get("function")

            if isinstance(function, dict):
                name = function.get("name")
            else:
                name = item.get("name")

            if isinstance(name, str) and name:
                names.add(name)

        else:
            name = getattr(item, "name", None)

            if isinstance(name, str) and name:
                names.add(name)

    valid_names = getattr(agent, "valid_tool_names", None)

    if valid_names:
        names.update(
            str(name)
            for name in valid_names
            if name
        )

    return names


def _build_agent() -> tuple[Any, str, str]:
    from run_agent import AIAgent

    model, configured_provider, runtime = _configured_runtime()

    provider = str(
        runtime.get("provider")
        or configured_provider
        or ""
    )

    agent = AIAgent(
        api_key=runtime.get("api_key"),
        base_url=runtime.get("base_url"),
        provider=runtime.get("provider"),
        requested_provider=runtime.get("requested_provider"),
        api_mode=runtime.get("api_mode"),
        model=model,

        # Critical Phase 2 safety boundary.
        enabled_toolsets=[],

        # Diagnosis is stateless and context-bounded.
        skip_context_files=True,
        skip_memory=True,

        # One reasoning turn only. No tool loop is required.
        max_iterations=1,

        quiet_mode=True,
        platform="cli",
        credential_pool=runtime.get("credential_pool"),
    )

    names = _tool_names(agent)

    if names:
        try:
            agent.close()
        finally:
            pass

        raise RuntimeError(
            "Hermes diagnosis worker exposed tools: "
            + ",".join(sorted(names))
        )

    return agent, model, provider


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--preflight",
        action="store_true",
    )
    args = parser.parse_args()

    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()

    agent = None

    try:
        with (
            redirect_stdout(captured_stdout),
            redirect_stderr(captured_stderr),
        ):
            agent, model, provider = _build_agent()

            names = _tool_names(agent)

            if args.preflight:
                response = None
            else:
                prompt = sys.stdin.read()

                if not prompt.strip():
                    raise RuntimeError(
                        "diagnosis prompt is empty"
                    )

                result = agent.run_conversation(prompt)

                response = (
                    result.get("final_response")
                    or ""
                ).strip()

                if not response:
                    raise RuntimeError(
                        "Hermes produced no final response"
                    )

        output = {
            "status": "ok",
            "model": model,
            "provider": provider,
            "tool_count": len(names),
            "tool_names": sorted(names),
        }

        if response is not None:
            output["response"] = response

        print(
            json.dumps(
                output,
                ensure_ascii=False,
            )
        )

        return 0

    except Exception as exc:
        error = {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

        print(
            json.dumps(
                error,
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )

        return 2

    finally:
        if agent is not None:
            try:
                agent.shutdown_memory_provider()
            except Exception:
                pass

            try:
                agent.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
