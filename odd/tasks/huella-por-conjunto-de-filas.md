# Huella del listado: hashear el conjunto de filas, no la última

Corrección del experimento del PR #339, con los datos de su primera pasada.

## Lo que mostró la primera pasada (2026-10-14, 09:08)

Nueve cambios de huella sobre 2.613 causas tocadas, todos en los mismos
segundos:

```
3 ×  cuaderno: 'Incidente Abandono del Proc.' -> 'Principal'
6 ×  estado_cuaderno y cuaderno IDÉNTICOS — cambió alguna de las otras 5 celdas
0 ×  estado_cuaderno cambiando  ('Tramitación' -> 'Tramitación' en los nueve)
```

## Problema 1 — la huella mide orden de filas, no avance

PJUD devuelve **más de una fila por causa**: una causa con cuadernos
(Principal, Medida prejudicial, Incidente Abandono) aparece varias veces en
"Mis Causas". El upsert matchea por ROL, así que todas escriben sobre el
mismo registro y la huella queda siendo la de **la fila procesada última**.

Evidencia: `'Incidente Abandono del Proc.' -> 'Principal'`. Una causa no deja
de tener un incidente y vuelve a Principal; procesalmente va para atrás. Como
artefacto de orden, encaja.

**No está probado**: 9 de 2.613 es poco, y la prueba definitiva es la segunda
pasada (si es artefacto, esas mismas causas vuelven a loguear al revés). El
arreglo se hace igual porque **es correcto en los dos mundos**: hashear el
conjunto degrada exactamente al comportamiento actual si hay una sola fila
por ROL.

Y lo importante: que aparezca `Incidente Abandono del Proc.` **no es ruido,
es información procesal real**. Un incidente de abandono es exactamente lo
que un estudio que defiende al ejecutado quiere saber. El listado sí trae
señal; la estábamos hasheando mal.

## Problema 2 — dos tercios de los eventos son indiagnosticables

La línea de log trae antes-y-después solo de `estado_cuaderno` y `cuaderno`.
De las otras cinco celdas solo el valor nuevo. En 6 de los 9 eventos las dos
celdas logueadas eran idénticas, así que **no se puede saber qué cambió**.

## Qué se construye

1. **La huella se calcula sobre el conjunto de filas del ROL**, ordenado y
   deduplicado, de modo que el orden de llegada no importe pero **una fila
   nueva (un cuaderno nuevo) sí registre**.
2. **La línea de log lleva antes y después de las siete celdas**, no de dos.

## Restricciones

1. **Sigue siendo un experimento**: no cambia la rotación, ni qué causas se
   visitan, ni el orden. Observa y registra.
2. **`case_token` sigue fuera de la huella** (JWT rotativo).
3. **Se escribe solo cuando la huella cambia.**
4. **Un fallo nunca rompe el sync.**
5. **Con una sola fila por ROL, el resultado es idéntico al de hoy.** Un test
   lo fija: es lo que hace seguro el cambio sin tener la hipótesis probada.

## Modo TDD

Habilitado. Runner: el venv de Poetry.

## Tareas

- [x] **T1** — Huella por conjunto de filas + log de las siete celdas.

## Progreso

- Ruta: delegada (un escritor, 4+ archivos). TDD estricto, RED observado (ImportError / 2 tests del scheduler) antes de implementar.
- Costura: `sync_cases` agrupa las filas por ROL normalizado sobre TODA la lista, antes del bucle de chunks, y `_upsert_case` recibe el mapa. Con `listing_complete=False` (PartialListingError en el scheduler) no se observa nada.
- Una fila por ROL: el payload es la lista plana de celdas, byte a byte el de #339, así que las huellas ya guardadas siguen siendo válidas.
- Migración 067: una columna `listing_rows_raw` (TEXT, JSON). El log solo no basta: el valor viejo de 5 de las 7 celdas no está guardado en ningún lado (066 guardó 2) y el baseline es silencioso.
- Efecto esperado en el primer pase tras desplegar: cada ROL con 2+ filas loguea un cambio con `rows=?->N`. Eso cuenta los ROL multi-fila y es la medición directa de la hipótesis.
- Commit: ver git log de la rama.
