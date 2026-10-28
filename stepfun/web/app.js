import { pipeline, env } from "/lib/transformers.js";

env.allowRemoteModels = false;
env.localModelPath = "/models/";

const $ = (id) => document.getElementById(id);
const state = {
  extractor: null,
  device: null,
  precision: null,
  modelMs: null,
};

function status(text) {
  $("status").textContent += text + "\n";
  $("status").scrollTop = $("status").scrollHeight;
}

async function loadModel(precision) {
  $("load-model").disabled = true;
  $("model-progress").style.display = "block";
  status(`正在加载模型（${precision.toUpperCase()}）…`);
  const started = performance.now();
  try {
    let extractor;
    try {
      extractor = await pipeline("feature-extraction", "Xenova/all-MiniLM-L6-v2", {
        device: "webgpu",
        quantized: precision === "int8",
        dtype: precision === "fp32" ? "fp32" : undefined,
        progress_callback: (p) => {
          $("model-progress").value = p.progress ?? 0;
        },
      });
      state.device = "webgpu";
    } catch (gpuError) {
      status("WebGPU 不可用，降级到 WASM：" + (gpuError?.message || gpuError));
      extractor = await pipeline("feature-extraction", "Xenova/all-MiniLM-L6-v2", {
        device: "wasm",
        quantized: precision === "int8",
        dtype: precision === "fp32" ? "fp32" : undefined,
      });
      state.device = "wasm";
    }
    state.extractor = extractor;
    state.precision = precision;
    state.modelMs = performance.now() - started;
    $("backend-badge").textContent = "推理后端: " + state.device.toUpperCase();
    $("precision-badge").textContent = "精度: " + precision.toUpperCase();
    status(`模型加载完成（${(state.modelMs / 1000).toFixed(1)}s）`);
  } catch (error) {
    status("模型加载失败：" + error);
  } finally {
    $("load-model").disabled = false;
    $("model-progress").style.display = "none";
  }
}

function requireModel() {
  if (!state.extractor) throw new Error("请先加载模型");
  return state.extractor;
}

function splitDocs(raw) {
  return raw
    .split(/\n\s*\n/)
    .flatMap((chunk) => chunk.split("\n"))
    .map((t) => t.trim())
    .filter(Boolean);
}

async function embed(texts, batchSize) {
  const extractor = requireModel();
  const output = [];
  for (let i = 0; i < texts.length; i += batchSize) {
    const batch = texts.slice(i, i + batchSize);
    const result = await extractor(batch, { pooling: "mean", normalize: true });
    const data = result.data;
    const dim = result.dims[1];
    const vectors = Array.from({ length: batch.length }, (_, r) =>
      Array.from(data.slice(r * dim, (r + 1) * dim)),
    );
    output.push(...vectors);
    $("model-progress").style.display = "block";
    $("model-progress").value = Math.round(((i + batch.length) / texts.length) * 100);
  }
  $("model-progress").value = 0;
  $("model-progress").style.display = "none";
  return output;
}

$("load-model").addEventListener("click", () => loadModel($("precision").value));

$("ingest").addEventListener("click", async () => {
  const docs = splitDocs($("docs").value);
  if (!docs.length) return status("请先粘贴语料");
  $("ingest").disabled = true;
  try {
    const started = performance.now();
    const embeddings = await embed(docs, Number($("batch-size").value));
    const embedMs = performance.now() - started;
    status(`本地向量化 ${docs.length} 篇：${(embedMs / 1000).toFixed(2)}s（后端 ${state.device}）`);
    const response = await fetch("/api/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        items: docs.map((text, i) => ({
          id: `doc-${Date.now()}-${i}`,
          text,
          metadata: { source: "ui", index: i },
          embedding: embeddings[i],
        })),
      }),
    });
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error);
    status(`已写入 seekdb ${payload.ingested} 条并刷新索引（${payload.tookMs}ms）`);
    refreshCount();
  } catch (error) {
    status("导入失败：" + error);
  } finally {
    $("ingest").disabled = false;
  }
});

$("search").addEventListener("click", async () => {
  const text = $("query").value.trim();
  if (!text) return;
  try {
    const started = performance.now();
    const [embedding] = await embed([text], 1);
    const embedMs = performance.now() - started;
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        embedding,
        nResults: Number($("k").value),
      }),
    });
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error);
    const totalMs = performance.now() - started;
    $("search-time").textContent =
      `查询向量化 ${embedMs.toFixed(0)}ms · seekdb 混合检索 ${payload.tookMs}ms · 端到端 ${totalMs.toFixed(0)}ms（后端 ${state.device}）`;
    const results = $("results");
    results.innerHTML = "";
    const ids = payload.ids ?? [];
    const docs = payload.documents ?? [];
    const metas = payload.metadatas ?? [];
    const dists = payload.distances ?? [];
    if (!ids.length) {
      results.innerHTML = '<div class="muted">无结果</div>';
      return;
    }
    ids.forEach((id, i) => {
      const el = document.createElement("div");
      el.className = "result";
      el.innerHTML =
        `<div><span class="score">#${i + 1} · distance ${Array.isArray(dists) && dists[i] != null ? Number(dists[i]).toFixed(4) : "-"}</span></div>` +
        `<div>${escapeHtml(docs[i] ?? "")}</div>` +
        `<div class="meta">${escapeHtml(JSON.stringify(metas[i] ?? {}))} · id=${escapeHtml(String(id))}</div>`;
      results.appendChild(el);
    });
  } catch (error) {
    status("检索失败：" + error);
  }
});

$("reset-db").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/reset", { method: "POST" });
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error);
    status("知识库已重置");
    refreshCount();
  } catch (error) {
    status("重置失败：" + error);
  }
});

$("bench").addEventListener("click", async () => {
  $("bench").disabled = true;
  $("bench-out").textContent = "运行中…";
  const report = { time: new Date().toISOString(), userAgent: navigator.userAgent, rows: {} };
  try {
    requireModel();
    const queries = [
      "端侧 AI 推理",
      "向量数据库混合检索",
      "本地知识库",
      "模型量化 INT8",
      "seekdb 全文索引",
    ];
    const docs = splitDocs($("docs").value).slice(0, 40);
    const samples = docs.length ? docs : [
      "OceanBase 数据库大赛考察端侧 AI 与混合检索。",
      "seekdb 支持向量与全文混合检索。",
      "浏览器本地推理不需要云端 API。",
      "INT8 量化压缩模型体积并保持检索质量。",
      "WebGPU 与 WASM 是常用的端侧推理后端。",
    ];

    // 模型加载耗时（本次会话实测）
    report.rows.modelLoadMs = Math.round(state.modelMs ?? -1);
    report.rows.precision = state.precision;
    report.rows.device = state.device;

    // 批量向量化吞吐：三种批大小
    for (const batchSize of [1, 16, 64]) {
      const started = performance.now();
      await embed(samples, batchSize);
      const ms = performance.now() - started;
      report.rows[`embed_${samples.length}_docs_batch_${batchSize}_ms`] = Math.round(ms);
      report.rows[`embed_throughput_batch_${batchSize}_docs_per_sec`] = Math.round((samples.length / ms) * 1000);
    }

    // 端到端查询延迟（5 次取中位）
    const latencies = [];
    for (const q of queries) {
      const started = performance.now();
      const [embedding] = await embed([q], 1);
      const response = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: q, embedding, nResults: 5 }),
      });
      await response.json();
      latencies.push(performance.now() - started);
    }
    latencies.sort((a, b) => a - b);
    report.rows.e2eQueryMedianMs = Math.round(latencies[Math.floor(latencies.length / 2)]);
    report.rows.e2eQueryP90Ms = Math.round(latencies[Math.floor(latencies.length * 0.9)]);
    report.rows.e2eQueryAllMs = latencies.map((x) => Math.round(x));

    $("bench-out").textContent = JSON.stringify(report, null, 2);
    await fetch("/api/bench-results", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(report),
    });
  } catch (error) {
    $("bench-out").textContent = "基准失败：" + error;
  } finally {
    $("bench").disabled = false;
  }
});

async function refreshCount() {
  try {
    const response = await fetch("/api/health");
    const payload = await response.json();
    if (payload.ok) {
      $("db-badge").textContent = "seekdb: 嵌入式 · " + payload.documents + " 文档";
      $("doc-count").textContent = `当前 ${payload.documents} 篇文档`;
    }
  } catch {
    /* server not ready */
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

refreshCount();
setInterval(refreshCount, 5000);
