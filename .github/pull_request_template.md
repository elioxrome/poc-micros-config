## Qué cambia

<!-- Micro(s) y entorno(s) afectados, y por qué. -->

## Checklist

- [ ] `pre-commit run --all-files` pasa en local (o `python scripts/validate.py`).
- [ ] No hay secretos: solo referencias a rutas de Vault.
- [ ] Si fijo `chart_version` en un `deploy.yaml`, hay un comentario con el motivo.
- [ ] Si toca `pro`, el mismo cambio ya está probado en `pre`.
- [ ] Si cambia `microservices/<micro>/values.yaml` (base), he revisado el impacto en **todos** sus entornos.
