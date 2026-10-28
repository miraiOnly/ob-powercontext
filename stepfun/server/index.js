// Edge KB backend: seekdb embedded storage + hybrid retrieval API.
// All embedding computation happens in the browser; this server only
// persists vectors/text/metadata into seekdb and runs hybrid search
// INSIDE seekdb (fulltext + vector fused by the database).
import express from "express";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { SeekdbClient, Schema, FulltextIndexConfig, VectorIndexConfig } from "seekdb";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const DATA_DIR = process.env.SEEKDB_DATA_DIR || path.join(ROOT, "data");
const PORT = Number(process.env.PORT || 8787);
const COLLECTION = process.env.SEEKDB_COLLECTION || "edge_kb";
const DIMENSION = 384;

fs.mkdirSync(DATA_DIR, { recursive: true });

// Two deployment modes (both keep vectors/text/metadata inside seekdb):
//   SEEKDB_MODE=embedded (default): in-process seekdb via native bindings.
//   SEEKDB_MODE=server: connect to a local seekdb server (see scripts/start-seekdb.sh).
const MODE = process.env.SEEKDB_MODE || "embedded";
const client =
  MODE === "server"
    ? new SeekdbClient({
        host: process.env.SEEKDB_HOST || "127.0.0.1",
        port: Number(process.env.SEEKDB_PORT || 2881),
        user: process.env.SEEKDB_USER || "root",
        password: process.env.SEEKDB_PASSWORD || "",
        database: process.env.SEEKDB_DATABASE || "edgekb",
      })
    : new SeekdbClient({
        path: DATA_DIR,
        database: process.env.SEEKDB_DATABASE || "edgekb",
      });

let collection = null;
let startedAt = Date.now();

async function ensureCollection() {
  const schema = new Schema({
    // keyword search over document text (IK tokenizer handles CJK)
    fulltextIndex: new FulltextIndexConfig("ik", { ik_mode: "smart" }),
    // dense vector similarity search; embeddings come from the browser
    vectorIndex: new VectorIndexConfig({
      hnsw: { dimension: DIMENSION, distance: "cosine" },
      embeddingFunction: null,
    }),
  });
  collection = await client.getOrCreateCollection({
    name: COLLECTION,
    schema,
    embeddingFunction: null,
  });
  return collection;
}

const app = express();
app.use(express.json({ limit: "256mb" }));
app.use(express.static(path.join(ROOT, "web")));

app.get("/api/health", async (req, res) => {
  try {
    const coll = await ensureCollection();
    const count = await collectionCount();
    res.json({
      ok: true,
      backend: MODE === "server" ? "seekdb server" : "seekdb embedded",
      collection: COLLECTION,
      dimension: DIMENSION,
      documents: count,
      uptimeSec: Math.round((Date.now() - startedAt) / 1000),
    });
  } catch (error) {
    res.status(500).json({ ok: false, error: String(error) });
  }
});

// Incremental ingest: vectors are computed in the browser; seekdb stores
// documents, metadata and vectors and refreshes the indexes so the new
// items are searchable immediately after this call returns.
app.post("/api/ingest", async (req, res) => {
  try {
    await ensureCollection();
    const items = req.body?.items;
    if (!Array.isArray(items) || items.length === 0) {
      return res.status(400).json({ error: "items must be a non-empty array" });
    }
    const ids = items.map((item) => String(item.id));
    const documents = items.map((item) => String(item.text ?? ""));
    const metadatas = items.map((item) => item.metadata ?? {});
    const embeddings = items.map((item) => item.embedding);
    const started = Date.now();
    await collection.add({ ids, documents, metadatas, embeddings });
    const ms = Date.now() - started;
    res.json({ ok: true, ingested: items.length, tookMs: ms, refreshed: true });
  } catch (error) {
    res.status(500).json({ ok: false, error: String(error) });
  }
});

// Hybrid search executed by seekdb: fulltext predicate + KNN vector
// similarity fused with ranking inside the database (no client-side merge).
app.post("/api/search", async (req, res) => {
  try {
    await ensureCollection();
    const text = String(req.body?.text ?? "");
    const embedding = req.body?.embedding;
    const nResults = Math.max(1, Math.min(100, Number(req.body?.nResults ?? 10)));
    const useHybrid = req.body?.hybrid !== false;
    const started = Date.now();
    let result;
    if (useHybrid && Array.isArray(embedding) && embedding.length === DIMENSION) {
      result = await collection.hybridSearch({
        query: { whereDocument: { $contains: text } },
        knn: { queryEmbeddings: [embedding] },
        nResults,
      });
    } else if (Array.isArray(embedding) && embedding.length === DIMENSION) {
      result = await collection.query({ queryEmbeddings: [embedding], nResults });
    } else {
      result = await collection.query({ queryTexts: text, nResults });
    }
    const ms = Date.now() - started;
    res.json({
      ok: true,
      mode: useHybrid && Array.isArray(embedding) ? "hybrid" : "single",
      tookMs: ms,
      ids: result.ids ?? [],
      distances: result.distances ?? [],
      documents: result.documents ?? [],
      metadatas: result.metadatas ?? [],
    });
  } catch (error) {
    res.status(500).json({ ok: false, error: String(error) });
  }
});

app.post("/api/reset", async (req, res) => {
  try {
    if (await client.hasCollection(COLLECTION)) {
      await client.deleteCollection({ name: COLLECTION });
    }
    await ensureCollection();
    res.json({ ok: true, collection: COLLECTION, documents: 0 });
  } catch (error) {
    res.status(500).json({ ok: false, error: String(error) });
  }
});

app.post("/api/bench-results", async (req, res) => {
  try {
    const report = req.body;
    fs.writeFileSync(
      path.join(DATA_DIR, "bench-results.json"),
      JSON.stringify(report, null, 2),
    );
    res.json({ ok: true });
  } catch (error) {
    res.status(500).json({ ok: false, error: String(error) });
  }
});

async function collectionCount() {
  try {
    if (await client.hasCollection(COLLECTION)) {
      const rows = await client.execute(
        "SELECT COUNT(*) AS n FROM `" + COLLECTION + "`",
      );
      const count = Number(rows?.[0]?.n ?? rows?.[0]?.["COUNT(*)"] ?? NaN);
      if (Number.isFinite(count)) return count;
    }
    return 0;
  } catch {
    return -1;
  }
}

await ensureCollection();
app.listen(PORT, () => {
  console.log(
    `Edge KB ready: http://127.0.0.1:${PORT} (${MODE === "server" ? `seekdb server ${process.env.SEEKDB_HOST || "127.0.0.1"}:${process.env.SEEKDB_PORT || 2881}` : `seekdb embedded at ${DATA_DIR}`})`,
  );
});
