#!/usr/bin/env bash
# 下载 all-MiniLM-L6-v2 的 FP32 与 INT8 量化 ONNX 及分词器到 models/。
# 模型: Xenova/all-MiniLM-L6-v2, 来源 hf-mirror.com（HF 镜像）
set -e
cd "$(dirname "$0")/.."
MODEL_DIR="models/Xenova/all-MiniLM-L6-v2"
mkdir -p "$MODEL_DIR/onnx"
BASE="https://hf-mirror.com/Xenova/all-MiniLM-L6-v2/resolve/main"
for f in config.json tokenizer.json tokenizer_config.json; do
  if [ ! -f "$MODEL_DIR/$f" ] || [ ! -s "$MODEL_DIR/$f" ]; then
    curl -sL --retry 3 -o "$MODEL_DIR/$f" "$BASE/$f"
  fi
done
if [ ! -s "$MODEL_DIR/onnx/model.onnx" ]; then
  curl -sL --retry 3 -o "$MODEL_DIR/onnx/model.onnx" "$BASE/onnx/model.onnx"
fi
if [ ! -s "$MODEL_DIR/onnx/model_quantized.onnx" ]; then
  curl -sL --retry 3 -o "$MODEL_DIR/onnx/model_quantized.onnx" "$BASE/onnx/model_quantized.onnx"
fi
ls -la "$MODEL_DIR" "$MODEL_DIR/onnx"
du -sh models/
