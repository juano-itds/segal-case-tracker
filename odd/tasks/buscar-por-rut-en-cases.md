# Buscar por RUT en /cases

## El problema, reportado por Marcelo

Copia un RUT de la plataforma, lo pega en el buscador de Plazos y no trae
nada. Tiene razón, y la pantalla ya lo está diciendo:

```jsx
searchHasRuts = verdictSelected
placeholder = searchHasRuts ? "Buscar por RUT, ROL o carátula…"
                            : "Buscar por ROL o carátula…"
```

La búsqueda por RUT existe desde el PR front #154 (06-10) **pero solo sobre
las tarjetas de veredicto** (CUMPLIDO, REVISAR, DUDOSAS, SIN PRESENTAR), que
se alimentan de `/cases/deadlines/verdicts` — el único endpoint que hoy
devuelve `ruts`.

La lista principal (TOTAL, VENCIDOS, VENCE HOY…) sale de `GET /cases` y
**esas filas no traen RUT**. Y la búsqueda global del header también pega a
`/cases`, cuyo filtro `q` cubre rol, demandante y demandado **por nombre**,
nunca por RUT.

## Qué se construye, todo en `/cases`

1. **`q` matchea también el RUT del demandado.** Arregla el buscador del
   header sin tocar una línea de frontend: ya manda `q`.
2. **Cada fila de `/cases` trae `ruts`**, igual que ya hace el endpoint de
   veredictos. Con eso el filtro en cliente de Plazos funciona en todas las
   tarjetas y el placeholder deja de alternar.

## Solo el demandado, a propósito

El precedente ya está en `deadlines.py:491-505`:

```python
CaseLitigante.participante == "DDO."
```

El estudio defiende al ejecutado: **el demandado es el cliente**, y es el RUT
que el usuario tiene a mano. Incluir al demandante traería la causa cada vez
que alguien busque el RUT del banco que demanda, que es ruido puro.

## El techo, medido — decirlo, no esconderlo

```
causas totales                      15.089
con al menos un RUT de demandado     9.503   (63 %)
con RUT de demandante                9.488
```

**Un tercio de la cartera no tiene el RUT del demandado scrapeado.** Ahí la
búsqueda no va a encontrar nada por más que el código esté bien: falta el
dato, no la función. Son causas sin litigantes scrapeados — el mismo techo de
siempre.

## Restricciones

1. **El RUT se normaliza en los dos lados.** El usuario lo pega como lo ve
   ("12.345.678-9"); la base lo guarda sin puntos. Hay `app/utils/rut.py`
   con `clean_rut` — reusarlo, no escribir una segunda normalización.
2. **`ruts` se arma con UNA query para toda la página**, nunca una por fila.
   `/cases` es el endpoint más usado de la app y ya tuvo N+1 antes.
3. **El filtro por RUT no puede degradar `/cases`.** Medir el plan con
   `EXPLAIN ANALYZE` contra la base real, no con el reloj del cliente: por el
   proxy, 137 ms de latencia tapan cualquier medición desde el cliente.
4. **No cambia lo que hoy devuelve `/cases`** para quien no busca por RUT:
   mismos resultados, mismo orden.

## Fuera de alcance

- El frontend (es otro repo y otro PR): usar `ruts` en el filtro de la lista
  principal de Plazos y dejar de alternar el placeholder.
- Buscar por RUT de demandante.
- Las causas sin litigantes scrapeados.

## Modo TDD

Habilitado. Runner: el venv de Poetry.

## Tareas

- [x] **T1** — `q` por RUT de demandado + `ruts` en las filas de `/cases`.

## Progreso

T1 hecho. RED observado (10 fallas), GREEN 15 tests, suite completa 25 fallas =
baseline. 11 mutantes, todos muertos (2 sobrevivieron y se cerraron con tests).
EXPLAIN ANALYZE (QA, en el servidor): busqueda por RUT ~59 ms vs ~45 ms de la
busqueda de texto actual; texto sin cambios (mismo SQL).
