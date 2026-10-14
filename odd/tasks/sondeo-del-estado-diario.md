# Sondeo del estado diario (solo observación)

**Esto es un sondeo, no una feature.** No cambia el comportamiento del
scraping: no toca la rotación, ni qué causas se visitan, ni el orden, ni
ninguna alerta. Observa y registra. Si el endpoint está muerto, lo sabemos sin
haber construido nada encima.

## Por qué

Medido el 2026-10-13/14: la demora entre que un movimiento ocurre en el PJUD y
que lo vemos es de **17 días de mediana, 99 de p99**. Todas las palancas de
scraping están medidas y cerradas; el techo era p99 12-19 días. Un plazo fatal
se cuenta en días hábiles.

El 2026-10-14 Carla reenvió un correo real de `no-responder@pjud.cl`: la OJV
manda **todos los días** la lista de causas notificadas por estado diario, con
un Excel adjunto. Leído el Excel del 13/10 (un solo abogado, competencia
civil):

```
37  causas notificadas ese día
34  las tenemos en la base
 3  NO las tenemos
mediana de días desde que las miramos:   8
máximo:                                 96
```

**La rotación se iba a enterar de esas 37 en una mediana de 8 días, y de una
de ellas en 96.** El feed las nombra todas, gratis, a las 4 de la mañana.

Columnas del Excel civil: `Rol | Tribunal | Fecha Ingreso | Caratulado`. Trae
ROL **y tribunal**, que es justo la identidad que hoy nos falta.

## Qué se sondea

Hay un endpoint capturado de una sesión viva el **2026-07-06**, nunca usado:

```
POST /miEstadoDiario/civil/estadoDiarioFechaCivil.php
     fechaEstDiaCiv=DD/MM/YYYY
→ tabla HTML con  Rol · Fecha · Caratulado · Tribunal
   <tbody> vacío = sin actividad esa fecha (ej. fines de semana)
```

Tiene tres meses y nunca se verificó. El sondeo existe para eso.

## Qué se construye

Un POST por abogado por ciclo, con la fecha hábil anterior, **reusando la
sesión que el sync ya tiene abierta**. Se loguea:

- cuántas filas devolvió,
- cuántos de esos ROL están en nuestra base y cuántos no,
- la mediana y el máximo de días desde que miramos cada uno.

Nada más. Ni se visitan esas causas, ni se crean, ni se reordena la rotación.

## Restricciones (si se violan, se rechaza)

1. **No cambia el comportamiento del sync.** Ni rotación, ni orden, ni alertas,
   ni qué se visita.
2. **No abre sesión ni contexto nuevo.** Reusa la página autenticada que el
   ciclo ya tiene. Abrir una segunda sesión rompe la regla de una por IP.
3. **Un request por abogado por ciclo, no más**, y respetando los delays
   anti-detección existentes. Acelerar es la forma de quedarnos sin scraping.
4. **Detrás de un flag** (`ESTADO_DIARIO_PROBE_ENABLED`, default OFF) para
   poder apagarlo sin desplegar.
5. **Un fallo del sondeo nunca rompe el ciclo.** Y distinguir en el log tres
   casos que no son lo mismo: sin actividad esa fecha (tbody vacío, esperable),
   endpoint muerto o cambiado, y rebote al login o desafío de Shape.

## El riesgo, dicho de frente

Es un endpoint que nunca llamamos. Para Shape es una firma de request nueva.
Se mitiga llamándolo con el mismo patrón AJAX en página que ya usa el listado
—que pasa hoy— y no con un POST crudo. Aun así, si el log muestra rebote al
login o desafío, **se apaga el flag y se reporta**: no se insiste.

## Modo TDD

Habilitado. Runner: el venv de Poetry.

## Tareas

- [x] **T1** — El sondeo, detrás del flag, con su línea de log y sus tests.

## Progreso

T1 hecho (RED observado: ImportError del modulo; luego GREEN).
- Modulo: app/services/estado_diario_probe.py. Gancho: sync_lawyer_cases, DESPUES de la fase de detalle
  y de la actualizacion de sync_history; se omite si el lote se corto (stop_reason).
- Flag ESTADO_DIARIO_PROBE_ENABLED (default False). Pagina: scraper.live_page() (nunca crea una).
- Lunes: pide el viernes (previous_business_day con include_saturday=False; sabado cuenta como habil
  en business_days pero el estado diario no se publica en sabado).
- Sesgo conocido: la edad se lee despues del detalle, asi que las causas del lote de este ciclo
  ya figuran como recien miradas.
