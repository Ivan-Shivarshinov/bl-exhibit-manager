"""Actual SumatraPDF link clicks on a disposable Windows CI desktop only.

Never controls the developer/user desktop. The generated index and six PDFs
must match bytes exported by the verified 0.6.0 Windows package. Native window
messages are used only for zoom/properties; link activation uses mouse input.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from zipfile import ZipFile


def wait_for(fn, description, seconds=15):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=fn()
        if value:return value
        time.sleep(.15)
    raise AssertionError('Timed out: '+description)


class Desktop:
    def __init__(self):
        self.u=C.WinDLL('user32',use_last_error=True);self.k=C.WinDLL('kernel32',use_last_error=True)
        self.u.SetProcessDPIAware()
        self.callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
        self.u.EnumWindows.argtypes=[self.callback,W.LPARAM]
        self.u.EnumChildWindows.argtypes=[W.HWND,self.callback,W.LPARAM]
        self.u.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
        self.u.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
        self.u.GetClassNameW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
        self.u.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)]
        self.u.IsWindowVisible.argtypes=[W.HWND]
        self.u.ShowWindow.argtypes=[W.HWND,C.c_int]
        self.u.SetWindowPos.argtypes=[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT]
        self.u.SetForegroundWindow.argtypes=[W.HWND]
        self.u.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
        self.u.OpenClipboard.argtypes=[W.HWND]
        self.u.GetClipboardData.argtypes=[W.UINT];self.u.GetClipboardData.restype=W.HANDLE
        self.k.GlobalLock.argtypes=[W.HGLOBAL];self.k.GlobalLock.restype=C.c_void_p
        self.k.GlobalUnlock.argtypes=[W.HGLOBAL]

    def windows(self,pid):
        result=[]
        @self.callback
        def collect(hwnd,unused):
            owner=W.DWORD();self.u.GetWindowThreadProcessId(hwnd,C.byref(owner))
            if owner.value==pid and self.u.IsWindowVisible(hwnd):
                title=C.create_unicode_buffer(2048);cls=C.create_unicode_buffer(256)
                self.u.GetWindowTextW(hwnd,title,len(title));self.u.GetClassNameW(hwnd,cls,len(cls))
                result.append({'hwnd':hwnd,'title':title.value,'class':cls.value})
            return True
        self.u.EnumWindows(collect,0);return result

    def capture(self,hwnd):
        from PIL import ImageGrab
        rect=W.RECT();assert self.u.GetWindowRect(hwnd,C.byref(rect))
        return ImageGrab.grab(bbox=(rect.left,rect.top,rect.right,rect.bottom)),rect

    def children(self,hwnd):
        result=[]
        @self.callback
        def collect(child,unused):
            text=C.create_unicode_buffer(2048);self.u.GetWindowTextW(child,text,len(text))
            result.append({'hwnd':child,'text':text.value});return True
        self.u.EnumChildWindows(hwnd,collect,0);return result

    def click(self,x,y):
        class Mouse(C.Structure):
            _fields_=[('dx',W.LONG),('dy',W.LONG),('data',W.DWORD),('flags',W.DWORD),('time',W.DWORD),('extra',C.c_size_t)]
        class Payload(C.Union):_fields_=[('mouse',Mouse)]
        class Input(C.Structure):_fields_=[('kind',W.DWORD),('payload',Payload)]
        width=self.u.GetSystemMetrics(0);height=self.u.GetSystemMetrics(1)
        assert 0<=x<width and 0<=y<height,(x,y,width,height)
        values=(Input*3)()
        for i,flags in enumerate((0x8001,0x8003,0x8005)):
            values[i].kind=0;values[i].payload.mouse=Mouse(round(x*65535/(width-1)),round(y*65535/(height-1)),0,flags,0,0)
        self.u.SendInput.argtypes=[W.UINT,C.c_void_p,C.c_int]
        assert self.u.SendInput(3,C.byref(values),C.sizeof(Input))==3,'Native mouse input was unavailable'

    def clipboard(self):
        if not self.u.OpenClipboard(None):return ''
        try:
            handle=self.u.GetClipboardData(13)
            if not handle:return ''
            pointer=self.k.GlobalLock(handle)
            if not pointer:return ''
            try:return C.wstring_at(pointer)
            finally:self.k.GlobalUnlock(handle)
        finally:self.u.CloseClipboard()


def page_origin(image,scale):
    """Find the actual white page, excluding titlebar/toolbar and grey canvas."""
    rgb=image.convert('RGB');expected=595.2756*scale
    for y in range(55,min(image.height-1,240)):
        run=None
        for x in range(image.width+1):
            white=x<image.width and min(rgb.getpixel((x,y)))>=250
            if white and run is None:run=x
            if not white and run is not None:
                length=x-run
                if abs(length-expected)<8 and run>10:return run,y
                run=None
    return None


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--viewer-zip',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if not (os.name=='nt' and os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('RUNNER_OS')=='Windows'):
        raise SystemExit('Refusing GUI automation: disposable hosted Windows CI only. Never run on a workstation.')
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from exhibit import index_documents,index_settings
    from exhibit.samples import make_pdf
    from pypdf import PdfReader
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    tools=output.parent/'viewer-tools';tools.mkdir(exist_ok=True)
    assert sha256(args.viewer_zip.read_bytes()).hexdigest()=='98b33a518d42986856d225064b0cd2d3643ecf78cbf84ab873d26cc51877a544'
    with ZipFile(args.viewer_zip) as z:
        assert z.namelist()==['SumatraPDF-3.6.1-64.exe'];z.extractall(tools)
    exe=tools/'SumatraPDF-3.6.1-64.exe'
    cases=[(1,'','Annex 1.pdf'),(2,'Материалы','Annex 2.pdf'),(3,'Percent 50%','Annex 3%.pdf'),(4,'Hash #','Annex 4.pdf'),(12,"Материалы # 50%/O'Brien & Co",'Annex 12%.pdf'),(13,"Материалы # 50%/O'Brien & Co",'Annex 13%.pdf')]
    rows=[{'path':(folder+'/' if folder else '')+name,'identifier':f'Annex {n}','title':f'Target {n}','pages':1} for n,folder,name in cases]
    config={**index_settings.DEFAULT,'enabled':True,'language':'ru','title':'Проверка переходов','formats':['docx','pdf'],'columns':['pages','path'],'order':'identifier'}
    data=index_documents.generate('pdf',config,rows)
    assert sha256(data).hexdigest()=='e1c3fe48fc32c2ae4c63cc121b2398fdfd0b534571e218be67f40ec1e0aefe80','Index differs from native 0.6.0 export'
    before=output/'Before move/Submission';before.mkdir(parents=True)
    (before/'Exhibit index.pdf').write_bytes(data)
    for n,folder,name in cases:
        path=before/folder/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(make_pdf(f'Annex {n}',[[f'SYNTHETIC TARGET {n}',f'Annex {n}']]))
    after=output/'После переноса/Submission';shutil.copytree(before,after)
    checksums={str(p.relative_to(before)):sha256(p.read_bytes()).hexdigest() for p in before.rglob('*.pdf')}
    desktop=Desktop();report={'viewer_version':'3.6.1','environment':'Hosted Windows CI; native application with automated mouse clicks, not user desktop','index_matches_native_package':True,'cases':[]}
    annotations=PdfReader(BytesIO(data)).pages[0]['/Annots'];page_height=float(PdfReader(BytesIO(data)).pages[0].mediabox.height)
    for location,folder in (('before',before),('after',after)):
        for number in (12,13):
            label=f'{location}-{number}'
            proc=subprocess.Popen([str(exe),'-new-window','-zoom','100',str(folder/'Exhibit index.pdf')],cwd=tools)
            try:
                window=wait_for(lambda:next((w for w in desktop.windows(proc.pid) if 'Exhibit index.pdf' in w['title']),None),'index window')
                hwnd=window['hwnd'];desktop.u.ShowWindow(hwnd,9);desktop.u.SetWindowPos(hwnd,W.HWND(-1),10,10,1000,700,0x40);desktop.u.SetForegroundWindow(hwnd)
                desktop.u.PostMessageW(hwnd,0x111,285,0) # 3.6.1 CmdZoom100
                scale=96/72
                def rendered():
                    image,bounds=desktop.capture(hwnd);origin=page_origin(image,scale)
                    if origin:return image,bounds,origin
                    image.save(output/(label+'-waiting.png'));return None
                image,bounds,origin=wait_for(rendered,'rendered PDF page')
                image.save(output/(label+'-index.png'))
                target=next(r['path'] for r in rows if r['identifier']==f'Annex {number}')
                action=next(a.get_object() for a in annotations if str(a.get_object()['/A']['/F']['/UF'])==target)
                x0,y0,x1,y1=map(float,action['/Rect'])
                x=bounds.left+origin[0]+(x0+x1)/2*scale;y=bounds.top+origin[1]+(page_height-(y0+y1)/2)*scale
                desktop.click(x,y)
                opened=wait_for(lambda:next((w for w in desktop.windows(proc.pid) if f'Annex {number}%.pdf' in w['title']),None),'clicked PDF target')
                desktop.capture(opened['hwnd'])[0].save(output/(label+'-target.png'))
                desktop.u.PostMessageW(opened['hwnd'],0x111,217,0) # 3.6.1 CmdProperties
                props=wait_for(lambda:next((w for w in desktop.windows(proc.pid) if w['class']=='SUMATRA_PDF_PROPERTIES'),None),'native document properties')
                desktop.capture(props['hwnd'])[0].save(output/(label+'-properties.png'))
                controls=desktop.children(props['hwnd'])
                copy=next(c for c in controls if 'copy' in c['text'].lower())
                desktop.u.PostMessageW(copy['hwnd'],0xF5,0,0) # BM_CLICK on the native Copy To Clipboard button.
                expected=str((folder/target).resolve())
                def copied():
                    text=desktop.clipboard()
                    (output/(label+'-clipboard.json')).write_text(json.dumps({'properties':text,'expected':expected,'controls':controls},ensure_ascii=False,indent=2),'utf-8')
                    return text if expected.casefold() in text.replace('/','\\').casefold() else None
                properties=wait_for(copied,'actual opened file path')
                report['cases'].append({'location':location,'number':number,'path':expected,'title':opened['title'],'properties':properties,'passed':True})
                print(json.dumps(report['cases'][-1],ensure_ascii=True),flush=True)
            finally:
                proc.terminate();proc.wait(timeout=15)
                (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    for folder in (before,after):
        for name,digest in checksums.items():assert sha256((folder/name).read_bytes()).hexdigest()==digest
    report['source_files_unchanged']=True
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')


if __name__=='__main__':main()
