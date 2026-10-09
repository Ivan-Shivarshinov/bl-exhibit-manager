"""Cancelable operations with durable status, independent of the UI request."""
from threading import Thread, Event, RLock
from uuid import uuid4
from pathlib import Path
import inspect

from .library import atomic_json, uid
from .backup import json_data


class Jobs:
    def __init__(self, library):
        self.library = library
        self.root = library.store.root/'.material-jobs'
        self.active = {}
        self.lock = RLock()
        self.actions = {'save_preview':library.save_preview,'publish':library.publish,
                        'import_preview':library.import_preview,'import_apply':library.import_apply,
                        'compare':library.compare,'update_preview':library.update_preview,'update_apply':library.update_apply}
        self.actions.update({name:getattr(library,name) for name in ('archive_save','archive_preview','archive_restore','upload_preview')})
        if self.root.exists():
            for path in self.root.glob('*.json'):
                state=json_data(path.read_bytes())
                if state.get('state') in ('running','queued'):
                    atomic_json(path,{**state,'state':'interrupted','message':'Операция прервана при остановке. Повторите действие; опубликованные данные сохраняются.'})

    def status(self, token):
        try:
            with self.lock: return json_data((self.root/(uid(token)+'.json')).read_bytes())
        except FileNotFoundError as exc: raise ValueError('Операция не найдена.') from exc

    def start(self, action, arguments):
        if action not in self.actions or not isinstance(arguments,dict): raise ValueError('Неизвестная операция библиотеки.')
        method=self.actions[action]
        try: inspect.signature(method).bind(**arguments)
        except TypeError as exc: raise ValueError('Некорректные параметры операции.') from exc
        if {'cancel','progress'} & set(arguments): raise ValueError('Некорректные параметры операции.')
        token=uuid4().hex; stopped=Event()
        state={'id':token,'state':'queued','message':'Начинаем…','completed':0,'total':0}
        path=self.root/(token+'.json');atomic_json(path,state)
        def progress(done,total,message):
            with self.lock:
                state.update(state='running',completed=done,total=total,message=message)
                atomic_json(path,state)
        def work():
            try:
                result=method(**arguments,cancel=stopped.is_set,progress=progress)
                state.update(state='complete',result=result,message='Готово',completed=state['total'])
            except Exception as exc:
                state.update(state='cancelled' if stopped.is_set() else 'error',message=str(exc))
            finally:
                with self.lock:
                    atomic_json(path,state)
                    self.active.pop(token,None)
        with self.lock: self.active[token]=stopped
        Thread(target=work,daemon=True,name='material-'+token[:8]).start()
        return {'id':token}

    def cancel(self, token):
        uid(token)
        with self.lock:
            if token in self.active: self.active[token].set()
        return self.status(token)

    def shutdown(self):
        with self.lock:
            for stopped in self.active.values(): stopped.set()
