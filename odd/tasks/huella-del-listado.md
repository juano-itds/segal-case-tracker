# Huella del listado: ¿trae el listado una señal de cambio?

**Esto es un EXPERIMENTO, no una feature.** No cambia el comportamiento del
scraping: solo observa y registra. Si el experimento falla, se revierte y no
se pierde nada.

## La pregunta

¿Cambia algo en la fila del listado de "Mis Causas" cuando una causa avanza?

Si la respuesta es sí, el problema de los plazos se da vuelta entero: el
listado ya cubre **todas** las causas en cada ciclo y cuesta solo el 11% del
tiempo, contra el detalle que cuesta 17 s por causa y el 74%. Sabríamos qué
bajar y qué no.

## Por qué vale la pena preguntarlo ahora

Medido el 2026-10-13: la demora entre que un movimiento ocurre en el PJUD y
que lo vemos es de **17 días de mediana, 99 de p99**. Un plazo fatal se cuenta
en días hábiles. Las otras cuatro salidas están medidas y cerradas:

| | |
|---|---|
| Rotación por riesgo | empeora la cola (p99 64 contra 29) |
| Correr 24/7 | descartado por la decisión anti-Shape |
| Podar la cartera | no hay nada seguro que sacar |
| Scrapear más rápido | los delays son anti-detección deliberados |

El techo dentro de la restricción es p99 ≈ 12-19 días. Esta es la única puerta
barata que queda sin abrir.

## El estado del arte, verificado en el código

- El parser del listado (`app/scrapper/pjud/civil.py:186`) lee las 8 columnas
  que hay: `Acciones, RIT, Tribunal, Caratulado, Fecha, Estado, Cuaderno,
  Institución`. **No hay columna de último movimiento.** Eso ya está
  descartado.
- `estado_cuaderno` del listado **sí se usa**, pero se colapsa a tres valores
  en `SyncService._map_status` (`sync_service.py:1397`): `closed` si dice
  archivado/terminado, `suspended` si dice suspendido, `active` para todo lo
  demás. **Las 15.047 causas están en `active`.**
- O sea: el valor mapeado nunca cambia, pero **el crudo no se guarda en
  ninguna parte**. Puede haber señal tirada a la basura y nadie lo verificó.

## Qué se construye

Guardar, por causa, una huella de su fila del listado, y registrar cuándo
cambia. Nada más.

1. **Huella** = `sha256` de las celdas de la fila **excluyendo `case_token`**.
   Ese token es un JWT que rota en cada request: si entra en la huella, todo
   cambia siempre y el experimento no mide nada. Es el detalle que decide si
   esto sirve o es ruido.
2. **Valores crudos** de `estado_cuaderno` y `cuaderno`, para poder ver *qué*
   cambió y no solo *que* cambió.
3. **Una línea de log en cada cambio**, con el valor anterior y el nuevo.

## Restricciones (si se violan, se rechaza)

1. **No cambia el comportamiento del scraping.** No altera la rotación, ni qué
   causas se visitan, ni el orden. Observa y registra.
2. **`case_token` fuera de la huella.** Es un JWT rotativo.
3. **Solo se escribe cuando la huella cambia.** El listado toca ~11.000 causas
   por pasada; un UPDATE incondicional por causa y por pasada es amplificación
   de escritura gratuita contra una base que ya se cae sola.
4. **Un fallo del cálculo de la huella nunca rompe el sync.** Loguear y seguir.

## Cómo se lee el resultado

A los pocos días se cruza, en SQL:

- cuántas causas cambiaron de huella sin que el detalle las visitara;
- de las que estrenaron movimientos nuevos, cuántas habían cambiado la huella
  antes (los verdaderos positivos);
- cuántas cambiaron la huella y no tenían movimientos nuevos (falsos
  positivos).

Si la señal existe y es limpia, la rotación pasa a priorizar por huella
cambiada. Si no, se revierte y queda descartada **con evidencia**, no con una
suposición.

## Modo TDD

Habilitado. Runner: el venv de Poetry
(`.../segal-case-tracker-VujtO8MO-py3.11/bin/python -m pytest`).

## Tareas

- [x] **T1** — Migración 066. HECHO (`a3a1875`).
- [x] **T2** — Huella calculada y persistida en el upsert del listado.
      HECHO (`a3a1875`). Suite completa y 11 mutaciones, todas muertas.

## Progreso

**T1 y T2 — hechos** (`a3a1875`, delegado). Suite: 25 failed (los
preexistentes), 2958 passed. 16 tests nuevos.

### Correcciones del implementador a lo que yo había dado por cierto

1. **`case_token` nunca llega a `ScrapedCase`**: se descarta al armar los
   dicts en `sync_scheduler.py`, `api/v1/sync.py` y `client_sync.py`. La
   restricción 2 se cumple por construcción. El test se escribió contra un
   objeto que sí lo lleva, para que la mutación sea matable.
2. **La restricción 3 solo aplica a las columnas nuevas.** `_upsert_case` ya
   hacía `existing.updated_at = utcnow()` incondicional
   (`sync_service.py:1243`), así que toda causa existente ya recibía un UPDATE
   en cada pasada. La amplificación de escritura que me preocupaba **ya
   existía y no es de este cambio**.
3. Solo se guarda el último cambio: `listing_fingerprint_at` se pisa. El
   historial es el log, no la tabla.

### El hallazgo colateral que vale más que el experimento

La corrección 2 se cruzó con la medición de latencia del mismo día: el
listado toca ~11.000 causas por pasada y **cada una recibe un UPDATE aunque
no haya cambiado nada**. A 137,69 ms de ida y vuelta, eso es tiempo puro de
red por nada. La huella que este experimento introduce es exactamente el
mecanismo para saltarse ese UPDATE.

### Cómo se lee el resultado

Las cuatro queries (A: cambios sin visita del detalle · B: verdaderos
positivos · C: falsos positivos · D: movimientos sin cambio previo de huella)
están en el reporte del implementador. Recall = B/(B+D), precisión = B/(B+C).
`:t0` es el cierre de la primera pasada completa post-deploy.

**Caveat**: B pierde las causas cuya huella cambió dos veces, porque solo
sobrevive el último timestamp. Para una lectura rigurosa hay que cruzar
contra las líneas de log `listing fingerprint changed`.

### Pendiente

Push y PR: no hechos. Verificación independiente del orquestador: pendiente.
