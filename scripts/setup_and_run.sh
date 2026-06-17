#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

APP_FILE="${APP_FILE:-app.py}"
APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8501}"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-phdhub}"
PYTHON_VERSION="${PYTHON_VERSION:-3.10}"
TOTAL_STEPS=6

step() {
  local number="$1"
  local message="$2"
  printf "\n[%s/%s] %s\n" "$number" "$TOTAL_STEPS" "$message"
}

done_step() {
  printf "[OK] %s\n" "$1"
}

die() {
  printf "[ERROR] %s\n" "$1" >&2
  exit 1
}

packages_ready() {
  python - <<'PY'
import importlib

modules = [
    "streamlit",
    "openai",
    "google.generativeai",
    "pandas",
    "plotly",
    "pycountry",
    "streamlit_autorefresh",
    "requests",
    "fitz",
    "tzdata",
]

missing = []
for module in modules:
    try:
        importlib.import_module(module)
    except Exception:
        missing.append(module)

if missing:
    print("Missing packages:", ", ".join(missing))
    raise SystemExit(1)
PY
}

if [[ ! -f "$APP_FILE" ]]; then
  die "App entry not found: $APP_FILE"
fi

step 1 "Checking conda"
if ! command -v conda >/dev/null 2>&1; then
  for conda_sh in \
    "$HOME/miniconda3/etc/profile.d/conda.sh" \
    "$HOME/anaconda3/etc/profile.d/conda.sh" \
    "/opt/conda/etc/profile.d/conda.sh"; do
    if [[ -f "$conda_sh" ]]; then
      # shellcheck source=/dev/null
      source "$conda_sh"
      break
    fi
  done
fi

if ! command -v conda >/dev/null 2>&1; then
  die "conda was not found. Please install Miniconda or Anaconda first, then run: bash run.sh"
fi
done_step "conda is available"

step 2 "Loading conda shell support"
CONDA_BASE="$(conda info --base)"
# shellcheck source=/dev/null
source "$CONDA_BASE/etc/profile.d/conda.sh"
done_step "conda shell support loaded from $CONDA_BASE"

step 3 "Preparing conda environment: $CONDA_ENV_NAME"
if conda env list | awk '{print $1}' | grep -qx "$CONDA_ENV_NAME"; then
  done_step "conda environment already exists"
else
  conda create -y -n "$CONDA_ENV_NAME" "python=$PYTHON_VERSION"
  done_step "conda environment created with Python $PYTHON_VERSION"
fi

step 4 "Activating conda environment"
conda activate "$CONDA_ENV_NAME"
done_step "activated: $CONDA_DEFAULT_ENV"

step 5 "Checking app packages"
if packages_ready; then
  done_step "app packages are ready; skipping install"
else
  printf "[INFO] Installing missing packages automatically...\n"
  python -m pip install -U pip
  python -m pip install -r requirements.txt
  done_step "app packages installed"
fi

step 6 "Starting PhDHub"
printf "[OK] Open http://localhost:%s after Streamlit starts.\n\n" "$APP_PORT"
exec python -m streamlit run "$APP_FILE" --server.address "$APP_HOST" --server.port "$APP_PORT" "$@"
