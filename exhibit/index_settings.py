"""Strict, JSON-compatible settings for the optional submission index."""
from copy import deepcopy
from typing import TypedDict
import unicodedata


class IndexSettings(TypedDict):
    enabled: bool
    language: str
    title: str
    formats: list[str]
    columns: list[str]
    order: str


TITLES = {'ru': 'Перечень приложений', 'en': 'List of Exhibits'}
DEFAULT: IndexSettings = dict(enabled=False, language='ru', title=TITLES['ru'],
                             formats=['docx', 'pdf'], columns=['pages'], order='identifier')


def validate(value) -> IndexSettings:
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Некорректные поля настройки перечня приложений.')
    if type(value['enabled']) is not bool or not isinstance(value['language'], str) or value['language'] not in TITLES:
        raise ValueError('Выберите язык RU/EN и состояние перечня.')
    title = value['title']
    if not isinstance(title, str) or not title.strip() or len(title) > 200 or any(unicodedata.category(c).startswith('C') for c in title):
        raise ValueError('Заголовок перечня: от 1 до 200 символов без управляющих знаков.')
    for field, allowed in [('formats', {'docx', 'pdf'}), ('columns', {'pages', 'path'})]:
        items = value[field]
        if not isinstance(items, list) or any(not isinstance(i, str) or i not in allowed for i in items) or len(set(items)) != len(items):
            raise ValueError('Некорректные форматы или колонки перечня.')
    if value['enabled'] and not value['formats']:
        raise ValueError('Выберите хотя бы один формат перечня: DOCX или PDF.')
    if not isinstance(value['order'], str) or value['order'] not in ('identifier', 'project', 'folder'):
        raise ValueError('Выберите порядок перечня: по обозначению, проекту или папкам.')
    return deepcopy(value)


def settings(project) -> IndexSettings:
    if project['schema'] < 3:
        if 'submission_index' in project:
            raise ValueError('Настройки перечня требуют формат проекта 3.')
        return deepcopy(DEFAULT)
    return validate(project.get('submission_index'))


def save(store, project, value):
    from .schema import upgrade
    config = validate(value)
    proposed = deepcopy(upgrade(store, project, target=3, persist=False))
    proposed['submission_index'] = config
    store.save(proposed)
    return proposed
