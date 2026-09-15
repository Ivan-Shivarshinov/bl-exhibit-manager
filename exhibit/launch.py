"""Launch and gracefully stop a source or self-contained loopback app."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen
import webbrowser

from . import __version__

WORKSPACE = Path(__file__).resolve().parent.parent


def health(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health",timeout=2) as response:
            info=json.load(response)
            return info if info.get("application")=="bl-exhibit-manager" else None
    except (URLError,OSError,ValueError):return None


def healthy(port):
    return health(port) is not None


def command(*args):
    return [sys.executable,*args] if getattr(sys,"frozen",False) else [sys.executable,"-m","exhibit.launch",*args]


def serve(port):
    import logging
    import uvicorn
    from .app import app
    from .project import ROOT
    ROOT.mkdir(parents=True,exist_ok=True)
    # Windowed executables can have no stderr, even when the parent redirects it.
    logging.basicConfig(filename=ROOT/"application.log",encoding="utf-8",
                        level=logging.WARNING,force=True,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,access_log=False,log_level="warning",log_config=None))
    from .word_tls import start as start_word
    word_server = start_word(app, ROOT, port)
    def stop_servers():
        server.should_exit = True
        if word_server: word_server[0].should_exit = True
    app.state.stop_server = stop_servers
    try: server.run()
    finally:
        stop_servers()
        if word_server: word_server[1].join(timeout=5)


def stop(port):
    if not healthy(port):return "Приложение уже остановлено."
    request=Request(f"http://127.0.0.1:{port}/api/application/stop",data=b"{}",
                    headers={"X-Exhibit-Local":"1","Content-Type":"application/json"},method="POST")
    try:
        with urlopen(request,timeout=5) as response:json.load(response)
    except HTTPError as exc:
        try:message=json.load(exc).get("detail","Не удалось остановить сервер.")
        except (ValueError,AttributeError):message="Не удалось остановить сервер."
        raise ValueError(message) from exc
    for _ in range(60):
        if not healthy(port):return "Приложение остановлено. Проекты сохранены."
        time.sleep(.25)
    raise ValueError("Сервер завершает текущую операцию. Подождите и повторите остановку.")


def launch(port,no_browser=False):
    if not (WORKSPACE/"web/dist/index.html").is_file():
        raise ValueError("Не найден интерфейс. Для исходников выполните npm ci --prefix web и npm run build --prefix web. Для готового приложения распакуйте весь ZIP.")
    info=health(port)
    if info and info.get("version")!=__version__:
        raise ValueError("Уже работает другая версия приложения. Сначала остановите её через Stop и запустите новую версию.")
    if not info:
        # Do not start a second server against an occupied/non-app port.
        with socket.socket() as probe:
            # Match asyncio's POSIX listener: a recently stopped server can leave
            # connections in TIME_WAIT while the listening port is already free.
            if os.name != "nt":probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            try:probe.bind(("127.0.0.1",port))
            except OSError as exc:raise ValueError(f"Порт {port} занят. Закройте прежний сервер или выберите --port.") from exc
        from .project import ROOT
        ROOT.mkdir(parents=True,exist_ok=True)
        log=ROOT/"application.log"
        if log.exists() and log.stat().st_size>2_000_000:log.replace(ROOT/"application.previous.log")
        options={"start_new_session":True} if os.name!="nt" else {"creationflags":subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW}
        env=os.environ.copy()
        if getattr(sys,"frozen",False):env["PYINSTALLER_RESET_ENVIRONMENT"]="1"
        with log.open("ab") as output:
            process=subprocess.Popen(command("--serve","--port",str(port)),cwd=WORKSPACE,env=env,
                stdin=subprocess.DEVNULL,stdout=output,stderr=output,close_fds=True,**options)
        for _ in range(100):
            if healthy(port):break
            if process.poll() is not None:raise ValueError(f"Сервер не запустился. Диагностика: {log}")
            time.sleep(.2)
        else:
            process.terminate()
            raise ValueError(f"Сервер не ответил вовремя. Диагностика: {log}")
    url=f"http://127.0.0.1:{port}"
    if not no_browser:webbrowser.open(url)
    return url


def notify(message,error=False):
    if sys.stdout:print(message)
    if not getattr(sys,"frozen",False):return
    if os.name=="nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,message,"BL Exhibit Manager",0x10 if error else 0x40)
    elif sys.platform=="darwin":
        # argv carries text, never interpolates it into AppleScript source.
        script='on run argv\ndisplay dialog (item 1 of argv) with title "BL Exhibit Manager" buttons {"OK"} default button "OK"\nend run'
        subprocess.run(["/usr/bin/osascript","-e",script,message],check=False)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--no-browser",action="store_true")
    parser.add_argument("--quiet",action="store_true")
    group=parser.add_mutually_exclusive_group();group.add_argument("--serve",action="store_true");group.add_argument("--stop",action="store_true")
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error("Port must be between 1024 and 65535")
    try:
        if args.serve:serve(args.port)
        elif args.stop:
            message=stop(args.port)
            if not args.quiet:notify(message)
        else:
            result=launch(args.port,args.no_browser)
            if sys.stdout:print(result)
        return 0
    except Exception as exc:
        if not args.quiet:notify(str(exc),error=True)
        elif sys.stderr:print(str(exc),file=sys.stderr)
        return 1


if __name__=="__main__":sys.exit(main())
