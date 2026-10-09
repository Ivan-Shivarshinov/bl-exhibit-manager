"""Change one exact certificate via the protected Windows system-store provider.

The caller checks ownership and obtains consent. Windows owns its prompts.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


class CertificateError(ValueError):
    def __init__(self, operation, code):
        self.code = code & 0xffffffff
        super().__init__(f'Windows не разрешила {operation} (код {self.code:#010x}). Подключение панели не завершено; можно повторить.')


def add(der, *, admin=False, store='Root'):
    crypt = ctypes.WinDLL(str(Path(os.environ['SYSTEMROOT'])/'System32/crypt32.dll'), use_last_error=True)
    crypt.CertOpenStore.argtypes = [ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p]
    crypt.CertOpenStore.restype = ctypes.c_void_p
    crypt.CertAddEncodedCertificateToStore.argtypes = [ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
    crypt.CertAddEncodedCertificateToStore.restype = wintypes.BOOL
    crypt.CertCloseStore.argtypes = [ctypes.c_void_p,wintypes.DWORD]
    crypt.CertCloseStore.restype = wintypes.BOOL
    # Keep the protected provider and its user prompts. No UNPROTECTED flag.
    handle = crypt.CertOpenStore(10,0,None,(0x20000 if admin else 0x10000)|0x4000,ctypes.cast(ctypes.c_wchar_p(store),ctypes.c_void_p))
    if not handle: raise CertificateError('открыть хранилище сертификата', ctypes.get_last_error())
    try:
        encoded = ctypes.create_string_buffer(der)
        # ADD_NEW never replaces another certificate or its trust properties.
        if crypt.CertAddEncodedCertificateToStore(handle,1,encoded,len(der),1,None): return True
        error = ctypes.get_last_error() & 0xffffffff
        if error == 0x80092005: return False  # CRYPT_E_EXISTS: unchanged.
        raise CertificateError('добавить сертификат приложения', error)
    finally:
        crypt.CertCloseStore(handle,0)


def add_worker(cert_path, result_path, *, admin=False, store='Root'):
    """Private child mode: safe result works with windowed/frozen Python too."""
    import json
    from . import word_setup
    try:
        cert = word_setup.x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
        der = cert.public_bytes(word_setup.serialization.Encoding.DER)
        result = {'added': add(der, admin=admin, store=store)}
        code = 0
    except CertificateError as exc:
        result = {'error': str(exc), 'code': exc.code}; code = 1
    except (OSError, ValueError):
        result = {'error': 'Не удалось выполнить системное добавление сертификата. Повторите подключение; проекты сохранены.'}; code = 1
    word_setup.atomic_write(Path(result_path), json.dumps(result).encode('utf-8'))
    return code


def remove(der, *, admin=False, store='Root'):
    crypt = ctypes.WinDLL(str(Path(os.environ['SYSTEMROOT'])/'System32/crypt32.dll'), use_last_error=True)

    class Context(ctypes.Structure):
        _fields_ = [('encoding',wintypes.DWORD), ('encoded',ctypes.POINTER(ctypes.c_ubyte)),
                    ('length',wintypes.DWORD), ('info',ctypes.c_void_p), ('store',ctypes.c_void_p)]

    pointer = ctypes.POINTER(Context)
    crypt.CertOpenStore.argtypes = [ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p]
    crypt.CertOpenStore.restype = ctypes.c_void_p
    crypt.CertEnumCertificatesInStore.argtypes = [ctypes.c_void_p,pointer]
    crypt.CertEnumCertificatesInStore.restype = pointer
    crypt.CertDeleteCertificateFromStore.argtypes = [pointer]
    crypt.CertDeleteCertificateFromStore.restype = wintypes.BOOL
    crypt.CertFreeCertificateContext.argtypes = [pointer]
    crypt.CertFreeCertificateContext.restype = wintypes.BOOL
    crypt.CertCloseStore.argtypes = [ctypes.c_void_p,wintypes.DWORD]
    crypt.CertCloseStore.restype = wintypes.BOOL
    # SYSTEM_W honors the system-store provider. OPEN_EXISTING never creates a
    # store, and CURRENT_USER cannot elevate to the machine's writable store.
    handle = crypt.CertOpenStore(10,0,None,(0x20000 if admin else 0x10000)|0x4000,ctypes.cast(ctypes.c_wchar_p(store),ctypes.c_void_p))
    if not handle:
        raise ValueError(f'Windows не разрешила открыть хранилище сертификата (код {ctypes.get_last_error()}). Повторите удаление; проекты сохранены.')
    context = None
    try:
        while True:
            context = crypt.CertEnumCertificatesInStore(handle,context)
            if not context:
                error = ctypes.get_last_error() & 0xffffffff
                if error != 0x80092004:  # CRYPT_E_NOT_FOUND: enumeration completed.
                    raise ValueError(f'Windows не смогла проверить сертификаты (код {error}). Повторите удаление.')
                return False
            if ctypes.string_at(context.contents.encoded,context.contents.length) != der:
                continue
            # Delete consumes the context even on failure; do not free it twice.
            selected,context = context,None
            if not crypt.CertDeleteCertificateFromStore(selected):
                raise ValueError(f'Windows не разрешила удалить сертификат приложения (код {ctypes.get_last_error()}). Повторите удаление; проекты сохранены.')
            return True
    finally:
        if context: crypt.CertFreeCertificateContext(context)
        crypt.CertCloseStore(handle,0)
