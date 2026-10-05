"""Short-lived panel presence, separate from TLS and document processing."""
import secrets
import threading
import time


class WordPanels:
    lifetime = 45
    limit = 32

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.sessions = {}
        self.seen = False

    def _expire(self):
        now = self.clock()
        self.sessions = {token: value for token, value in self.sessions.items() if value[1] > now}

    def update(self, data):
        if not isinstance(data, dict):
            raise ValueError('Некорректное подтверждение панели Word.')
        action = data.get('action')
        with self.lock:
            self._expire()
            if action == 'open':
                if set(data) != {'action', 'host', 'supported'} or data['host'] != 'Word' or type(data['supported']) is not bool:
                    raise ValueError('Подтверждение доступно только панели внутри Word.')
                if len(self.sessions) >= self.limit:
                    raise ValueError('Открыто слишком много панелей. Закройте лишние панели и повторите подключение.')
                token = secrets.token_urlsafe(32)
                self.sessions[token] = (data['supported'], self.clock() + self.lifetime)
                self.seen = True
                return {'token': token}
            token = data.get('token')
            if set(data) != {'action', 'token'} or not isinstance(token, str) or not 32 <= len(token) <= 64:
                raise ValueError('Некорректное подтверждение панели Word.')
            if action == 'close':
                self.sessions.pop(token, None)
                return {'closed': True}
            if action != 'ping' or token not in self.sessions:
                raise ValueError('Сессия панели истекла. Подключитесь повторно.')
            supported, _ = self.sessions[token]
            self.sessions[token] = (supported, self.clock() + self.lifetime)
            return {'active': True}

    def clear(self):
        with self.lock:
            self.sessions.clear()

    def status(self):
        with self.lock:
            self._expire()
            if any(supported for supported, _ in self.sessions.values()):
                return {'state': 'connected', 'message': 'Панель открыта в Word и отвечает. Можно создавать сноски.'}
            if self.sessions:
                return {'state': 'unsupported', 'message': 'Панель открыта в Word, но нужные функции недоступны. Нужна версия Word с поддержкой WordApi 1.5, например Microsoft 365 или Office 2024.'}
            if self.seen:
                return {'state': 'disconnected', 'message': 'Панель закрыта или связь с Word потеряна. Откройте панель в Word; статус обновится автоматически.'}
            return {'state': 'waiting', 'message': 'Откройте панель внутри Word. Доступность HTTPS ещё не подтверждает подключение панели.'}
