"""Build a native pilot ZIP; run separately on each target OS/architecture."""
from pathlib import Path
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from exhibit import __version__


def notices(destination):
    destination.mkdir(parents=True,exist_ok=True)
    inventory=[]
    for dist in metadata.distributions():
        name=dist.metadata['Name'];version=dist.version
        inventory.append({'name':name,'version':version,'license':dist.metadata.get('License-Expression') or dist.metadata.get('License','See included notices')})
        for relative in dist.files or []:
            path=Path(str(relative))
            if any(piece.lower().startswith(('license','copying','notice','copyright')) for piece in path.parts) and '..' not in path.parts:
                source=Path(dist.locate_file(relative))
                if source.is_file():
                    target=destination/name/path;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    for candidate in (Path(sys.base_prefix)/'LICENSE.txt',Path(sys.base_prefix)/'LICENSE',Path(sys.base_prefix)/'Resources/Python.app/Contents/Resources/LICENSE.txt'):
        if candidate.is_file():shutil.copyfile(candidate,destination/'PYTHON-LICENSE.txt');break
    else:
        # Standard CPython license, retrieved by the build only; no runtime network dependency.
        from urllib.request import urlopen
        with urlopen(f'https://raw.githubusercontent.com/python/cpython/v{platform.python_version()}/LICENSE',timeout=30) as response:
            (destination/'PYTHON-LICENSE.txt').write_bytes(response.read())
    shutil.copyfile(ROOT/'exhibit/assets/FONT-LICENSE.txt',destination/'DejaVu-FONT-LICENSE.txt')
    (destination/'inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),'utf-8')


def main():
    os.chdir(ROOT)
    system={'win32':'windows','darwin':'macos'}.get(sys.platform)
    if not system:raise SystemExit('Build on Windows or macOS.')
    arch='arm64' if platform.machine().lower() in ('arm64','aarch64') else 'x64'
    target=f'{system}-{arch}';build=ROOT/'build'/target;dist=ROOT/'dist'/target
    # All replaceable build output stays within this repository's known build directories.
    for path in (build,dist):
        if not path.resolve().is_relative_to(ROOT):raise SystemExit('Unsafe build path')
        path.mkdir(parents=True,exist_ok=True)
    if not (ROOT/'web/dist/index.html').is_file():raise SystemExit('Build web/dist first.')
    args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed','--noupx',
        '--name','BLExhibitManager','--distpath',str(dist),'--workpath',str(build/'work'),'--specpath',str(build),
        '--paths',str(ROOT),'--add-data',f'{ROOT / "web/dist"}{os.pathsep}web/dist',
        '--add-data',f'{ROOT / "exhibit/assets"}{os.pathsep}exhibit/assets',
        '--add-data',f'{ROOT / "exhibit/word_pdf.ps1"}{os.pathsep}exhibit',
        '--collect-all','pypdfium2','--collect-all','pypdfium2_raw','--collect-data','docx','--collect-data','certifi']
    if system=='macos':args+=['--osx-bundle-identifier','org.bl-exhibit-manager.desktop']
    subprocess.run([*args,str(ROOT/'scripts/desktop_entry.py')],check=True)
    stage=build/f'BLExhibitManager-{__version__}-{target}'
    if stage.exists():
        if not stage.resolve().is_relative_to(build.resolve()):raise SystemExit('Unsafe staging path')
        shutil.rmtree(stage)
    stage.mkdir()
    if system=='windows':
        shutil.copytree(dist/'BLExhibitManager',stage,dirs_exist_ok=True)
        (stage/'Start.cmd').write_text('@echo off\ncd /d "%~dp0"\nstart "" "BLExhibitManager.exe"\n',encoding='utf-8')
        (stage/'Stop.cmd').write_text('@echo off\ncd /d "%~dp0"\n"BLExhibitManager.exe" --stop\n',encoding='utf-8')
    else:
        shutil.copytree(dist/'BLExhibitManager.app',stage/'BLExhibitManager.app',symlinks=True)
        stop=stage/'Stop.command';stop.write_text('#!/bin/sh\ncd "$(dirname "$0")" || exit 1\nexec ./BLExhibitManager.app/Contents/MacOS/BLExhibitManager --stop\n','utf-8');stop.chmod(0o755)
    for name in ('LICENSE','NOTICE'):shutil.copyfile(ROOT/name,stage/name)
    shutil.copyfile(ROOT/'docs/PILOT.md',stage/'READ-ME-FIRST.md')
    notices(stage/'THIRD-PARTY-NOTICES')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True,cwd=ROOT).strip()
    (stage/'build-info.json').write_text(json.dumps({'version':__version__,'target':target,'commit':commit,'python':platform.python_version()},indent=2),'utf-8')
    release=ROOT/'release';release.mkdir(exist_ok=True)
    archive=release/(stage.name+'.zip')
    if system=='macos':subprocess.run(['/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',str(stage),str(archive)],check=True)
    else:shutil.make_archive(str(archive.with_suffix('')),'zip',stage.parent,stage.name)
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{checksum}  {archive.name}\n','utf-8')
    print(f'BUNDLE={archive}')


if __name__=='__main__':main()
