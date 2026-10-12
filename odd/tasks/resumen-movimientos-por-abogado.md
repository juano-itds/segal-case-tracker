# Un resumen de movimientos por abogado, en vez de un correo por movimiento

## Objetivo

Que un abogado reciba UN correo con sus movimientos nuevos agrupados, en vez
de uno por movimiento.

## Problema

Medido 2026-10-12: de 630 correos del día, **624 son `new_movement`** — uno
por cada movimiento nuevo.

| abogado | correos en un día |
|---|---|
| Melissa Acevedo | **204** |
| Rodrigo Campos | **191** |
| Carla Lavín | 95 |

Nadie lee 204 correos. Hoy Melissa está igual de desinformada que si no le
mandáramos nada, solo que con el buzón lleno.

Y cuesta: a 7,23 s por correo (medido tras reusar la conexión SMTP, PR #335),
el bucket `email` se lleva el **32% del ciclo de scraping** — tiempo que no
se usa en traer movimientos, contra el techo de ~60 min por cuenta de PJUD.

## La contradicción que lo explica

`ACTIONABLE_ALERT_TYPES` (`app/api/v1/alerts.py`) **no incluye
`new_movement`**. El sistema ya decidió que un movimiento nuevo NO requiere
atención: está fuera del feed "Requiere atención" de Novedades y fuera del
resumen diario.

Y aun así se manda por correo, uno por uno. No hay una decisión detrás: hay
dos mecanismos que crecieron por separado y se contradicen.

## Restricciones que mandan sobre el diseño

1. **Una alerta accionable NO puede esperar al resumen.** Un plazo fatal, un
   semáforo en rojo o un cambio de estado se siguen mandando en el momento.
   Agrupar eso sería cambiar un problema de ruido por uno de seguridad.
2. **Los abogados no tienen login** (sin `password_hash`). El correo es su
   único canal: apagar los avisos de movimiento sin reemplazo los deja
   ciegos. Por eso es un resumen, no un silencio.
3. **Los webhooks siguen disparando por movimiento.** Son consumidores
   máquina, no personas; el ruido no les importa y agruparlos rompería a
   quien los consuma.
4. **Las alertas se siguen persistiendo todas.** Esto cambia el correo,
   nunca el dato.

## Alcance

Un resumen **por abogado y por lote de sync** (no por día). El lote ya es la
unidad natural: `detect_and_sync_movements` corre por abogado y ya tiene un
cierre donde colgar el envío. Con el intervalo de 2h eso da como mucho unos
pocos correos diarios por abogado, contra 204.

Fuera de alcance: agendar el resumen diario de `send_daily_calendars.py`
(existe y nunca se agendó — es otro trabajo), y tocar `NOTIFY_MAX`.

## Modo TDD

Habilitado. `poetry run pytest -m "not integration" -n 4`.

## Tareas

- [x] **T1** — Acumular los `new_movement` por abogado durante el lote y
      emitir UN correo al cierre, reusando el estilo de render de
      `daily_agenda.py`. Las accionables y los webhooks siguen inmediatos.
      Ruta: delegada.
      Checks: suite completa + verificación por mutación. HECHOS.
      Commit: `701d312`.
- [x] **T2** — Commitear el `email_sent` del resumen en vez de solo
      flushearlo. Defecto encontrado en la verificación, no en la
      implementación. Ruta: inline (un archivo, cambio entendido).
      Checks: test RED→GREEN + suite completa + mutación propia.
      Commit: `4e7c7f0`.

## Progreso

**T1 — hecho** (`701d312`, delegado).

**T2 — hecho** (`4e7c7f0`, inline).

### Verificación independiente (orquestador, no el reporte del subagente)

**Suite completa.** 2923 passed, 1 xfailed, **25 failed**. Esos 25 son
PREEXISTENTES: corrí la suite completa en un worktree sobre `9fc6638` (el
commit padre, #335 ya mergeado) y salen los mismos 25, uno por uno. Son todos
de logging/caplog y pasan en aislado — contaminación entre tests, trabajo
aparte.

**Mutaciones propias** (script independiente del que usó el subagente,
`scratchpad/my_mut.py`): 6/6 muertas.

| Mutación | La mata |
|---|---|
| Volver a un correo por movimiento | 19 tests, entre ellos `test_many_movements_of_one_lawyer_send_exactly_one_email` |
| Diferir la alerta de PLAZO (restricción 1) | `test_deadline_alert_emails_immediately_and_is_not_queued` |
| Marcar `email_sent` ANTES del envío (restricción 3) | `test_failed_digest_does_not_mark_and_does_not_raise` + 2 |
| `close()` deja de flushear | `test_close_flushes_so_forgetting_flush_never_loses_emails` + 3 |
| Diferir los WEBHOOKS (restricción 2) | `test_webhook_fires_per_movement_before_any_flush` + 1 |
| Dejar escapar un fallo del resumen (restricción 4) | `test_digest_failure_does_not_break_sync_and_alerts_stay_unmarked` + 3 |

**Defecto encontrado leyendo el código, no por los tests** (de ahí T2): el
resumen marcaba `email_sent` con `self.db.flush()`. Pero el flush del resumen
ocurre en el CIERRE del lote, pasado el último commit del camino de sync
(`sync_via_consulta` no tiene ninguno después del `close()`). Una marca solo
flusheada muere con la sesión: el correo salía y la base seguía diciendo que
no. En esta tabla `email_sent=false` significa "no se envía deliberadamente",
no "pendiente", así que la marca perdida no es un dato faltante sino uno
falso. Antes esto no pasaba porque el envío iba dentro de `sync_movements`,
que commitea al final de la misma llamada.

Reproducido con un test que hace `db.rollback()` después del cierre del lote
(RED: `[False, False, False]`), arreglado con `commit()` + `rollback()` ante
fallo, y la mutación inversa (volver a `flush()`) mata el test nuevo.

### Pendiente

Push y PR: no hechos, esperan decisión de Marcelo.
