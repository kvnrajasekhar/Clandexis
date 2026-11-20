#!/bin/bash
set -eu
cd "$(dirname "$0")/.."
state="$HOME/.local/state/clandexis-demo"
umask 077
mkdir -p "$state"
if lsof -nP -iTCP:8180 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "The dashboard is already listening on 8180. Stop that demo instance before restarting." >&2
  exit 1
fi
if [ ! -f "$state/session.env" ] || [ "${1:-}" = fresh ]; then
  .venv/bin/python - "$state/session.env" <<'PY'
import secrets, sys, pathlib
values = dict(DASHBOARD_DEMO_MERCHANT_ID="+91"+str(secrets.randbelow(9000000000)+1000000000), DASHBOARD_SESSION_SECRET=secrets.token_urlsafe(40), DASHBOARD_DEMO_PASSWORD=secrets.token_urlsafe(24), DASHBOARD_DEMO_USERNAME="demo")
pathlib.Path(sys.argv[1]).write_text("".join(f"export {k}={v}\n" for k,v in values.items()))
PY
fi
source "$state/session.env"
export FIRESTORE_EMULATOR_HOST=127.0.0.1:8185 GCP_PROJECT_ID=demo-clandexis-local
export TRUSTED_ORIGINS=http://127.0.0.1:8180 INVOICE_LOCAL_ROOT="$state/invoices" PYTHONPATH=.
export ENVIRONMENT=development META_APP_SECRET='' META_WHATSAPP_TOKEN='' META_PHONE_NUMBER_ID=''
if ! curl -fsS "http://$FIRESTORE_EMULATOR_HOST/" >/dev/null 2>&1; then
  java_bin="${JAVA_BIN:-$HOME/.cache/clandexis-tools/jdk-21.0.13.1+2-jre/Contents/Home/bin/java}"
  jar="${FIRESTORE_EMULATOR_JAR:-$HOME/.cache/firebase/emulators/cloud-firestore-emulator-v1.22.0.jar}"
  nohup "$java_bin" -jar "$jar" --host 127.0.0.1 --port 8185 --project_id demo-clandexis-local >"$state/emulator.log" 2>&1 </dev/null &
  for i in {1..50}; do curl -fsS "http://$FIRESTORE_EMULATOR_HOST/" >/dev/null 2>&1 && break; sleep .2; done
  curl -fsS "http://$FIRESTORE_EMULATOR_HOST/" >/dev/null
fi
.venv/bin/python scripts/demo_seed.py
echo $$ >"$state/web.pid"
exec .venv/bin/uvicorn webhook_service.main:app --host 127.0.0.1 --port 8180
