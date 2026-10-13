# Desacoplar el cap de notificaciones del resumen de movimientos

Continuación de `resumen-movimientos-por-abogado.md` (PR #336, mergeado en
`e727560`). Misma feature, segunda rebanada.

## Objetivo

Que el resumen de movimientos diga la verdad sobre cuántos movimientos
nuevos hubo, sin aumentar el volumen de correo ni el de webhooks.

## El problema medido

El resumen ya funciona y el correo dejó de ser el cuello del ciclo:

```
ANTES  (2026-10-12 21:14)  elapsed=4h08m   detail=1h34m (38%)  email=1h45m26s (42%)
DESPUES(2026-10-13 09:41)  elapsed=36m31s  detail=27m20s (75%) email=4s (0%)
```

Pero el primer resumen real salió así:

```
Movement digest sent to lawyer 16: 399 movements
Synced civil for lawyer 16: 1790 cases, 0 new, 1901 new movements
```

399 comunicados de 1901 detectados: **el 21%**. El correo dice "399
movimientos nuevos" y quien lo lee entiende "estos son todos". Quedaron
1502 afuera.

La causa es `NotifyBudget`, creado **por causa** (no por lote) en
`app/services/sync_service.py:2417` y `:3027`, con un valor efectivo de 5
en la estación (el default del código es 25, hay override en su `.env`).
Cada warning lo muestra: `dispatched 5 of 35 notifications`. Con 80 causas
por lote eso da 80 × 5 = 400 despachos, y salieron 399.

Ese cap existía por una razón que el PR #336 ya borró: cada despacho
costaba un correo de 7,23 s. Hoy el correo es uno por abogado por lote,
traiga 5 movimientos o 2000. Lo único que sigue escalando 1:1 con los
movimientos son los webhooks, que son POSTs HTTP reales.

Un solo número gobierna dos costos que dejaron de ser el mismo.

## Distribución medida

Alertas `new_movement` por abogado y lote de 2h, 12 días, 141 lotes (QA):

| | |
|---|---|
| mediana | 40 movimientos |
| p90 | 866 |
| p99 | 1956 |
| máximo | 1958 |
| lotes sobre 25 | 58,9 % |

Caveat: la ventana incluye el backfill de causas nunca revisadas, así que
p90/p99 están inflados contra un régimen estable. La mediana es la cifra
más confiable.

## Qué se construye

1. **Los webhooks conservan el cap tal cual está.** Es un costo real por
   evento y nadie midió que aguante más. Cero cambios de volumen ahí.
2. **El resumen deja de depender del presupuesto.** Cuenta *todos* los
   `new_movement` del lote, incluidos los que hoy el cap descarta por
   completo.
3. **El render se topea en 150 entradas**, agrupadas por causa, y cierra
   con una línea explícita del resto: "y N movimientos más en M causas —
   míralos en la app". En HTML y en texto plano.
4. **El asunto reporta el total verdadero.** Si hubo 1901 movimientos
   nuevos, el asunto dice 1901. El cuerpo muestra 150 y declara el resto.

## Restricciones (si se violan, se rechaza)

1. **El volumen de webhooks no sube.** Sigue topeado por
   `NOTIFY_MAX_PER_SYNC` por causa, exactamente como hoy. Un test lo fija.
2. **`email_sent` se marca SOLO en las alertas efectivamente renderizadas.**
   Las que solo entraron en el conteo de "y N más" quedan en `false`. En
   esta tabla `email_sent=false` significa "no se envía deliberadamente", y
   marcar como enviada una alerta que el abogado nunca vio sería la misma
   mentira que arreglamos en `4e7c7f0`.
3. **Las alertas accionables siguen inmediatas.** `deadline_fatal`,
   `semaforo_rojo`, `status_change` y el resto de `ACTIONABLE_ALERT_TYPES`
   no se agrupan ni se difieren.
4. **Un fallo del resumen nunca rompe el ciclo de sync.** Loguear y seguir,
   nunca `raise`.
5. **La acumulación es acotada en memoria.** No guardar 2000 entradas
   completas: conservar como mucho las 150 que se van a renderizar, más un
   contador de movimientos y un conjunto de `case_id` para los totales.

## Modo TDD

Habilitado. Runner: el venv de Poetry
(`poetry env info --path` → `.../segal-case-tracker-VujtO8MO-py3.11/bin/python -m pytest`).
No hay `python` ni `.venv` en el PATH del repo.

## Tareas

- [x] **T1** — Acumular todos los `new_movement` del lote en el resumen,
      acotado en memoria, sin tocar el presupuesto de webhooks.
      Ruta: delegada (3 archivos de producción + tests).
      Checks: suite completa + verificación por mutación propia. HECHOS.
      Commit: `66af963`.
- [x] **T2** — Render topeado en 150 con la línea del resto, en HTML y en
      texto plano, y asunto con el total verdadero.
      Ruta: delegada, junto con T1.
      Checks: idem, más revisión de copy. HECHOS. Commit: `66af963`.

## Progreso

**T1 y T2 — hechos** (`66af963`, delegado).

### Diseño: el acotamiento de memoria y la honestidad son el mismo mecanismo

`DigestBucket` guarda como máximo `MAX_DIGEST_ENTRIES = 150` entradas, más
`total_movements` y el set `case_ids`. Las entradas descartadas no se
retienen, así que ni siquiera existe el objeto `Alert` sobre el cual marcar
`email_sent` de más: la restricción 2 queda garantizada por construcción, no
por disciplina. Un test lo fija (`test_dropped_entries_release_their_alert`).

El asunto y el saludo llevan `total_movements` y `len(case_ids)` — los
totales verdaderos. El cuerpo renderiza las 150 y, cuando sobró algo, cierra
con la línea del resto.

### Verificación independiente (orquestador)

**Suite completa:** 25 failed, 2940 passed, 1 xfailed. Los 25 son los
preexistentes de siempre (logging/caplog), verificados antes contra el commit
padre; 2940 passed contra 2922 de la línea base = 18 tests nuevos. Ninguna
falla nueva.

Corrección a lo que reportó el implementador: dijo "0 failed, no vi ninguna
de las 25". Corriendo la suite completa sin filtro de marcadores, las 25
están. No cambia el veredicto —el baseline está intacto— pero el reporte no
era exacto.

**Mutaciones propias, 8/8 muertas** (script en el scratchpad,
independiente del suyo):

| Mutación | La mata |
|---|---|
| El camino sobre-presupuesto también dispara webhooks | `test_exhausted_budget_still_feeds_the_digest_but_not_the_webhooks`, `test_zero_budget_means_zero_webhooks_but_a_full_digest` |
| El bucket deja de acotarse y guarda todo | `test_body_renders_at_most_150_entries` + 7 |
| `email_sent` marcado antes del envío | 25 tests |
| Se borra la línea del resto | `test_closing_line_names_movements_and_causas_left_out` + 2 |
| El asunto reporta lo renderizado | `test_subject_reports_the_true_total_not_the_rendered_count` + 3 |
| Las causas omitidas se cuentan en cero | `test_closing_line_names_...`, `test_singular_forms` |
| Un fallo del resumen se escapa | `test_failed_digest_does_not_mark_and_does_not_raise` + 3 |
| Se difiere la alerta de plazo | `test_deadline_alert_emails_immediately_and_is_not_queued` |

**Copy verificado renderizando, no leyendo:**

| Caso | Asunto | Línea de cierre |
|---|---|---|
| 1901 mov / 300 causas | `1901 movimientos nuevos en 300 causas` | y 1751 movimientos más en 150 causas |
| 200 mov / 1 causa | `200 movimientos nuevos en 1 causa` | y 50 movimientos más en causas ya listadas |
| 150 mov / 20 causas | `150 movimientos nuevos en 20 causas` | (sin línea — nada quedó afuera) |
| 151 mov / 20 causas | `151 movimientos nuevos en 20 causas` | y 1 movimiento más en causas ya listadas |
| 1 mov / 1 causa | `1 movimiento nuevo en 1 causa` | (sin línea) |

Singulares correctos, sin voseo, y el borde de "todo el resto vive en causas
ya listadas" resuelto sin decir "en 0 causas".

### Criterio no especificado que quedó definido

Las 150 que se muestran son **las primeras en orden de detección**, no las
más recientes ni las más relevantes. No estaba en el pedido; queda anotado
por si alguna vez se prefiere otro criterio.

### Pendiente

Push y PR: no hechos.
