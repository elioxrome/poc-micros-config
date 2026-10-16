# micros-config

Fuente de la verdad de la configuración de despliegue de los microservicios, por entorno.

Este repo **solo contiene datos**. Quien los lee y los transforma es
[`micros-config-service`](../micros-config-service): una API que, a partir de estos ficheros,
sirve dos cosas distintas:

| Salida | Para quién | Se construye con |
|---|---|---|
| JSON de despliegue | Tower / AAP (extra_vars) | `environments/<env>.yaml` + `microservices/<micro>/<env>/deploy.yaml` + `env` + `action` + `values_version` |
| `values.yaml` | Helm (`helm upgrade -f`) | `microservices/<micro>/values.yaml` + `microservices/<micro>/<env>/values.yaml` |

## Estructura

```
.github/
  CODEOWNERS                 # quién aprueba qué (pro protegido)
  pull_request_template.md
  workflows/validate.yml     # pre-commit en cada PR y push a main
environments/                # común a todos los micros de cada entorno
  int.yaml · pre.yaml · pro.yaml
microservices/
  <micro>/
    service.yaml             # metadatos: name, description, owner, domain, stack
    values.yaml              # values base, comunes a todos los entornos
    <env>/
      deploy.yaml            # descriptor del micro en ese entorno
      values.yaml            # solo diferencias respecto a la base (opcional)
schemas/                     # un schema por tipo de fichero
scripts/validate.py          # schemas + merge; lo ejecutan pre-commit y el CI
```

- Los entornos que existen son los ficheros de `environments/`.
- Un micro se despliega en un entorno **si existe** `microservices/<micro>/<env>/deploy.yaml`.
- El **entorno** sale de la ruta: no se escribe en ningún fichero.
- El stack (`java`, `python`…) es un dato de `service.yaml`, no una carpeta: así la ruta
  de un micro no cambia si cambia su stack y su nombre es único en todo el repo.
- Nombres de micro y entorno: `[a-z0-9-]`.

## Cómo se combinan los ficheros

**Descriptor (JSON de Tower): merge superficial.** Las claves de `deploy.yaml` pisan a las
de `environments/<env>.yaml`. Las listas (p. ej. `kubernetes`) se sustituyen enteras.

**Values de Helm: merge profundo, igual que `helm -f base -f overlay`.** Los mapas se
fusionan recursivamente; listas y escalares se sustituyen; una clave a `null` en el
overlay borra la de la base.

```yaml
# values.yaml (base)          # pro/values.yaml              # resultado en pro
resources:                    resources:                     resources:
  requests: {cpu: 100m}         limits: {cpu: "1"}             requests: {cpu: 100m}
  limits:   {cpu: 500m}                                        limits:   {cpu: "1"}
```

## values_version

- **Por defecto** la API lo calcula: hash corto del último commit que tocó la base o el
  overlay de ese micro. Cambiar otro micro no le cambia la versión; cambiar la base sí
  cambia la de todos sus entornos (correcto: cambia lo que se despliega).
- **Modo transición**: si `deploy.yaml` define `values_version`, la API lo respeta. Sirve
  mientras el playbook siga descargando los values de Artifactory. Cuando Ansible pida
  los values a la API (`/values.yaml?version=<values_version>`), se borra de los `deploy.yaml`.

## Cómo se hace un cambio

1. Una vez por clon: `pip install pre-commit && pre-commit install`.
2. Rama + edición del YAML. Al hacer commit se validan formato (yamllint), schemas y merge.
3. PR. El CI ejecuta los mismos hooks (`pre-commit run --all-files`).
4. Cambios en `pro` necesitan aprobación de los CODEOWNERS.
5. Merge a `main` → la API se sincroniza (webhook o `POST /api/v1/sync`).

Sin pre-commit: `pip install -r scripts/requirements.txt && python scripts/validate.py`.

## Reglas

- **Nunca secretos.** Solo referencias a rutas de Vault (p. ej. `vault.secretPath`).
- Subir el chart de todo un entorno = cambiar `chart_version` en `environments/<env>.yaml`.
- Un micro desfasado se fija con `chart_version` en su `deploy.yaml`, con un comentario del motivo.

## Añadir un micro

```bash
mkdir -p microservices/ms-nuevo/pre
cat > microservices/ms-nuevo/service.yaml <<'EOF'
name: ms-nuevo
description: Qué hace
owner: team-xxx
domain: xxx
stack: java
EOF
cp microservices/ms-demo-1/values.yaml microservices/ms-nuevo/values.yaml
cat > microservices/ms-nuevo/pre/deploy.yaml <<'EOF'
app_name: ms-nuevo
namespace: pre-ns-xxx
release_name: ms-nuevo
EOF
python scripts/validate.py
```

## Configuración de GitHub (una vez)

En *Settings → Branches → main*: exigir PR, que pase el check `validate` y
**Require review from Code Owners**. Sin esto último, `CODEOWNERS` solo sugiere revisores.
