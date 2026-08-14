from __future__ import annotations

import argparse
import sys

from kubernetes.client.exceptions import ApiException

from .client import ObserverClientConfig, build_api_client
from .collector import KubernetesObservationCollector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect a read-only OpsLab Kubernetes observation snapshot."
    )

    parser.add_argument(
        "--namespace",
        default="opslab",
    )

    parser.add_argument(
        "--workload",
        default="opslab-api",
    )

    parser.add_argument(
        "--service-name",
        default=None,
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        config = ObserverClientConfig.from_env()

        with build_api_client(config) as api_client:
            collector = KubernetesObservationCollector(
                api_client,
                namespace=args.namespace,
                workload_name=args.workload,
                service_name=args.service_name,
            )

            snapshot = collector.collect()

        print(snapshot.to_json())
        return 0

    except ApiException as exc:
        print(
            f"Kubernetes API error: "
            f"status={exc.status} reason={exc.reason}",
            file=sys.stderr,
        )
        return 2

    except Exception as exc:
        print(
            f"Observation error: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
