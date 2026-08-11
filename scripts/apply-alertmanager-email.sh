#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${HOME}/.config/opslab/alertmanager-email.env"
MODE="${1:-dry-run}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} not found" >&2
  exit 1
fi

set -a
source "${ENV_FILE}"
set +a

for var in SMTP_USERNAME SMTP_FROM SMTP_TO; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: ${var} is not set" >&2
    exit 1
  fi
done

if ! kubectl get secret opslab-alertmanager-email \
  -n opslab >/dev/null 2>&1; then
  echo "ERROR: Secret opslab-alertmanager-email not found" >&2
  exit 1
fi

SERVED_VERSIONS="$(
  kubectl get crd alertmanagerconfigs.monitoring.coreos.com \
    -o jsonpath='{range .spec.versions[?(@.served==true)]}{.name}{"\n"}{end}'
)"

if grep -qx 'v1beta1' <<<"${SERVED_VERSIONS}"; then
  API_VERSION="monitoring.coreos.com/v1beta1"
elif grep -qx 'v1alpha1' <<<"${SERVED_VERSIONS}"; then
  API_VERSION="monitoring.coreos.com/v1alpha1"
else
  echo "ERROR: No supported AlertmanagerConfig API version found" >&2
  exit 1
fi

case "${MODE}" in
  dry-run)
    KUBECTL_ARGS=(apply --dry-run=server -f -)
    ;;
  apply)
    KUBECTL_ARGS=(apply -f -)
    ;;
  *)
    echo "Usage: $0 [dry-run|apply]" >&2
    exit 1
    ;;
esac

cat <<MANIFEST | kubectl "${KUBECTL_ARGS[@]}"
apiVersion: ${API_VERSION}
kind: AlertmanagerConfig
metadata:
  name: opslab-email-alerts
  namespace: opslab
  labels:
    app.kubernetes.io/part-of: opslab-k8s
    app.kubernetes.io/component: alerting
spec:
  route:
    receiver: qq-email
    groupBy:
      - alertname
      - service
    groupWait: 10s
    groupInterval: 1m
    repeatInterval: 4h
    matchers:
      - name: alertname
        value: FastAPITargetDown
        matchType: "="
      - name: service
        value: opslab-api
        matchType: "="

  receivers:
    - name: qq-email
      emailConfigs:
        - sendResolved: true
          to: "${SMTP_TO}"
          from: "${SMTP_FROM}"
          smarthost: "smtp.qq.com:465"
          authUsername: "${SMTP_USERNAME}"
          authPassword:
            name: opslab-alertmanager-email
            key: smtp-password
          requireTLS: true
          forceImplicitTLS: true
MANIFEST

unset SMTP_USERNAME SMTP_FROM SMTP_TO SMTP_AUTH_CODE
