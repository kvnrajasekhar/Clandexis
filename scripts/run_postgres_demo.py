"""Run the isolated local PostgreSQL dashboard without reconnecting Meta."""

import json
import os
import secrets
import re
import socket
import subprocess
from pathlib import Path
from uuid import uuid4
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root.parent / ".env", override=False)
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn or conninfo_to_dict(dsn).get("host") not in (
        "127.0.0.1",
        "localhost",
        "::1",
    ):
        raise SystemExit(
            "Set DATABASE_URL to the isolated local PostgreSQL demo database."
        )
    phone = os.environ.get("POSTGRES_DEMO_PHONE")
    if not phone:
        approved = json.loads(os.environ.get("WHATSAPP_DEMO_PHONE_MAP", "{}"))
        if len(approved) != 1:
            raise SystemExit(
                "Set POSTGRES_DEMO_PHONE to the explicitly approved demo sender."
            )
        phone = next(iter(approved))
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone):
        raise SystemExit("The configured approved demo phone must be E.164.")
    state = Path(
        os.environ.get(
            "POSTGRES_DEMO_STATE",
            str(Path.home() / ".local/state/clandexis-postgres-demo"),
        )
    ).expanduser()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with socket.create_connection(
            (
                conninfo_to_dict(dsn)["host"],
                int(conninfo_to_dict(dsn).get("port", 5432)),
            ),
            timeout=2,
        ):
            pass
    except OSError:
        data = state / "pgdata"
        control = os.environ.get("PG_CTL", "/opt/homebrew/opt/postgresql@17/bin/pg_ctl")
        if not data.exists() or conninfo_to_dict(dsn).get("port") != "55439":
            raise SystemExit("Start the configured local PostgreSQL instance first.")
        subprocess.run(
            [
                control,
                "-D",
                str(data),
                "-l",
                str(state / "postgres.log"),
                "-o",
                f"-h 127.0.0.1 -p 55439 -k {state}",
                "start",
            ],
            check=True,
        )
    session = state / "session.json"
    if not session.exists():
        session.write_text(
            json.dumps(
                dict(
                    merchant_id=str(uuid4()),
                    username="demo",
                    password=secrets.token_urlsafe(24),
                    session_secret=secrets.token_urlsafe(40),
                )
            )
        )
        session.chmod(0o600)
    values = json.loads(session.read_text())
    if values.get("phone") not in (None, phone):
        raise SystemExit("This saved demo belongs to another approved phone.")
    if not values.get("phone"):
        values["phone"] = phone
        session.write_text(json.dumps(values))
        session.chmod(0o600)
    os.environ.update(
        DATABASE_BACKEND="postgres",
        DASHBOARD_DEMO_MERCHANT_ID=values["merchant_id"],
        DASHBOARD_DEMO_USERNAME=values["username"],
        DASHBOARD_DEMO_PASSWORD=values["password"],
        DASHBOARD_SESSION_SECRET=values["session_secret"],
        TRUSTED_ORIGINS="http://127.0.0.1:8180",
        INVOICE_LOCAL_ROOT=str(state / "invoices"),
        META_APP_SECRET="",
        META_WHATSAPP_TOKEN="",
        META_PHONE_NUMBER_ID="",
        WHATSAPP_DEMO_PHONE_MAP=json.dumps({phone: values["merchant_id"]}),
        ENVIRONMENT="development",
    )
    from app.services.postgres_db import PostgresDatabase
    from seed.postgres_textile import seed_demo_data

    seed_demo_data(PostgresDatabase(), values["merchant_id"], phone)
    print(
        "PostgreSQL demo at http://127.0.0.1:8180/dashboard/ — username demo. Password is in the protected local session.json."
    )
    import uvicorn

    uvicorn.run("webhook_service.main:app", host="127.0.0.1", port=8180)


if __name__ == "__main__":
    main()
