"""Validate a JSON payload (stdin) against contracts/sync_payload.schema.json."""

import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    schema = json.loads((ROOT / "contracts/sync_payload.schema.json").read_text())
    payload = json.load(sys.stdin)
    try:
        jsonschema.validate(payload, schema, format_checker=jsonschema.FormatChecker())
    except jsonschema.ValidationError as e:
        print(f"contract violation at {list(e.absolute_path)}: {e.message}", file=sys.stderr)
        return 1
    print("extension payload: matches contract")
    return 0


if __name__ == "__main__":
    sys.exit(main())
