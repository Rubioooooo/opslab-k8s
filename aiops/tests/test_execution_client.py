from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opslab_aiops.execution.client import (
    ExecutorClientConfig,
    build_executor_api_client,
)


class ExecutorClientTests(unittest.TestCase):
    def _ca_file(self, directory: str) -> Path:
        path = Path(directory) / "ca.crt"
        path.write_text("test-ca", encoding="utf-8")
        return path

    def test_executor_requires_dedicated_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ca = self._ca_file(tmp)

            env = {
                "OPSLAB_K8S_API_SERVER": "https://192.168.8.10:6443",
                "OPSLAB_K8S_CA_CERT": str(ca),
                "OPSLAB_K8S_TOKEN": "observer-token-must-not-be-used",
            }

            with patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "OPSLAB_K8S_EXECUTOR_TOKEN is required",
                ):
                    ExecutorClientConfig.from_env()

    def test_executor_reads_only_dedicated_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ca = self._ca_file(tmp)

            env = {
                "OPSLAB_K8S_API_SERVER": "https://192.168.8.10:6443",
                "OPSLAB_K8S_CA_CERT": str(ca),
                "OPSLAB_K8S_TOKEN": "observer-token",
                "OPSLAB_K8S_EXECUTOR_TOKEN": "executor-token",
            }

            with patch.dict(os.environ, env, clear=True):
                config = ExecutorClientConfig.from_env()

            self.assertEqual(config.bearer_token, "executor-token")
            self.assertNotIn("executor-token", repr(config))

    def test_build_client_uses_bearer_token_and_ca(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ca = self._ca_file(tmp)

            config = ExecutorClientConfig(
                api_server="https://192.168.8.10:6443",
                ca_cert_path=ca,
                bearer_token="executor-token",
            )

            api_client = build_executor_api_client(config)

            try:
                configuration = api_client.configuration

                self.assertEqual(
                    configuration.host,
                    "https://192.168.8.10:6443",
                )
                self.assertTrue(configuration.verify_ssl)
                self.assertEqual(
                    configuration.ssl_ca_cert,
                    str(ca),
                )
                self.assertEqual(
                    configuration.api_key["BearerToken"],
                    "executor-token",
                )
                self.assertEqual(
                    configuration.api_key_prefix["BearerToken"],
                    "Bearer",
                )
            finally:
                api_client.close()


if __name__ == "__main__":
    unittest.main()
