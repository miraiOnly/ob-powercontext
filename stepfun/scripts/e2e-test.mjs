import path from "node:path";
import { pipeline, env } from "@xenova/transformers";
env.allowRemoteModels = false;
env.localModelPath = path.resolve("./models/");

const extractor = await pipeline("feature-extraction", "Xenova/all-MiniLM-L6-v2", { quantized: true });
const embed = async (texts) => {
  const out = [];
  for (const t of texts) {
    const r = await extractor(t, { pooling: "mean", normalize: true });
    out.push(Array.from(r.data));
  }
  return out;
};

const health = await (await fetch("http://127.0.0.1:8787/api/health")).json();
console.log("health:", JSON.stringify(health));

const docs = [
  "OceanBase 数据库大赛考察端侧 AI 与混合检索。",
  "seekdb 支持向量与全文混合检索，索引刷新后新文档可查。",
  "浏览器本地推理不需要云端 API，WebGPU 与 WASM 是常用后端。",
  "INT8 量化压缩模型体积并保持检索质量。",
];
const embeddings = await embed(docs);
const ingest = await (await fetch("http://127.0.0.1:8787/api/ingest", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ items: docs.map((text, i) => ({ id: `t-${i}`, text, metadata: { n: i }, embedding: embeddings[i] })) }),
})).json();
console.log("ingest:", JSON.stringify(ingest));

const qv = await embed(["端侧 AI 推理与向量检索"]);
const search = await (await fetch("http://127.0.0.1:8787/api/search", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ text: "端侧 AI 推理与向量检索", embedding: qv[0], nResults: 4 }),
})).json();
console.log("search mode:", search.mode, "tookMs:", search.tookMs);
console.log("ids:", JSON.stringify(search.ids));
console.log("docs:", JSON.stringify(search.documents));
console.log("distances:", JSON.stringify(search.distances));

const health2 = await (await fetch("http://127.0.0.1:8787/api/health")).json();
console.log("health after ingest:", JSON.stringify(health2));
