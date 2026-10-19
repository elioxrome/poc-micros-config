"""Valida la fuente de la verdad antes de que llegue a main.

Qué comprueba:
  1. environments/<env>.yaml: nombre válido, YAML con un mapa en la raíz y
     schemas/environment.schema.json.
  2. microservices/<micro>/service.yaml: existe, cumple schemas/service.schema.json
     y su 'name' coincide con la carpeta (la API identifica al micro por la carpeta).
  3. microservices/<micro>/<env>/: el entorno existe en environments/ y la carpeta
     tiene deploy.json (es lo que indica que el micro se despliega ahí). Un micro sin
     ninguna carpeta de entorno es válido: existe, pero aún no se despliega.
  4. deploy.json suelto contra schemas/deployment.schema.json SIN 'required', para
     señalar el fichero exacto de un typo; y el MERGE environments/<env>.yaml +
     deploy.json con 'required', porque un campo obligatorio puede venir de
     cualquiera de las dos capas. Las plataformas de 'kubernetes' de un deploy.json
     deben estar entre las que declara su entorno: el fichero del entorno es el catálogo.
  5. values.yaml base + overlay del entorno: ambos mapas y fusionables (merge
     profundo, igual que Helm).
  6. Que no haya dos micros con el mismo release_name en el mismo namespace y
     entorno: Helm los trataría como el mismo release y uno pisaría al otro.

Uso:  python scripts/validate.py            (desde cualquier sitio)
Sale con código 1 si hay errores, para que el CI y pre-commit bloqueen el cambio.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent.parent
ENVS_DIR = ROOT / "environments"
SERVICES_DIR = ROOT / "microservices"
SCHEMAS_DIR = ROOT / "schemas"
# Nombre del descriptor de un micro en un entorno. Debe coincidir con el que usa la API
# (CFG_LAYOUT_DEPLOY_FILE). Es JSON: no admite comentarios (el motivo va en el PR).
DEPLOY_FILE = "deploy.json"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def load_schemas() -> tuple[dict[str, dict[str, Any]], Registry]:
    """Carga todos los schemas y un registro para resolver los $ref entre ellos.

    environment.schema.json reutiliza las propiedades de deployment.schema.json
    por $ref, así una regla (p. ej. el patrón de chart_version) vive en un solo sitio.
    """
    schemas = {
        p.name: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(SCHEMAS_DIR.glob("*.schema.json"))
    }
    registry: Registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in schemas.values()
    )
    return schemas, registry


def load_map(path: Path) -> dict[str, Any]:
    """Lee un fichero según su extensión: .json como JSON; el resto, como YAML."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(text) if text.strip() else {}
    else:
        data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise TypeError("la raíz debe ser un mapa (clave: valor)")
    return data


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge con la semántica de 'helm -f base -f overlay'.

    Mapas: se fusionan recursivamente. Listas y escalares: se sustituyen.
    Una clave a null en el overlay BORRA la de la base (Helm hace lo mismo), que es
    la única forma de quitar en un entorno algo que define la base.
    """
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def schema_errors(validator: Draft202012Validator, data: dict[str, Any], where: str) -> list[str]:
    out = []
    for err in validator.iter_errors(data):
        field = ".".join(str(p) for p in err.absolute_path) or "(raíz)"
        out.append(f"{where}: {field}: {err.message}")
    return out


def platform_errors(
    env_data: dict[str, Any], deploy: dict[str, Any], where: str, env: str
) -> list[str]:
    """Un micro solo puede usar plataformas que su entorno declara.

    No hay lista de plataformas en ningún schema: la de cada entorno está en su
    environments/<env>.yaml. Así dar de alta una (p. ej. m3) es añadirla al entorno, y un
    typo o un clúster que ese entorno no tiene se para aquí. Misma regla que la API
    (DescriptorComposer). Si el entorno no declara ninguna, decide el micro.
    """
    declared, own = env_data.get("kubernetes"), deploy.get("kubernetes")
    if not isinstance(declared, list) or not isinstance(own, list):
        return []
    unknown = [p for p in own if p not in declared]
    if not unknown:
        return []
    return [
        f"{where}: kubernetes: {', '.join(map(str, unknown))} no es una plataforma de {env} "
        f"(el entorno declara: {', '.join(map(str, declared))})"
    ]


def main() -> int:
    schemas, registry = load_schemas()

    def validator(name: str, *, required: bool = True) -> Draft202012Validator:
        schema = schemas[name]
        if not required:
            schema = {k: v for k, v in schema.items() if k != "required"}
        return Draft202012Validator(schema, registry=registry)

    env_validator = validator("environment.schema.json")
    service_validator = validator("service.schema.json")
    deploy_part_validator = validator("deployment.schema.json", required=False)
    deploy_full_validator = validator("deployment.schema.json")

    errors: list[str] = []
    checked = 0

    # 1. Entornos ---------------------------------------------------------------
    environments: dict[str, dict[str, Any]] = {}
    for env_file in sorted(ENVS_DIR.glob("*.yaml")):
        env, where = env_file.stem, rel(env_file)
        if not NAME_RE.match(env):
            errors.append(f"{where}: nombre de entorno inválido")
            continue
        try:
            env_data = load_map(env_file)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{where}: YAML inválido: {exc}")
            continue
        errors += schema_errors(env_validator, env_data, where)
        environments[env] = env_data

    if not environments:
        errors.append("environments/: no hay ningún entorno definido")

    # (env, namespace, release_name) -> micro, para detectar colisiones de release.
    releases: dict[tuple[str, str, str], str] = {}

    # 2-6. Micros ---------------------------------------------------------------
    for svc_dir in sorted(p for p in SERVICES_DIR.iterdir() if p.is_dir()):
        svc, where = svc_dir.name, rel(svc_dir)
        if not NAME_RE.match(svc):
            errors.append(f"{where}: nombre de micro inválido")
            continue

        try:
            service = load_map(svc_dir / "service.yaml")
        except FileNotFoundError:
            errors.append(f"{where}: falta service.yaml")
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{where}/service.yaml: YAML inválido: {exc}")
            continue
        errors += schema_errors(service_validator, service, f"{where}/service.yaml")
        if service.get("name") != svc:
            errors.append(
                f"{where}/service.yaml: name '{service.get('name')}' no coincide con la carpeta"
            )

        base_values_path = svc_dir / "values.yaml"
        try:
            base_values = load_map(base_values_path) if base_values_path.exists() else {}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{rel(base_values_path)}: YAML inválido: {exc}")
            continue

        # Sin carpetas <env>/ también vale: el micro existe (catálogo, values base) antes de
        # desplegarse en ninguno; se da de alta en cada entorno cuando toque.
        env_dirs = sorted(p for p in svc_dir.iterdir() if p.is_dir())

        for env_dir in env_dirs:
            env, ewhere = env_dir.name, rel(env_dir)
            if env not in environments:
                errors.append(f"{ewhere}: el entorno '{env}' no existe en environments/")
                continue

            deploy_path = env_dir / DEPLOY_FILE
            if not deploy_path.exists():
                errors.append(f"{ewhere}: falta {DEPLOY_FILE}")
                continue
            try:
                deploy = load_map(deploy_path)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{rel(deploy_path)}: inválido: {exc}")
                continue

            # Primero el fichero suelto: un typo se reporta en deploy.json, no en el merge.
            part_errors = schema_errors(deploy_part_validator, deploy, rel(deploy_path))
            errors += part_errors
            if not part_errors:
                # Merge superficial: deploy.json pisa a environments/<env>.yaml.
                merged = {**environments[env], **deploy}
                missing = [
                    f"{ewhere} (fusionado): {e.message}"
                    for e in deploy_full_validator.iter_errors(merged)
                    if e.validator == "required"
                ]
                errors += missing
                errors += platform_errors(environments[env], deploy, rel(deploy_path), env)
                # Sin namespace o release_name no hay release que comparar.
                if not missing:
                    key = (env, merged["namespace"], merged["release_name"])
                    if key in releases:
                        errors.append(
                            f"{ewhere}: release '{key[2]}' en namespace '{key[1]}' "
                            f"ya lo usa {releases[key]}"
                        )
                    else:
                        releases[key] = svc

            overlay_path = env_dir / "values.yaml"
            if overlay_path.exists():
                try:
                    deep_merge(base_values, load_map(overlay_path))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{rel(overlay_path)}: YAML inválido: {exc}")
                    continue
            checked += 1

    if errors:
        print("ERROR: configuración inválida:\n  " + "\n  ".join(errors))
        return 1
    print(f"OK: {len(environments)} entornos y {checked} despliegues válidos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
