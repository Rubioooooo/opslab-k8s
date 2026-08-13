#!/usr/bin/env bash

set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-opslab}"
POD="${POD:-opslab-mysql-0}"
CONTAINER="${CONTAINER:-mysql}"

usage() {
  cat <<USAGE
Usage:

  Safe restore verification:
    ./scripts/mysql-restore.sh verify BACKUP_FILE [TARGET_DATABASE]

  Controlled single-table restore:
    ./scripts/mysql-restore.sh table BACKUP_FILE TARGET_DATABASE EXPECTED_TABLE --confirm

Modes:

  verify
    Restore a SQL backup into a NEW database.
    The script refuses to overwrite an existing database.

    Default target database:
      opslab_restore_script_verify

  table
    Restore a single-table SQL dump into an existing database.

    Safety requirements:
      - dump must contain exactly one table structure
      - table name must match EXPECTED_TABLE
      - dump must not contain CREATE DATABASE or USE statements
      - --confirm is mandatory

Examples:

  ./scripts/mysql-restore.sh \
    verify \
    /home/rubio/backups/opslab/mysql/opslab-20260813-044505.sql

  ./scripts/mysql-restore.sh \
    table \
    /home/rubio/backups/opslab/mysql/sre_backup_restore_test-20260813-054043.sql \
    opslab \
    sre_backup_restore_test \
    --confirm
USAGE
}

for command_name in kubectl sha256sum realpath grep sed; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "ERROR: ${command_name} is required." >&2
    exit 1
  fi
done

MODE="${1:-}"

if [[ "${MODE}" == "--help" || "${MODE}" == "-h" || -z "${MODE}" ]]; then
  usage
  exit 0
fi

if [[ "${MODE}" != "verify" && "${MODE}" != "table" ]]; then
  echo "ERROR: unsupported mode: ${MODE}" >&2
  usage >&2
  exit 1
fi

BACKUP_FILE="${2:-}"

if [[ -z "${BACKUP_FILE}" ]]; then
  echo "ERROR: BACKUP_FILE is required." >&2
  usage >&2
  exit 1
fi

if [[ ! -f "${BACKUP_FILE}" || ! -s "${BACKUP_FILE}" ]]; then
  echo "ERROR: backup file does not exist or is empty: ${BACKUP_FILE}" >&2
  exit 1
fi

BACKUP_FILE="$(realpath "${BACKUP_FILE}")"

if ! kubectl get pod \
  -n "${NAMESPACE}" \
  "${POD}" \
  >/dev/null 2>&1
then
  echo "ERROR: MySQL Pod not found: ${NAMESPACE}/${POD}" >&2
  exit 1
fi

mysql_scalar() {
  local sql="$1"

  kubectl exec \
    -n "${NAMESPACE}" \
    -c "${CONTAINER}" \
    "${POD}" \
    -- /bin/sh -ec '
exec mysql \
  -uroot \
  -p"${MYSQL_ROOT_PASSWORD}" \
  -Nse "$1"
' sh "${sql}"
}

validate_identifier() {
  local identifier="$1"
  local label="$2"

  if ! [[ "${identifier}" =~ ^[A-Za-z0-9_]+$ ]]; then
    echo "ERROR: invalid ${label}: ${identifier}" >&2
    exit 1
  fi
}

echo "=== OpsLab MySQL Restore ==="
echo "Mode       : ${MODE}"
echo "Backup file: ${BACKUP_FILE}"
echo "SHA256     : $(sha256sum "${BACKUP_FILE}" | awk '{print $1}')"
echo

case "${MODE}" in
  verify)
    TARGET_DATABASE="${3:-opslab_restore_script_verify}"

    if (($# > 3)); then
      usage >&2
      exit 1
    fi

    validate_identifier "${TARGET_DATABASE}" "target database"

    if grep -Eiq '^[[:space:]]*(CREATE[[:space:]]+DATABASE|USE[[:space:]]+)' "${BACKUP_FILE}"; then
      echo "ERROR: verify mode refuses dumps containing CREATE DATABASE or USE statements." >&2
      exit 1
    fi

    DATABASE_EXISTS="$(
      mysql_scalar "
SELECT COUNT(*)
FROM information_schema.SCHEMATA
WHERE SCHEMA_NAME='${TARGET_DATABASE}';
"
    )"

    if [[ "${DATABASE_EXISTS}" != "0" ]]; then
      echo "ERROR: target database already exists: ${TARGET_DATABASE}" >&2
      echo "The verify mode refuses to overwrite existing databases." >&2
      exit 1
    fi

    echo "Creating isolated verification database: ${TARGET_DATABASE}"

    mysql_scalar "
CREATE DATABASE \`${TARGET_DATABASE}\`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;
"

    echo "Restoring backup..."

    if ! kubectl exec \
      -i \
      -n "${NAMESPACE}" \
      -c "${CONTAINER}" \
      "${POD}" \
      -- /bin/sh -ec '
exec mysql \
  -uroot \
  -p"${MYSQL_ROOT_PASSWORD}" \
  "$1"
' sh "${TARGET_DATABASE}" < "${BACKUP_FILE}"
    then
      echo "ERROR: restore failed." >&2
      echo "Verification database was left in place for inspection:" >&2
      echo "  ${TARGET_DATABASE}" >&2
      exit 1
    fi

    echo
    echo "Restored tables:"

    mysql_scalar "
SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA='${TARGET_DATABASE}'
ORDER BY TABLE_NAME;
"

    echo
    echo "MYSQL_RESTORE_VERIFY=PASS"
    ;;

  table)
    TARGET_DATABASE="${3:-}"
    EXPECTED_TABLE="${4:-}"
    CONFIRM="${5:-}"

    if (($# != 5)); then
      usage >&2
      exit 1
    fi

    validate_identifier "${TARGET_DATABASE}" "target database"
    validate_identifier "${EXPECTED_TABLE}" "expected table"

    if [[ "${CONFIRM}" != "--confirm" ]]; then
      echo "ERROR: table restore requires explicit --confirm." >&2
      exit 1
    fi

    DATABASE_EXISTS="$(
      mysql_scalar "
SELECT COUNT(*)
FROM information_schema.SCHEMATA
WHERE SCHEMA_NAME='${TARGET_DATABASE}';
"
    )"

    if [[ "${DATABASE_EXISTS}" != "1" ]]; then
      echo "ERROR: target database does not exist: ${TARGET_DATABASE}" >&2
      exit 1
    fi

    mapfile -t DUMP_TABLES < <(
      sed -n \
        's/^-- Table structure for table `\([^`]*\)`.*/\1/p' \
        "${BACKUP_FILE}"
    )

    if ((${#DUMP_TABLES[@]} != 1)); then
      echo "ERROR: table restore requires a dump containing exactly one table." >&2
      echo "Detected table count: ${#DUMP_TABLES[@]}" >&2
      exit 1
    fi

    if [[ "${DUMP_TABLES[0]}" != "${EXPECTED_TABLE}" ]]; then
      echo "ERROR: dump table does not match expected table." >&2
      echo "Expected: ${EXPECTED_TABLE}" >&2
      echo "Found   : ${DUMP_TABLES[0]}" >&2
      exit 1
    fi

    if grep -Eq '^[[:space:]]*(CREATE DATABASE|USE[[:space:]]+`)' "${BACKUP_FILE}"; then
      echo "ERROR: dump contains database-level statements." >&2
      exit 1
    fi

    echo "Scope check : PASS"
    echo "Target DB   : ${TARGET_DATABASE}"
    echo "Target table: ${EXPECTED_TABLE}"
    echo
    echo "Restoring single-table backup..."

    if ! kubectl exec \
      -i \
      -n "${NAMESPACE}" \
      -c "${CONTAINER}" \
      "${POD}" \
      -- /bin/sh -ec '
exec mysql \
  -uroot \
  -p"${MYSQL_ROOT_PASSWORD}" \
  "$1"
' sh "${TARGET_DATABASE}" < "${BACKUP_FILE}"
    then
      echo "ERROR: table restore failed." >&2
      exit 1
    fi

    TABLE_EXISTS="$(
      mysql_scalar "
SELECT COUNT(*)
FROM information_schema.TABLES
WHERE TABLE_SCHEMA='${TARGET_DATABASE}'
  AND TABLE_NAME='${EXPECTED_TABLE}';
"
    )"

    if [[ "${TABLE_EXISTS}" != "1" ]]; then
      echo "ERROR: restored table was not found." >&2
      exit 1
    fi

    echo
    echo "MYSQL_TABLE_RESTORE=PASS"
    ;;
esac
