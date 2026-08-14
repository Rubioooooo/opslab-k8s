from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from kubernetes import client


@dataclass(frozen=True, slots=True)
class ObserverClientConfig:
    api_server: str
    ca_cert_path: Path
    bearer_token: str
    timeout_seconds: int = 10

    @classmethod
    def from_env(cls) -> "ObserverClientConfig":
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
            "OPSLAB_K8S_TOKEN",
            "",
        ).strip()

        if not bearer_token:
            raise RuntimeError(
                "OPSLAB_K8S_TOKEN is required; "
                "the observer never stores a persistent token."
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


def build_api_client(
    config: ObserverClientConfig,
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
