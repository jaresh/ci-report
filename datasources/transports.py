"""
datasources/transports.py
─────────────────────────
Reusable DB connection + query mixins, separated from schema/mapping logic.

A data source tool subclasses DataSource plus the transport for its database:

    class FrameworkASource(DataSource, MySQLTransport):
        ...   # SQL + row→contract mapping only

The transport provides `_connect()` and the query method (`_query` for MySQL,
`_execute` for ClickHouse); the tool provides the SQL constants and the
row→contract mapping. This lets two tools share a database driver without
sharing a schema — the key to supporting multiple test frameworks that each
have their own tables.

Drivers are imported lazily inside `_connect()` so a tool only needs the driver
for the database it actually uses.
"""

from __future__ import annotations

import re


class MySQLTransport:
    """PyMySQL / mysql-connector-python connection and dict-cursor query."""

    def _connect(self, host: str, port: int, database: str, user: str, password: str):
        try:
            import pymysql
            conn = pymysql.connect(
                host=host, port=port, db=database,
                user=user, password=password,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=10,
            )
            return conn, 'pymysql'
        except ImportError:
            pass

        try:
            import mysql.connector
            conn = mysql.connector.connect(
                host=host, port=port, database=database,
                user=user, password=password,
                charset='utf8mb4',
                connection_timeout=10,
            )
            return conn, 'connector'
        except ImportError:
            raise RuntimeError(
                "No MySQL driver found. Install one:\n"
                "  pip install PyMySQL\n"
                "  pip install mysql-connector-python"
            )

    def _query(self, conn_pair, sql: str, params=()):
        conn, driver = conn_pair
        if driver == 'pymysql':
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        else:
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute(sql, params)
                return cur.fetchall()
            finally:
                cur.close()


class ClickHouseTransport:
    """clickhouse-driver / clickhouse-connect connection and %s param adapter."""

    def _connect(self, host: str, port: int, database: str, user: str, password: str):
        try:
            from clickhouse_driver import Client
            client = Client(
                host=host, port=port, database=database,
                user=user, password=password,
                connect_timeout=10,
            )
            return client, 'driver'
        except ImportError:
            pass

        try:
            import clickhouse_connect
            client = clickhouse_connect.get_client(
                host=host, port=8123, database=database,
                username=user, password=password,
                connect_timeout=10,
            )
            return client, 'connect'
        except ImportError:
            raise RuntimeError(
                "No ClickHouse driver found. Install one:\n"
                "  pip install clickhouse-driver\n"
                "  pip install clickhouse-connect"
            )

    def _execute(self, client, driver: str, sql: str, params: list = None) -> list[dict]:
        """Execute a query and return rows as dicts, normalising both driver APIs."""
        params = list(params or [])
        if driver == 'driver':
            rows, col_types = client.execute(sql, params, with_column_types=True)
            col_names = [c[0] for c in col_types]
            return [dict(zip(col_names, row)) for row in rows]
        else:
            # clickhouse-connect uses {name} style params — convert %s positionally
            idx = [0]
            def _repl(m):
                key = f"_p{idx[0]}"
                idx[0] += 1
                return f"{{{key}}}"
            named_sql  = re.sub(r'%s', _repl, sql)
            param_dict = {f"_p{i}": v for i, v in enumerate(params)}
            result     = client.query(named_sql, parameters=param_dict)
            return [dict(zip(result.column_names, row)) for row in result.result_rows]
