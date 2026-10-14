# Veredicto de la ratificación: hoy "cumplido" no mira la fecha

## El problema, medido

```
case_deadlines  deadline_type='acreditar_poder_3d'  status='cumplido'    835
  con movimiento de cumplimiento encontrado                              835
  DENTRO del plazo  (fecha <= due_date)                                  565   68 %
  FUERA del plazo   (fecha  > due_date)                                  270   32 %
```

**Una de cada tres ratificaciones marcadas "cumplido" se presentó fuera del
plazo de 3 días.** El motor escribe `status="cumplido"` cuando detecta
cualquier movimiento de cumplimiento posterior a la resolución
(`deadline_engine.py:673,684`), sin comparar contra `due_date`.

## Por qué eso está mal, y no es opinable

La Dirección Jurídica entregó la especificación "Flujo Excepciones
Dilatorias" (2026-10-14). Define:

```
semaforo_ratificacion:  verde / amarillo / rojo / cumplido / vencido
Checklist ítem 1: "Ratificación ingresada DENTRO de los 3 días desde la
                   notificación de la resolución"
```

**"Cumplido" significa dentro del plazo.** La pregunta estaba abierta y el
estudio la contestó.

Y no es solo un problema interno: Sysgal ya consume ese campo como
`ratificacion.cumplida` (`app/api/sysgal/excepciones.py`).

## Qué se construye

Un detector de veredicto para `acreditar_poder_3d`, igual que el que ya existe
para `excepciones_8d`. `DETECTORS` (`deadline_verdict.py:125-127`) hoy tiene
uno solo; todo otro plazo queda "sin determinar" a propósito.

El movimiento de cumplimiento ya se identifica con la regex de
`poder_deadline.py:46-48` (`Acredita Poder|Poder acreditado|Patrocinio y
poder|Delega poder`). Los 835 casos tienen uno, así que el detector es viable.

## Restricciones

1. **No tocar `status`.** Es un contrato externo: Sysgal lo lee. Se agrega el
   `verdict`, que es el campo veraz y ya está separado a propósito de las
   marcas humanas. Cambiar lo que Sysgal ve es otra decisión, del estudio.
2. **Nunca un "cumplido" falso.** Es el peor resultado posible: le dice a un
   abogado que está cubierto cuando no lo está. Ante la duda, el veredicto
   débil (`registro_tardio`) o ninguno.
3. **El margen de publicación aplica, y acá se suma a otra cosa.**
   `DEADLINE_PUBLICATION_MARGIN_DAYS` existe porque PJUD publica ~4 días tarde.
   Además, nuestro `due_date` del poder se cuenta desde la **fecha del
   movimiento** de la resolución (`poder_deadline.py:31-33`), mientras que la
   especificación dice que los 3 días corren desde la **notificación** de esa
   resolución, que es posterior. Los dos sesgos empujan hacia un
   `fuera_de_plazo` falso. El margen tiene que absorber ambos.
4. **Un fallo del veredicto nunca rompe el sync.**

## Fuera de alcance

- **La alerta.** Marcelo pidió "una alerta de cumplido o no". Primero el dato
  veraz: una alerta sobre un campo que miente un 32 % de las veces multiplica
  el daño en vez de ayudar. La alerta va en la rebanada siguiente, y además
  la especificación pide escalamiento (abogado → supervisora → dirección) que
  hoy no existe.
- **Corregir los `status` ya escritos** y lo que ve Sysgal.
- **El plazo contado desde la notificación** en vez de la dictación.

## Modo TDD

Habilitado. Runner: el venv de Poetry.

## Tareas

- [x] **T1** — Detector de veredicto para `acreditar_poder_3d` + el reporte de
      cómo quedan los 835 casos conocidos.

## Progreso

T1 hecha. RED observado (35 fallas) -> GREEN (39 tests nuevos). Suite completa:
25 failed (= linea base de caplog) / 3040 passed.
Ruta: delegated-direct no aplico (un solo agente ejecutor, cambio acotado).

Decisiones:
- El detector exige el movimiento ESTRICTAMENTE posterior a la resolucion
  (`triggered_at`): una demanda suele decir "Acredita Poder" meses antes.
- Margen propio: publicacion (5) + holgura de notificacion (2, NO medida).
- Ausencia = no_cumplido solo pasado el margen (el escrito puede no estar publicado).
- Filas reemplazadas quedan sin veredicto.
