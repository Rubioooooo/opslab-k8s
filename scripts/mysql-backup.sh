#!/usr/bin/env bash

set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-opslab}"
POD="${POD:-opslab-mysql-0}"
CONTAINER="${CONTAINER:-mysql}"
DATABASE="${DATABASE:-opslab}"
BACKUP_DIR="${BACKUP_DIR:-${HOME}/backups/opslab/mysql}"

usage() {
  cat <<USAGE
Usage:
  ./scripts/mysql-backup.sh

Optional environment variables:
  NAMESPACE   Kubernetes namespace
              default: opslab

  POD         MySQL Pod
              default: opslab-mysql-0

  CONTAINER   MySQL container
              default: mysql

  DATABASE    Database to back up
              default: opslab

  BACKUP_DIR  Backup destination
              default: \$HOME/backups/opslab/mysql

Example:
  ./scripts/mysql-backup.sh
USAGE
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if (($# > 0)); then
  usage >&2
  exit 1
fi

for command_name in kubectl sha256sum realpath; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "ERROR: ${command_name} is required." >&2
    exit 1
  fi
done

if ! [[ "${DATABASE}" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "ERROR: invalid database name: ${DATABASE}" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

BACKUP_DIR_ABS="$(realpath -m "${BACKUP_DIR}")"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"

if [[ -n "${REPO_ROOT}" ]] &&
   [[ "${BACKUP_DIR_ABS}" == "${REPO_ROOT}" ||
      "${BACKUP_DIR_ABS}" == "${REPO_ROOT}"/* ]]; then
  echo "ERROR: refusing to store database backups inside the Git repository." >&2
  echo "Repository : ${REPO_ROOT}" >&2
  echo "Backup dir : ${BACKUP_DIR_ABS}" >&2
  exit 1
fi

if ! kubectl get pod \
  -n "${NAMESPACE}" \
  "${POD}" \
  >/dev/null 2>&1
then
  echo "ERROR: MySQL Pod not found: ${NAMESPACE}/${POD}" >&2
  exit 1
fi

if ! kubectl exec \
  -n "${NAMESPACE}" \
  -c "${CONTAINER}" \
  "${POD}" \
  -- true \
  >/dev/null 2>&1
then
  echo "ERROR: cannot exec into ${NAMESPACE}/${POD}:${CONTAINER}" >&2
  exit 1
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="${BACKUP_DIR_ABS}/${DATABASE}-${TIMESTAMP}.sql"
PARTIAL_FILE="${BACKUP_FILE}.partial.$$"

cleanup() {
  rm -f "${PARTIAL_FILE}"
}

trap cleanup EXIT

umask 077

echo "=== OpsLab MySQL Logical Backup ==="
echo "Namespace : ${NAMESPACE}"
echo "Pod       : ${POD}"
echo "Database  : ${DATABASE}"
echo "Backup dir: ${BACKUP_DIR_ABS}"
echo

echo "Creating logical backup..."

if ! kubectl exec \
  -n "${NAMESPACE}" \
  -c "${CONTAINER}" \
  "${POD}" \
  -- /bin/sh -ec '
exec mysqldump \
  -uroot \
  -p"${MYSQL_ROOT_PASSWORD}" \
  --single-transaction \
  --quick \
  --skip-lock-tables \
  --set-gtid-purged=OFF \
  --routines \
  --triggers \
  --events \
  --default-character-set=utf8mb4 \
  "$1"
' sh "${DATABASE}" > "${PARTIAL_FILE}"
then
  echo "ERROR: mysqldump failed." >&2
  exit 1
fi

if [[ ! -s "${PARTIAL_FILE}" ]]; then
  echo "ERROR: backup file is empty." >&2
  exit 1
fi

mv "${PARTIAL_FILE}" "${BACKUP_FILE}"
chmod 600 "${BACKUP_FILE}"

BACKUP_SIZE="$(du -h "${BACKUP_FILE}" | awk '{print $1}')"
BACKUP_SHA256="$(sha256sum "${BACKUP_FILE}" | awk '{print $1}')"

echo
echo "Backup completed."
echo "File   : ${BACKUP_FILE}"
echo "Size   : ${BACKUP_SIZE}"
echo "SHA256 : ${BACKUP_SHA256}"
echo
echo "MYSQL_BACKUP_CREATE=PASS"
