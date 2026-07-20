#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/home/yanghaotong/premem/exp/mem/bin/python}"
LOG_DIR="${NOTIFY_LOG_DIR:-${ROOT_DIR}/outputs/notify_logs}"

if [[ "${1:-}" == "--" ]]; then
  shift
fi
if [[ "$#" -eq 0 ]]; then
  echo "Usage: $0 -- command [args ...]" >&2
  exit 2
fi

mkdir -p "${LOG_DIR}"
STAMP="$(date '+%Y%m%d_%H%M%S')"
LOG_FILE="${NOTIFY_LOG_FILE:-${LOG_DIR}/run_${STAMP}.log}"
START_EPOCH="$(date +%s)"
COMMAND_TEXT="$(printf '%q ' "$@")"

echo "Running: ${COMMAND_TEXT}"
echo "Log: ${LOG_FILE}"

"$@" 2>&1 | tee "${LOG_FILE}"
STATUS="${PIPESTATUS[0]}"
END_EPOCH="$(date +%s)"
DURATION="$((END_EPOCH - START_EPOCH))"

if [[ "${STATUS}" -eq 0 ]]; then
  STATE="SUCCESS"
else
  STATE="FAILED(${STATUS})"
fi

BODY="$(
  printf 'PreMem experiment %s\n' "${STATE}"
  printf 'Host: %s\n' "$(hostname)"
  printf 'Duration: %ss\n' "${DURATION}"
  printf 'Command: %s\n' "${COMMAND_TEXT}"
  printf 'Log: %s\n' "${LOG_FILE}"
)"

if ! "${PYTHON_BIN}" "${ROOT_DIR}/send_email.py" \
  --subject "[PreMem] ${STATE} on $(hostname)" \
  --body "${BODY}" \
  --attach "${LOG_FILE}"; then
  echo "Warning: experiment finished, but the email notification failed." >&2
fi

exit "${STATUS}"
