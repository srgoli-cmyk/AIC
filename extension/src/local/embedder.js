// Sentence embeddings inside the extension: bge-small-en-v1.5 (8-bit ONNX) run by transformers.js
// on WebAssembly. Every file it needs ships inside the extension (vendor/, made by
// scripts/vendor_extension.mjs), so nothing is downloaded at run time and no lecture text leaves
// the browser to be embedded. Only extension pages (the app page and the offscreen document) load
// it; the service worker never does.
import { ModelLoadError } from "./ingest.js";

export const MODEL_ID = "Xenova/bge-small-en-v1.5";
export const EMBEDDING_DIM = 384;
// bge models are trained to retrieve passages for a question written with this prefix. Passages
// themselves are embedded without it.
export const QUERY_PREFIX = "Represent this sentence for searching relevant passages: ";

let loading = null;

/** Loads the pipeline once per page. `getURL` maps a path inside the extension to its URL. */
export function loadPipeline(getURL = (p) => chrome.runtime.getURL(p)) {
  loading ??= (async () => {
    try {
      const { pipeline, env } = await import(getURL("vendor/transformers/transformers.min.js"));
      env.allowRemoteModels = false;
      env.allowLocalModels = true;
      env.localModelPath = getURL("vendor/models/");
      env.useBrowserCache = false;
      env.backends.onnx.wasm.wasmPaths = getURL("vendor/transformers/");
      env.backends.onnx.wasm.numThreads = 1; // extension pages are not cross-origin isolated
      return await pipeline("feature-extraction", MODEL_ID, { dtype: "q8", device: "wasm" });
    } catch (e) {
      loading = null;
      throw new ModelLoadError(e?.name || "Error");
    }
  })();
  return loading;
}

/** One normalized 384-number vector per text, in order (CLS pooling, like sentence-transformers). */
export async function embed(texts, getURL) {
  if (!texts.length) return [];
  const extractor = await loadPipeline(getURL);
  const out = await extractor(texts, { pooling: "cls", normalize: true });
  const flat = out.data;
  const dim = out.dims.at(-1);
  const vectors = [];
  for (let i = 0; i < texts.length; i++) vectors.push(Float32Array.from(flat.subarray(i * dim, (i + 1) * dim)));
  return vectors;
}

export async function embedQuery(question, getURL) {
  return (await embed([QUERY_PREFIX + question], getURL))[0];
}
