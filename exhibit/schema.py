"""Guarded metadata-only schema upgrades; no invented legacy project format."""
from copy import deepcopy
from uuid import uuid4

CURRENT = 1
UPGRADES = {}


def check_version(version):
    if type(version) is not int or version < 1 or version > CURRENT:
        raise ValueError('Версия проекта не поддерживается. Обновите приложение; файлы не изменены.')
    if any(v not in UPGRADES for v in range(version, CURRENT)):
        raise ValueError('Для формата проекта нет безопасного преобразования. Файлы не изменены.')


def upgrade(store, project):
    """Only project.json changes; immutable source/history bytes remain in place."""
    from .backup import save_archive, check_project, inspect_archive
    from .project import LOCK
    with LOCK:
        check_version(project.get('schema'))
        if project['schema'] == CURRENT: return project
        folder = store.root / 'backups'; folder.mkdir(exist_ok=True)
        path = folder / f"before-upgrade-{project['id']}-{uuid4().hex}.zip"
        # save_archive normally loads the project; use a non-migrating view to avoid recursion.
        class View:
            root = store.root
            def folder(self, pid): return store.folder(pid)
            def load(self, pid): return project
        from .backup import io_errors
        with io_errors(): save_archive(View(), project['id'], path)
        checked = inspect_archive(path)
        updated = deepcopy(project)
        try:
            while updated['schema'] < CURRENT:
                version = updated['schema']
                updated = UPGRADES[version](deepcopy(updated))
                if updated.get('schema') != version + 1 or updated.get('id') != project['id']:
                    raise ValueError('Преобразование вернуло некорректный проект.')
            from zipfile import ZipFile
            with ZipFile(path) as archive: check_project(updated, checked['files'], archive.read)
            updated['schema_backup'] = 'backups/'+path.name
            store.save(updated)
        except Exception as exc:
            raise ValueError(f'Обновление проекта не выполнено. Прежнее состояние сохранено; резервная копия: {path}') from exc
        return updated
