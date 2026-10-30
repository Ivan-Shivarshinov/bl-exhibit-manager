"""Owner-only local credentials; chmod is not an ACL on Windows."""
from contextlib import contextmanager
import ctypes
import errno
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import stat
import tempfile
import secrets
import sys

ERROR = 'Не удалось защитить локальный сеанс. Проверьте владельца и права каталога .local-access в папке данных; доступ к проектам не открыт.'


class WindowsSecurity:
    def __init__(self):
        self.a = ctypes.WinDLL('advapi32', use_last_error=True)
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        p = ctypes.c_void_p
        signatures = {
            'OpenProcessToken': ([w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], w.BOOL),
            'GetTokenInformation': ([w.HANDLE, ctypes.c_int, p, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL),
            'ConvertSidToStringSidW': ([p, ctypes.POINTER(w.LPWSTR)], w.BOOL),
            'ConvertStringSecurityDescriptorToSecurityDescriptorW': ([w.LPCWSTR, w.DWORD, ctypes.POINTER(p), p], w.BOOL),
            'GetFileSecurityW': ([w.LPCWSTR, w.DWORD, p, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL),
            'GetSecurityDescriptorOwner': ([p, ctypes.POINTER(p), ctypes.POINTER(w.BOOL)], w.BOOL),
            'GetSecurityDescriptorDacl': ([p, ctypes.POINTER(w.BOOL), ctypes.POINTER(p), ctypes.POINTER(w.BOOL)], w.BOOL),
            'GetAce': ([p, w.DWORD, ctypes.POINTER(p)], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.a, name); fn.argtypes = args; fn.restype = result
        self.k.GetCurrentProcess.restype = w.HANDLE
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.k.LocalFree.argtypes = [p]; self.k.LocalFree.restype = p
        self.k.CreateDirectoryW.argtypes = [w.LPCWSTR, p]; self.k.CreateDirectoryW.restype = w.BOOL
        self.k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, p, w.DWORD, w.DWORD, w.HANDLE]
        self.k.CreateFileW.restype = w.HANDLE
        token = w.HANDLE()
        if not self.a.OpenProcessToken(self.k.GetCurrentProcess(), 8, ctypes.byref(token)): self.failed()
        try:
            needed = w.DWORD()
            self.a.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            buf = ctypes.create_string_buffer(needed.value)
            if not self.a.GetTokenInformation(token, 1, buf, len(buf), ctypes.byref(needed)): self.failed()
            self.sid = self.sid_text(ctypes.c_void_p.from_buffer(buf).value)
        finally: self.k.CloseHandle(token)

    @staticmethod
    def failed():
        raise OSError(ctypes.get_last_error(), 'Private credential ACL failed')

    def sid_text(self, sid):
        text = w.LPWSTR()
        if not self.a.ConvertSidToStringSidW(sid, ctypes.byref(text)): self.failed()
        try: return text.value
        finally: self.k.LocalFree(ctypes.cast(text, ctypes.c_void_p))

    @contextmanager
    def attributes(self):
        class Attributes(ctypes.Structure):
            _fields_ = [('length', w.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', w.BOOL)]
        sd = ctypes.c_void_p()
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f'O:{self.sid}D:P(A;OICI;FA;;;{self.sid})', 1, ctypes.byref(sd), None): self.failed()
        try:
            sa = Attributes(ctypes.sizeof(Attributes), sd, False)
            yield ctypes.byref(sa)
        finally: self.k.LocalFree(sd)

    def mkdir(self, path):
        with self.attributes() as sa:
            if not self.k.CreateDirectoryW(str(path), sa) and ctypes.get_last_error() != 183: self.failed()

    def open(self, path, new=False):
        import msvcrt
        with self.attributes() as sa:
            handle = self.k.CreateFileW(str(path), 0xC0000000, 3, sa, 1 if new else 4, 0x200080, None)
        if handle == ctypes.c_void_p(-1).value: self.failed()
        try: return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except BaseException:
            self.k.CloseHandle(handle); raise

    def check(self, path):
        needed = w.DWORD()
        self.a.GetFileSecurityW(str(path), 5, None, 0, ctypes.byref(needed))
        buf = ctypes.create_string_buffer(needed.value)
        if not self.a.GetFileSecurityW(str(path), 5, buf, len(buf), ctypes.byref(needed)): self.failed()
        owner, acl = ctypes.c_void_p(), ctypes.c_void_p()
        defaulted, present = w.BOOL(), w.BOOL()
        if not self.a.GetSecurityDescriptorOwner(buf, ctypes.byref(owner), ctypes.byref(defaulted)): self.failed()
        if self.sid_text(owner) != self.sid: raise ValueError(ERROR)
        if not self.a.GetSecurityDescriptorDacl(buf, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)): self.failed()
        # ACL header: revision/size/ACE count. A NULL or extra ACE can grant another user access.
        if not present.value or not acl.value or ctypes.c_ushort.from_address(acl.value + 4).value != 1:
            raise ValueError(ERROR)
        ace = ctypes.c_void_p()
        if not self.a.GetAce(acl, 0, ctypes.byref(ace)): self.failed()
        if (ctypes.c_ubyte.from_address(ace.value).value != 0
                or ctypes.c_uint32.from_address(ace.value + 4).value != 0x1F01FF
                or self.sid_text(ace.value + 8) != self.sid): raise ValueError(ERROR)


class MacSecurity:
    """Extended macOS ACLs can grant access despite mode 0600/0700."""
    def __init__(self):
        self.lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        p = ctypes.c_void_p
        for name, args, result in (
            ('acl_get_link_np', [ctypes.c_char_p, ctypes.c_int], p),
            ('acl_valid', [p], ctypes.c_int), ('acl_free', [p], ctypes.c_int),
            ('acl_get_entry', [p, ctypes.c_int, ctypes.POINTER(p)], ctypes.c_int),
            ('acl_get_tag_type', [p, ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
        ):
            fn = getattr(self.lib, name); fn.argtypes = args; fn.restype = result

    def check(self, path):
        ctypes.set_errno(0)
        acl = self.lib.acl_get_link_np(os.fsencode(path), 0x100)  # ACL_TYPE_EXTENDED
        if not acl:
            if ctypes.get_errno() == errno.ENOENT: return  # No extended ACL.
            raise ValueError(ERROR)
        try:
            if self.lib.acl_valid(acl) != 0: raise ValueError(ERROR)
            for index in range(128):  # Apple's ACL_MAX_ENTRIES; nonnegative entry indexes.
                entry = ctypes.c_void_p(); ctypes.set_errno(0)
                if self.lib.acl_get_entry(acl, index, ctypes.byref(entry)) != 0:
                    if ctypes.get_errno() == errno.EINVAL: return  # End of a validated ACL.
                    raise ValueError(ERROR)
                tag = ctypes.c_int()
                if self.lib.acl_get_tag_type(entry, ctypes.byref(tag)) != 0 or tag.value != 2:
                    raise ValueError(ERROR)  # Only DENY entries are safe; never repair user ACLs.
        finally: self.lib.acl_free(acl)


class PrivateFiles:
    def __init__(self, root):
        self.folder = Path(root) / '.local-access'
        self.windows = WindowsSecurity() if os.name == 'nt' else None
        self.mac = MacSecurity() if sys.platform == 'darwin' else None
        try:
            Path(root).mkdir(parents=True, exist_ok=True)
            if self.windows: self.windows.mkdir(self.folder)
            else: self.folder.mkdir(mode=0o700, exist_ok=True)
            self.check(self.folder, directory=True)
        except (OSError, ValueError) as exc: raise ValueError(ERROR) from exc

    def check(self, path, directory=False):
        value = path.lstat()
        if (getattr(value, 'st_file_attributes', 0) & 0x400
                or not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))
                or not directory and value.st_nlink != 1): raise ValueError(ERROR)
        if self.windows: self.windows.check(path)
        elif value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) != (0o700 if directory else 0o600):
            raise ValueError(ERROR)
        if self.mac: self.mac.check(path)

    @contextmanager
    def credentials(self):
        """Serialize first creation and word-grant rotation across owner launchers."""
        path = self.folder / 'credentials.json'
        try:
            self.check(self.folder, directory=True)
            if path.exists() or path.is_symlink(): self.check(path)
            fd = self.windows.open(path) if self.windows else os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'r+b') as stream:
                self.check(path)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX)
                try:
                    raw = stream.read(2049)
                    if len(raw) > 2048: raise ValueError(ERROR)
                    data = json.loads(raw) if raw else None
                    yield data, stream
                finally:
                    stream.seek(0)
                    if os.name == 'nt': msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else: fcntl.flock(stream, fcntl.LOCK_UN)
        except (OSError, ValueError) as exc: raise ValueError(ERROR) from exc

    def write_browser(self, port, content):
        self.check(self.folder, directory=True)
        path = self.folder / f'browser-{int(port)}.html'
        if path.exists() or path.is_symlink(): self.check(path)
        if self.windows:
            name = str(self.folder / ('.browser-' + secrets.token_hex(16)))
            fd = self.windows.open(name, new=True)
        else: fd, name = tempfile.mkstemp(dir=self.folder, prefix='.browser-')
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(content.encode('utf-8')); out.flush(); os.fsync(out.fileno())
            self.check(Path(name))
            os.replace(name, path)
        finally:
            if os.path.exists(name): os.unlink(name)
        return path
