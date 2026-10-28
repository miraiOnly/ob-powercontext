# Edge KB — 端侧 AI 推理 + seekdb 混合检索

浏览器本地完成 embedding 推理（Transformers.js，WebGPU 优先 / WASM 自动降级），
向量、原文与元数据写入 seekdb（嵌入式），向量 + 全文混合检索在 seekdb 内完成。

## 目录

```
stepfun/
  server/index.js         # Express + seekdb embedded（seekdb-js）
  web/index.html, app.js  # 前端：模型加载/语料导入/混合检索/基准
  web/lib/transformers.js # 本地自带的 Transformers.js（离线运行）
  models/                 # all-MiniLM-L6-v2 ONNX（FP32 + INT8 量化）+ 分词器
  scripts/                # 模型下载、Node 端质量与性能测量
  TECHNICAL_NOTES.md      # 一页技术说明（含实测数据）
```

## 启动

要求：Node.js ≥ 20（本机实测 v20.18.1）、Chrome/Edge 最新版。

```bash
cd stepfun
npm install            # 安装 express + seekdb（含原生绑定）
npm run download-models  # 或提前执行 scripts/download-models.sh
npm start              # http://127.0.0.1:8787
```

打开 http://127.0.0.1:8787 ：

1. 选择精度（INT8 默认 / FP32 基线）并点击「加载模型」——顶部徽标显示当前推理后端（WEBGPU 或 WASM 降级提示）；
2. 粘贴语料（每段一篇）点击「导入语料」——浏览器本地批量向量化后写入 seekdb 并刷新索引；
3. 输入查询点击「检索」——seekdb 内完成向量 + 全文混合检索；
4. 「运行浏览器基准」把实测数据写入 `data/bench-results.json`。

数据目录 `data/seekdb.db` 为 seekdb 嵌入式存储；「重置知识库」重建集合以演示集合状态管理。
增量导入直接追加调用 `/api/ingest`，新文档在调用返回后即可被检索。

## Node 端质量/性能测量（量化前后对比）

```bash
cd stepfun
node scripts/quality.mjs   # FP32 vs INT8：模型体积、压缩比、Recall@5、nDCG@5、向量化吞吐
```

## 运行模式

| 环境变量 | 默认 | 说明 |
|---|---|---|
| SEEKDB_MODE | embedded | embedded=嵌入式（原生绑定）；server=连接本地 seekdb |
| SEEKDB_HOST / SEEKDB_PORT | 127.0.0.1 / 2881 | server 模式连接参数 |
| SEEKDB_DATABASE | edgekb | 数据库名 |
| PORT | 8787 | Web 端口 |

server 模式：先 `bash scripts/start-seekdb.sh`（或自备 seekdb 并 `CREATE DATABASE IF NOT EXISTS edgekb;`），
再 `npm run start:server`。嵌入式模式在 Windows/Linux/macOS 的主流 Node 环境开箱即用；
个别受限环境（如本仓库曾遇到的 WSL 静态 TLS 限制）可直接切 server 模式。
