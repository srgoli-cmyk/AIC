"""Local testing command: mint a short-lived Bearer token for a test user.

    python -m app.dev_session --email smoke-test@ucsd.edu [--ttl-hours N] [--revoke-all]

Run from backend/ with the normal DATABASE_URL. Notes:
- This is for local testing (for example `make smoke`).
- Anyone who can run it already has full database access, so it adds no new power.
- It is never exposed over HTTP: no route calls it.
- A placeholder user created here keeps its own `local-cli:` google_sub. If that address later
  signs in with Google, Google's real `sub` will not match, so login would create a second,
  separate account with the same email (or fail on the unique email). Use a dedicated test
  address such as smoke-test@ucsd.edu, never a real person's.

stdout carries only the raw token (so a script can capture it). Messages go to stderr. Nothing is
logged. `--revoke-all` deletes every session of that user instead and prints the count to stderr.
Exits 1 on a refusal."""

import argparse
import sys

from . import auth_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.dev_session",
        description="Print a short-lived Bearer token for a local test user.",
    )
    parser.add_argument("--email", required=True, help="test address in the allowed domain")
    parser.add_argument(
        "--ttl-hours",
        type=float,
        default=1,
        help=f"token life in hours (default 1, at most {auth_db.MAX_LOCAL_TTL_HOURS})",
    )
    parser.add_argument(
        "--revoke-all", action="store_true", help="delete every session of this user instead"
    )
    args = parser.parse_args(argv)

    try:
        if args.revoke_all:
            count = auth_db.revoke_all_sessions(args.email)
            print(f"Revoked {count} session(s).", file=sys.stderr)
            return 0
        token, _expires_at = auth_db.create_local_session(args.email, args.ttl_hours)
    except auth_db.LocalSessionRefused as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 1

    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
