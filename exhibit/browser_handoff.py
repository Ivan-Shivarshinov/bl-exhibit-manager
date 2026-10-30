"""Open an owner-private handoff in the HTTP browser, not the HTML file editor."""
import ctypes
from ctypes import wintypes as w
import sys
import webbrowser


def windows_browser_class():
    query = ctypes.WinDLL('shlwapi', use_last_error=True).AssocQueryStringW
    query.argtypes = [w.DWORD, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    query.restype = ctypes.c_long
    length = w.DWORD()
    query(0x1000, 20, 'http', None, None, ctypes.byref(length))  # IS_PROTOCOL, ASSOCSTR_PROGID
    if not 0 < length.value <= 4096: raise OSError('No HTTP browser association')
    value = ctypes.create_unicode_buffer(length.value)
    if query(0x1000, 20, 'http', None, value, ctypes.byref(length)) != 0: raise OSError('Cannot read HTTP browser association')
    return value.value


def windows_open(uri):
    # https://learn.microsoft.com/windows/win32/api/shellapi/ns-shellapi-shellexecuteinfow
    class Execute(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('mask', w.ULONG), ('window', w.HWND), ('verb', w.LPCWSTR),
                    ('file', w.LPCWSTR), ('parameters', w.LPCWSTR), ('directory', w.LPCWSTR), ('show', ctypes.c_int),
                    ('instance', w.HINSTANCE), ('idlist', ctypes.c_void_p), ('class_name', w.LPCWSTR),
                    ('class_key', w.HKEY), ('hotkey', w.DWORD), ('icon', w.HANDLE), ('process', w.HANDLE)]
    execute = ctypes.WinDLL('shell32', use_last_error=True).ShellExecuteExW
    execute.argtypes = [ctypes.POINTER(Execute)]; execute.restype = w.BOOL
    value = Execute(); value.size = ctypes.sizeof(Execute)
    value.mask = 0x1 | 0x100 | 0x400  # CLASSNAME, NOASYNC, FLAG_NO_UI; never disable zone/security checks.
    value.verb = 'open'; value.file = uri; value.show = 1; value.class_name = windows_browser_class()
    if not execute(ctypes.byref(value)): raise OSError(ctypes.get_last_error(), 'Cannot open HTTP browser')


def mac_browser_bundle():
    # https://developer.apple.com/documentation/coreservices/1441725-lscopydefaulthandlerforurlscheme
    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    ls = ctypes.CDLL('/System/Library/Frameworks/CoreServices.framework/CoreServices')
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    ls.LSCopyDefaultHandlerForURLScheme.argtypes = [ctypes.c_void_p]
    ls.LSCopyDefaultHandlerForURLScheme.restype = ctypes.c_void_p
    scheme = cf.CFStringCreateWithCString(None, b'http', 0x08000100)
    if not scheme: raise OSError('Cannot query HTTP browser')
    handler = None
    try:
        handler = ls.LSCopyDefaultHandlerForURLScheme(scheme)
        value = ctypes.create_string_buffer(4096)
        if not handler or not cf.CFStringGetCString(handler, value, len(value), 0x08000100): raise OSError('No HTTP browser association')
        return value.value.decode('utf-8')
    finally:
        if handler: cf.CFRelease(handler)
        cf.CFRelease(scheme)


def open_entry(uri):
    if not uri.startswith('file:'): raise ValueError('Ожидалась приватная ссылка запуска.')
    try:
        controller = webbrowser.get()
        if sys.platform == 'win32' and type(controller).__name__ == 'WindowsDefault': windows_open(uri)
        elif sys.platform == 'darwin' and getattr(controller, 'name', '') == 'default':
            from .external_process import popen
            import subprocess
            process = popen(['/usr/bin/open', '-b', mac_browser_bundle(), uri], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if process.wait(timeout=15) != 0: raise OSError('Cannot open HTTP browser')
        elif not controller.open(uri): raise OSError('Cannot open browser')
    except (OSError, webbrowser.Error) as exc:
        raise ValueError('Не удалось открыть браузер. Проверьте выбор браузера для HTTP в настройках системы и повторите запуск.') from exc
