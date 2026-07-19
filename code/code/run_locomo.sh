#!/usr/bin/env sh
set -eu

CODE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$CODE_DIR"

PYTHON_BIN="/home/yanghaotong/premem/exp/mem/bin/python"
LIMIT=20
EVAL_MODE="time-sliced"
VLLM_HOST="127.0.0.1"
VLLM_PORT=30000
VLLM_MODEL="../Qwen2.5-7B-Instruct"
RERANKER_PROVIDER="flagembedding"
RERANKER_MODEL="BAAI/bge-reranker-v2-m3"
RERANKER_DEVICES="cuda:2"
RERANKER_DEVICE_LABEL="cuda2"
RERANKER_CACHE_DIR="/home/yanghaotong/models/cache"
RERANKER_REQUIRE_AVAILABLE=1
RERANKER_TOP_K=3
RERANKER_DYNAMIC_TOP_K=0
OUTPUT_DIR="outputs"
RUN_NAME="locomo_${EVAL_MODE}_prequery_compressed_allreader_qwen_bge_hybrid_top${RERANKER_TOP_K}_${RERANKER_DEVICE_LABEL}_limit${LIMIT}"

export RERANKER_PROVIDER RERANKER_MODEL RERANKER_DEVICES RERANKER_CACHE_DIR RERANKER_REQUIRE_AVAILABLE RERANKER_TOP_K RERANKER_DYNAMIC_TOP_K
export HF_HOME="$RERANKER_CACHE_DIR"
export PYTHONUNBUFFERED=1

mkdir -p "$OUTPUT_DIR"

JSON_LOG="${OUTPUT_DIR}/${RUN_NAME}.json"
TEXT_LOG="${OUTPUT_DIR}/${RUN_NAME}.txt"
TRACE_LOG="${OUTPUT_DIR}/${RUN_NAME}.trace.md"

echo "Running LoCoMo evaluation"
echo "  code dir:   $CODE_DIR"
echo "  python:     $PYTHON_BIN"
echo "  limit:      $LIMIT"
echo "  eval mode:  $EVAL_MODE"
echo "  vLLM:       http://${VLLM_HOST}:${VLLM_PORT}/v1"
echo "  model:      $VLLM_MODEL"
echo "  reranker:   ${RERANKER_PROVIDER}:${RERANKER_MODEL} on devices=${RERANKER_DEVICES}"
echo "  reranker top-k: ${RERANKER_TOP_K}, dynamic_top_k=no, hybrid=yes"
echo "  require reranker: yes"
echo "  main method: Pre-query Prepared + Reader"
echo "  pre-query compression: enabled, budget follows cache_budget"
echo "  json log:   $JSON_LOG"
echo "  trace log:  $TRACE_LOG"
echo "  text log:   $TEXT_LOG"
echo

"$PYTHON_BIN" locomo.py \
  --limit "$LIMIT" \
  --eval-mode "$EVAL_MODE" \
  --vllm-host "$VLLM_HOST" \
  --vllm-port "$VLLM_PORT" \
  --vllm-model "$VLLM_MODEL" \
  --reranker-provider "$RERANKER_PROVIDER" \
  --reranker-model "$RERANKER_MODEL" \
  --reranker-devices "$RERANKER_DEVICES" \
  --reranker-cache-dir "$RERANKER_CACHE_DIR" \
  --require-reranker \
  --details \
  --json-log "$JSON_LOG" \
  --trace-log "$TRACE_LOG" \
  "$@" | tee "$TEXT_LOG"
