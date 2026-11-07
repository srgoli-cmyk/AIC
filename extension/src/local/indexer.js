// Service-worker side of indexing. The embedding model runs in an offscreen document (WebAssembly
// needs a page), so the worker only opens that document and tells it to work. The document closes
// itself through the worker when nothing is left.
export const OFFSCREEN_PATH = "offscreen.html";

export function createIndexer(chrome) {
  let creating = null;

  async function hasDocument() {
    if (!chrome.runtime.getContexts) return false;
    const contexts = await chrome.runtime.getContexts({
      contextTypes: ["OFFSCREEN_DOCUMENT"],
      documentUrls: [chrome.runtime.getURL(OFFSCREEN_PATH)],
    });
    return contexts.length > 0;
  }

  async function ensureDocument() {
    if (await hasDocument()) return;
    creating ??= chrome.offscreen
      .createDocument({
        url: OFFSCREEN_PATH,
        reasons: ["WORKERS"],
        justification: "Runs the on-device search model (WebAssembly) that indexes synced lecture captions.",
      })
      .finally(() => {
        creating = null;
      });
    await creating;
  }

  return {
    /** Starts (or continues) indexing every lecture that is due. Safe to call often. */
    async kick() {
      await ensureDocument();
      // A document that is still loading starts on its own, so a lost message is fine.
      await chrome.runtime.sendMessage({ target: "offscreen", type: "INGEST" }).catch(() => {});
    },
    async close() {
      if (await hasDocument()) await chrome.offscreen.closeDocument();
    },
  };
}
