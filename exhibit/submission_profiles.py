"""Named immutable rule versions. A bound version travels inside its project."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
import re,json

from .project import LOCK,digest,check_format,effective_format,DEFAULT_LABELS,DEFAULT_STYLE,NEW_LAYOUT,identifier,safe_path
from .word_bridge import check_settings as citation_settings,settings as citation_style
from .volume_settings import validate as volume_validate,settings as volume_settings
from .library import atomic_json
from .backup import json_data
from .batch import snapshot
from .submission_index import fingerprint

ID=re.compile('[a-f0-9]{32}')
MAX_BYTES=1_000_000


def uid(value):
    if not isinstance(value,str) or not ID.fullmatch(value):raise ValueError('Некорректный идентификатор профиля.')
    return value


def validate_rules(value):
    if not isinstance(value,dict) or set(value)!={'format','citation_style','naming','folder','prefix','volumes'}:
        raise ValueError('Некорректные правила профиля.')
    check_format(value['format']);citation_settings(value['citation_style']);volume_validate(value['volumes'])
    if value['naming'] not in ('keep','identifier','identifier_title'):raise ValueError('Некорректное правило имён профиля.')
    if value['folder'] is not None:
        if not isinstance(value['folder'],str):raise ValueError('Некорректная папка профиля.')
        safe_path(value['folder'],'check.pdf')
    if value['prefix'] is not None and (not isinstance(value['prefix'],str) or value['prefix'] and not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*',value['prefix'])):
        raise ValueError('Некорректный префикс профиля.')
    v=value['volumes']
    if v['reference_pages'] or v['custom_order'] or v['order']=='custom' or v['accept_start_pages']:
        raise ValueError('Профиль не должен содержать частные назначения страниц/материалов.')
    return deepcopy(value)


def validate_version(value):
    keys={'format','profile_id','version_id','previous_version','name','created_at','rules','hash'}
    if not isinstance(value,dict) or set(value)!=keys or type(value['format']) is not int or value['format']!=1:raise ValueError('Неподдерживаемый формат версии профиля.')
    uid(value['profile_id']);uid(value['version_id'])
    if value['previous_version'] is not None:uid(value['previous_version'])
    if not isinstance(value['name'],str) or not value['name'].strip() or len(value['name'])>120 or any(ord(c)<32 for c in value['name']):
        raise ValueError('Название профиля: от 1 до 120 знаков.')
    if not isinstance(value['created_at'],str) or len(value['created_at'])>50:raise ValueError('Некорректная дата профиля.')
    validate_rules(value['rules'])
    if value['hash']!=digest({k:v for k,v in value.items() if k!='hash'}):raise ValueError('Контрольная сумма профиля не совпадает.')
    return deepcopy(value)


def capture(project,naming='keep',folder=None,prefix=None):
    volumes=volume_settings(project);volumes.update(custom_order=[],reference_pages={},accept_start_pages=False)
    if volumes['order']=='custom':volumes['order']='project'
    return validate_rules({'format':{**NEW_LAYOUT,**DEFAULT_LABELS,**project.get('format',{}),**project['style']},
                           'citation_style':citation_style(project),'naming':naming,'folder':folder,'prefix':prefix,'volumes':volumes})


class Profiles:
    def __init__(self,store):self.store=store;self.root=store.root/'.submission-profiles';self.root.mkdir(exist_ok=True)

    def read(self,pid):
        path=self.root/(uid(pid)+'.json')
        if not path.is_file():raise ValueError('Профиль не найден.')
        return self.validate_package(json_data(path.read_bytes()))

    def validate_package(self,package):
        if not isinstance(package,dict) or set(package)!={'application','format','profile_id','versions'} or package['application']!='bl-exhibit-profile' or type(package['format']) is not int or package['format']!=1:
            raise ValueError('Это не переносимый профиль BL Exhibit Manager.')
        uid(package['profile_id']);versions=package['versions']
        if not isinstance(versions,list) or not 1<=len(versions)<=100:raise ValueError('Профиль содержит от 1 до 100 версий.')
        seen=set();previous=None
        for value in versions:
            v=validate_version(value)
            if v['profile_id']!=package['profile_id'] or v['version_id'] in seen or v['previous_version']!=previous:
                raise ValueError('Нарушена цепочка версий профиля.')
            seen.add(v['version_id']);previous=v['version_id']
        if len(json.dumps(package,ensure_ascii=False).encode())>MAX_BYTES:raise ValueError('Профиль превышает 1 МБ.')
        return deepcopy(package)

    def list(self):
        rows=[]
        for path in sorted(self.root.glob('*.json')):
            if not ID.fullmatch(path.stem):continue
            p=self.read(path.stem);v=p['versions'][-1]
            rows.append({'id':p['profile_id'],'name':v['name'],'version_id':v['version_id'],'versions':len(p['versions'])})
        return rows

    def create(self,name,rules,pid=None):
        rules=validate_rules(rules)
        with LOCK:
            package=self.read(pid) if pid else {'application':'bl-exhibit-profile','format':1,'profile_id':uuid4().hex,'versions':[]}
            value={'format':1,'profile_id':package['profile_id'],'version_id':uuid4().hex,
                   'previous_version':package['versions'][-1]['version_id'] if package['versions'] else None,
                   'name':name,'created_at':datetime.now(timezone.utc).isoformat(),'rules':rules}
            value['hash']=digest(value);validate_version(value)
            package['versions'].append(value);self.validate_package(package)
            atomic_json(self.root/(package['profile_id']+'.json'),package)
            return package

    def import_package(self,raw):
        if len(raw)>MAX_BYTES:raise ValueError('Профиль превышает 1 МБ.')
        try:decoded=json_data(raw)
        except ValueError as exc:raise ValueError('Файл профиля повреждён или содержит некорректный JSON.') from exc
        package=self.validate_package(decoded);path=self.root/(package['profile_id']+'.json')
        with LOCK:
            if path.exists():
                existing=self.read(package['profile_id']);a,b=existing['versions'],package['versions']
                length=min(len(a),len(b))
                if a[:length]!=b[:length]:raise ValueError('Профиль с этим ID содержит другие версии. Ничего не заменено.')
                if len(a)>=len(b):return existing
            atomic_json(path,package)
        return package

    def version(self,pid,vid):
        for version in self.read(pid)['versions']:
            if version['version_id']==vid:return version
        raise ValueError('Версия профиля не найдена.')

    def preview(self,project_id,profile_id,version_id,options):
        if not isinstance(options,dict) or set(options)!={'reset_overrides','apply_paths','apply_prefix'} or any(type(v) is not bool for v in options.values()):
            raise ValueError('Некорректные действия применения профиля.')
        with LOCK:
            original=self.store.load(project_id);version=self.version(profile_id,version_id)
            p=deepcopy(original)
            from .schema import UPGRADES
            while p['schema']<4:p=UPGRADES[p['schema']](p)
            rules=version['rules']
            from .batch import plan
            p,_=plan(self.store,p,{'action':'format','scope':'project','values':rules['format'],'reset_overrides':options['reset_overrides']})
            p['citation_style']=deepcopy(rules['citation_style'])
            oldvol=volume_settings(p);p['volume_settings']=deepcopy(rules['volumes'])
            p['volume_settings']['reference_pages']=oldvol['reference_pages']
            for doc in p['documents']:
                changes={}
                if options['apply_prefix'] and rules['prefix'] is not None:changes['prefix']=rules['prefix']
                if options['apply_paths'] and rules['folder'] is not None:changes['folder']=rules['folder']
                if changes:self.store.update(p,doc['id'],changes,persist=False)
            if options['apply_paths'] and rules['naming']!='keep':
                p,_=plan(self.store,p,{'action':'organize','document_ids':[d['id'] for d in p['documents']],'names':rules['naming']})
            p['applied_profile']=version
            conflicts=[v for v in self.store.validate(p) if v['code'] in ('duplicate_id','duplicate_path','path_hierarchy','source','main')]
            changes=[]
            for old,doc in zip(original['documents'],p['documents']):
                a,b=snapshot(original,old),snapshot(p,doc)
                if a!=b:changes.append({'id':doc['id'],'title':doc['title'],'before':a,'after':b})
            settings_changes=[{'field':k,'before':original.get(k),'after':p.get(k)} for k in ('format','style','citation_style','volume_settings') if original.get(k)!=p.get(k)]
            token=digest({'project':fingerprint(original),'profile':version,'options':options})
            return p,{'token':token,'profile':version,'changes':changes,'settings_changes':settings_changes,'conflicts':conflicts,
                      'warnings':['Местные исключения и ручные имена сохранены.' if not options['reset_overrides'] else 'Выбран явный сброс индивидуального оформления.',
                                  'Изменённые результаты и ссылки требуют проверки. Другие подачи не меняются.']}

    def apply(self,project_id,profile_id,version_id,options,token):
        with LOCK:
            p,report=self.preview(project_id,profile_id,version_id,options)
            if token!=report['token']:raise ValueError('Подача или профиль изменились. Повторите preview профиля.')
            if report['conflicts']:raise ValueError('Профиль не применён: '+report['conflicts'][0]['message'])
            from .schema import upgrade
            upgraded=upgrade(self.store,self.store.load(project_id),target=4,persist=False)
            if upgraded.get('schema_backup'):p['schema_backup']=upgraded['schema_backup']
            self.store.save(p)
            return p
