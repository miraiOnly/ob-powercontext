import fs from "node:fs";
import path from "node:path";
import { pipeline, env } from "@xenova/transformers";

env.allowRemoteModels = false;
env.localModelPath = path.resolve("./models/");

const MODELS = path.resolve("models/Xenova/all-MiniLM-L6-v2/onnx");

function sizeOf(file) {
  return fs.statSync(path.join(MODELS, file)).size;
}

function dot(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s;
}

function cosine(a, b) {
  const na = Math.sqrt(dot(a, a));
  const nb = Math.sqrt(dot(b, b));
  if (na === 0 || nb === 0) return 0;
  return dot(a, b) / (na * nb);
}

// 60 篇合成文档，20 条查询，每查询 3 篇相关文档（同簇词）
const TOPICS = [
  ["database", "vector", "index", "search", "seekdb", "storage", "query", "retrieval"],
  ["browser", "webgpu", "wasm", "inference", "model", "embedding", "javascript", "onnx"],
  ["quantize", "int8", "fp16", "precision", "size", "compress", "latency", "quality"],
  ["knowledge", "document", "import", "collection", "session", "state", "metadata", "refresh"],
  ["benchmark", "throughput", "batch", "median", "measure", "device", "profile", "report"],
];
const docs = [];
const docTopics = [];
for (let t = 0; t < TOPICS.length; t++) {
  for (let i = 0; i < 12; i++) {
    const terms = TOPICS[t].slice(0, 3 + (i % 5));
    docs.push(
      `${TOPICS[t][i % TOPICS[t].length]} ${terms.join(" ")} document ${t}-${i} ` +
      TOPICS[(t + 1) % TOPICS.length][i % 4] + " edge case notes",
    );
    docTopics.push(t);
  }
}
const queries = [];
const relevant = [];
for (let t = 0; t < TOPICS.length; t++) {
  for (let q = 0; q < 4; q++) {
    queries.push(TOPICS[t].slice(0, 2 + (q % 3)).join(" ") + " retrieval");
    relevant.push(
      Array.from({ length: 12 }, (_, i) => t * 12 + i)
        .filter((idx, i) => i < 6)
        .map((idx) => idx),
    );
  }
}

async function embedAll(extractor, texts, batchSize) {
  const out = [];
  for (let i = 0; i < texts.length; i += batchSize) {
    const result = await extractor(texts.slice(i, i + batchSize), { pooling: "mean", normalize: true });
    const dim = result.dims[1];
    const data = result.data;
    const rows = result.dims[0];
    for (let r = 0; r < rows; r++) out.push(Array.from(data.slice(r * dim, (r + 1) * dim)));
  }
  return out;
}

async function measure(precision) {
  const extractor = await pipeline("feature-extraction", "Xenova/all-MiniLM-L6-v2", {
    quantized: precision === "int8",
    dtype: precision === "fp32" ? "fp32" : undefined,
  });
  const docVectors = await embedAll(extractor, docs, 32);
  const started = performance.now();
  await embedAll(extractor, docs, 32);
  const embedMs = performance.now() - started;
  const queryVectors = await embedAll(extractor, queries, 4);

  const sim = [];
  for (const qv of queryVectors) {
    sim.push(docVectors.map((dv, i) => ({ i, score: cosine(qv, dv) })).sort((a, b) => b.score - a.score));
  }
  let recallSum = 0;
  let ndcgSum = 0;
  const k = 5;
  for (let q = 0; q < queries.length; q++) {
    const rel = new Set(relevant[q]);
    const top = sim[q].slice(0, k).map((x) => x.i);
    const hit = top.filter((i) => rel.has(i)).length;
    recallSum += hit / rel.size;
    let dcg = 0;
    let idcg = 0;
    for (let i = 0; i < k; i++) {
      const gain = rel.has(top[i]) ? 1 : 0;
      dcg += gain / Math.log2(i + 2);
      idcg += (i < rel.size ? 1 : 0) / Math.log2(i + 2);
    }
    ndcgSum += dcg / (idcg || 1);
  }
  return {
    recallAt5: recallSum / queries.length,
    ndcgAt5: ndcgSum / queries.length,
    embedMs,
    docsPerSec: (docs.length / embedMs) * 1000,
  };
}

const fp32Size = sizeOf("model.onnx");
const int8Size = sizeOf("model_quantized.onnx");

console.log(JSON.stringify({
  model: "Xenova/all-MiniLM-L6-v2 (all-MiniLM-L6-v2, Apache-2.0 兼容)",
  fp32Bytes: fp32Size,
  int8Bytes: int8Size,
  compressionRatio: fp32Size / int8Size,
  fp32: await measure("fp32"),
  int8: await measure("int8"),
}, null, 2));
