"""Builds the Chrome Web Store version of the extension: dist/extension-prod.zip.

The dev build in extension/ is not changed. The copy differs in:
- the backend URL in src/config.js: https://<domain> instead of http://localhost:8000;
- the manifest: the name "AI Lecture Search", the domain's host permission instead of localhost,
  icons 16/48/128, and no "key" (the Web Store assigns its own id, which goes in EXTENSION_ID);
- no tests, notes or package.json.

Usage: python3 scripts/build_extension_prod.py --domain ailecture.app [--out dist/extension-prod.zip]
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "extension"
ICONS = ROOT / "assets/extension-icons"
NAME = "AI Lecture Search"
LEFT_OUT = {"tests", "CLAUDE.md", "package.json", "node_modules", ".DS_Store"}
DEV_BACKEND = 'export const BACKEND_URL = "http://localhost:8000";'
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def prod_manifest(dev: dict, domain: str) -> dict:
    m = {k: v for k, v in dev.items() if k != "key"}
    m["name"] = NAME
    m["description"] = "Syncs your UCSD lecture captions and slides from Canvas to AI Lecture Search."
    hosts = [h for h in dev["host_permissions"] if not h.startswith("http://localhost")]
    m["host_permissions"] = [*hosts, f"https://{domain}/*"]
    m["icons"] = {str(n): f"icons/icon{n}.png" for n in (16, 48, 128)}
    m["action"] = {**dev.get("action", {}), "default_title": NAME, "default_icon": m["icons"]}
    return m


def build(domain: str, out: Path) -> Path:
    if not DOMAIN_RE.match(domain):
        sys.exit(f"not a plain domain name: {domain!r}")
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "extension"
        shutil.copytree(SRC, stage, ignore=lambda _d, names: [n for n in names if n in LEFT_OUT])
        config = stage / "src/config.js"
        text = config.read_text()
        if text.count(DEV_BACKEND) != 1:
            sys.exit("src/config.js no longer has the expected BACKEND_URL line; update this script")
        config.write_text(text.replace(DEV_BACKEND, f'export const BACKEND_URL = "https://{domain}";'))
        manifest = prod_manifest(json.loads((SRC / "manifest.json").read_text()), domain)
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (stage / "icons").mkdir()
        for n in (16, 48, 128):
            shutil.copy(ICONS / f"icon{n}.png", stage / f"icons/icon{n}.png")
        # Where the extension connects: the manifest and the backend URL must not name localhost.
        if "localhost" in (stage / "manifest.json").read_text() or "localhost" in (stage / "src/config.js").read_text():
            sys.exit("the production build still points at localhost")
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(stage.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(stage).as_posix())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "dist/extension-prod.zip")
    args = ap.parse_args()
    out = build(args.domain.strip().lower(), args.out)
    print(f"wrote {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
