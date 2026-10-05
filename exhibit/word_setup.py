"""Local, opt-in Word setup. Never installs system trust or opens Office."""
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import threading
import time
from urllib.error import URLError
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

SETUP_LOCK = threading.RLock()
ERROR = 'Не удалось прочитать настройки Word. Проверьте сертификат и ключ прежнего подключения; обычная работа доступна.'


def atomic_write(path, data, mode=0o600):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.word-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def configuration(root, http_port=8765):
    try:
        config = json.loads((root / 'word-connection.json').read_text('utf-8'))
        port = config['port']
        if type(port) is not int or not 1024 <= port <= 65535 or port == http_port:
            raise ValueError('port')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(config['cert'], config['key'])
        certificate = x509.load_pem_x509_certificate(Path(config['cert']).read_bytes())
        now = datetime.now(timezone.utc)
        if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
            raise ValueError('expired')
        return config, certificate
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise ValueError(ERROR) from exc


def manifest(root, port):
    # Stable add-in identity keeps existing documents connected across upgrades.
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<OfficeApp xmlns="http://schemas.microsoft.com/office/appforoffice/1.1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="TaskPaneApp">
<Id>43a6f2bd-d10f-4c7a-91b5-f3938c499ac4</Id><Version>0.2.0.0</Version><ProviderName>Buzko Legal</ProviderName><DefaultLocale>ru-RU</DefaultLocale>
<DisplayName DefaultValue="BL Exhibit Manager"/><Description DefaultValue="Сноски и приложения локальной подачи"/>
<Hosts><Host Name="Document"/></Hosts><Requirements><Sets DefaultMinVersion="1.5"><Set Name="WordApi" MinVersion="1.5"/></Sets></Requirements>
<DefaultSettings><SourceLocation DefaultValue="https://localhost:{int(port)}/word/index.html"/></DefaultSettings><Permissions>ReadWriteDocument</Permissions>
</OfficeApp>'''
    path = root / 'BLExhibitManager.Word.xml'
    atomic_write(path, xml.encode('utf-8'), 0o644)
    return path


def configure(root, cert, key, port):
    """Compatibility entry point for developer-provided certificates."""
    if type(port) is not int or not 1024 <= port <= 65535 or port == 8765:
        raise ValueError('Выберите отдельный HTTPS-порт, например 8769.')
    cert, key = Path(cert).resolve(), Path(key).resolve()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    root.mkdir(parents=True, exist_ok=True)
    manifest(root, port)
    atomic_write(root / 'word-connection.json', json.dumps({'cert': str(cert), 'key': str(key), 'port': port}).encode())
    return root / 'BLExhibitManager.Word.xml'


def prepare(root, http_port, port=None):
    with SETUP_LOCK:
        root.mkdir(parents=True, exist_ok=True)
        if (root / 'word-connection.json').exists():
            config, _ = configuration(root, http_port)
            if not (root / 'BLExhibitManager.Word.xml').exists(): manifest(root, config['port'])
            return
        automatic = port is None
        if automatic:
            port = 8769
        if type(port) is not int or not 1024 <= port <= 65535 or port == http_port:
            if automatic:
                port = 0
            else:
                raise ValueError('Укажите свободный HTTPS-порт от 1024 до 65535, отличный от порта приложения.')
        try:
            with socket.socket() as probe:
                try:
                    probe.bind(('127.0.0.1', port))
                except OSError:
                    if not automatic:
                        raise
                    probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
        except OSError as exc:
            if not automatic:
                raise ValueError('HTTPS-порт занят. Выберите другой порт и повторите подготовку.') from exc
            raise ValueError('Не удалось выбрать HTTPS-порт. Закройте другие экземпляры приложения и повторите подготовку.') from exc
        if not 1024 <= port <= 65535 or port == http_port:
            raise ValueError('Укажите свободный HTTPS-порт от 1024 до 65535, отличный от порта приложения.')
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'BL Exhibit Manager localhost')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=365))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True,
                    data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                    encipher_only=False, decipher_only=False), critical=True)
                .sign(key, hashes.SHA256()))
        folder = root / 'word-local'
        # A unique directory avoids replacing keys left by an interrupted setup.
        folder.mkdir(mode=0o700, exist_ok=True)
        cert_path = folder / f'{cert.serial_number:x}.crt'
        key_path = cert_path.with_suffix('.key')
        try:
            atomic_write(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            atomic_write(cert_path, cert.public_bytes(serialization.Encoding.PEM), 0o644)
            manifest(root, port)
            atomic_write(root / 'word-connection.json', json.dumps({'cert': str(cert_path.resolve()), 'key': str(key_path.resolve()), 'port': port, 'generated': True}).encode())
        except OSError:
            cert_path.unlink(missing_ok=True)
            key_path.unlink(missing_ok=True)
            raise ValueError('Не удалось сохранить настройку Word. Проверьте доступ к папке данных и свободное место.')


def trust_context():
    if sys.platform != 'win32':
        return ssl.create_default_context()
    # Python's default Windows context imports both ROOT and CA as trust
    # anchors. That can trust our self-signed certificate in Intermediate CAs
    # even though Windows browsers reject it. Only ROOT can anchor this check.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    for cert, encoding, trust in ssl.enum_certificates('ROOT'):
        if encoding == 'x509_asn' and (trust is True or ssl.Purpose.SERVER_AUTH.oid in trust):
            try:
                context.load_verify_locations(cadata=cert)
            except ssl.SSLError:
                continue  # Ignore malformed unrelated roots, never bypass verification.
    return context


def status(root, app, verify=False):
    from .word_install import manager
    installation = manager(app, root).status()
    checked = getattr(app.state, 'word_https_check', {})
    # Saved installations recheck local HTTPS on reopening, and periodically.
    # This only reads system trust; consent remains in the installer.
    automatic = installation.get('installed') and time.monotonic() - checked.get('at', 0) >= 60
    result = _status(root, app, verify or automatic)
    result['installation'] = installation
    return result


def _status(root, app, verify=False):
    if not (root / 'word-connection.json').exists():
        return {'state': 'not_configured', 'message': 'Панель Word не подключена. Она необязательна.'}
    try:
        config, cert = configuration(root, getattr(app.state, 'http_port', 8765))
    except ValueError:
        return {'state': 'error', 'message': ERROR}
    result = {'state': 'stopped', 'message': 'Настройки сохранены, соединение остановлено. Повторите настройку панели.',
              'origin': f"https://localhost:{config['port']}", 'port': config['port'],
              'generated': config.get('generated') is True,
              'fingerprint': cert.fingerprint(hashes.SHA256()).hex().upper(),
              'expires': cert.not_valid_after_utc.date().isoformat()}
    listener = getattr(app.state, 'word_listener', None)
    running = bool(getattr(app.state, 'word_connection', {}).get('configured') and listener and listener[1].is_alive())
    panels = getattr(app.state, 'word_panels', None)
    result['panel'] = panels.status() if running and panels else {'state': 'unavailable', 'message': 'Запустите соединение панели с приложением.'}
    if running:
        result.update(state='running', message='HTTPS запущен. Его доверие и связь с Word проверяются отдельно.')
        checked = getattr(app.state, 'word_https_check', {})
        if checked.get('fingerprint') == result['fingerprint'] and time.monotonic() - checked.get('at', 0) < 60:
            result.update(state=checked['state'], message=checked['message'])
    if verify:
        try:
            # Direct loopback only, never system proxy or untrusted remote URL.
            context = trust_context()
            if sys.platform == 'darwin':
                from .word_install import NativeSettings
                if not NativeSettings().trusted(Path(config['cert'])):
                    raise ValueError('System Keychain trust is not confirmed')
                # OpenSSL doesn't read Keychain. A private CA is accepted here
                # only after the system SSL/hostname policy has verified it.
                context = ssl.create_default_context(cafile=config['cert'])
            from urllib.request import build_opener, ProxyHandler, HTTPSHandler
            opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
            with opener.open(result['origin'] + '/api/health', timeout=3) as response:
                health = json.loads(response.read(4096))
            if health.get('application') != 'bl-exhibit-manager': raise ValueError('Wrong listener')
            result.update(state='trusted', message='Защищённое соединение проверено через системное доверие. Связь с панелью внутри Word показана отдельно.')
        except (OSError, URLError, ValueError):
            result.update(state='needs_trust', message='Защищённое соединение не подтверждено. Повторите настройку панели и подтвердите запрос системы, если он появится. При ограничении организации можно продолжить работу без панели.')
        app.state.word_https_check = {'fingerprint': result['fingerprint'], 'state': result['state'], 'message': result['message'], 'at': time.monotonic()}
    return result
