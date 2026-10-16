"""Movements detected during the previous Chilean calendar day for Sysgal."""

from datetime import datetime, time, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.sysgal.deps import require_sysgal_key
from app.models.case import Case
from app.models.case_litigante import CaseLitigante
from app.models.client import Client
from app.models.document import Document
from app.models.movement import Movement
from app.utils.rut import normalize_rut


router = APIRouter()
_CHILE_TZ = ZoneInfo("America/Santiago")
_REPRESENTATIVE_PREFIXES = ("AB.", "ABG.", "AP.")


class SysgalMovimientoItem(BaseModel):
    movimiento_id: int
    fecha_movimiento: datetime
    encontrado_at: datetime
    etapa: Optional[str] = None
    tramite: Optional[str] = None
    descripcion: str
    folio: Optional[str] = None
    causa_id: int
    rol: str
    caratulado: str
    rut_cliente: Optional[str] = None
    documento_url: Optional[str] = None
    documento_ruta: Optional[str] = None


class SysgalMovimientosResponse(BaseModel):
    fecha_deteccion: str
    total: int
    limit: int
    items: List[SysgalMovimientoItem]


def _previous_chilean_day_bounds(
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime, str]:
    """Return UTC-naive bounds for yesterday in America/Santiago.

    Application timestamps are persisted as naive UTC datetimes. Converting
    both Chilean midnights separately also handles DST transition days.
    """
    chile_now = now.astimezone(_CHILE_TZ) if now else datetime.now(_CHILE_TZ)
    target = chile_now.date() - timedelta(days=1)
    start_local = datetime.combine(target, time.min, tzinfo=_CHILE_TZ)
    end_local = datetime.combine(target + timedelta(days=1), time.min, tzinfo=_CHILE_TZ)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc, target.isoformat()


def _client_ruts_by_case(db: Session, case_ids: list[int]) -> dict[int, str]:
    """Resolve one best client RUT per case without an N+1 query.

    ``Case.client`` is authoritative when populated. Legacy cases commonly do
    not have it, so fall back to a non-representative litigant. Natural-person
    demandados are preferred, matching the existing portfolio convention.
    """
    if not case_ids:
        return {}

    result = {
        case_id: normalize_rut(rut)
        for case_id, rut in (
            db.query(Case.id, Client.rut)
            .join(Client, Client.id == Case.client_id)
            .filter(Case.id.in_(case_ids), Client.rut.isnot(None), Client.rut != "")
            .all()
        )
    }

    rows = (
        db.query(
            CaseLitigante.case_id,
            CaseLitigante.rut,
            CaseLitigante.participante,
            CaseLitigante.persona_type,
        )
        .filter(
            CaseLitigante.case_id.in_(case_ids),
            CaseLitigante.rut.isnot(None),
            CaseLitigante.rut != "",
        )
        .order_by(CaseLitigante.case_id, CaseLitigante.id)
        .all()
    )

    candidates: dict[int, list[tuple[int, str]]] = {}
    for case_id, rut, role, person_type in rows:
        upper_role = (role or "").upper()
        if upper_role.startswith(_REPRESENTATIVE_PREFIXES):
            continue
        priority = (
            0
            if upper_role.startswith("DDO") and person_type != "JURIDICA"
            else 1
        )
        candidates.setdefault(case_id, []).append((priority, rut))

    for case_id, values in candidates.items():
        if case_id not in result:
            result[case_id] = normalize_rut(min(values, key=lambda item: item[0])[1])
    return result


@router.get("/movimientos", response_model=SysgalMovimientosResponse)
def list_previous_day_movements(
    limit: int = Query(100, ge=1, le=1000, description="Cantidad máxima de movimientos"),
    db: Session = Depends(get_db),
    _key=Depends(require_sysgal_key),
) -> SysgalMovimientosResponse:
    """List movements first detected during yesterday in Chile.

    Reads directly from ``movements`` and includes its associated case, the
    best available client RUT, and the linked document URL/storage path.
    """
    start_utc, end_utc, target = _previous_chilean_day_bounds()
    rows = (
        db.query(Movement, Case)
        .join(Case, Case.id == Movement.case_id)
        .filter(Movement.created_at >= start_utc, Movement.created_at < end_utc)
        .order_by(Movement.created_at.desc(), Movement.id.desc())
        .limit(limit)
        .all()
    )

    movement_ids = [movement.id for movement, _case in rows]
    case_ids = list({case.id for _movement, case in rows})
    client_ruts = _client_ruts_by_case(db, case_ids)

    documents: dict[int, Document] = {}
    if movement_ids:
        for document in (
            db.query(Document)
            .filter(Document.movement_id.in_(movement_ids))
            .order_by(Document.movement_id, Document.id)
            .all()
        ):
            documents.setdefault(document.movement_id, document)

    items = []
    for movement, case in rows:
        document = documents.get(movement.id)
        items.append(
            SysgalMovimientoItem(
                movimiento_id=movement.id,
                fecha_movimiento=movement.movement_date,
                encontrado_at=movement.created_at,
                etapa=movement.stage,
                tramite=movement.procedure,
                descripcion=movement.description,
                folio=movement.folio,
                causa_id=case.id,
                rol=case.rol,
                caratulado=f"{case.plaintiff or ''}/{case.defendant or ''}",
                rut_cliente=client_ruts.get(case.id),
                documento_url=(
                    movement.document_url
                    or (document.pjud_url if document is not None else None)
                ),
                documento_ruta=document.gcs_path if document is not None else None,
            )
        )

    return SysgalMovimientosResponse(
        fecha_deteccion=target,
        total=len(items),
        limit=limit,
        items=items,
    )
