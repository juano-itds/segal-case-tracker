"""Recompute the cases whose excepciones_8d plazo is closed by a filed defense.

Thin CLI: the logic lives in ``app.services.recompute_excepciones_defense``.

Context: the firm defends the ejecutado. When the demandado files "Opone
excepciones" the art. 464 plazo is fulfilled, but the engine only notices on the
next scrape of that case. This script runs the engine now, only for the cases
that have a live excepciones_8d row AND a dated opposition escrito from the
demandado, so their stale red semaforo clears.

DRY-RUN BY DEFAULT: without ``--apply`` the recompute runs to learn the real
outcome and is then rolled back; nothing is written. It is silent (no alerts)
and idempotent (a second run finds no candidates).

Usage (``PYTHONPATH=.`` is REQUIRED: no script in ``scripts/`` runs without it):
  PYTHONPATH=. poetry run python scripts/recompute_excepciones_with_defense.py
  PYTHONPATH=. poetry run python scripts/recompute_excepciones_with_defense.py --apply
"""
import argparse
import sys

from app.core.database import SessionLocal
from app.services.recompute_excepciones_defense import run

SAMPLE_SIZE = 10


def _report(result) -> None:
    mode = "APPLY" if result.apply else "DRY-RUN"
    print(f"=== excepciones_8d closed by a filed defense ({mode}) ===")
    print(f"  candidate cases (live row + defense escrito): {result.candidates}")
    print(f"  skipped, firm is not demandado:               {result.skipped_not_demandado}")
    print(f"  rolled back, engine safe-failed to GRIS:      {len(result.failed)}")
    print(f"  rows evaluated:                               {len(result.outcomes)}")

    print(f"\n=== sample (first {SAMPLE_SIZE}) ===")
    if not result.outcomes:
        print("  (none)")
    for o in result.outcomes[:SAMPLE_SIZE]:
        print(
            f"  case {o.case_id:<7} {o.rol or '-':<16} due {o.due_date}  "
            f"{o.old_status} -> {o.new_status}   semaforo {o.old_semaforo} -> {o.new_semaforo}"
        )

    print("\n=== summary ===")
    print(f"  -> cumplido (defense on time):      {result.to_cumplido}")
    print(f"  -> no_cumplido (defense was late):  {result.to_no_cumplido}")
    print(f"  unchanged:                          {result.unchanged}")
    print(f"  red semaforo cleared:               {result.rojo_cleared}")
    if result.failed:
        print(f"  failed case ids: {result.failed}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Persist the recompute (default is a dry-run that writes nothing).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Explicit dry-run (this is already the default).",
    )
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")

    db = SessionLocal()
    try:
        result = run(db, apply=args.apply)
        _report(result)
        if args.apply:
            print("\nApplied. No alerts were emitted.")
        else:
            print("\nDRY-RUN: nothing was written. Re-run with --apply to persist.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
