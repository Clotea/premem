#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CODE_DIR="${ROOT_DIR}/code"

: "${LIMIT:=10}"
: "${EVAL_MODE:=time-sliced}"
: "${VLLM_HOST:=127.0.0.1}"
: "${VLLM_PORT:=30000}"
: "${VLLM_MODEL:=../Qwen2.5-7B-Instruct}"
: "${OUTPUT_DIR:=${ROOT_DIR}/outputs}"
: "${PYTHON_BIN:=}"

if [[ -z "${PYTHON_BIN}" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    echo "Error: Python was not found. Set PYTHON_BIN to the Python executable." >&2
    exit 127
  fi
fi

if [[ ! -f "${CODE_DIR}/locomo.py" ]]; then
  echo "Error: LoCoMo entry point not found: ${CODE_DIR}/locomo.py" >&2
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"

RUN_NAME="locomo_qwen_vllm_${EVAL_MODE}_limit${LIMIT}"
JSON_LOG="${OUTPUT_DIR}/${RUN_NAME}.json"
TEXT_LOG="${OUTPUT_DIR}/${RUN_NAME}.txt"
TRACE_LOG="${OUTPUT_DIR}/${RUN_NAME}.trace.md"

echo "Running LoCoMo evaluation"
echo "  Python:    ${PYTHON_BIN}"
echo "  samples:   ${LIMIT} (0 means all)"
echo "  mode:      ${EVAL_MODE}"
echo "  vLLM:      http://${VLLM_HOST}:${VLLM_PORT}/v1"
echo "  model:     ${VLLM_MODEL}"
echo "  JSON log:  ${JSON_LOG}"
echo "  trace log: ${TRACE_LOG}"
echo "  text log:  ${TEXT_LOG}"
echo

cd "${CODE_DIR}"

"${PYTHON_BIN}" locomo.py \
  --limit "${LIMIT}" \
  --eval-mode "${EVAL_MODE}" \
  --download-locomo \
  --vllm-host "${VLLM_HOST}" \
  --vllm-port "${VLLM_PORT}" \
  --vllm-model "${VLLM_MODEL}" \
  --details \
  --json-log "${JSON_LOG}" \
  --trace-log "${TRACE_LOG}" \
  "$@" 2>&1 | tee "${TEXT_LOG}"
