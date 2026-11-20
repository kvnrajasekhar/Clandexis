"""Tenant-scoped PostgreSQL connections. No fallback to a second database."""

from contextlib import contextmanager
from uuid import UUID
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from config import settings


class PostgresDatabase:
    def __init__(self, dsn=None):
        self.dsn = dsn or settings.DATABASE_URL
        if not self.dsn:
            raise ValueError("DATABASE_URL is required for PostgreSQL")
        options = conninfo_to_dict(self.dsn)
        host = options.get("host", "")
        local = host in ("localhost", "127.0.0.1", "::1") or host.startswith("/")
        if not local and options.get("sslmode") != "verify-full":
            raise ValueError(
                "Remote PostgreSQL requires sslmode=verify-full and a trusted CA"
            )

    @contextmanager
    def transaction(self, merchant_id):
        tenant = str(UUID(str(merchant_id)))
        with psycopg.connect(
            self.dsn, row_factory=dict_row, connect_timeout=10
        ) as conn:
            conn.execute("SELECT set_config('app.merchant_id', %s, true)", (tenant,))
            yield conn
