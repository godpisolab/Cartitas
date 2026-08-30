#!/usr/bin/env python3
"""Levanta el panel de gestor en local -- los dos procesos que necesita
(`api/main.py` y el servicio interno de jobs de `store_monitor/`,
`jobs_api.py`) y todo lo necesario para que funcionen, en un solo comando.

Qué hace, en orden:
1. Instala dependencias (`shared` en editable + `requirements.txt` de
   `api/`/`store_monitor/`) -- salvo `--skip-install`.
2. Comprueba que Postgres responda en `localhost:5433` -- si no, y hay
   Docker disponible, levanta `docker_composes/docker-compose.yml` (mismo
   mecanismo que `run_tests.py`).
3. Se asegura de que la base de DESARROLLO (`cartitas`, la persistente --
   NO `cartitas_test`, que es desechable) tenga el esquema aplicado. A
   diferencia de `run_tests.py`, esto NUNCA la resetea si ya existe con
   datos -- solo aplica `schema-postgresql-app-tcg.sql` +
   `seed-catalog-app-tcg.sql` la primera vez, cuando está genuinamente
   vacía (nada que perder).
4. Siembra el catálogo oficial de Bandai contra ella
   (`seed_official_catalog.py`, idempotente por `name_canonical`) -- salvo
   `--skip-seed`. Sin esto el panel arranca, pero sin productos canónicos
   con los que comparar precio.
5. Si `ADMIN_USERNAME`/`ADMIN_PASSWORD` no están ya en el entorno, usa una
   credencial de desarrollo fija (`admin`/`admin`) y la imprime en
   pantalla -- **nunca uses esto en un despliegue real**, solo para poder
   entrar al panel en local sin configurar nada.
6. Lanza los dos procesos y se queda corriendo hasta Ctrl+C, parando los
   dos juntos:
   - `store_monitor/jobs_api.py` -- `uvicorn jobs_api:app --port 8001`
   - `api/main.py` -- `uvicorn main:app --reload --port 1708`

Uso:
    python run_panel.py                    # todo, con instalación de deps y semilla del catálogo
    python run_panel.py --skip-install      # si ya tienes las deps instaladas
    python run_panel.py --skip-seed         # no volver a sembrar el catálogo oficial
    python run_panel.py --no-reload         # sin --reload en la API (útil probando flujos de jobs largos)
    python run_panel.py --port 8010 --jobs-port 8011

Una vez arriba: panel en http://127.0.0.1:<port>/admin/matches (credenciales
por pantalla), documentación interactiva en http://127.0.0.1:<port>/docs.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMPOSE_FILE = ROOT / "docker_composes" / "docker-compose.yml"
CONTAINER_NAME = "cartitas-postgres"

DB_HOST = "localhost"
DB_PORT = 5433
DB_USER = "cartitas"
DB_PASSWORD = "cartitas"
SYSTEM_DB = "postgres"  # existe siempre, se usa para comprobar conectividad / CREATE DATABASE
DEV_DB = "cartitas"  # la base de desarrollo persistente -- nunca se resetea si ya tiene datos

SCHEMA_SQL = ROOT / "schema-postgresql-app-tcg.sql"
SEED_SQL = ROOT / "seed-catalog-app-tcg.sql"

DEV_ADMIN_USERNAME = "admin"
DEV_ADMIN_PASSWORD = "admin"


def _url(dbname: str) -> str:
    return f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{dbname}"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def install_dependencies() -> None:
    print("\n=== 1. Instalando dependencias ===")
    python = sys.executable
    _run([python, "-m", "pip", "install", "-e", str(ROOT / "shared")])
    # cwd=cada servicio: sus requirements.txt traen "-e ../shared" (ruta
    # relativa a SU PROPIO directorio) -- pip solo la resuelve bien desde ahí.
    _run([python, "-m", "pip", "install", "-r", "requirements.txt"], cwd=str(ROOT / "store_monitor"))
    _run([python, "-m", "pip", "install", "-r", "requirements.txt"], cwd=str(ROOT / "api"))


def _postgres_reachable(dbname: str = SYSTEM_DB, timeout: int = 2) -> bool:
    import psycopg2

    try:
        conn = psycopg2.connect(_url(dbname), connect_timeout=timeout)
        conn.close()
        return True
    except Exception:
        return False


def ensure_postgres_running() -> None:
    print("\n=== 2. Comprobando Postgres ===")
    if _postgres_reachable():
        print(f"Postgres ya responde en {DB_HOST}:{DB_PORT} -- no hace falta levantar nada.")
        return

    if shutil.which("docker") is None:
        raise SystemExit(
            "No se pudo conectar a Postgres y Docker no está instalado/en el PATH.\n"
            "Instala Docker Desktop y vuelve a correr este script, o levanta Postgres "
            "manualmente en localhost:5433 (ver docker_composes/docker-compose.yml)."
        )

    print(f"Postgres no responde -- levantando {CONTAINER_NAME} vía docker compose...")
    try:
        _run(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"])
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            "No se pudo levantar el contenedor de Postgres -- ¿está el daemon de "
            "Docker (Docker Desktop) corriendo?"
        ) from e

    print("Esperando a que Postgres acepte conexiones...")
    deadline = time.time() + 60
    while time.time() < deadline:
        if _postgres_reachable():
            print("Postgres listo.")
            return
        time.sleep(1)
    raise SystemExit(f"Postgres no respondió en {DB_HOST}:{DB_PORT} tras 60s de espera.")


def _apply_schema_and_seed(dbname: str) -> None:
    import psycopg2

    conn = psycopg2.connect(_url(dbname))
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
            cur.execute(SEED_SQL.read_text(encoding="utf-8"))
    finally:
        conn.close()


def ensure_dev_database_ready() -> None:
    import psycopg2

    print("\n=== 3. Comprobando la base de datos de desarrollo ===")

    admin_conn = psycopg2.connect(_url(SYSTEM_DB))
    admin_conn.autocommit = True
    try:
        with admin_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DEV_DB,))
            exists = cur.fetchone() is not None
            if not exists:
                print(f"Creando base de datos '{DEV_DB}'...")
                cur.execute(f'CREATE DATABASE "{DEV_DB}"')
    finally:
        admin_conn.close()

    if not exists:
        print(f"Aplicando esquema + catálogo de categorías sobre '{DEV_DB}' (recién creada, nada que perder)...")
        _apply_schema_and_seed(DEV_DB)
        print(f"'{DEV_DB}' lista.")
        return

    conn = psycopg2.connect(_url(DEV_DB))
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.category')")
            has_schema = cur.fetchone()[0] is not None
            missing = []
            if has_schema:
                # Chequeo puntual, no un framework de migraciones -- este
                # repo no tiene uno (schema-postgresql-app-tcg.sql es la
                # única fuente de verdad, sin versiones incrementales). Dos
                # cosas cambiaron con el Recognition Pipeline y las dos hay
                # que comprobar por separado: la columna `packaging` (nueva
                # en `product`) y la taxonomía de categorías (BOOSTER_BOX/
                # BOOSTER_PACK/BOOSTER_CASE/PREMIUM_COLLECTION/LEARN_DECK se
                # fusionaron/separaron en categorías con slugs nuevos -- una
                # base sembrada con la jerarquía vieja no tiene, por
                # ejemplo, 'one-piece' en absoluto, no solo una columna de
                # menos).
                cur.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'product' AND column_name = 'packaging'"
                )
                if cur.fetchone() is None:
                    missing.append("la columna 'packaging' en 'product'")

                cur.execute("SELECT 1 FROM category WHERE slug = 'one-piece'")
                if cur.fetchone() is None:
                    missing.append("la categoría 'one-piece' (taxonomía nueva de 10 categorías)")
    finally:
        conn.close()

    if not has_schema:
        print(f"'{DEV_DB}' existe pero está vacía -- aplicando esquema + catálogo de categorías...")
        _apply_schema_and_seed(DEV_DB)
        print(f"'{DEV_DB}' lista.")
        return

    if missing:
        raise SystemExit(
            f"'{DEV_DB}' tiene un esquema DESACTUALIZADO respecto al Recognition Pipeline "
            f"(ver docs/propuestas/guia_nuevo_matcher.md) -- falta: {', '.join(missing)}.\n"
            f"Este script nunca modifica el esquema/categorías de una base con datos reales "
            f"por su cuenta. La columna es un cambio aditivo (seguro aplicar a mano), pero la "
            f"taxonomía de categorías se fusionó/separó (booster-box/booster-pack/booster-case "
            f"-> one-piece, premium-collection -> premium-card-collection + "
            f"premium-booster-box, learn-deck -> starter-deck) -- no hay un ALTER simple para "
            f"eso, hace falta decidir cómo remapear los productos/store_product ya sembrados. "
            f"Dos caminos:\n"
            f"  1. Si te importan los datos ya scrapeados: escribe esa migración a mano "
            f"(fuera del alcance de este script) antes de continuar.\n"
            f"  2. Si no: 'docker compose -f docker_composes/docker-compose.yml down -v' y "
            f"vuelve a correr este script -- se recreará desde cero con el esquema y la "
            f"taxonomía actuales."
        )

    print(f"'{DEV_DB}' ya existe con el esquema aplicado -- no se toca (es la base de desarrollo persistente).")


def seed_official_catalog() -> None:
    print("\n=== 4. Sembrando el catálogo oficial de Bandai ===")
    env = os.environ.copy()
    env.setdefault("DATABASE_URL", _url(DEV_DB))
    python = sys.executable
    env_pythonpath = os.pathsep.join([str(ROOT / "store_monitor"), str(ROOT / "shared"), str(ROOT)])
    env["PYTHONPATH"] = env_pythonpath + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run([python, "seed_official_catalog.py"], cwd=str(ROOT / "store_monitor"), env=env)
    if result.returncode != 0:
        print("Aviso: seed_official_catalog.py terminó con error -- el panel sigue arrancando igual, "
              "pero puede que falten productos canónicos.")


def ensure_admin_credentials() -> dict[str, str]:
    print("\n=== 5. Credenciales del panel ===")
    env_updates: dict[str, str] = {}
    username = os.environ.get("ADMIN_USERNAME") or DEV_ADMIN_USERNAME
    password = os.environ.get("ADMIN_PASSWORD") or DEV_ADMIN_PASSWORD
    if not os.environ.get("ADMIN_USERNAME") or not os.environ.get("ADMIN_PASSWORD"):
        print(f"ADMIN_USERNAME/ADMIN_PASSWORD no configuradas -- usando credencial de desarrollo "
              f"'{DEV_ADMIN_USERNAME}'/'{DEV_ADMIN_PASSWORD}'. NO USAR ESTO EN UN DESPLIEGUE REAL.")
        env_updates["ADMIN_USERNAME"] = username
        env_updates["ADMIN_PASSWORD"] = password
    else:
        print("Usando ADMIN_USERNAME/ADMIN_PASSWORD ya configuradas en el entorno.")
    return env_updates


def _stop_process(process: subprocess.Popen, timeout: float) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # uvicorn --reload en Windows lanza un proceso "reloader" que a su
        # vez lanza el proceso servidor real como HIJO -- process.terminate()
        # (equivalente a matar solo el reloader) no siempre se propaga al
        # hijo en Windows, y el servidor real queda huérfano ocupando el
        # puerto en la siguiente ejecución. taskkill /T mata todo el árbol.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def launch_services(port: int, jobs_port: int, reload: bool, extra_env: dict[str, str]) -> int:
    print("\n=== 6. Arrancando los servicios ===")

    for name, p in (("api (--port)", port), ("jobs_api (--jobs-port)", jobs_port)):
        if _port_in_use(p):
            raise SystemExit(
                f"El puerto {p} ya está en uso (hace falta para {name}) -- para el proceso que lo "
                f"ocupa o vuelve a correr con --port/--jobs-port apuntando a otro puerto libre."
            )

    base_env = os.environ.copy()
    base_env.update(extra_env)
    base_env.setdefault("DATABASE_URL", _url(DEV_DB))
    base_env.setdefault("JOBS_API_URL", f"http://127.0.0.1:{jobs_port}")

    jobs_env = base_env.copy()
    jobs_env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "store_monitor"), str(ROOT / "shared")]
        + ([base_env["PYTHONPATH"]] if base_env.get("PYTHONPATH") else [])
    )
    jobs_cmd = [sys.executable, "-m", "uvicorn", "jobs_api:app", "--host", "127.0.0.1", "--port", str(jobs_port)]

    api_env = base_env.copy()
    api_env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "api"), str(ROOT / "shared")]
        + ([base_env["PYTHONPATH"]] if base_env.get("PYTHONPATH") else [])
    )
    api_cmd = [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)]
    if reload:
        api_cmd.append("--reload")

    print(f"(cwd={ROOT / 'store_monitor'}) $ {' '.join(jobs_cmd)}")
    jobs_process = subprocess.Popen(jobs_cmd, cwd=str(ROOT / "store_monitor"), env=jobs_env)

    print(f"(cwd={ROOT / 'api'}) $ {' '.join(api_cmd)}")
    api_process = subprocess.Popen(api_cmd, cwd=str(ROOT / "api"), env=api_env)

    print(f"\nPanel de gestor: http://127.0.0.1:{port}/admin/matches")
    print(f"Documentación interactiva (Swagger): http://127.0.0.1:{port}/docs")
    print(f"Servicio interno de jobs: http://127.0.0.1:{jobs_port}")
    print("\nCtrl+C para parar los dos servicios.\n")

    exit_code = 0
    try:
        while True:
            for name, process in (("jobs_api", jobs_process), ("api", api_process)):
                code = process.poll()
                if code is not None:
                    print(f"\n'{name}' terminó solo (exit {code}) -- parando el otro servicio.")
                    exit_code = code if code != 0 else 1
                    raise KeyboardInterrupt
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nParando servicios...")
    finally:
        deadline = time.time() + 10
        for process in (api_process, jobs_process):
            _stop_process(process, timeout=max(0.0, deadline - time.time()))

    return exit_code


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-install", action="store_true", help="No reinstalar dependencias.")
    parser.add_argument("--skip-seed", action="store_true", help="No volver a sembrar el catálogo oficial.")
    parser.add_argument("--no-reload", action="store_true", help="Arrancar la API sin --reload.")
    parser.add_argument("--port", type=int, default=1708, help="Puerto de api/main.py (default 1708).")
    parser.add_argument("--jobs-port", type=int, default=17081,
                         help="Puerto de store_monitor/jobs_api.py (default 17081).")
    args = parser.parse_args()

    if not args.skip_install:
        install_dependencies()
    else:
        print("\n=== 1. Instalación de dependencias omitida (--skip-install) ===")

    ensure_postgres_running()
    ensure_dev_database_ready()

    if not args.skip_seed:
        seed_official_catalog()
    else:
        print("\n=== 4. Siembra del catálogo omitida (--skip-seed) ===")

    extra_env = ensure_admin_credentials()

    return launch_services(args.port, args.jobs_port, reload=not args.no_reload, extra_env=extra_env)


if __name__ == "__main__":
    raise SystemExit(main())
