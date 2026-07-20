#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="${VLLM_ENV_DIR:-${ROOT_DIR}/exp/mem}"
MODEL_PATH="${VLLM_MODEL_PATH:-${ROOT_DIR}/Qwen2.5-7B-Instruct}"
SERVED_MODEL_NAME="${VLLM_SERVED_MODEL_NAME:-../Qwen2.5-7B-Instruct}"
GPU_ID="${1:-${VLLM_GPU:-0}}"
HOST="${VLLM_HOST:-127.0.0.1}"
PORT=30000
GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-0.7}"
MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-}"

if [[ ! -f "${ENV_DIR}/bin/activate" ]]; then
  echo "Error: uv environment not found: ${ENV_DIR}" >&2
  exit 1
fi

if [[ ! -d "${MODEL_PATH}" ]]; then
  echo "Error: Qwen model directory not found: ${MODEL_PATH}" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${ENV_DIR}/bin/activate"

COMMAND=(
  python
  -m vllm.entrypoints.openai.api_server
  --model "${MODEL_PATH}"
  --served-model-name "${SERVED_MODEL_NAME}"
  --host "${HOST}"
  --port "${PORT}"
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}"
)

if [[ -n "${MAX_MODEL_LEN}" ]]; then
  COMMAND+=(--max-model-len "${MAX_MODEL_LEN}")
fi

echo "Starting vLLM"
echo "  environment:  ${VIRTUAL_ENV}"
echo "  python:       $(command -v python)"
echo "  GPU:          ${GPU_ID}"
echo "  endpoint:     http://${HOST}:${PORT}/v1"
echo "  model path:   ${MODEL_PATH}"
echo "  served name:  ${SERVED_MODEL_NAME}"
echo "  GPU memory:   ${GPU_MEMORY_UTILIZATION}"
if [[ -n "${MAX_MODEL_LEN}" ]]; then
  echo "  max model len: ${MAX_MODEL_LEN}"
fi
echo
echo "Health check: curl http://127.0.0.1:${PORT}/v1/models"

exec env CUDA_VISIBLE_DEVICES="${GPU_ID}" "${COMMAND[@]}"
