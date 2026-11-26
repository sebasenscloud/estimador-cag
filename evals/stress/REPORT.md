# Stress test del CAG: reporte

> Mapa empírico de dónde se rompe este CAG, con los números de
> `evals/stress/run.py` contra el LLM real. Plantilla basada en
> `evals/stress/REPORT.md` del curso.

**Parámetros de la corrida (prueba rápida, 2026-11-26):**

| Parámetro | Valor |
|---|---|
| Modelo | `anthropic/claude-haiku-4-5` (`LLM_MODEL`) |
| Prompt | `v3` |
| Escenarios | `growing` |
| Tamaños de adjunto (KB) | 0 |
| Repeticiones por celda | 1 |
| Presupuesto de latencia | 8000 ms |
| Presupuesto de costo por turno | USD 0,02 |
| Ventana (`SESSION_MAX_TURNS`) | 6 turnos (12 mensajes) |
| Filas en el CSV | 20 (`evals/stress/results_quick.csv`, no se commitea) |
| Duración de la corrida | 6 min 21 s |
| Gasto total | **USD 0,529** (estimado previo: USD 0,30) |

> Alcance: es la prueba rápida, una sola sesión. Sin repeticiones no hay
> varianza medida, y los escenarios `pivot` y `contradiction` y las celdas con
> adjuntos quedan sin correr (corrida reducida pendiente, ~USD 3,5). Las
> conclusiones de abajo son de un escenario y hay que leerlas como hipótesis
> fuertes, no como mediciones cerradas.
>
> Tamaños de 50 y 100 KB: nuestro tope por adjunto es `ATTACHMENT_MAX_CHARS =
> 20000` (el curso usa 60000), así que llegan truncados al largo del de 20 KB.

---

## 1. Tabla resumen

| Escenario | KB | n | P50 ms | P95 ms | Total USD | Drift pass % |
|---|--:|--:|--:|--:|--:|--:|
| growing | 0 | 20 | 10.557 | 16.332 | 0,529 | 100,0 |
| growing | 20 | — | sin correr | | | |
| pivot | 0 / 20 | — | sin correr | | | |
| contradiction | 0 / 20 | — | sin correr | | | |

`latency_ms` es solo la llamada de estimación. El turno completo
(`wall_clock_ms`: estimación + metadata + resumen) tiene P50 **19.823 ms** y
P95 **32.648 ms**. Pasan el presupuesto de latencia 8 de 20 turnos y el de
costo, 7 de 20.

---

## 2. Tres curvas (como tablas)

### 2a. Latencia frente al tamaño del contexto

| `tokens_in` | Latencia mediana de la estimación (ms) | Mediana del turno completo (ms) | n |
|---|--:|--:|--:|
| 0–2.999 | — | — | 0 |
| 3.000–6.999 | 6.774 | 9.165 | 4 |
| 7.000–14.999 | 8.203 | 14.846 | 8 |
| 15.000+ | 12.232 | 26.053 | 8 |

### 2b. Costo acumulado por turno (tamaño 0)

| Turno | growing USD acumulado | Costo de ese turno |
|--:|--:|--:|
| 1 | 0,018 | 0,018 |
| 5 | 0,076 | 0,013 |
| 10 | 0,179 | 0,032 |
| 15 | 0,319 | 0,024 |
| 20 | 0,529 | 0,041 |

Costo medio por turno: **USD 0,015** en los turnos 1-6 (la ventana se llena)
y **USD 0,031** en los turnos 7-20 (con compresión): se duplica.

### 2c. Drift de memoria por turno (tamaño 0)

Dato rastreado: `Nimbus` en `project_metadata.project_name`.

| Turno N | Drift pass (%) | n |
|--:|--:|--:|
| 2 | 100 | 1 |
| 5 | 100 | 1 |
| 10 | 100 | 1 |
| 15 | 100 | 1 |
| 20 | 100 | 1 |

---

## 3. Lectura: dónde empieza a romperse el CAG

**Párrafo 1: el modo de falla dominante.** En este escenario lo que se rompe
primero no es la memoria, es la **latencia**, y después el **costo**. El
nombre del turno 1 sobrevive el 100% de las veces hasta el turno 20, pero no
gracias a las anclas (el turno 1 no tiene ninguna frase de compromiso:
`anchors_count` es 0 en todos los turnos), sino porque el extractor lo
consolidó en `project_metadata` en el turno 1, y la metadata viaja intacta en
cada system prompt. La latencia de la estimación ya supera 8 s en el turno 1
(P50 10,6 s), y el turno completo pasa de ~9 s a ~20-34 s cuando arranca la
compresión (turno 7): cada turno suma una llamada al resumidor y el overhead
fuera de la estimación sube de una mediana de 2,3 s a 12,8 s. Un hallazgo que
no esperábamos: **10 de los 20 turnos tuvieron reintentos de Instructor**
(los saltos de `tokens_in` de ~8.000 a ~16.000-30.000 son el uso acumulado de
2 o 3 intentos, típicamente porque las fases no suman el total). Esos
reintentos explican los picos de latencia y de costo. Además, el resumen
superó el largo pedido (hasta 2.749 caracteres contra `SUMMARY_MAX_CHARS =
1500`): el límite está solo en el prompt y nadie lo hace cumplir.

**Párrafo 2: qué implica para la decisión de RAG.** Con 0 KB de adjuntos, el
contexto se estabiliza en ~7.500-9.000 tokens por turno (la ventana de 12
mensajes y el resumen ponen un techo), así que en una conversación sin
documentos el CAG no rompe la ventana: rompe el SLA de latencia y duplica el
costo por la memoria (resumen + extracción en cada turno). El argumento para
RAG viene del lado del corpus, no del historial: el paso 2.1 muestra que el
Proyecto 2 (250.000 tokens) no entra en la ventana útil. Antes de pasar a RAG
hay dos arreglos baratos que estos datos piden: bajar los reintentos (mejor
instrucción sobre la suma de fases, o calcular los totales en código en lugar
de pedírselos al modelo) y no llamar al resumidor en cada turno (resumir cada
N turnos o con un modelo más rápido). Falta la celda de 20 KB para medir
cuánto empuja un adjunto y si el dato del centro se pierde (lost in the
middle).

---

## 4. Cuatro afirmaciones para defender

1. Mi CAG empieza a degradarse en el **turno 7**, cuando la ventana se llena
   y arranca la compresión: el turno completo pasa de ~9 s a ~20 s porque cada
   turno suma la llamada al resumidor.
2. El costo por turno crece de forma **lineal hasta el turno 6 y después se
   estabiliza en ~USD 0,031** (el doble del inicio), porque cada turno vuelve a
   inyectar la ventana completa de 12 mensajes, el resumen y la metadata, y
   suma la llamada de resumen. Los reintentos lo empujan a USD 0,04-0,054.
3. El cuello de botella dominante de latencia es **la memoria, no el
   modelo**: la estimación sola tiene P50 10,6 s, pero el turno completo 19,8 s.
   La otra mitad es la extracción de metadata y el resumen, en serie.
4. Para recortar el contexto un 50% sin perder recall atacaría primero **los
   reintentos y la ventana de 12 mensajes**: el dato que hay que recordar
   (`project_name`) sobrevive por la metadata, no por el historial literal,
   así que una ventana más corta no perdería ese recall.

### ¿Qué restricción del CAG se rompió primero?

| # | Restricción rota | Número | Escenario |
|--:|---|---|---|
| 1 | Latencia | P50 de la estimación 10,6 s contra un SLA de 8 s, desde el turno 1 | growing, 0 KB |
| 2 | Costo | USD 0,031 por turno desde el turno 7 contra un tope de 0,02 | growing, 0 KB |
| 3 | Ventana de contexto | No se rompió: ~9.000 tokens contra 200.000 | growing, 0 KB |
| 4 | Lost in the middle | No medido: el dato rastreado vive en la metadata, no en el centro del contexto | — |

---

## 5. Reproducibilidad

```bash
# PDFs de prueba (deterministas; no se commitean).
uv run python -m evals.stress.fixtures.build_pdfs

# Prueba rápida (USD 0,53 medidos): 1 escenario, sin adjuntos, 1 repetición.
uv run python -m evals.stress.run --scenarios growing --attachment-sizes 0 --repeats 1

# Corrida reducida (~USD 3,5): 3 escenarios × 0 y 20 KB × 1 repetición.
uv run python -m evals.stress.run --attachment-sizes 0,20 --repeats 1

# Matriz completa del curso (~USD 25-35): subí el tope de gasto.
uv run python -m evals.stress.run --repeats 3 --max-total-usd 40
```
