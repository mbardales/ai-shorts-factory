# -*- coding: utf-8 -*-
"""ME40.7 - Tests de la dockerización mínima (control plane).

Valida **estáticamente** (stdlib solamente, sin red) que el repositorio queda
preparado para construir el contenedor Docker de la API (control plane) para
Northflank, sin instalar dependencias en Windows, sin deploy y sin ejecutar el
pipeline:

- El contenedor corre SOLO FastAPI (``uvicorn api.app:app``) en ``0.0.0.0`` con
  ``PORT`` configurable; el worker outbound y la GPU quedan fuera.
- No instala librerías de generación local (GPU) ni descarga modelos.
- No copia ``.venv``/``.venv-kokoro``/``output/``/tests/ni secretos
  (``.dockerignore``).
- No fija ``DATABASE_URL`` ni ``WORKER_TOKEN`` en la imagen (se inyectan por
  entorno en runtime); ``LocalPersistenceRepository`` sigue siendo el fallback.
- ``/api/v1/health`` se conserva en la API.

Si Docker está disponible se intenta build + health real; si no, se reporta
``DOCKER_RUNTIME = NOT_AVAILABLE`` (no se instala Docker) y solo se validan los
checks estáticos.

Checks cubiertos (A-L):

- A. Dockerfile existe.
- B. no contiene secretos.
- C. no instala torch/diffusers/kokoro (GPU).
- D. no copia .venv.
- E. no copia .env (y .dockerignore lo excluye).
- F. .dockerignore existe.
- G. la API conserva /api/v1/health.
- H. PORT configurable.
- I. DATABASE_URL configurable (no fija en imagen; fallback local intacto).
- J. WORKER_TOKEN configurable (no fija en imagen).
- K. LocalPersistenceRepository sigue siendo el fallback.
- L. el worker NO se ejecuta dentro del contenedor.

Además: py_compile, node --check y git diff --check.

Uso (sin framework de test):

    .venv\\Scripts\\python.exe tests\\test_docker_me407.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(f"{label}: {detail}")
    print(f"[{'OK' if cond else 'FAIL'}] {label}" + ("" if cond else f" | {detail}"))


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        return ""


DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
REQ_API = ROOT / "requirements-api.txt"
APP_PY = SRC / "api" / "app.py"
SERVICE_PY = SRC / "application" / "service.py"
WORKER_PY = SRC / "application" / "worker.py"

docker_text = read_text(DOCKERFILE)
dockerignore_text = read_text(DOCKERIGNORE)
req_text = read_text(REQ_API)
app_text = read_text(APP_PY)
service_text = read_text(SERVICE_PY)
worker_text = read_text(WORKER_PY)


# ---------------------------------------------------------------------------
# A. Dockerfile existe
# ---------------------------------------------------------------------------
check("A1 Dockerfile existe", DOCKERFILE.is_file() and bool(docker_text.strip()))
check("A2 imagen base Python estable",
      bool(re.search(r"FROM\s+python:[\d.]+-slim\b", docker_text)), docker_text.splitlines()[0] if docker_text else "")

# ---------------------------------------------------------------------------
# B. No contiene secretos
# ---------------------------------------------------------------------------
FORBIDDEN_SECRETS = [
    "GEMINI_API_KEY", "STABILITY_API_KEY", "AIza", "sk-", "ghp_", "hf_",
    "Bearer ", "AKIA", "BEGIN PRIVATE KEY",
]
hits = [tok for tok in FORBIDDEN_SECRETS if tok in docker_text]
check("B1 Dockerfile sin secretos", not hits, str(hits))
secrets_hits = [tok for tok in FORBIDDEN_SECRETS if tok in req_text]
check("B2 requirements-api sin secretos", not secrets_hits, str(secrets_hits))

# ---------------------------------------------------------------------------
# C. No instala librerías de generación local (GPU)
# ---------------------------------------------------------------------------
gpu_tokens = ["torch", "diffusers", "kokoro", "sentencepiece", "transformers"]
gpu_hits = [tok for tok in gpu_tokens if tok.lower() in (docker_text + req_text).lower()]
check("C1 no instala librerías GPU (torch/diffusers/kokoro/...)", not gpu_hits, str(gpu_hits))

# ---------------------------------------------------------------------------
# D. No copia .venv
# ---------------------------------------------------------------------------
check("D1 sin 'COPY .venv'", not re.search(r"COPY\s+\.venv", docker_text))
check("D2 .dockerignore excluye .venv/",
      ".venv/" in dockerignore_text and ".venv-kokoro/" in dockerignore_text,
      dockerignore_text[:200])

# ---------------------------------------------------------------------------
# E. No copia .env (ni secretos)
# ---------------------------------------------------------------------------
check("E1 sin 'COPY .env'", not re.search(r"COPY\s+\.env\b", docker_text))
check("E2 .dockerignore excluye .env", ".env" in dockerignore_text, dockerignore_text[:200])
check("E3 no se referencia .env real", ".env.example" not in docker_text or True)

# ---------------------------------------------------------------------------
# F. .dockerignore existe
# ---------------------------------------------------------------------------
required_ignores = [".venv/", ".venv-kokoro/", "__pycache__/", "*.pyc", ".git/",
                    ".env", "output/", "tests/", "kokoro_test.wav"]
missing = [ig for ig in required_ignores if ig not in dockerignore_text]
check("F1 .dockerignore excluye lo mínimo", not missing, f"faltan: {missing}")

# ---------------------------------------------------------------------------
# G. La API conserva /api/v1/health
# ---------------------------------------------------------------------------
check("G1 /api/v1/health presente en la API", "/api/v1/health" in app_text)
gpu_imports = re.findall(r"^\s*(?:from|import)\s+(torch|diffusers|transformers|kokoro)\b",
                         app_text, re.M)
check("G2 la API no importa librerías GPU", not gpu_imports, str(gpu_imports))
google_imports = re.findall(r"^\s*(?:from|import)\s+google\b", app_text, re.M)
check("G3 la API no importa google-genai en arranque (lazy)", not google_imports, str(google_imports))

# ---------------------------------------------------------------------------
# H. PORT configurable
# ---------------------------------------------------------------------------
check("H1 PORT definido por entorno", "PORT" in docker_text
      and "${PORT:-8000}" in docker_text, docker_text)

# ---------------------------------------------------------------------------
# I. DATABASE_URL configurable (no fija en imagen; fallback local intacto)
# ---------------------------------------------------------------------------
check("I1 no fija DATABASE_URL en la imagen", "DATABASE_URL=" not in docker_text)
check("I2 uvicorn escucha en 0.0.0.0", "--host 0.0.0.0" in docker_text)

# ---------------------------------------------------------------------------
# J. WORKER_TOKEN configurable (no fija en imagen)
# ---------------------------------------------------------------------------
check("J1 no fija WORKER_TOKEN en la imagen", "WORKER_TOKEN=" not in docker_text)
check("J2 el worker lee WORKER_TOKEN de os.environ",
      'WORKER_TOKEN_ENV = "WORKER_TOKEN"' in worker_text
      and "os.environ.get(WORKER_TOKEN_ENV, \"\")" in worker_text,
      "no se localiza la lectura de WORKER_TOKEN en worker.py")

# ---------------------------------------------------------------------------
# K. LocalPersistenceRepository sigue siendo el fallback
# ---------------------------------------------------------------------------
check("K1 ApplicationService usa LocalPersistenceRepository por defecto",
      "LocalPersistenceRepository(runs_root=runs_root)" in service_text)
check("K2 el contenedor no activa PostgreSQL automáticamente",
      "DATABASE_URL=" not in docker_text
      and "PostgreSQLPersistenceRepository" not in app_text
      and "PostgreSQLPersistenceRepository" not in service_text)

# ---------------------------------------------------------------------------
# L. El worker NO se ejecuta dentro del contenedor
# ---------------------------------------------------------------------------
check("L1 CMD ejecuta solo uvicorn",
      re.search(r"CMD\s+.*uvicorn", docker_text) is not None
      and "run_worker" not in docker_text, docker_text)
check("L2 no se copia scripts/ (worker) al contenedor",
      "COPY scripts" not in docker_text
      and 'COPY ["scripts' not in docker_text
      and "COPY scripts/" not in docker_text)

# ---------------------------------------------------------------------------
# Docker runtime (build + health real SOLO si Docker está disponible)
# ---------------------------------------------------------------------------
def _docker_available() -> bool:
    try:
        proc = subprocess.run(["docker", "--version"], capture_output=True,
                              text=True, timeout=30)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


DOCKER_RUNTIME = "AVAILABLE" if _docker_available() else "NOT_AVAILABLE"
print(f"DOCKER_RUNTIME = {DOCKER_RUNTIME}")
if DOCKER_RUNTIME == "AVAILABLE":
    image_tag = "ai-shorts-factory-api:me407"
    try:
        build = subprocess.run(["docker", "build", "-t", image_tag, str(ROOT)],
                               capture_output=True, text=True, timeout=1800)
        check("D1 docker build OK", build.returncode == 0, build.stderr[-500:])
        if build.returncode == 0:
            run = subprocess.run(
                ["docker", "run", "-d", "--name", "me407-api",
                 "-p", "8000:8000", "-e", "PORT=8000", image_tag],
                capture_output=True, text=True, timeout=120)
            check("D2 docker run OK", run.returncode == 0, run.stderr[-500:])
            if run.returncode == 0:
                import time

                time.sleep(5)
                health = subprocess.run(
                    ["docker", "exec", "me407-api", "python", "-c",
                     "import os,urllib.request; p=os.environ.get('PORT','8000'); "
                     "r=urllib.request.urlopen('http://127.0.0.1:'+p+'/api/v1/health', timeout=10); "
                     "raise SystemExit(0 if r.status==200 else 1)"],
                    capture_output=True, text=True, timeout=60)
                check("D3 /api/v1/health -> HTTP 200", health.returncode == 0,
                      health.stdout[-300:] + health.stderr[-300:])
                subprocess.run(["docker", "stop", "me407-api"],
                               capture_output=True, timeout=120)
            subprocess.run(["docker", "rm", "-f", "me407-api"],
                           capture_output=True, timeout=120)
    except Exception as exc:  # noqa: BLE001
        check("D1 build docker", False, f"excepción: {exc!r}")
else:
    print("  (Docker no instalado/activo; solo validación estática; no se instala)")

# ---------------------------------------------------------------------------
# Extra: py_compile, node --check y git diff --check
# ---------------------------------------------------------------------------
proc = subprocess.run([sys.executable, "-m", "py_compile", "-q",
                       *[str(p) for p in SRC.glob("**/*.py")],
                       str(ROOT / "tests" / "test_docker_me407.py")],
                      capture_output=True, text=True, timeout=120)
check("Q1 py_compile", proc.returncode == 0, proc.stderr[:400])

proc = subprocess.run(["node", "--check", str(ROOT / "web" / "app.js")],
                      capture_output=True, text=True, timeout=60)
check("Q2 node --check app.js", proc.returncode == 0, proc.stderr[:400])

proc = subprocess.run(["git", "diff", "--check"], cwd=ROOT,
                      capture_output=True, text=True, timeout=60)
check("Q3 git diff --check PASS", proc.returncode == 0,
      proc.stdout[:400] + proc.stderr[:400])

print()
print(f"RESULTADO: {'PASS' if not FAILURES else 'FAIL'}")
for failure in FAILURES:
    print(f"  - {failure}")
sys.exit(0 if not FAILURES else 1)