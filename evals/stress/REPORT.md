# Stress test del CAG: reporte

> Mapa empírico de dónde se rompe este CAG. Se completa a mano después de
> correr `evals/stress/run.py` contra el LLM real. Los `_xxx_` se reemplazan
> con los números del CSV (`evals/stress/results.csv`, no se commitea).
> Plantilla basada en `evals/stress/REPORT.md` del curso.

**Parámetros de la corrida:**

| Parámetro | Valor |
|---|---|
| Modelo | `anthropic/claude-haiku-4-5` (`LLM_MODEL`) |
| Prompt | `v3` |
| Escenarios | _growing, pivot, contradiction_ |
| Tamaños de adjunto (KB) | _0, 20_ |
| Repeticiones por celda | _1_ |
| Presupuesto de latencia | 8000 ms |
| Presupuesto de costo por turno | USD 0,02 |
| Ventana (`SESSION_MAX_TURNS`) | 6 turnos |
| Filas en `results.csv` | _<n>_ |
| Duración de la corrida | _<minutos>_ |
| Gasto total | USD _<x.xx>_ |

> Tamaños de 50 y 100 KB: nuestro tope por adjunto es `ATTACHMENT_MAX_CHARS =
> 20000` (el curso usa 60000), así que llegan al modelo truncados al mismo
> largo que el de 20 KB. Esperá `attachments_total_chars` ≈ 20000 en esas
> celdas: miden el régimen truncado, no el literal.

---

## 1. Tabla resumen

Una fila por `(escenario, tamaño)`, tal como la imprime el runner al terminar.

| Escenario | KB | n | P50 ms | P95 ms | Total USD | Drift pass % |
|---|--:|--:|--:|--:|--:|--:|
| growing | 0 | _xx_ | _xxx_ | _xxx_ | _x.xx_ | _xx.x_ |
| growing | 20 | _xx_ | _xxx_ | _xxx_ | _x.xx_ | _xx.x_ |
| pivot | 0 | _xx_ | _xxx_ | _xxx_ | _x.xx_ | _xx.x_ |
| pivot | 20 | _xx_ | _xxx_ | _xxx_ | _x.xx_ | _xx.x_ |
| contradiction | 0 | _xx_ | _xxx_ | _xxx_ | _x.xx_ | _xx.x_ |
| contradiction | 20 | _xx_ | _xxx_ | _xxx_ | _x.xx_ | _xx.x_ |

---

## 2. Tres curvas (como tablas)

### 2a. Latencia frente al tamaño del contexto

`tokens_in` es el mejor proxy de cuánto contexto se tragó el modelo en el
turno (system + historial + turno + texto de adjuntos). Agrupar por
`tokens_in` y tomar la mediana de `latency_ms`.

| `tokens_in` | Latencia mediana (ms) | n |
|---|--:|--:|
| 0–2.999 | _xxx_ | _x_ |
| 3.000–6.999 | _xxx_ | _x_ |
| 7.000–14.999 | _xxx_ | _x_ |
| 15.000+ | _xxx_ | _x_ |

### 2b. Costo acumulado por turno (tamaño 0)

Suma acumulada de `cost_usd` por escenario.

| Turno | growing USD | pivot USD | contradiction USD |
|--:|--:|--:|--:|
| 1 | _x.xx_ | _x.xx_ | _x.xx_ |
| 5 | _x.xx_ | _x.xx_ | _x.xx_ |
| 10 | _x.xx_ | _x.xx_ | _x.xx_ |
| 15 | _x.xx_ | _x.xx_ | _x.xx_ |
| 20 | _x.xx_ | _x.xx_ | _x.xx_ |

### 2c. Drift de memoria por turno (tamaño 0)

En el turno N ≥ 2, ¿el dato del turno 1 (`tracked_fact`) sigue en la sesión?

| Turno N | Drift pass (%) | n |
|--:|--:|--:|
| 2 | _xx.x_ | _x_ |
| 5 | _xx.x_ | _x_ |
| 10 | _xx.x_ | _x_ |
| 15 | _xx.x_ | _x_ |
| 20 | _xx.x_ | _x_ |

---

## 3. Lectura: dónde empieza a romperse el CAG

**Párrafo 1: el modo de falla dominante** (~5 líneas, anclado a los números).

> _Ejemplo de lo que se espera: «El project_name del turno 1 sobrevive el
> 100% de las veces hasta el turno ~N porque … , pero cae a ~M% en el turno
> 20 del escenario contradiction porque el resumen reescribe …»._

**Párrafo 2: qué implica para la decisión de RAG** (~5 líneas).

> _Ejemplo: «Si RAG recupera ≤ 2.000 tokens del adjunto por turno, la curva
> de latencia se aplana al nivel de la celda de 0 KB (~Y ms) y el costo por
> turno baja ~Z veces…»._

---

## 4. Cuatro afirmaciones para defender

1. Mi CAG empieza a degradarse entre los turnos ___ y ___ porque ___.
2. El costo por turno crece con O(___) porque cada turno vuelve a inyectar ___.
3. El cuello de botella dominante de latencia es ___ porque ___.
4. Para recortar el contexto un 50% sin perder recall, atacaría primero ___
   porque mis datos muestran que aporta ___.

### ¿Qué restricción del CAG se rompió primero?

| # | Restricción rota | Número | Escenario |
|--:|---|---|---|
| 1 | _ventana de contexto / costo / latencia / lost in the middle_ | | |
| 2 | | | |
| 3 | | | |
| 4 | | | |

---

## 5. Reproducibilidad

```bash
# PDFs de prueba (deterministas; no se commitean).
uv run python -m evals.stress.fixtures.build_pdfs

# Prueba rápida (~USD 0,30): 1 escenario, sin adjuntos, 1 repetición.
uv run python -m evals.stress.run --scenarios growing --attachment-sizes 0 --repeats 1

# Corrida reducida (~USD 2-3): 3 escenarios × 0 y 20 KB × 1 repetición.
uv run python -m evals.stress.run --attachment-sizes 0,20 --repeats 1

# Matriz completa del curso (~USD 18-25): subí el tope de gasto.
uv run python -m evals.stress.run --repeats 3 --max-total-usd 30
```
