// Prints the payload built from tests/raw_inputs.json, so scripts/check_extension.sh can
// validate it against contracts/sync_payload.schema.json.
import { readFileSync } from "node:fs";
import { buildSyncPayload } from "../src/payload.js";

const raw = JSON.parse(readFileSync(new URL("./raw_inputs.json", import.meta.url)));
process.stdout.write(JSON.stringify(buildSyncPayload({ ...raw, now: new Date("2026-11-02T18:00:00Z") })));
