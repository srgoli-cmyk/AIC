"""Gate for the extension: permissions may only shrink without an approved contract change."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_HOSTS = {
    "https://canvas.ucsd.edu/*",
    "https://canvaskaf.ucsd.edu/*",
    "https://cdnapisec.kaltura.com/*",
    "http://localhost:8000/*",
}
ALLOWED_PERMISSIONS = {"scripting", "tabs", "storage"}


def main() -> int:
    m = json.loads((ROOT / "extension/manifest.json").read_text())
    errors = []
    if m.get("manifest_version") != 3:
        errors.append("manifest_version must be 3")
    extra_hosts = set(m.get("host_permissions", [])) - ALLOWED_HOSTS
    if extra_hosts:
        errors.append(f"host_permissions not allowed: {sorted(extra_hosts)}")
    extra_perms = set(m.get("permissions", [])) - ALLOWED_PERMISSIONS
    if extra_perms:
        errors.append(f"permissions not allowed: {sorted(extra_perms)}")
    if "<all_urls>" in json.dumps(m):
        errors.append("<all_urls> is forbidden")
    if m.get("content_security_policy"):
        errors.append("custom content_security_policy needs review")
    for e in errors:
        print(f"manifest: {e}", file=sys.stderr)
    if not errors:
        print("manifest: ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
