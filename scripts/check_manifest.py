"""Gate for the extension: permissions may only shrink without an approved contract change.

  python3 scripts/check_manifest.py                              the dev build (extension/)
  python3 scripts/check_manifest.py --prod <zip> --domain <d>    a production build
  python3 scripts/check_manifest.py --prod <zip> --domain <d> --team   a team build (dev key kept)
"""

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANVAS_HOSTS = {
    "https://canvas.ucsd.edu/*",
    "https://canvaskaf.ucsd.edu/*",
    # Lecture podcasts for every class the student is enrolled in (all lecture sections).
    "https://podcast.ucsd.edu/*",
    "https://cdnapisec.kaltura.com/*",
}
ALLOWED_HOSTS = CANVAS_HOSTS | {"http://localhost:8000/*"}
# alarms + idle: the daily auto-sync (wake up, check someone is at the computer). Neither gives
# host access or shows an install warning.
ALLOWED_PERMISSIONS = {"scripting", "tabs", "storage", "alarms", "idle"}
# The public key that gives the unpacked extension its stable id, gdgflcpoijokbdnckcojphfoinhkbbmd.
# The backend allows CORS for that id only (EXTENSION_ID), so changing the key needs review.
EXPECTED_KEY_SHA256 = "bedd827b60d14db8246a35bd1a4dab1b8168a878085788f009718522d6dec378"
PROD_NAME = "AI Lecture Search"


def common(m: dict, hosts: set[str]) -> list[str]:
    errors = []
    if m.get("manifest_version") != 3:
        errors.append("manifest_version must be 3")
    extra_hosts = set(m.get("host_permissions", [])) - hosts
    if extra_hosts:
        errors.append(f"host_permissions not allowed: {sorted(extra_hosts)}")
    extra_perms = set(m.get("permissions", [])) - ALLOWED_PERMISSIONS
    if extra_perms:
        errors.append(f"permissions not allowed: {sorted(extra_perms)}")
    if "<all_urls>" in json.dumps(m):
        errors.append("<all_urls> is forbidden")
    if m.get("content_security_policy"):
        errors.append("custom content_security_policy needs review")
    return errors


def check_dev(m: dict) -> list[str]:
    errors = common(m, ALLOWED_HOSTS)
    key = m.get("key")
    if not isinstance(key, str) or hashlib.sha256(key.encode()).hexdigest() != EXPECTED_KEY_SHA256:
        errors.append("manifest key missing or changed (the extension id would change)")
    return errors


def check_prod(m: dict, files: set[str], config_js: str, domain: str, team: bool = False) -> list[str]:
    backend = f"https://{domain}/*"
    errors = common(m, CANVAS_HOSTS | {backend})
    if backend not in m.get("host_permissions", []):
        errors.append(f"host_permissions must include {backend}")
    if team:
        key = m.get("key")
        if not isinstance(key, str) or hashlib.sha256(key.encode()).hexdigest() != EXPECTED_KEY_SHA256:
            errors.append("a team build must carry the dev key (the dev extension id)")
    elif "key" in m:
        errors.append("a production build must not carry the dev key (the Web Store assigns the id)")
    if m.get("name") != PROD_NAME:
        errors.append(f"name must be {PROD_NAME!r}")
    icons = m.get("icons", {})
    for n in ("16", "48", "128"):
        if icons.get(n) not in files:
            errors.append(f"icon {n} missing")
    if f'BACKEND_URL = "https://{domain}"' not in config_js:
        errors.append("src/config.js does not point at the domain")
    if "localhost" in json.dumps(m) or "localhost" in config_js:
        errors.append("localhost must not appear in a production build")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", type=Path, help="a production zip to check instead of extension/")
    ap.add_argument("--domain")
    ap.add_argument("--team", action="store_true", help="with --prod: the build keeps the dev key")
    args = ap.parse_args()
    if args.prod:
        if not args.domain:
            ap.error("--prod needs --domain")
        with zipfile.ZipFile(args.prod) as z:
            files = set(z.namelist())
            m = json.loads(z.read("manifest.json"))
            config_js = z.read("src/config.js").decode()
        label = "team manifest" if args.team else "production manifest"
        errors = check_prod(m, files, config_js, args.domain, team=args.team)
    else:
        errors, label = check_dev(json.loads((ROOT / "extension/manifest.json").read_text())), "manifest"
    for e in errors:
        print(f"{label}: {e}", file=sys.stderr)
    if not errors:
        print(f"{label}: ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
