// podcast.ucsd.edu paths and how they match Canvas courses. Pure functions.
import { PODCAST_HOST, PODCAST_ORIGIN } from "./config.js";

const SECTION_PATH = /^\/watch\/([a-z]{2}[0-9]{2})\/([a-z0-9]{2,12})_([0-9]{3}-[0-9]{3}-[a-z]{2})$/;

/** { term: "fa26", course: "dsc010", section: "004-000-le", key: "dsc010_fa26" } or null. */
export function parseSectionPath(path) {
  const m = typeof path === "string" ? SECTION_PATH.exec(path) : null;
  return m ? { term: m[1], course: m[2], section: m[3], key: `${m[2]}_${m[1]}` } : null;
}

/** "dsc010_fa26" from Canvas codes like "DSC010_FA26_001"; null for codes of another shape. */
export function canvasClassKey(code) {
  const m = /^([A-Z]{2,5})(\d{1,3}[A-Z]{0,2})_([A-Z]{2}\d{2})(?:_|$)/.exec(typeof code === "string" ? code : "");
  return m ? `${m[1]}${m[2]}_${m[3]}`.toLowerCase() : null;
}

/**
 * Groups podcast sections by class and pairs each class with the student's Canvas course.
 * Returns { matched: [{ courseId, code, sections: [path] }], unmatched: [key] }.
 */
export function matchClasses(sectionPaths, canvasCourses) {
  const byKey = new Map();
  for (const path of sectionPaths) {
    const p = parseSectionPath(path);
    if (!p) continue;
    if (!byKey.has(p.key)) byKey.set(p.key, []);
    if (!byKey.get(p.key).includes(path)) byKey.get(p.key).push(path);
  }
  const canvasByKey = new Map();
  for (const c of canvasCourses) {
    const key = canvasClassKey(c.course_code);
    if (key && !canvasByKey.has(key)) canvasByKey.set(key, c);
  }
  const matched = [];
  const unmatched = [];
  for (const [key, sections] of byKey) {
    const c = canvasByKey.get(key);
    if (c) matched.push({ courseId: c.id, code: c.course_code, sections: sections.sort() });
    else unmatched.push(key);
  }
  return { matched, unmatched };
}

/** The lecture page, which the contract accepts as media_url. */
export function lectureUrl(sectionPath, n) {
  return `${PODCAST_ORIGIN}${sectionPath}/${n}`;
}

/** "DSC 010" from "dsc010_fa26", for notes. */
export function classLabel(key) {
  const m = /^([a-z]{2,5})(\d{1,3}[a-z]{0,2})_/.exec(key || "");
  return m ? `${m[1].toUpperCase()} ${m[2].toUpperCase()}` : String(key);
}

export function isPodcastHost(host) {
  return host === PODCAST_HOST;
}
