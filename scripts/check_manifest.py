"""Gate for the extension: permissions may only shrink without an approved contract change."""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_HOSTS = {
    "https://canvas.ucsd.edu/*",
    "https://canvaskaf.ucsd.edu/*",
    # Lecture podcasts for every class the student is enrolled in (all lecture sections).
    "https://podcast.ucsd.edu/*",
    "https://cdnapisec.kaltura.com/*",
    "http://localhost:8000/*",
}
# alarms + idle: the daily auto-sync (wake up, check someone is at the computer). Neither gives
# host access or shows an install warning.
ALLOWED_PERMISSIONS = {"scripting", "tabs", "storage", "alarms", "idle"}
# The public key that gives the unpacked extension its stable id, gdgflcpoijokbdnckcojphfoinhkbbmd.
# The backend allows CORS for that id only (EXTENSION_ID), so changing the key needs review.
EXPECTED_KEY_SHA256 = "bedd827b60d14db8246a35bd1a4dab1b8168a878085788f009718522d6dec378"


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
    key = m.get("key")
    if not isinstance(key, str) or hashlib.sha256(key.encode()).hexdigest() != EXPECTED_KEY_SHA256:
        errors.append("manifest key missing or changed (the extension id would change)")
    if m.get("content_security_policy"):
        errors.append("custom content_security_policy needs review")
    for e in errors:
        print(f"manifest: {e}", file=sys.stderr)
    if not errors:
        print("manifest: ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
