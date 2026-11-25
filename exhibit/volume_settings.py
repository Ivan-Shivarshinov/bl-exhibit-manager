"""Strict portable settings for optional PDF volumes (project schema 4)."""
from copy import deepcopy
import re

DEFAULT = {'enabled': False, 'include_main': False, 'dividers': True,
           'order': 'identifier', 'custom_order': [], 'start': 1,
           'number_format': '{page}', 'position': 'band',
           'max_pages': 0, 'max_mb': 0, 'reference_pages': {}, 'accept_start_pages': False}
ID = re.compile(r'[a-f0-9]{32}')


def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Некорректные поля настройки PDF-томов.')
    for k in ('enabled', 'include_main', 'dividers', 'accept_start_pages'):
        if type(value[k]) is not bool: raise ValueError('Некорректный переключатель PDF-томов.')
    for k, low, high in [('start', 1, 1_000_000), ('max_pages', 0, 10_000), ('max_mb', 0, 1024)]:
        if type(value[k]) is not int or not low <= value[k] <= high:
            raise ValueError('Некорректный номер или предел PDF-тома: '+k)
    if value['order'] not in ('identifier', 'project', 'folder', 'custom') or value['position'] not in ('band', 'top-right', 'bottom-right', 'bottom-center'):
        raise ValueError('Выберите порядок и положение номера PDF-тома.')
    fmt = value['number_format']
    if not isinstance(fmt, str) or len(fmt) > 60 or fmt.count('{page}') != 1 or '{' in fmt.replace('{page}', '') or '}' in fmt.replace('{page}', '') or any(ord(c) < 32 for c in fmt):
        raise ValueError('Подпись номера должна содержать {page} один раз, до 60 знаков.')
    ids = value['custom_order']
    if not isinstance(ids, list) or len(ids) > 2000 or any(not isinstance(i, str) or not ID.fullmatch(i) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Некорректный список порядка приложений.')
    targets = value['reference_pages']
    if not isinstance(targets, dict) or len(targets) > 20_000: raise ValueError('Слишком много целей ссылок.')
    for key, target in targets.items():
        if not isinstance(key, str) or not key or len(key) > 200 or any(ord(c) < 32 for c in key) or not isinstance(target, dict) or set(target) != {'document_id', 'revision', 'page'}:
            raise ValueError('Некорректная цель ссылки PDF-тома.')
        if not isinstance(target['document_id'], str) or not ID.fullmatch(target['document_id']) or not isinstance(target['revision'], str) or not re.fullmatch('[a-f0-9]{64}', target['revision']) or type(target['page']) is not int or not 1 <= target['page'] <= 10_000:
            raise ValueError('Некорректная страница или версия цели PDF-тома.')
    return deepcopy(value)


def settings(project):
    if project.get('schema', 1) < 4:
        if 'volume_settings' in project: raise ValueError('Настройки PDF-томов требуют формат проекта 4.')
        return deepcopy(DEFAULT)
    return validate(project.get('volume_settings'))


def save(store, project, value):
    from .schema import upgrade
    config = validate(value)
    updated = deepcopy(upgrade(store, project, target=4, persist=False))
    updated['volume_settings'] = config
    store.save(updated)
    return updated
