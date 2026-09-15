"""Optional HTTPS listener for Office, sharing the existing process and Store lock."""
import json
from pathlib import Path
import threading
import time
import uvicorn


def start(app, root, http_port):
    path = root / 'word-connection.json'
    if not path.exists(): return None
    config = json.loads(path.read_text('utf-8'))
    port = config['port']
    if type(port) is not int or not 1024 <= port <= 65535 or port == http_port:
        raise ValueError('Некорректный HTTPS-порт Word.')
    for key in ('cert', 'key'):
        if not Path(config[key]).is_file(): raise ValueError('Не найден сертификат Word. Повторите настройку подключения.')
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
        ssl_certfile=config['cert'], ssl_keyfile=config['key'], access_log=False, log_level='warning', log_config=None))
    thread = threading.Thread(target=server.run, name='word-https', daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        raise ValueError('HTTPS-подключение Word не запустилось. Проверьте сертификат и свободный порт.')
    app.state.word_connection = {'origin': f'https://localhost:{port}', 'configured': True}
    return server, thread
