# Delta-sync: bajar el detalle de lo que se movió, no de lo que tocaba

## Objetivo

Que una causa que se movió en el PJUD se vea en ~1 día en vez de 17 (ver la
interacción con el guard de frescura en los pendientes conocidos).

## El problema medido

| Medición | Valor |
|---|---|
| Demora entre que algo ocurre en el PJUD y lo vemos (mediana) | 17 días |
| La misma demora, p99 | 99 días |
| Vuelta completa a la cartera | ~80 días |
| Causas sin revisar hace más de 30 días | 6.104 de 15.098 (40 %) |
| Causas nunca revisadas en detalle | 2.150 (14 %) |

Un plazo fatal se cuenta en días hábiles. Con 17 días de mediana, el control de
plazos avisa tarde por diseño.

Las cinco palancas de scraping están medidas y **agotadas**: rotar por riesgo
empeora la cola (p99 64 contra 29), correr 24/7 está descartado por la decisión
anti-bloqueo, no hay nada seguro que podar, los retardos son deliberados, y el
techo dentro de esa restricción es de 12 a 19 días.

## La salida

La OJV publica todos los días la lista de causas notificadas por estado diario.
Ya la consumimos en modo observación (`ESTADO_DIARIO_PROBE_ENABLED`) y midió:

```
~343 causas notificadas por dia  /  14 abogados  =  ~25 por abogado
```

En vez de barrer la cartera a ciegas durante 80 días, el feed dice **qué**
bajar.

## Lo que el mapeo corrigió del plan inicial

1. **El `case_token` solo sale de recorrer el listado completo.** Es un JWT en
   memoria; el modelo `Case` ni siquiera tiene columna. El delta-sync **no
   puede saltearse el listado**.
   Pero el listado ya corre en cada ciclo y cuesta el 12 % del tiempo; el
   detalle cuesta el 60 %. La pregunta nunca fue cómo evitar el listado: es
   **cuáles 30 causas reciben el detalle**. Hoy se eligen por antigüedad.
2. **El sondeo corre DESPUÉS de la rotación y descarta la lista de ROL.**
   Invertir ese orden y conservar la lista es el cambio estructural.
3. **No existe presupuesto repartido.** La rotación es un solo orden
   (`last_detail_checked_at ASC NULLS FIRST`) con un solo tope
   (`DETAIL_BATCH_SIZE` = 30). No hay cuotas.
4. `Case.next_review_at` **no** es una cola de scraping: es la fecha de revisión
   manual del abogado y la rotación no la lee.
5. Una causa del feed que no está en la base **se scrapea y se descarta sin
   guardar** (`sync_service.py` ~2562, warning "DB case not found").

## El diseño

El feed prioriza, con **tope y piso**:

```
tope del feed       20 causas del lote      (configurable por entorno)
piso de rotacion    10 garantizadas         (30 - 20)
```

### Por qué el tope no es opcional

```
abogado 1     64 causas notificadas en un dia
abogado 17   190 causas notificadas en un dia
lote                        30 por ciclo
```

Las 190 del abogado 17 se comerían seis ciclos enteros y **congelarían su cola
de causas olvidadas por días**. Es un caso real medido el 2026-10-15, no una
hipótesis.

### El costo, aceptado a conciencia

```
hoy          la rotacion de base recibe 30 por ciclo
con el tope  primer ciclo del dia: 10  ·  siguientes: 30 (el feed ya se consumio)
perdida      ~20 por abogado por dia = ~280 en el estudio
             sobre ~1.000/dia de drenaje = backlog ~27 % mas lento
```

A cambio, las 343 causas que **sabemos** que se movieron pasan de 17 días a
~1 día. El backlog son causas donde probablemente no pasó nada; el feed son
causas donde pasó algo, y entre ellas están los plazos fatales que hoy
descubrimos tarde.

## Restricciones (si se violan, se rechaza)

1. **Arranca apagado, detrás de un flag**, como el sondeo. Con el flag en
   `false` el comportamiento es byte a byte el de hoy.
2. **El tope es configurable por variable de entorno**, nunca una constante: es
   un número que vamos a mover mirando datos reales, sin deploy.
3. **El piso de rotación se respeta siempre.** Aunque el feed traiga 190, la
   rotación por antigüedad recibe sus 10 lugares. Un test lo fija.
4. **No cambia el listado, ni los delays, ni la frecuencia del ciclo.** Solo
   cambia *qué* causas del listado reciben el detalle.
5. **Un fallo del feed nunca rompe el ciclo.** Si el estado diario falla, la
   rotación funciona exactamente como hoy.
6. **No se crean causas desde el feed** en esta rebanada. Las que no están en la
   base quedan registradas como hoy (ya se cuentan y se muestrean).

## Modo TDD

Habilitado. Runner: el venv de Poetry
(`.../segal-case-tracker-VujtO8MO-py3.11/bin/python -m pytest`).
Y `mypy app/core`, que es un paso del CI que ya costó un deploy fallido.

## Tareas

- [x] **T1** — El sondeo devuelve sus filas en vez de descartarlas, y corre
      ANTES de la rotación. Sin cambio de comportamiento: la lista se obtiene y
      se loguea, nadie la usa todavía.
- [x] **T2** — La selección de rotación acepta un conjunto de ROL prioritarios
      con tope y piso. Flag apagado por defecto.
- [ ] **T3** — Medición: comparar la demora antes y después sobre datos reales.
- [ ] **T4** — El sondeo pide el rango de días hábiles desde la última lectura
      exitosa de ese abogado, no solo el día hábil anterior, para que un ciclo
      salteado no deje un agujero silencioso. Hace falta recordar por abogado
      cuándo fue la última lectura exitosa del feed (hoy no se persiste nada del
      sondeo). El rango tiene que tener tope: si un abogado estuvo semanas sin
      sincronizar no se pueden pedir treinta días de una; el tope es necesario y
      el número hay que medirlo. **Conviene que entre antes de prender el flag en
      producción**: si no, el delta-sync tendría agujeros justo en los abogados
      que menos se sincronizan, que son los que más lo necesitan.

## Progreso

Documento creado 2026-10-16 tras el mapeo. Diseño acordado con Marcelo:
tope 20 / piso 10, configurable, detrás de flag.

**T1 y T2 hechas (2026-10-16).** Flags: `DETAIL_FEED_PRIORITY_ENABLED` (false) y
`DETAIL_FEED_MAX_PER_BATCH` (20). Requiere `ESTADO_DIARIO_PROBE_ENABLED=true`:
el sondeo es lo que trae el feed.

Hallazgos al leer el código:

- El sondeo hace `page.goto(/miEstadoDiario.php)`: al correr antes del detalle
  saca la página del panel Mis Causas. El detalle se recupera solo
  (`_ensure_panel_loaded` verifica que exista la función), a costa de recargar
  el panel una vez por abogado. El comentario "navegar no cuesta nada" ya no vale.
- Mover el sondeo antes del detalle elimina su guarda `not stop_reason` (no existe
  todavía). Un lote cortado por Shape ya no impide el sondeo; la guarda real
  está en el detalle. El test que lo fijaba cambió.
- Los números de `ESTADO_DIARIO_PROBE` cambian: antes la antigüedad se medía
  después del detalle (las causas del lote aparecían recién miradas). Ahora se
  mide antes, sin ese sesgo. Es mejora, no regresión.
- Sin un criterio de "consumido" el feed ocuparía 20 plazas todo el día: la causa
  ya visitada desde el inicio del día (UTC) deja de contar como feed. Es un
  agregado al diseño; sin él no se cumple "siguientes ciclos: 30".
- Los hits del feed sobre el tope no se derraman a las plazas de rotación: si
  no, las 190 del abogado 17 seguirían comiéndose la cola.
- Si el abogado casi no tiene causas fuera del feed, el lote queda corto (el tope
  manda sobre el aprovechamiento).
- El cruce es por ROL normalizado, no por tribunal: el mismo ROL en otra corte
  del mismo abogado también se prioriza (bug de identidad conocido).
- `DETAIL_FEED_MAX_PER_BATCH >= DETAIL_BATCH_SIZE` anula el piso (el piso es
  derivado: `batch - tope`).

## Pendientes conocidos, fuera de esta feature

- **El token dura 30 min documentados y el lote puede durar 55.** Nadie midió si
  el servidor lo rechaza. Si lo hace, las últimas causas de cada lote vienen
  fallando desde siempre. Merece su propia medición.
- Las causas del feed que no están en la base se cruzan con el bug de identidad
  `ROL + tribunal` (2.160 causas en un ROL repetido en otra corte).
- El contador `not_in_db` del sondeo mezcla causas faltantes con causas
  pre-2021 que no ingerimos a propósito.
- **El feed solo se lee para los abogados que el ciclo sincroniza.** El sondeo
  corre dentro de `sync_lawyer_cases`, y el guard de frescura (`needs_sync` con
  `MAX_DATA_AGE_HOURS`, `sync_scheduler.py`) saltea a los abogados frescos antes
  de entrar. Medido en producción (2026-10-16): 20 de 25 salteados en un ciclo,
  así que para ellos el feed no se lee. Una causa movida hoy se ve cuando a su
  abogado le toca un ciclo real: del orden de un día, no de horas. Mover el
  sondeo fuera del guard sería otro cambio (más sesiones y carga sobre el PJUD),
  no autorizado en esta feature. El agujero que esto deja entre ciclos (el
  sondeo pide un solo día hábil) se trata en T4.
