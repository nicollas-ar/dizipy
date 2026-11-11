"""Small SQL compatibility layer for the application's SQLite/Postgres queries.

Postgres reads autocommit; write blocks commit/rollback without closing the
request connection. A transaction advisory lock preserves BEGIN IMMEDIATE's
serialization across the web service and scheduler on the same database.
"""
import re
import sqlite3

import psycopg

IntegrityError = (sqlite3.IntegrityError, psycopg.IntegrityError)
AUTO_IDS = {'parish','app_user','member','message','audit'}


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def row_factory(cursor):
    names = [column.name for column in (cursor.description or ())]
    return lambda values: Row(zip(names, values))


class Result:
    def __init__(self, cursor, inserted=False):
        self.cursor = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = None
        if inserted and cursor.rowcount:
            row = cursor.fetchone()
            self.lastrowid = row['id'] if row else None

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def __iter__(self):
        return iter(self.cursor)


class Postgres:
    def __init__(self, url):
        self.connection = psycopg.connect(url, autocommit=True, row_factory=row_factory,
                                         connect_timeout=20, prepare_threshold=None)
        self.depth = 0

    def __enter__(self):
        if self.depth == 0:
            self.connection.execute('BEGIN')
        self.depth += 1
        return self

    def __exit__(self, kind, value, traceback):
        self.depth -= 1
        if self.depth == 0:
            self.connection.execute('ROLLBACK' if kind else 'COMMIT')

    def close(self):
        self.connection.close()

    def execute(self, query, params=()):
        if query.strip().upper() == 'BEGIN IMMEDIATE':
            # All app callers use a surrounding write context.
            if not self.depth:
                raise RuntimeError('A write context is required before locking.')
            return Result(self.connection.execute('SELECT pg_advisory_xact_lock(731905281)'))
        query = re.sub(r'\bCOLLATE NOCASE\b', '', query, flags=re.I)
        ignore = bool(re.match(r'\s*INSERT OR IGNORE\b', query, re.I))
        query = re.sub(r'\bINSERT OR IGNORE\b', 'INSERT', query, flags=re.I)
        if ignore:
            query += ' ON CONFLICT DO NOTHING'
        # Historical SQLite statements use double quotes only for string values.
        # All identifiers in the application schema are lower-case unquoted names.
        query = re.sub(r'"([^"\n]*)"', lambda m: "'"+m[1].replace("'", "''")+"'", query)
        query = query.replace('%', '%%').replace('?', '%s')
        match = re.match(r'\s*INSERT INTO (\w+)', query, re.I)
        inserted = bool(match and match[1] in AUTO_IDS and ' RETURNING ' not in query.upper())
        if inserted:
            query += ' RETURNING id'
        # Unknown placeholder types in `? IS NULL` are not inferable by PostgreSQL.
        query = query.replace('%s IS NULL', 'CAST(%s AS TEXT) IS NULL')
        return Result(self.connection.execute(query, params or None), inserted)

    def executescript(self, script):
        for query in script.split(';'):
            if query.strip():
                self.execute(query)
