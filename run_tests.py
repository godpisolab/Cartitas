#!/usr/bin/env python3
"""Levanta todo lo necesario y corre la suite de tests completa (`shared/`
via `store_monitor/tests/` + `api/tests/`). Pensado para usarse igual en
local y en CI (`.github/workflows/tests.yaml`) -- mismo script, mismo
comportamiento, para que "pasa en mi máquina" y "pasa en CI" signifiquen lo
mismo.

Qué hace, en orden:
1. Instala dependencias (`shared` en editable + `requirements-dev.txt` de
   `store_monitor/`/`api/`, o solo la relevante con `--only`) -- salvo
   `--skip-install`.
2. Comprueba que Postgres responda en `localhost:5433` contra la base
   administrativa `postgres` (existe siempre en cualquier cluster, a
   diferencia de `cartitas`/`cartitas_test` que pueden no estar creadas
   todavía) -- si no responde y hay Docker disponible, levanta
   `docker_composes/docker-compose.yml`. En CI, el servicio de Postgres del
   job ya lo deja levantado antes de este paso, así que este bloque no hace
   nada ahí.
3. Crea `cartitas_test` si no existe (vía la base `postgres`), y reaplica el
   esquema + catálogo actuales (`schema-postgresql-app-tcg.sql` +
   `seed-catalog-app-tcg.sql`) desde cero -- salvo `--no-reset-db`. Se
   resetea por defecto para que los tests siempre corran contra el esquema
   real del código, no contra uno desactualizado de una ejecución anterior.
4. Corre `pytest` en `store_monitor/tests/` y en `api/tests/` (o solo una
   con `--only`), cada uno con su propio `PYTHONPATH` (mismo patrón que
   usan sus respectivos `conftest.py`) y `cwd` en su propio directorio
   (para que `--cov`/`coverage.xml` midan y aterricen donde ya esperan los
   pasos de CI que suben el informe).

Uso:
    python run_tests.py                       # todo, con reset de BBDD e instalación de deps
    python run_tests.py --skip-install         # si ya tienes las deps instaladas
    python run_tests.py --no-reset-db          # si no ha cambiado el esquema desde la última vez
    python run_tests.py --only store_monitor   # solo una de las dos suites
    python run_tests.py --fail-on-skipped      # además, falla si algún test se SKIPPED (uso en CI)
    python run_tests.py -- -k "classify"       # argumentos extra, se pasan tal cual a pytest

Requiere Docker Desktop (o el daemon de Docker) corriendo si Postgres no
está ya levantado -- si no hay Docker disponible en absoluto, deja un
mensaje claro en vez de fallar a medias.
"""

from __future__ import annotations

import argparse
import os
import shutil
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
# "postgres" -- la base administrativa que existe siempre en cualquier
# cluster Postgres, se use docker-compose local (que crea "cartitas") o el
# servicio de Postgres de CI (que crea "cartitas_test" directamente) --
# sirve para comprobar conectividad y para poder ejecutar CREATE DATABASE
# sin asumir cuál de las otras dos ya existe.
SYSTEM_DB = "postgres"
TEST_DB = "cartitas_test"

SCHEMA_SQL = ROOT / "schema-postgresql-app-tcg.sql"
SEED_SQL = ROOT / "seed-catalog-app-tcg.sql"

SERVICES = ("store_monitor", "api")


def _url(dbname: str) -> str:
    return f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{dbname}"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def install_dependencies(only: str | None) -> None:
    print("\n=== 1. Instalando dependencias ===")
    python = sys.executable
    _run([python, "-m", "pip", "install", "-e", str(ROOT / "shared")])
    # cwd=cada servicio, no ruta absoluta al fichero: requirements-dev.txt
    # trae "-e ../shared" (ruta relativa a SU PROPIO directorio) -- pip solo
    # la resuelve bien si corre desde ahí. Solo se instala la del servicio
    # pedido con --only (mismo motivo que separa los jobs de CI: no tiene
    # sentido instalar fastapi/sqlmodel para correr solo store_monitor, ni
    # cloudscraper/pybreaker/apscheduler para correr solo api).
    for service in SERVICES:
        if only not in (None, service):
            continue
        _run([python, "-m", "pip", "install", "-r", "requirements-dev.txt"], cwd=str(ROOT / service))


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


def reset_test_database() -> None:
    import psycopg2

    print("\n=== 3. Preparando la base de datos de test ===")

    admin_conn = psycopg2.connect(_url(SYSTEM_DB))
    admin_conn.autocommit = True
    try:
        with admin_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
            if cur.fetchone() is None:
                print(f"Creando base de datos '{TEST_DB}'...")
                cur.execute(f'CREATE DATABASE "{TEST_DB}"')
            else:
                print(f"Base de datos '{TEST_DB}' ya existe.")
    finally:
        admin_conn.close()

    print(f"Reaplicando esquema + catálogo actuales sobre '{TEST_DB}' (DROP SCHEMA CASCADE primero)...")
    conn = psycopg2.connect(_url(TEST_DB))
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
            cur.execute(SEED_SQL.read_text(encoding="utf-8"))
    finally:
        conn.close()
    print("Base de datos de test lista.")


def run_pytest_suite(label: str, cwd: Path, extra_pythonpath: list[Path], pytest_args: list[str],
                      fail_on_skipped: bool) -> int:
    print(f"\n=== Corriendo tests: {label} ===")
    env = os.environ.copy()
    env["TEST_DATABASE_URL"] = _url(TEST_DB)
    existing = env.get("PYTHONPATH")
    parts = [str(p) for p in extra_pythonpath] + ([existing] if existing else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)

    cmd = [sys.executable, "-m", "pytest", "tests", *pytest_args]
    print(f"(cwd={cwd}) $ {' '.join(cmd)}")

    if not fail_on_skipped:
        return subprocess.run(cmd, cwd=str(cwd), env=env).returncode

    # Streaming línea a línea (no subprocess.run(capture_output=True), que
    # bufferea todo hasta el final) para que CI siga viendo el progreso en
    # vivo, mientras también se guardan las líneas "SKIPPED" para el chequeo
    # de abajo -- requiere que el llamador pase "-rs" en pytest_args, si no
    # pytest no imprime el motivo/línea de cada skip en el resumen.
    process = subprocess.Popen(
        cmd, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    assert process.stdout is not None  # garantizado por stdout=subprocess.PIPE arriba
    skipped_lines = []
    for line in process.stdout:
        print(line, end="")
        if line.startswith("SKIPPED"):
            skipped_lines.append(line.rstrip())
    process.wait()

    if process.returncode != 0:
        return process.returncode
    if skipped_lines:
        print(f"\nHay tests SKIPPED en {label} -- en CI, Postgres debería estar siempre "
              f"disponible; revisa que el esquema/seed se hayan aplicado bien.")
        for line in skipped_lines:
            print(f"  {line}")
        return 1
    return 0


def main() -> int:
    # Evita mojibake en consolas Windows con codepage distinto de UTF-8 --
    # los mensajes de este script usan acentos/eñes.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-install", action="store_true", help="No reinstalar dependencias.")
    parser.add_argument("--no-reset-db", action="store_true",
                         help="No recrear cartitas_test -- usa el estado que ya tenga.")
    parser.add_argument("--only", choices=SERVICES, default=None,
                         help="Correr solo una de las dos suites (por defecto, las dos).")
    parser.add_argument("--fail-on-skipped", action="store_true",
                         help="Falla si algún test queda SKIPPED (uso en CI -- localmente, sin "
                              "Postgres disponible, saltarse tests es normal).")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER,
                         help="Argumentos extra, se pasan tal cual a pytest (usa -- antes si empiezan por -).")
    args = parser.parse_args()

    pytest_args = args.pytest_args
    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]

    if not args.skip_install:
        install_dependencies(args.only)
    else:
        print("\n=== 1. Instalación de dependencias omitida (--skip-install) ===")

    ensure_postgres_running()

    if not args.no_reset_db:
        reset_test_database()
    else:
        print("\n=== 3. Reset de BBDD omitido (--no-reset-db) ===")

    results: dict[str, int] = {}

    if args.only in (None, "store_monitor"):
        results["store_monitor"] = run_pytest_suite(
            "store_monitor/tests",
            cwd=ROOT / "store_monitor",
            extra_pythonpath=[ROOT, ROOT / "shared", ROOT / "store_monitor"],
            pytest_args=pytest_args,
            fail_on_skipped=args.fail_on_skipped,
        )

    if args.only in (None, "api"):
        results["api"] = run_pytest_suite(
            "api/tests",
            cwd=ROOT / "api",
            extra_pythonpath=[ROOT / "api", ROOT / "shared", ROOT],
            pytest_args=pytest_args,
            fail_on_skipped=args.fail_on_skipped,
        )

    print("\n=== Resumen ===")
    overall_ok = True
    for label, code in results.items():
        status = "OK" if code == 0 else f"FALLÓ (exit {code})"
        print(f"  {label}: {status}")
        overall_ok = overall_ok and code == 0

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
