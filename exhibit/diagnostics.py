"""Support data from an allowlist, not a redacted log or project snapshot."""
import platform
from . import __version__, main_pdf, translation_cli

STATES = {'available', 'missing', 'needs_login', 'error', 'unchecked', 'not_configured', 'running', 'trusted', 'needs_trust', 'stopped'}


def converter():
    found = main_pdf.available()
    engine = found.get('engine') if found.get('engine') in ('word', 'libreoffice') else None
    return {'state': 'available' if found.get('available') else 'missing', 'engine': engine,
            'message': 'Конвертер найден. Качество PDF проверьте после сборки.' if found.get('available') else 'Для основного PDF установите LibreOffice и повторите проверку. На Windows также поддерживается установленный Microsoft Word.'}


def provider(name):
    if name not in ('claude', 'codex'): raise ValueError('Неизвестный переводчик.')
    try: translation_cli.executable(name)
    except (ValueError, OSError):
        return {'state': 'missing', 'message': f'Установите официальный {name.capitalize()} CLI, затем выполните вход через личную подписку.'}
    result = translation_cli.status(name)
    state = 'available' if result.get('ready') else result.get('state', 'error')
    return {'state': state if state in STATES else 'error', 'message': {
        'available': 'CLI и вход доступны. Проверка не обращается к модели.',
        'needs_login': 'Выполните вход через личную подписку в официальном CLI и повторите проверку.',
    }.get(state, 'Проверка CLI не завершилась успешно. Обновите CLI, проверьте вход и повторите.')}


def report(components):
    system = platform.system()
    arch = platform.machine().lower()
    result = {'application': 'bl-exhibit-manager', 'version': __version__,
            'platform': system if system in ('Windows', 'Darwin', 'Linux') else 'Other',
            'architecture': arch if arch in ('amd64', 'x86_64', 'arm64', 'aarch64') else 'Other',
            'components': {name: {'state': value.get('state') if isinstance(value.get('state'), str) and value.get('state') in STATES else 'error'}
                           for name, value in components.items() if name in ('converter', 'word', 'claude', 'codex')}}
    panel = components.get('word', {}).get('panel')
    if isinstance(panel, dict):
        panel_state = panel.get('state')
        result['components']['word']['panel_state'] = panel_state if panel_state in ('connected', 'unsupported', 'disconnected', 'waiting', 'unavailable', 'setup_required') else 'unavailable'
    installation = components.get('word', {}).get('installation')
    if isinstance(installation, dict):
        phase = installation.get('phase')
        result['components']['word']['installation_state'] = phase if phase in ('not_installed','needs_update','preparing','system_confirmation','registering','opening_word','waiting_word','cancelling','cancelled','error','removing','removed') else 'error'
    return result
