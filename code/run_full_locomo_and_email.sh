#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUN_SCRIPT="${ROOT_DIR}/code/run_locomo_eval.sh"
EMAIL_SCRIPT="${ROOT_DIR}/teste.sh"
START_EPOCH="$(date +%s)"
NOTIFIED=0

notify() {
  local status="${1:-1}"
  if [[ "${NOTIFIED}" -eq 1 ]]; then
    return
  fi
  NOTIFIED=1

  local end_epoch duration state
  end_epoch="$(date +%s)"
  duration="$((end_epoch - START_EPOCH))"
  if [[ "${status}" -eq 0 ]]; then
    state="SUCCESS"
  else
    state="FAILED(${status})"
  fi

  echo
  echo "LoCoMo full evaluation finished: ${state}"
  echo "Duration: ${duration}s"
  echo "Calling email notifier: ${EMAIL_SCRIPT}"

  export PREMEM_JOB_NAME="LoCoMo full evaluation"
  export PREMEM_JOB_STATUS="${state}"
  export PREMEM_JOB_DURATION_SECONDS="${duration}"
  export PREMEM_JOB_OUTPUT_DIR="${ROOT_DIR}/code/outputs"

  if [[ -f "${EMAIL_SCRIPT}" ]]; then
    (
      cd "${ROOT_DIR}"
      bash "${EMAIL_SCRIPT}"
    ) || echo "Warning: teste.sh failed to send the notification." >&2
  else
    echo "Warning: email notifier not found: ${EMAIL_SCRIPT}" >&2
  fi
}

on_exit() {
  local status="$?"
  trap - EXIT
  notify "${status}"
  exit "${status}"
}
trap on_exit EXIT

if [[ ! -f "${RUN_SCRIPT}" ]]; then
  echo "LoCoMo full-evaluation script not found: ${RUN_SCRIPT}" >&2
  exit 2
fi
if [[ ! -f "${EMAIL_SCRIPT}" ]]; then
  echo "Email notifier not found: ${EMAIL_SCRIPT}" >&2
  exit 2
fi

echo "Starting LoCoMo full evaluation"
echo "Host: $(hostname)"
echo "Started: $(date --iso-8601=seconds)"
echo "Runner: ${RUN_SCRIPT}"
echo "Email notifier: ${EMAIL_SCRIPT}"

cd "${ROOT_DIR}/code"
bash "${RUN_SCRIPT}"
