# Plantillas para dar de alta un micro

El dashboard las usa con **«Partir de → Ejemplo»** (al dar de alta un micro en un entorno o
al crear uno nuevo). Tienen la misma forma que la carpeta de un micro:

```
examples/
├── service.yaml        → microservices/<micro>/service.yaml   (solo micros nuevos)
├── values.yaml         → microservices/<micro>/values.yaml    (solo micros nuevos)
└── env/
    ├── deploy.yaml     → microservices/<micro>/<env>/deploy.yaml
    ├── values.yaml     → microservices/<micro>/<env>/values.yaml
    └── <otro fichero>  → microservices/<micro>/<env>/<otro fichero>
```

Marcadores que se sustituyen al copiar:

| Marcador | Se cambia por | Ejemplo |
|---|---|---|
| `${service}` | nombre del micro | `ms-facturas` |
| `${env}` | entorno | `pre` |
| `${ENV}` | entorno en mayúsculas | `PRE` |

Todo lo demás (claves, orden, comentarios) se copia tal cual: **para cambiar el formato de
las altas, se cambian estos ficheros** por PR. El CI no los valida como configuración
(`validate.py` solo mira `environments/` y `microservices/`), pero sí pasan yamllint: por eso
los marcadores son `${...}` y no `{{...}}`, que en YAML abriría un mapa.
