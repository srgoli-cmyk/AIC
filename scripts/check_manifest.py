"""Gate for the extension: permissions may only shrink without an approved contract change."""

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
    # The answer service (proxy/) when run locally with `make proxy-dev`. The store build replaces
    # it with the deployed proxy's https origin (scripts/package_extension.mjs checks that one).
    "http://localhost:8787/*",
}
# alarms + idle: the daily auto-sync (wake up, check someone is at the computer). unlimitedStorage:
# captions and search vectors live in IndexedDB. offscreen: the search model runs in an offscreen
# page (WebAssembly). None of these gives host access or shows an install warning. "tabs" is not
# needed: reading the active tab's URL works through the host permissions for Canvas.
ALLOWED_PERMISSIONS = {"scripting", "storage", "unlimitedStorage", "alarms", "idle", "offscreen"}
# The default extension-page policy plus WebAssembly, which the on-device model needs. Scripts
# still load only from the extension itself.
ALLOWED_CSP = {"extension_pages": "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'"}


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
    if m.get("content_security_policy", ALLOWED_CSP) != ALLOWED_CSP:
        errors.append("content_security_policy differs from the reviewed one")
    for e in errors:
        print(f"manifest: {e}", file=sys.stderr)
    if not errors:
        print("manifest: ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
