"""Rebuildable text-only cache. OCR revisions stay in the project archive."""
from contextlib import contextmanager
import sqlite3

from . import pdf


MAX_BYTES = 64 * 1024 * 1024
FORMAT = f'1-{pdf.pdfium.PDFIUM_INFO.build}'


class Index:
    def __init__(self, connection):
        self.connection = connection

    def get(self, source, page):
        if self.connection is None:
            return None
        try:
            row = self.connection.execute('SELECT value FROM pages WHERE source=? AND number=?', (source, page)).fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    def put(self, source, page, text):
        if self.connection is None:
            return False
        try:
            self.connection.execute('INSERT OR REPLACE INTO pages VALUES (?, ?, ?)', (source, page, text))
            return True
        except sqlite3.Error:
            return False


@contextmanager
def open_index(root):
    path = root / f'.material-text-{FORMAT}.sqlite3'
    connection = None
    try:
        if not path.is_symlink():
            connection = sqlite3.connect(path, timeout=.25, isolation_level=None)
            page_size = connection.execute('PRAGMA page_size').fetchone()[0]
            connection.execute(f'PRAGMA max_page_count={MAX_BYTES // page_size}')
            connection.execute('CREATE TABLE IF NOT EXISTS pages '
                               '(source TEXT NOT NULL, number INTEGER NOT NULL, value TEXT NOT NULL, '
                               'PRIMARY KEY(source, number)) WITHOUT ROWID')
    except (OSError, sqlite3.Error):
        if connection:
            connection.close()
        connection = None
    try:
        yield Index(connection)
    finally:
        if connection:
            connection.close()
