"""Forced recompute of the cases whose excepciones_8d plazo a filed defense closes.

WHY IT EXISTS
The engine now closes the ``excepciones_8d`` plazo of a DEMANDADO case when a
"Opone excepciones" escrito from the ejecutado is in ``case_escritos``
(``DeadlineEngine._close_excepciones_on_defense``). The engine only runs per
case when that case is scraped again, which can take weeks. This job re-runs
the engine NOW, and only for the affected cases, so stale red alarms clear.

SCOPE
A case is a candidate when it has a live (``active``), automatic
``excepciones_8d`` row AND a dated opposition escrito from the demandado. Cases
where the firm is demandante are skipped: the rebeldía logic of that side is
not part of this job.

SAFETY
* Dry-run by default: the recompute is executed to learn the real outcome, then
  rolled back. Nothing is persisted.
* With --apply each case runs in a SAVEPOINT. A case the engine safe-fails to GRIS is rolled
  back and reported, never persisted.
* Alerts fire only from ``sync_service.emit_deadline_alerts`` off the
  transition ``recompute_case`` returns. This module never calls it, so a
  recompute here is silent (and leaving ROJO never alerts anyway).
* Idempotent: a closed row is no longer ``active``, so a second run finds no
  candidates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.deadlines_config import DeadlineType
from app.models.case import Case
from app.models.case_deadline import CaseDeadline
from app.models.case_escrito import CaseEscrito
from app.services.deadline_engine import (
    DeadlineEngine,
    _firm_side,
    _is_demandado_opposition_escrito,
)

logger = logging.getLogger(__name__)

_TYPE = DeadlineType.EXCEPCIONES_8D.value
_COMMIT_CHUNK = 100


@dataclass(frozen=True)
class CaseOutcome:
    case_id: int
    rol: Optional[str]
    old_status: str
    new_status: str
    due_date: date
    old_semaforo: Optional[str]
    new_semaforo: Optional[str]


@dataclass
class Result:
    apply: bool
    candidates: int = 0
    skipped_not_demandado: int = 0
    failed: List[int] = field(default_factory=list)  # case ids rolled back
    outcomes: List[CaseOutcome] = field(default_factory=list)

    @property
    def to_cumplido(self) -> int:
        return sum(1 for o in self.outcomes if o.new_status == "cumplido")

    @property
    def to_no_cumplido(self) -> int:
        return sum(1 for o in self.outcomes if o.new_status == "no_cumplido")

    @property
    def unchanged(self) -> int:
        return sum(1 for o in self.outcomes if o.new_status == o.old_status)

    @property
    def rojo_cleared(self) -> int:
        return sum(
            1 for o in self.outcomes
            if o.old_semaforo == "rojo" and o.new_semaforo != "rojo"
        )


def find_candidate_ids(db: Session) -> List[int]:
    """Case ids with a live automatic excepciones row and a defense escrito."""
    prefiltered = (
        db.query(CaseDeadline.case_id)
        .join(CaseEscrito, CaseEscrito.case_id == CaseDeadline.case_id)
        .filter(
            CaseDeadline.deadline_type == _TYPE,
            CaseDeadline.status == "active",
            CaseDeadline.is_manual == False,  # noqa: E712
            CaseEscrito.tipo_escrito.ilike("%opone%excepciones%"),
        )
        .distinct()
        .order_by(CaseDeadline.case_id)
        .all()
    )
    ids = [cid for (cid,) in prefiltered]
    if not ids:
        return []
    # The SQL prefilter is a superset; the engine's own predicate decides.
    confirmed = {
        e.case_id
        for e in db.query(CaseEscrito).filter(CaseEscrito.case_id.in_(ids))
        if _is_demandado_opposition_escrito(e)
    }
    return [cid for cid in ids if cid in confirmed]


def _live_rows(db: Session, case_id: int) -> List[CaseDeadline]:
    return (
        db.query(CaseDeadline)
        .filter(
            CaseDeadline.case_id == case_id,
            CaseDeadline.deadline_type == _TYPE,
            CaseDeadline.status == "active",
            CaseDeadline.is_manual == False,  # noqa: E712
        )
        .all()
    )


def run(db: Session, *, apply: bool = False) -> Result:
    """Recompute the affected cases. Persists only with ``apply=True``."""
    result = Result(apply=apply)
    ids = find_candidate_ids(db)
    result.candidates = len(ids)

    for start in range(0, len(ids), _COMMIT_CHUNK):
        for case_id in ids[start:start + _COMMIT_CHUNK]:
            case = db.get(Case, case_id)
            if case is None:
                continue
            if _firm_side(db, case) != "demandado":
                result.skipped_not_demandado += 1
                continue

            rows = _live_rows(db, case_id)
            before = [(r.id, r.status, r.due_date) for r in rows]
            old_semaforo = case.semaforo

            # The savepoint only matters when persisting. A dry-run relies on
            # the plain end-of-chunk rollback instead: releasing a savepoint
            # must never be what decides whether a dry-run writes.
            savepoint = db.begin_nested() if apply else None
            DeadlineEngine.recompute_case(db, case)
            if case.semaforo == "gris" and old_semaforo != "gris":
                # recompute_case safe-fails to GRIS on any internal error.
                if savepoint is not None:
                    savepoint.rollback()
                result.failed.append(case_id)
                logger.warning("excepciones defense recompute failed for case %s", case_id)
                continue
            if savepoint is not None:
                savepoint.commit()

            for (row_id, old_status, due) in before:
                row = db.get(CaseDeadline, row_id)
                result.outcomes.append(CaseOutcome(
                    case_id=case_id,
                    rol=case.rol,
                    old_status=old_status,
                    new_status=row.status if row is not None else old_status,
                    due_date=due,
                    old_semaforo=old_semaforo,
                    new_semaforo=case.semaforo,
                ))
        if apply:
            db.commit()
        else:
            db.rollback()
    return result
