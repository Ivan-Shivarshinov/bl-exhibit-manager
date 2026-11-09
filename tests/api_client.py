"""Business tests use real owner bootstrap; security tests use the raw TestClient."""
from fastapi.testclient import TestClient as RawClient


class TestClient(RawClient):
    def request(self, method, url, **kwargs):
        import exhibit.app as web
        target = self.base_url.join(url)
        aud = f'{target.scheme}:{target.port or (443 if target.scheme == "https" else 80)}'
        access = web.get_access()
        if getattr(self, '_owner_access', None) is not access or getattr(self, '_owner_aud', None) != aud:
            result = super().request('POST', '/api/access/session', json={'bootstrap': access.bootstrap(aud)}, headers={'X-Exhibit-Local': '1'})
            assert result.status_code == 200, result.text
            self._owner_token = result.json()['token']; self._owner_access = access; self._owner_aud = aud
        headers = dict(kwargs.pop('headers', None) or {})
        headers.setdefault('Authorization', 'Bearer ' + self._owner_token)
        return super().request(method, url, headers=headers, **kwargs)
