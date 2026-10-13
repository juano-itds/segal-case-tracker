# Menos viajes a la base

## El problema, medido

```
ida y vuelta por query (estación en Chile → Cloud SQL en us-central1)  137,69 ms
ejecución real en el servidor                                            0,07 ms
→ el 99,95 % del costo de cada query es el viaje, no el trabajo
```

Por causa, medido sobre 1.130 causas del log del 2026-10-13:

```
CAPTURA → FIN (parseo + sync_movements)     2,0 s   (21 round-trips, contados)
FIN → siguiente CAPTURA (cola post-fetch)  11,0 s   (60 round-trips, contados)
                                     total 13,0 s
delay anti-detección deliberado ~2 s = 15 %
fetch al PJUD              ~0,5-1 s  = menos del 8 %
```

El cuello **no es el PJUD, ni los delays, ni la política de rotación**: es que
hablamos con una base en Iowa de a una fila por vez.

## El alcance de este PR

Solo lo medido y de riesgo acotado. Lo demás queda fuera a propósito.

| | Ahorro medido | |
|---|---|---|
| A · Dejar de reescribir `Movement.document_token` | hasta 30 RT (~4 s) | el JWT rota en cada sync, así que **toda** fila con documento se reescribe siempre — y **nadie lee esa columna** |
| B · `expire_on_commit=False` en el trabajo por causa | 3-4 RT | el commit expira los objetos y fuerza SELECTs de refresco |
| C · Sacar `doc_token` de los `updatable_fields` del escrito | N RT | mismo JWT rotativo, mismo caso |
| D · Memoizar `_firm_side` en el recálculo de plazos | 1-2 RT | se llama dos veces por recálculo |
| E · Último movimiento en Python para la matriz | 1 RT | la lista ya está cargada |

Verificado antes de escribir una línea: `Movement.document_token` se escribe en
`document_persistence.py:192` y **no lo lee nadie** — ni backend, ni scripts, ni
el frontend. Los hits de `document_token_pattern` son otra cosa (regex del
scraper).

## Fuera de alcance, y por qué

- **El recálculo de plazos en causas sin movimientos nuevos.** El semáforo
  depende de la fecha de HOY: una causa puede ponerse amarilla o roja sin que
  se mueva nada. Saltearlo apagaría alertas en silencio.
- **El límite del commit después de `sync_movements`.** Garantiza que los
  movimientos persisten aunque falle la fase de entidades o documentos.
- **El savepoint de documentos.** Aísla fallos para que un documento no tire
  abajo los movimientos.
- **Documentos a `bulk_insert_mappings`.** El downloader necesita los objetos
  ORM con su id y su status.
- **El prefetch global por `pjud_token_hash`.** El índice es global a propósito.
- **El orden de despacho y el `NotifyBudget`.** Solo se toca la persistencia
  alrededor, nunca el orden.

## Restricciones

1. **No se pierde ni se duplica nada**: movimientos, alertas, documentos ni
   entidades. Ningún valor persistido cambia, salvo los dos tokens que dejan
   de reescribirse.
2. **El comportamiento de notificaciones queda intacto** (PRs #336, #337,
   #338): resumen por abogado, webhooks inmediatos, accionables inmediatas,
   `email_sent` solo sobre lo efectivamente enviado.
3. **El ahorro se MIDE, no se asume**: contar round-trips antes y después con
   el mismo listener, y reportar los dos números.
4. **Un fallo de cualquiera de estos cambios no rompe el ciclo de sync.**

## Modo TDD

Habilitado. Runner: el venv de Poetry.

## Tareas

- [x] **T1** — A, B, D, E con tests y conteo de round-trips (C omitido: ver Progreso).

## Progreso

- Medido (listener `before_cursor_execute` + commit, UPDATE/DELETE executemany pesados por fila porque psycopg2 los manda uno a uno):
  sin movimientos nuevos 68 -> 56 RT; 5 movimientos nuevos 80 -> 72 RT.
- A: `document_token` solo se escribe si la columna esta vacia. B: `expire_on_commit` apagado alrededor de `_do_fetch`. D: `side` se resuelve una vez y se pasa a `_recompute_decision`. E: ultimo movimiento en Python desde la lista que ya carga el motor de plazos (`LoadedMovements`), orden `(movement_date, id)`.
- C OMITIDO: `CaseEscrito.doc_token` SI se lee: `EscritoResponse.doc_token` (app/api/v1/cases.py:187) lo sirve en el detalle de causa, y `test_escrito_doc_token_refreshed_on_resync` fija el refresco. Ningun consumidor del front lo usa, pero sacarlo es cambio de contrato de API: decision de Marcelo.
