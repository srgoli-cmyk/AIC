// Calls the answer service (proxy/). Sends the question and the retrieved caption excerpts, gets
// the reply text back. Also sends a random install id so the service can rate-limit one browser;
// it is not linked to the student's name, email or UCSD account.
import { AnswerUnavailable } from "./answer.js";

const TIMEOUT_MS = 90000;
const INSTALL_KEY = "installId";

export async function installId(storage = chrome.storage.local) {
  const got = await storage.get(INSTALL_KEY);
  if (typeof got[INSTALL_KEY] === "string" && /^[0-9a-f-]{36}$/.test(got[INSTALL_KEY])) return got[INSTALL_KEY];
  const id = crypto.randomUUID();
  await storage.set({ [INSTALL_KEY]: id });
  return id;
}

/** Returns post(body): sends one request to the service and gives back the parsed reply. */
function poster(url, { fetchFn = (...a) => fetch(...a), getInstallId = () => installId() } = {}) {
  return async function post(body) {
    let res;
    try {
      res = await fetchFn(url, {
        method: "POST",
        credentials: "omit",
        headers: { "Content-Type": "application/json", "X-Install-Id": await getInstallId() },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
    } catch (e) {
      throw new AnswerUnavailable(e?.name === "TimeoutError" ? "The answer service took too long. Try again." : undefined);
    }
    if (res.status === 429) throw new AnswerUnavailable("Too many questions in a short time. Wait a minute and try again.");
    let reply;
    try {
      reply = await res.json();
    } catch {
      throw new AnswerUnavailable();
    }
    if (!res.ok || !reply || typeof reply !== "object") throw new AnswerUnavailable();
    return reply;
  };
}

/** Returns a writeAnswer(question, passages) function bound to the service URL. */
export function answerClient(url, deps) {
  const post = poster(url, deps);
  return async function writeAnswer(question, passages) {
    const reply = await post({ question, passages });
    if (typeof reply.answer !== "string" || !reply.answer.trim()) throw new AnswerUnavailable();
    return reply.answer;
  };
}

/** Returns a writeStudy(task, passages) function: the service's reply for "teach", "flashcards" or "quiz". */
export function studyClient(url, deps) {
  const post = poster(url, deps);
  return (task, passages) => post({ task, passages });
}
