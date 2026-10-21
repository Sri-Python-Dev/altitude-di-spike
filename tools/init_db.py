"""Create the Postgres tables (idempotent) and show row counts.

Run: uv run --env-file .env python -m tools.init_db
"""
from storage.postgres import connect, database_url

def main():
    if not database_url():
        raise SystemExit('DATABASE_URL is not set: run with --env-file .env')
    with connect() as conn:
        for table in ('documents', 'quotes', 'quote_fields'):
            count = conn.execute(f'select count(*) from {table}').fetchone()[0]
            print(f'{table:13} {count} rows')

if __name__ == '__main__':
    main()
