from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from kubernetes import client


@dataclass(frozen=True, slots=True)
class ExecutorClientConfig:
    api_server: str
    ca_cert_path: Path
    bearer_token: str = field(repr=False)
    timeout_seconds: int = 10

    @classmethod
    def from_env(cls) -> "ExecutorClientConfig":
        api_server = os.environ.get(
            "OPSLAB_K8S_API_SERVER",
            "https://192.168.8.10:6443",
        ).rstrip("/")

        ca_cert_path = Path(
            os.environ.get(
                "OPSLAB_K8S_CA_CERT",
                "~/.config/opslab-aiops/ca.crt",
            )
        ).expanduser()

        bearer_token = os.environ.get(
            "OPSLAB_K8S_EXECUTOR_TOKEN",
            "",
        ).strip()

        if not bearer_token:
            raise RuntimeError(
                "OPSLAB_K8S_EXECUTOR_TOKEN is required; "
                "the executor never falls back to the observer token."
            )

        if not ca_cert_path.is_file():
            raise RuntimeError(
                f"Kubernetes CA file does not exist: {ca_cert_path}"
            )

        return cls(
            api_server=api_server,
            ca_cert_path=ca_cert_path,
            bearer_token=bearer_token,
        )


def build_executor_api_client(
    config: ExecutorClientConfig,
) -> client.ApiClient:
    configuration = client.Configuration()

    configuration.host = config.api_server
    configuration.verify_ssl = True
    configuration.ssl_ca_cert = str(config.ca_cert_path)

    configuration.api_key = {
        "BearerToken": config.bearer_token,
    }
    configuration.api_key_prefix = {
        "BearerToken": "Bearer",
    }

    return client.ApiClient(configuration)
