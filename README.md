# cuco-data

Robot gratis (GitHub Actions) que junta datos para **Cuco Fantasy** dos veces al día y los guarda en `data/experts.json`:

| Clave | Fuente | Qué es |
|---|---|---|
| `hh` | Hashtag Basketball | ranking 9-cat de la temporada pasada (todos, ~580) |
| `hhp` | Hashtag Basketball | proyecciones 2026-27 por categoría (`proj`, solo top 30: el resto es Premium) |
| `fp` | FantasyPros | ranking de consenso de expertos |
| `fpa` | FantasyPros | ADP promedio de ESPN/Yahoo/CBS |
| `bblast` / `bbcur` | Basketball-Reference | promedios reales temporada pasada / actual (`last`, `cur`) |

Si una fuente falla, se queda la última lectura buena con su fecha real.
Correrlo a mano: pestaña **Actions → Datos Cuco Fantasy → Run workflow**.
