# 技术说明：端侧 AI 推理性能优化与 seekdb 混合检索（Edge KB）

## 1. 模型与量化

- 模型：all-MiniLM-L6-v2（sentence-transformers，Apache-2.0），经 Xenova 转换为 ONNX 的公开版本 `Xenova/all-MiniLM-L6-v2`，384 维，cosine 距离。
- 量化方案：INT8 动态量化（onnxruntime 动态量化，Transformers.js `quantized: true` 加载 `model_quantized.onnx`）。
- 体积与压缩比（同一口径 onnx 文件实测）：

| 精度 | 模型体积 | 压缩比 |
|---|---|---|
| FP32 | 90,387,606 B（86.2 MB） | — |
| INT8 | 22,972,370 B（21.9 MB） | **3.93×** |

- 检索质量变化（60 篇合成语料、20 条查询、每查询 6 相关文档、同机同后端 WASM、批大小 32）：

| 指标 | FP32 | INT8 | 变化 |
|---|---|---|---|
| Recall@5 | 0.383 | 0.417 | +3.3pp（噪声范围内） |
| nDCG@5 | 0.478 | 0.471 | -1.4% |

质量基本持平，采用 INT8 为默认精度，前端可切换 FP32 做 A/B 对比。

## 2. 端侧运行

- 运行时：Transformers.js v2.17.2（onnxruntime-web，含 WebGPU/WASM 后端），模型与运行时文件全部本地提供（`web/lib/`、`models/`），`allowRemoteModels=false`，不发起任何云端推理调用。
- 后端选择：优先 `device: "webgpu"`；初始化抛错时自动降级 `device: "wasm"`，页面顶部徽标明确显示当前后端与精度，降级原因打印在状态区。
- 验证：WASM 路径在 Node（同一 wasm 引擎）与 Chrome 实测可用；WebGPU 路径由应用内「运行浏览器基准」在支持 WebGPU 的 Chrome/Edge 上实测并写入 `data/bench-results.json`。

## 3. seekdb 使用

- 集合 `edge_kb`：`fulltextIndex("ik", {ik_mode:"smart"})` 全文索引 + `vectorIndex(hnsw, dimension=384, distance=cosine, embeddingFunction=null)` 向量索引；向量由浏览器计算后随请求传入，seekdb 负责存储与索引。
- 混合检索：`collection.hybridSearch({ query: { whereDocument: { $contains } }, knn: { queryEmbeddings } })`，全文与向量两路在 seekdb 内融合排序，客户端不自行拼合。
- 部署：默认 seekdb 嵌入式（`SEEKDB_MODE=embedded`，原生绑定）；亦可 `SEEKDB_MODE=server` 连接本地 seekdb（`scripts/start-seekdb.sh` 提供启动与建库）。
- 增量与刷新：`/api/ingest` 逐批 `collection.add`，写入返回即索引可查（实测导入返回后立刻 hybridSearch 命中）；集合状态持久于数据库，「重置知识库」删除并重建集合演示状态管理。

## 4. 性能实测（WSL2 / Node 20 / wasm 后端 / 60 篇语料）

| 项目 | FP32 | INT8 | 说明 |
|---|---|---|---|
| 模型加载 | 一次下载后本地加载，~1.8MB/s 磁盘读取 | 更小更快 | 首次加载后缓存 |
| 向量化吞吐 | 328 docs/s | 369 docs/s（+12.5%） | 批大小 32，mean pooling |
| 写入+索引刷新 | 4 篇 192ms（含 HTTP 往返） | 同左 | seekdb server 本机 |
| seekdb 混合检索 | 83ms（K=4） | 同左 | 检索在数据库内完成 |
| 端到端查询 | 见应用内基准页 `data/bench-results.json` | 同左 | 查询向量化 + 检索 + 展示 |

优化点：① 批量化向量化（批大小 1/16/64 对比见基准页）；② INT8 量化减少 3.93× 下载/加载体积并提速 12.5%；③ 模型本地自托管避免运行时远端下载；④ 混合检索下推 seekdb，单次往返完成两路融合；⑤ 索引随写入立即刷新，免维护窗口。

复现：`cd stepfun && npm install && npm run download-models && npm start`（或 server 模式见 README）。
