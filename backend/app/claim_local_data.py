"""One-time command: move the data that existed before login (owned by `usr_local`) to a real
account. Run by hand from backend/ after that person has signed in once:

    python -m app.claim_local_data --email you@ucsd.edu [--dry-run]

Prints only counts, course ids and fixed messages. Exits 1 on a refusal."""

import argparse
import sys

from . import auth_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.claim_local_data",
        description="Move the pre-login (usr_local) data to the account with this email.",
    )
    parser.add_argument("--email", required=True, help="email of an account that has signed in")
    parser.add_argument(
        "--dry-run", action="store_true", help="only count what would move; change nothing"
    )
    args = parser.parse_args(argv)

    try:
        result = auth_db.claim_local_data(args.email, dry_run=args.dry_run)
    except auth_db.ClaimRefused as refusal:
        print(f"REFUSED: {refusal}")
        return 1

    moved = result["moved"]
    for table, count in moved.items():
        print(f"{table}: {count}")
    if not any(moved.values()):
        print("Nothing to move: no data is left under the local account.")
    elif result["dry_run"]:
        print("Dry run: nothing was changed.")
    else:
        print("Done: the data now belongs to that account.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
