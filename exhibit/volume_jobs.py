"""Cancelable volume snapshots, bounded temporary files and atomic history."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread, Event, RLock
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED
import json
import shutil
import re
import os
import time

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from .project import LOCK, identifier, revision, safe_path
from .submission_index import fingerprint, paths_ok, ordered_rows
from . import volumes, volume_settings, volume_pages, pdf, main_pdf, history, index_settings, index_documents, word
from .library import atomic_json
from .backup import io_errors

STALE='Подача изменилась. Сохраните настройки и заново подготовьте PDF-тома.'


def process_alive(pid):
    if type(pid) is not int or pid<=0:return False
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes as w
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
        kernel.GetExitCodeProcess.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)]
        kernel.CloseHandle.argtypes=[w.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return ctypes.get_last_error()==5  # unknown/access denied: retain files
        try:
            code=w.DWORD()
            return not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value==259
        finally:kernel.CloseHandle(handle)
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True


class VolumeJobs:
    def __init__(self,store):
        self.store=store;self.lock=RLock();self.active={};self.plans={};self.downloads={};self.busy=set();self.by_project={};self.memory={}
        self.work=store.root/'.volume-work';self.work.mkdir(exist_ok=True)
        self.states=store.root/'.volume-jobs';self.states.mkdir(exist_ok=True)
        for root in (self.work,self.states):
            if root.is_symlink() or getattr(root,'is_junction',lambda:False)():raise ValueError('Временная папка томов является ссылкой. Файлы не изменены.')
        owner=self.states/'owner'
        try:previous_owner=json.loads(owner.read_text('utf-8')).get('pid')
        except (FileNotFoundError,ValueError,AttributeError):previous_owner=None
        if previous_owner is not None and not process_alive(previous_owner):
            for path in self.work.iterdir():
                if (path.is_dir() and re.fullmatch(r'(?:snapshot|download)-[A-Za-z0-9_-]+',path.name)
                    and not path.is_symlink() and not getattr(path,'is_junction',lambda:False)()
                    and path.resolve().parent==self.work.resolve()):shutil.rmtree(path)
        atomic_json(owner,{'pid':os.getpid()})
        for path in sorted(self.states.glob('*.json'),key=lambda p:p.stat().st_mtime):
            state=json.loads(path.read_text('utf-8'))
            if state.get('project_id'):self.by_project[state['project_id']]=state.get('id')
            if state.get('state') in ('running','queued'):
                atomic_json(path,{**state,'state':'interrupted','message':'Приложение остановлено. Повторите подготовку; прежние комплекты сохранены.'})

    def checked(self,pid):
        p=self.store.load(pid);config=volume_settings.settings(p)
        if not config['enabled']:raise ValueError('Включите и сохраните PDF-тома.')
        issues=self.store.validate(p)
        if issues:raise ValueError('Сборка заблокирована: '+issues[0]['message'])
        if not p['documents']:raise ValueError('Добавьте PDF-приложения для тома.')
        paths=[safe_path(d['folder'],d['filename']) for d in p['documents']]
        paths+=['Volumes/Volume map.json','Volumes/Volume 01.pdf']
        if p['main']:paths+=['Main document.docx','Main document.pdf']
        index=index_settings.settings(p)
        if index['enabled']:paths+=[f'Exhibit index.{fmt}' for fmt in index['formats']]
        paths_ok(paths)
        return deepcopy(p)

    def current(self,pid,token):
        p=self.checked(pid)
        if fingerprint(p)!=token:raise ValueError(STALE)
        for d in p['documents']:
            self.store.source(p,d['original'])
            if d.get('translation'):self.store.source(p,d['translation'])
        if p.get('main'):self.store.source(p,p['main'])
        return p

    def main_item(self,p,config,items):
        by_id={i['id']:i for i in items};targets={};ref_paths={};choices=[];issues=[]
        for n,ref in enumerate(p['references']):
            if ref.get('keep_original'):continue
            item=by_id[ref['target']];chosen=config['reference_pages'].get(ref['key'])
            if chosen and (chosen['document_id']!=item['id'] or chosen['revision']!=item['revision'] or chosen['page']>len(item['source_map'])):
                issues.append('Цель ссылки устарела: '+ref['mention']);chosen=None
            page=chosen['page'] if chosen else 1
            if not chosen and not config['accept_start_pages']:issues.append('Выберите страницу или подтвердите начало материала: '+ref['mention'])
            path=f"BLVolumeTargets/{uuid4().hex}-{n}.pdf";ref_paths[ref['key']]=path
            targets[path]={'document_id':item['id'],'page':page}
            choices.append({'key':ref['key'],'mention':ref['mention'],'footnote':ref.get('footnote'),
                            'document_id':item['id'],'revision':item['revision'],'page':page,'confirmed':bool(chosen),
                            'pages':len(item['source_map'])})
        paths={i['id']:i['path'] for i in items}
        data=word.add_links(self.store.source(p,p['main']),p['references'],paths,reference_paths=ref_paths)
        result=main_pdf.convert_docx(data,list(ref_paths.values()))
        count=len(PdfReader(BytesIO(result)).pages)
        item={'id':'main','title':'Основной документ','identifier':'','path':'Main document.pdf','data':result,
              'revision':p['main']['sha256'],'targets':targets,'pages':count,
              'source_map':[{'part':'main','source_sha256':p['main']['sha256'],'source_page':n+1,
                             'prepared_page':n+1,'rect':None,'occurrence':1} for n in range(count)]}
        return item,choices,issues

    @io_errors()
    def preview(self,pid,stop=lambda:False,progress=lambda *args:None):
        with LOCK:p=self.checked(pid);token=fingerprint(p)
        config=volume_settings.settings(p);items=[];bytes_used=0
        temporary=TemporaryDirectory(prefix='snapshot-',dir=self.work)
        try:
            for n,doc in enumerate(volumes.ordered(p,config)):
                volumes.cancelled(stop);progress(n,len(p['documents']),'Готовим приложение '+str(n+1))
                data=self.store.prepare(p,doc);bytes_used+=len(data)
                if bytes_used>volumes.MAX_INPUT_BYTES or shutil.disk_usage(self.work).free<bytes_used*4+32_000_000:
                    raise ValueError('Недостаточно временного места для PDF-томов; предел входных PDF — 512 МБ.')
                item={'id':doc['id'],'title':doc['title'],'identifier':identifier(doc,p),'path':safe_path(doc['folder'],doc['filename']),
                      'data':data,'revision':revision(p,doc),'source_map':volumes.source_pages(doc)}
                items.append(item)
            refs=[];issues=[]
            if config['include_main']:
                if not p['main']:raise ValueError('Основной DOCX не загружен. Отключите его включение в том.')
                progress(0,1,'Подготавливаем основной PDF')
                item,refs,issues=self.main_item(p,config,items);items.insert(0,item)
            files,mapping=volumes.build(items,config,stop,progress)
            paths_ok([i['path'] for i in items]+['Volumes/'+n for n in files]+['Volumes/Volume map.json'])
            root=Path(temporary.name)
            for n,item in enumerate(items):
                path=root/f'source-{n}.pdf';path.write_bytes(item.pop('data'));item['file']=str(path)
                item['sha256']=sha256(path.read_bytes()).hexdigest()
            for n,data in files.items():(root/n).write_bytes(data)
            volumes.cancelled(stop)
            with LOCK:self.current(pid,token)
            with self.lock:
                previous=self.plans.pop(pid,None)
                if previous:previous['temporary'].cleanup()
                # One snapshot per service bounds disk use across projects.
                for oldpid in list(self.plans):
                    if oldpid not in self.busy:self.plans.pop(oldpid)['temporary'].cleanup()
                self.plans[pid]={'token':token,'temporary':temporary,'items':items,'mapping':mapping,
                                 'references':refs,'issues':issues,'settings':config}
            return self.public(self.plans[pid])
        except BaseException:
            temporary.cleanup();raise

    def public(self,plan):
        return {'preview_token':plan['token'],'settings':plan['settings'],'rows':plan['mapping']['rows'],
                'volumes':plan['mapping']['volumes'],'page_map':plan['mapping']['pages'],
                'references':plan['references'],'issues':plan['issues']}

    def plan(self,pid,token):
        with self.lock:plan=self.plans.get(pid)
        if not plan or token!=plan['token']:raise ValueError(STALE)
        with LOCK:self.current(pid,token)
        return plan

    def read_volume(self,pid,token,index):
        plan=self.plan(pid,token)
        if type(index) is not int or not 0<=index<len(plan['mapping']['volumes']):raise ValueError('Том не найден.')
        meta=plan['mapping']['volumes'][index]
        data=(Path(plan['temporary'].name)/meta['file']).read_bytes()
        if sha256(data).hexdigest()!=meta['sha256']:raise ValueError('PDF-том изменён. Повторите подготовку.')
        return data

    @io_errors()
    def export_bytes(self,pid,token,stop=lambda:False,progress=lambda *args:None,with_receipt=False):
        plan=self.plan(pid,token)
        if plan['issues']:raise ValueError(plan['issues'][0])
        with LOCK:p=self.current(pid,token)
        out=BytesIO();entries={};files={}
        with ZipFile(out,'w',ZIP_DEFLATED) as archive:
            for n,item in enumerate(plan['items']):
                volumes.cancelled(stop);progress(n,len(plan['items']),'Добавляем отдельные файлы')
                data=Path(item['file']).read_bytes()
                if sha256(data).hexdigest()!=item['sha256']:raise ValueError('Подготовленный PDF изменён. Повторите preview.')
                if item['id']=='main':
                    writer=PdfWriter(clone_from=BytesIO(data))
                    for page in writer.pages:
                        for entry in page.get('/Annots',[]):
                            action=entry.get_object().get('/A')
                            if not action:continue
                            action=action.get_object();f=action.get('/F')
                            name=str(f.get_object().get('/UF','')) if f and hasattr(f,'get_object') and isinstance(f.get_object(),dict) else str(f or '')
                            target=item['targets'].get(name)
                            if target:
                                doc=self.store.document(p,target['document_id'])
                                entry.get_object()[NameObject('/A')]=volume_pages.file_action(safe_path(doc['folder'],doc['filename']),target['page']-1)
                    normal=BytesIO();writer.write(normal);data=normal.getvalue()
                else:entries[item['id']]={'pages':item['pages']}
                archive.writestr('Submission/'+item['path'],data)
            if p['main']:archive.writestr('Submission/Main document.docx',self.store.linked_main(p)[0])
            cfg=index_settings.settings(p)
            if cfg['enabled']:
                rows=ordered_rows(p,entries)
                for fmt in cfg['formats']:archive.writestr('Submission/Exhibit index.'+fmt,index_documents.generate(fmt,cfg,rows))
            for i,meta in enumerate(plan['mapping']['volumes']):
                volumes.cancelled(stop);data=self.read_volume(pid,token,i);files[meta['file']]=data
                archive.writestr('Submission/Volumes/'+meta['file'],data)
            volumes.verify(files,plan['mapping'])
            archive.writestr('Submission/Volumes/Volume map.json',json.dumps(plan['mapping'],ensure_ascii=False,indent=2).encode('utf-8'))
        volumes.cancelled(stop);data=out.getvalue()
        if shutil.disk_usage(self.store.root).free<len(data)*2+16_000_000:raise ValueError('Недостаточно места для сохранения истории комплекта.')
        with LOCK:self.current(pid,token);receipt=history.save_export(self.store,p,data)
        return (data,receipt) if with_receipt else data

    def status(self,token):
        if not isinstance(token,str) or not re.fullmatch('[a-f0-9]{32}',token):raise ValueError('Некорректное задание PDF-томов.')
        with self.lock:
            if token in self.memory:return deepcopy(self.memory[token])
        path=self.states/(token+'.json')
        if not path.is_file():raise ValueError('Задание PDF-томов не найдено.')
        return json.loads(path.read_text('utf-8'))

    @io_errors()
    def start(self,pid,action,preview_token=None):
        self.store.folder(pid)
        if action not in ('preview','export'):raise ValueError('Неизвестная операция PDF-томов.')
        with self.lock:
            if self.active:raise ValueError('Дождитесь или отмените текущую операцию PDF-томов.')
            token=uuid4().hex;event=Event();self.active[token]=event;self.busy.add(pid);self.by_project[pid]=token
        state={'id':token,'project_id':pid,'state':'queued','completed':0,'total':0,'message':'Подготавливаем…'}
        path=self.states/(token+'.json')
        try:atomic_json(path,state)
        except BaseException:
            with self.lock:self.active.pop(token,None);self.busy.discard(pid)
            raise
        self.memory[token]=state
        last_write=[0.0]
        def progress(done,total,message):
            with self.lock:
                state.update(state='running',completed=done,total=total,message=message)
                now=time.monotonic()
                if now-last_write[0]>=.1 or done==total:
                    atomic_json(path,state);last_write[0]=now
        def work():
            try:
                if action=='preview':result=self.preview(pid,event.is_set,progress)
                else:
                    data,receipt=self.export_bytes(pid,preview_token,event.is_set,progress,with_receipt=True)
                    # The immutable history file is already complete. A closed
                    # browser must not leave a second, unclaimed ZIP on disk.
                    with self.lock:self.downloads[token]=(pid,receipt['id'])
                    result={'download_token':token,'bytes':len(data),'export_id':receipt['id']}
                with self.lock:state.update(state='complete',result=result,message='PDF-тома готовы')
            except Exception as exc:
                with self.lock:state.update(state='cancelled' if event.is_set() else 'error',message=str(exc))
            finally:
                with self.lock:
                    try:atomic_json(path,state)
                    except OSError:state.update(state='error',message='Не удалось записать статус. Проверьте место на диске; готовый комплект ищите в истории.')
                    finally:self.active.pop(token,None);self.busy.discard(pid)
                    # Operation receipts are reproducible; retain only the newest 20.
                    finished=sorted((p for p in self.states.glob('*.json') if re.fullmatch('[a-f0-9]{32}',p.stem) and p.stem not in self.active),key=lambda p:p.stat().st_mtime,reverse=True)
                    for old in finished[20:]:
                        self.memory.pop(old.stem,None)
                        self.downloads.pop(old.stem,None)
                        try:old.unlink(missing_ok=True)
                        except OSError:pass  # A retained status must not fail a completed export.
        try:Thread(target=work,daemon=True,name='volume-'+token[:8]).start()
        except RuntimeError:
            with self.lock:
                self.active.pop(token,None);self.busy.discard(pid)
                state.update(state='error',message='Не удалось запустить сборку. Повторите попытку.')
                atomic_json(path,state)
            raise ValueError(state['message'])
        return {'id':token}

    def cancel(self,token):
        self.status(token)
        with self.lock:
            if token in self.active:self.active[token].set()
        return self.status(token)

    def download(self,token):
        self.status(token)
        with self.lock:receipt=self.downloads.get(token)
        if not receipt:raise ValueError('Скачивание завершено. Используйте историю комплектов.')
        pid,eid=receipt
        with LOCK:
            p=self.store.load(pid);history.read_export(self.store,p,eid)
        return self.store.folder(pid)/'exports'/eid/'Submission.zip'

    def release(self,token):
        with self.lock:self.downloads.pop(token,None)

    def close(self):
        with self.lock:
            for stopped in self.active.values():stopped.set()
            for plan in self.plans.values():plan['temporary'].cleanup()
            self.plans.clear();self.downloads.clear()


def manager(store):
    with LOCK:
        if not hasattr(store,'volume_jobs'):store.volume_jobs=VolumeJobs(store)
        return store.volume_jobs
