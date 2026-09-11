"""Keep bundled Python libraries out of independently installed office/CLI tools."""
import os
from pathlib import Path
import subprocess
import sys
from threading import Lock

SPAWN_LOCK=Lock()


def popen(args,env=None,**kwargs):
    if not getattr(sys,'frozen',False):
        return subprocess.Popen(args,env=env,**kwargs)
    env=dict(os.environ if env is None else env)
    bundle=os.path.normcase(str(Path(sys._MEIPASS).resolve()))
    def external(path):
        if not path:return False
        normalized=os.path.normcase(str(Path(path).resolve()))
        return normalized!=bundle and not normalized.startswith(bundle+os.sep)
    env['PATH']=os.pathsep.join(x for x in env.get('PATH','').split(os.pathsep) if external(x))
    for name in ('LD_LIBRARY_PATH','DYLD_LIBRARY_PATH'):
        original=env.pop(name+'_ORIG',None)
        if original is not None:env[name]=original
        else:env.pop(name,None)
    if sys.platform=='darwin':
        env['PATH']+=os.pathsep+'/opt/homebrew/bin'+os.pathsep+'/usr/local/bin'
    with SPAWN_LOCK:
        if sys.platform!='win32':return subprocess.Popen(args,env=env,**kwargs)
        import ctypes
        set_directory=ctypes.windll.kernel32.SetDllDirectoryW
        set_directory.argtypes=[ctypes.c_wchar_p]
        set_directory.restype=ctypes.c_int
        set_directory(None)
        try:return subprocess.Popen(args,env=env,**kwargs)
        finally:set_directory(sys._MEIPASS)
