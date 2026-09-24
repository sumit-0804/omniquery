"""Create schema `bigdemo`: the 5 rideshare tables plus 35 decoys, to test table selection.

    uv run python evals/make_big_schema.py
    uv run python evals/run_eval.py --schema bigdemo

Rebuilds the schema from scratch on every run. Reads the rideshare data from `public`.
"""

import psycopg2
from psycopg2 import sql

from utils.config import db_config

SCHEMA = "bigdemo"
REAL_TABLES = ["users", "vehicles", "rides", "payments", "ratings"]

# `LIKE ... INCLUDING ALL` copies primary keys but not foreign keys, so these are re-added.
FOREIGN_KEYS = [
    ("vehicles", "driver_id", "users", "user_id"),
    ("rides", "rider_id", "users", "user_id"),
    ("rides", "driver_id", "users", "user_id"),
    ("payments", "ride_id", "rides", "ride_id"),
    ("payments", "user_id", "users", "user_id"),
    ("ratings", "ride_id", "rides", "ride_id"),
    ("ratings", "rider_id", "users", "user_id"),
    ("ratings", "driver_id", "users", "user_id"),
]

# Plausible-sounding tables with real rows, so choosing one gives a wrong answer
# rather than an obviously empty one.
NEAR_MISSES = {
    "ride_requests_archive": (
        "ride_id integer, rider_id integer, requested_at timestamp, status varchar(30)",
        "SELECT g, g % 7000 + 1, timestamp '2024-01-01' + g * interval '1 hour', "
        "(ARRAY['completed', 'cancelled'])[1 + g % 2] FROM generate_series(1, 500) g",
    ),
    "payment_methods": (
        "code varchar(30) PRIMARY KEY, label varchar(60)",
        "VALUES ('card', 'Card'), ('cash', 'Cash'), ('wallet', 'Wallet')",
    ),
    "driver_ratings_legacy": (
        "driver_id integer, score numeric(3,1), created_at timestamp",
        "SELECT g % 3000 + 1, (g % 5) + 1, timestamp '2024-01-01' + g * interval '1 day' "
        "FROM generate_series(1, 300) g",
    ),
    "user_profiles": (
        "user_id integer REFERENCES bigdemo.users (user_id), bio text, avatar_url text",
        "SELECT g, 'bio ' || g, 'https://example.com/a/' || g || '.png' FROM generate_series(1, 100) g",
    ),
    "trip_events": (
        "ride_id integer REFERENCES bigdemo.rides (ride_id), event_type varchar(30), created_at timestamp",
        "SELECT g, (ARRAY['pickup', 'dropoff'])[1 + g % 2], timestamp '2025-01-01' + g * interval '1 hour' "
        "FROM generate_series(1, 200) g",
    ),
    "promo_codes": (
        "code varchar(30) PRIMARY KEY, discount_pct integer, valid_until date",
        "VALUES ('WELCOME10', 10, '2026-12-31'), ('SUMMER', 15, '2026-08-31'), ('VIP', 25, '2027-01-31')",
    ),
}

UNRELATED = [
    "hr_employees", "hr_departments", "hr_payroll", "inventory_items", "inventory_warehouses",
    "inventory_movements", "marketing_campaigns", "marketing_emails", "marketing_clicks",
    "support_tickets", "support_agents", "support_messages", "fleet_maintenance",
    "fleet_fuel_logs", "finance_invoices", "finance_ledger", "finance_accounts", "app_sessions",
    "app_errors", "app_releases", "city_zones", "surge_zones", "weather_daily", "audit_log",
    "feature_flags", "notifications", "referrals", "loyalty_points", "partner_companies",
]


def main() -> None:
    conn = psycopg2.connect(**db_config())
    try:
        with conn, conn.cursor() as cur:
            ident = sql.Identifier(SCHEMA)
            cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(ident))

            for name in REAL_TABLES:
                target, source = sql.Identifier(SCHEMA, name), sql.Identifier("public", name)
                cur.execute(sql.SQL("CREATE TABLE {} (LIKE {} INCLUDING ALL)").format(target, source))
                cur.execute(sql.SQL("INSERT INTO {} SELECT * FROM {}").format(target, source))

            for table, column, ref_table, ref_column in FOREIGN_KEYS:
                cur.execute(sql.SQL("ALTER TABLE {} ADD FOREIGN KEY ({}) REFERENCES {} ({})").format(
                    sql.Identifier(SCHEMA, table), sql.Identifier(column),
                    sql.Identifier(SCHEMA, ref_table), sql.Identifier(ref_column),
                ))

            # Column specs and rows are fixed strings above, not user input.
            for name, (columns, rows) in NEAR_MISSES.items():
                cur.execute(f"CREATE TABLE {SCHEMA}.{name} ({columns})")
                cur.execute(f"INSERT INTO {SCHEMA}.{name} {rows}")

            for name in UNRELATED:
                cur.execute(f"CREATE TABLE {SCHEMA}.{name} (id integer PRIMARY KEY, name text, created_at timestamp)")

            total = len(REAL_TABLES) + len(NEAR_MISSES) + len(UNRELATED)
            for name in [*REAL_TABLES, *NEAR_MISSES, *UNRELATED]:
                cur.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(SCHEMA, name)))
    finally:
        conn.close()

    print(f"Created schema {SCHEMA} with {total} tables "
          f"({len(REAL_TABLES)} real, {len(NEAR_MISSES)} near-misses, {len(UNRELATED)} unrelated).")


if __name__ == "__main__":
    main()
