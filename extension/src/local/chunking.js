// Merge caption cues into search chunks, ported from backend/app/chunking.py. Pure and
// deterministic; see that file for the rules. Lengths count code points, like Python's len().

export const CHUNKER_VERSION = "1"; // bump when chunking output changes, so lectures are re-chunked

const MIN_WINDOW = 45.0;
const MAX_WINDOW = 60.0;
const PAUSE = 1.5;
const OVERLAP = 10.0;
const OVERLAP_CEILING = 13.0;
const OVERLAP_MAX_GAP = 10.0;
const MAX_CHARS = 2000;
const OVERLAP_MAX_CHARS = Math.floor(MAX_CHARS / 4);

// . ? ! optionally followed by closing quotes or brackets.
const SENTENCE_END = /[.?!]["'\)\]”’»]*$/;

const len = (s) => {
  let n = 0;
  for (const _ of s) n++;
  return n;
};

function windowEnd(cues, lengths, first) {
  let k = first;
  let latest = cues[first].end;
  let chars = lengths[first];
  while (k < cues.length - 1) {
    const nxt = cues[k + 1];
    if (latest - cues[first].start >= MIN_WINDOW && (SENTENCE_END.test(cues[k].text) || nxt.start - latest > PAUSE)) break;
    if (Math.max(latest, nxt.end) - cues[first].start > MAX_WINDOW) break; // would exceed the window
    if (chars + 1 + lengths[k + 1] > MAX_CHARS) break;
    k += 1;
    latest = Math.max(latest, nxt.end);
    chars += 1 + lengths[k];
  }
  return [k, latest];
}

function overlapStart(cues, lengths, prevFirst, last, prevEnd) {
  let best = last + 1;
  let bestKey = null;
  let chars = 0;
  for (let s = last; s > prevFirst; s--) {
    chars += 1 + lengths[s];
    const length = prevEnd - cues[s].start;
    const fits = length <= OVERLAP_CEILING || (s === last && length <= MAX_WINDOW);
    if (chars > OVERLAP_MAX_CHARS || (!fits && s !== last)) break;
    if (fits) {
      const key = [Math.abs(length - OVERLAP), -length];
      if (bestKey === null || key[0] < bestKey[0] || (key[0] === bestKey[0] && key[1] < bestKey[1])) {
        best = s;
        bestKey = key;
      }
    }
  }
  return best;
}

/** [{ start, end, text }] chunks from cues ({ start, end, text }). */
export function chunkCues(input) {
  const cues = [...input].sort((a, b) => a.start - b.start);
  const lengths = cues.map((c) => len(c.text));
  const chunks = [];
  let first = 0; // first cue of the chunk being built (may be an overlap cue)
  let newFrom = 0; // first cue not yet in any chunk
  while (newFrom < cues.length) {
    let [last, end] = windowEnd(cues, lengths, first);
    if (last < newFrom) {
      // Only overlap cues fit (the next cue is too long to join them): start fresh.
      first = newFrom;
      [last, end] = windowEnd(cues, lengths, first);
    }
    chunks.push({
      start: cues[first].start,
      end,
      text: cues
        .slice(first, last + 1)
        .map((c) => c.text)
        .join(" "),
    });
    newFrom = last + 1;
    if (newFrom < cues.length && cues[newFrom].start - end > OVERLAP_MAX_GAP) first = newFrom;
    else first = overlapStart(cues, lengths, first, last, end);
  }
  return chunks;
}
