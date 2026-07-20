#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/home/yanghaotong/premem/exp/mem/bin/python}"
LOCOMO_PATH="${LOCOMO_PATH:-${ROOT_DIR}/preact_demo/data/locomo/locomo10.json}"
CONFIG="${CONFIG:-${ROOT_DIR}/code/configs/python_demo.json}"
SAMPLE_ID="${SAMPLE_ID:-locomo_c01_tsqa_016}"
CACHE_BUDGET="${CACHE_BUDGET:-5}"
VLLM_URL="${VLLM_URL:-http://127.0.0.1:30000/v1}"
VLLM_MODEL="${VLLM_MODEL:-../Qwen2.5-7B-Instruct}"
OUTPUT_DIR="${OUTPUT_DIR:-${ROOT_DIR}/outputs/planning_trace_${SAMPLE_ID}}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python environment not found: ${PYTHON_BIN}" >&2
  exit 1
fi

echo "Running one traced PreMem planning sample"
echo "  sample:  ${SAMPLE_ID}"
echo "  vLLM:    ${VLLM_URL}"
echo "  model:   ${VLLM_MODEL}"
echo "  output:  ${OUTPUT_DIR}"

cd "${ROOT_DIR}"
PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -m code.planning_trace \
  --locomo-path "${LOCOMO_PATH}" \
  --config "${CONFIG}" \
  --sample-id "${SAMPLE_ID}" \
  --cache-budget "${CACHE_BUDGET}" \
  --vllm-url "${VLLM_URL}" \
  --vllm-model "${VLLM_MODEL}" \
  --output-dir "${OUTPUT_DIR}" \
  "$@"
