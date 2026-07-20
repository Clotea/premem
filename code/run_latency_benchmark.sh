#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CODE_DIR="${ROOT_DIR}/code"

: "${PYTHON_BIN:=/home/yanghaotong/premem/exp/mem/bin/python}"
: "${LIMIT:=20}"
: "${EVAL_MODE:=time-sliced}"
: "${LOCOMO_PATH:=${ROOT_DIR}/preact_demo/data/locomo/locomo10.json}"
: "${CONFIG:=${CODE_DIR}/configs/python_demo.json}"
: "${VLLM_HOST:=127.0.0.1}"
: "${VLLM_PORT:=30000}"
: "${VLLM_MODEL:=../Qwen2.5-7B-Instruct}"
: "${CACHE_BUDGET:=5}"
: "${RETRIEVAL_TOP_K:=5}"
: "${PREQUERY_WINDOW_MS:=500}"
: "${READY_WINDOWS_MS:=100,250,500,1000,2000}"
: "${MEMORY_LOAD_BASE_MS:=40}"
: "${MEMORY_LOAD_TAIL_MS:=180}"
: "${MEMORY_LOAD_PER_KB_MS:=3}"
: "${MEMORY_LOAD_MAX_MS:=500}"
: "${PREFETCH_CONCURRENCY:=4}"
: "${QUERY_ENCODING_MS:=20}"
: "${QUERY_RERANK_MS:=30}"
: "${WORKING_SET:=gold}"
: "${REACTIVE_RETRIEVER:=graph}"
: "${OUTPUT_DIR:=${ROOT_DIR}/outputs}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    PYTHON_BIN="$(command -v python)"
  fi
fi

mkdir -p "${OUTPUT_DIR}"

RUN_NAME="latency_qwen_graphcache_b${CACHE_BUDGET}_w${PREQUERY_WINDOW_MS}_c${PREFETCH_CONCURRENCY}_limit${LIMIT}"
JSON_LOG="${OUTPUT_DIR}/${RUN_NAME}.json"
TEXT_LOG="${OUTPUT_DIR}/${RUN_NAME}.txt"

echo "Running PreMem latency benchmark"
echo "  Python:           ${PYTHON_BIN}"
echo "  vLLM:             http://${VLLM_HOST}:${VLLM_PORT}/v1"
echo "  model:            ${VLLM_MODEL}"
echo "  limit:            ${LIMIT}"
echo "  eval mode:        ${EVAL_MODE}"
echo "  cache budget:     ${CACHE_BUDGET}"
echo "  pre-query window: ${PREQUERY_WINDOW_MS} ms"
echo "  Ready windows:    ${READY_WINDOWS_MS} ms"
echo "  prefetch workers: ${PREFETCH_CONCURRENCY}"
echo "  working set:      ${WORKING_SET}"
echo "  output:           ${JSON_LOG}"
echo

cd "${CODE_DIR}"

PYTHONUNBUFFERED=1 "${PYTHON_BIN}" benchmark_latency.py \
  --locomo-path "${LOCOMO_PATH}" \
  --config "${CONFIG}" \
  --limit "${LIMIT}" \
  --eval-mode "${EVAL_MODE}" \
  --vllm-host "${VLLM_HOST}" \
  --vllm-port "${VLLM_PORT}" \
  --vllm-model "${VLLM_MODEL}" \
  --cache-budget "${CACHE_BUDGET}" \
  --retrieval-top-k "${RETRIEVAL_TOP_K}" \
  --prequery-window-ms "${PREQUERY_WINDOW_MS}" \
  --ready-windows-ms "${READY_WINDOWS_MS}" \
  --memory-load-base-ms "${MEMORY_LOAD_BASE_MS}" \
  --memory-load-tail-ms "${MEMORY_LOAD_TAIL_MS}" \
  --memory-load-per-kb-ms "${MEMORY_LOAD_PER_KB_MS}" \
  --memory-load-max-ms "${MEMORY_LOAD_MAX_MS}" \
  --prefetch-concurrency "${PREFETCH_CONCURRENCY}" \
  --query-encoding-ms "${QUERY_ENCODING_MS}" \
  --query-rerank-ms "${QUERY_RERANK_MS}" \
  --working-set "${WORKING_SET}" \
  --reactive-retriever "${REACTIVE_RETRIEVER}" \
  --require-vllm \
  --output "${JSON_LOG}" \
  "$@" 2>&1 | tee "${TEXT_LOG}"
