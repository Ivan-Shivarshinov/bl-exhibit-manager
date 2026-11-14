"""Preview final assignments and rewrite only explicitly known DOCX ranges."""
from copy import deepcopy
from datetime import datetime,timezone
from difflib import SequenceMatcher
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED
import re
from lxml import etree as E

from . import word,citations,backup
from .project import LOCK,identifier,effective_document,revision,safe_path,digest
from .identifiers import stamp_label
from .word_bridge import link as managed_link,parse_link
from .batch import snapshot
from .submission_index import fingerprint

FIELDS={'number','prefix','designation','folder','filename'}


def validate_project(project):
    if project.get('schema',1)<4:return
    sources=project.get('assignment_sources');operations=project.get('assignment_operations')
    if not isinstance(sources,list) or not isinstance(operations,list) or len(sources)>2000 or len(operations)>2000:
        raise ValueError('Некорректное происхождение назначений подачи.')
    blobs=[]
    for source in sources:
        if not isinstance(source,dict) or not re.fullmatch('[a-f0-9]{64}\\.docx',str(source.get('blob',''))):raise ValueError('Некорректный исходный DOCX назначений.')
        blobs.append(source['blob'])
    if len(set(blobs))!=len(blobs):raise ValueError('Повтор исходного DOCX в происхождении назначений.')
    keys={'id','created_at','backup','changed_ids','original_main','derived_main','word_pending'}
    seen=set()
    for op in operations:
        if not isinstance(op,dict) or set(op)!=keys or not re.fullmatch('[a-f0-9]{32}',str(op.get('id',''))) or op['id'] in seen:
            raise ValueError('Повреждена запись изменения назначений.')
        seen.add(op['id'])
        if (not isinstance(op['created_at'],str) or len(op['created_at'])>50 or type(op['word_pending']) is not bool
            or not isinstance(op['backup'],str) or not re.fullmatch(r'backups/(?:before-upgrade|before-assignments)-[a-f0-9]{32}-[a-f0-9]{32}\.zip',op['backup'])):
            raise ValueError('Некорректные сведения изменения назначений.')
        ids=op['changed_ids']
        if not isinstance(ids,list) or not 1<=len(ids)<=2000 or any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{32}',v) for v in ids) or len(set(ids))!=len(ids):
            raise ValueError('Некорректные материалы изменения назначений.')
        for key in ('original_main','derived_main'):
            if op[key] is not None and (not isinstance(op[key],str) or not re.fullmatch('[a-f0-9]{64}',op[key])):
                raise ValueError('Некорректная версия DOCX изменения назначений.')


def replace_range(paragraph,start,end,replacement,anchor):
    if paragraph.xpath('.//w:fldChar | .//w:fldSimple | .//w:ins | .//w:del | .//w:sdt',namespaces=word.NS):
        raise ValueError('Поле, исправление или элемент управления Word: измените эту ссылку вручную.')
    word.split_layout_runs(paragraph,start,max(end,start+1))
    # Walk visible text in the same order as word.text_of; reject an affected
    # atom instead of converting it to text or discarding its structure.
    nodes=[];position=0
    def collect(node):
        nonlocal position
        if node.tag==f'{{{word.W}}}t':
            text=node.text or '';nodes.append((node,position,position+len(text)));position+=len(text)
        elif node.tag in word.RUN_ATOMS:position+=len(word.text_of(node))
        else:
            for child in node:collect(child)
    collect(paragraph)
    if start==end:
        candidates=[v for v in nodes if v[1]<start<=v[2] and v[2]>anchor]
        if not candidates:candidates=[v for v in nodes if v[1]<=start<v[2]]
        if not candidates:raise ValueError('Не найдено безопасное место вставки обозначения.')
        node,a,b=candidates[-1];text=node.text or '';i=start-a;node.text=text[:i]+replacement+text[i:]
        node.set('{http://www.w3.org/XML/1998/namespace}space','preserve');return
    affected=[v for v in nodes if v[1]<end and v[2]>start]
    if sum(min(end,b)-max(start,a) for _,a,b in affected)!=end-start:
        raise ValueError('Обозначение содержит нетекстовый элемент Word. Измените его вручную.')
    for i,(node,a,b) in enumerate(affected):
        text=node.text or '';node.text=text[:max(0,start-a)]+(replacement if i==0 else '')+text[min(len(text),end-a):]
        node.set('{http://www.w3.org/XML/1998/namespace}space','preserve')


def candidate(ref,old,new,original,proposed):
    old_effective,_=effective_document(original,old);new_effective,_=effective_document(proposed,new)
    pairs=[(stamp_label(old_effective),stamp_label(new_effective)),(identifier(old,original),identifier(new,proposed))]
    for before,after in sorted(set(pairs),key=lambda p:-len(p[0])):
        if not before:continue
        pattern=r'(?<![\w-])'+r'\s+'.join(re.escape(t) for t in before.split())+r'(?![\w-])'
        matches=list(re.finditer(pattern,ref['mention'],re.I))
        if len(matches)!=1:continue
        match=matches[0];actual=match.group()
        if re.sub(r'\d+$','',before).casefold()==re.sub(r'\d+$','',after).casefold():
            digits=re.search(r'\d+$',after)
            if digits:after=re.sub(r'\d+$',digits.group(),actual)
        return ref['start']+match.start(),ref['start']+match.end(),after
    if not identifier(old,original) and identifier(new,proposed) and ref.get('managed'):
        return ref['start'],ref['start'],identifier(new,proposed)+', '
    raise ValueError('Обозначение не найдено однозначно в известном упоминании. Измените эту строку в Word или явно оставьте её.')


def rewrite(data,references,edits,project_id):
    parts=word.package(data);root,paragraphs=word.paragraphs(parts)
    rels=word.xml(parts[word.RELS]) if word.RELS in parts else E.Element(f'{{{word.REL}}}Relationships')
    rel_by_id={r.get('Id'):r for r in rels};used=set(rel_by_id)
    edit_groups={}
    for edit in edits:edit_groups.setdefault((edit['ref']['fid'],edit['ref']['paragraph']),[]).append(edit)
    texts={};all_events={};changed_links={}
    for fid,ordinal,pi,paragraph in paragraphs:
        key=(fid,pi);text=word.text_of(paragraph);pending=edit_groups.get(key,[])
        events=[];managed_nodes=[]
        pos=0
        for child in paragraph:
            size=len(word.text_of(child))
            for edit in pending:
                ref=edit['ref']
                if ref.get('managed') and child.tag==f'{{{word.W}}}hyperlink' and pos==ref['start'] and pos+size==ref['end']:
                    managed_nodes.append((child,ref))
            pos+=size
        for edit in sorted(pending,key=lambda v:v['start'],reverse=True):
            a,b,value=edit['start'],edit['end'],edit['replacement']
            if text[edit['ref']['start']:edit['ref']['end']]!=edit['ref']['mention']:
                raise ValueError(f'Сноска {ordinal}: исходный диапазон изменился.')
            # Only changed characters are replaced: surrounding emphasis/runs
            # and unchanged spelling/case survive even inside the identifier.
            before=text[a:b]
            ops=SequenceMatcher(a=before,b=value,autojunk=False).get_opcodes()
            for op,x0,x1,y0,y1 in reversed(ops):
                if op!='equal':replace_range(paragraph,a+x0,a+x1,value[y0:y1],a)
            events.append((a,b,len(value)-(b-a),edit['ref']['key']))
        for node,ref in managed_nodes:
            rid=node.get(f'{{{word.R}}}id');relation=rel_by_id.get(rid)
            meta=parse_link(relation.get('Target','')) if relation is not None else None
            if not meta:raise ValueError('Управляемая ссылка Word потеряла идентичность.')
            origin=urlparse(relation.get('Target'))._replace(path='',query='',fragment='').geturl().rstrip('/')
            address=managed_link(origin,project_id,ref['target'],word.text_of(node),meta['form'],meta['citation'])
            i=1
            while f'rIdAssignment{i}' in used:i+=1
            fresh=f'rIdAssignment{i}';used.add(fresh)
            clone=deepcopy(relation);clone.set('Id',fresh);clone.set('Target',address);rels.append(clone)
            node.set(f'{{{word.R}}}id',fresh);changed_links[ref['key']]=parse_link(address)
        texts[key]=word.text_of(paragraph);all_events[key]=events
    def shifted(position,events,owner,end=False):
        return position+sum(delta for a,b,delta,key in events if b<position or b==position and (a<b or end and key==owner))
    transformed=[]
    for old in references:
        ref=deepcopy(old);key=(ref['fid'],ref['paragraph']);events=all_events.get(key,[]);text=texts[key]
        # Own insertions at the beginning belong inside this reference.
        start=old['start']+sum(d for a,b,d,k in events if b<=old['start'] and k!=old['key'])
        end=shifted(old['end'],events,old['key'],True)
        left=old.get('link_start',old['start'])+sum(d for a,b,d,k in events if b<=old.get('link_start',old['start']) and k!=old['key'])
        right=shifted(old.get('link_end',old['end']),events,old['key'],True)
        ref.update(start=start,end=end,mention=text[start:end],key=f"{ref['fid']}:{ref['paragraph']}:{start}:{end}",
                   link_start=left,link_end=right,link_text=text[left:right],custom=True,manual=True,scope_manual=True)
        if old['key'] in changed_links:ref['managed']=changed_links[old['key']]
        transformed.append(ref)
    parts[word.FOOT]=E.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    if word.RELS in parts or len(rels):parts[word.RELS]=E.tostring(rels,xml_declaration=True,encoding='UTF-8',standalone=True)
    out=BytesIO()
    with ZipFile(out,'w') as archive:
        for name,value in parts.items():
            info=ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=ZIP_DEFLATED;archive.writestr(info,value)
    return out.getvalue(),transformed,texts,all_events


def plan(store,pid,request):
    if not isinstance(request,dict) or set(request)!={'rows','naming','references'} or request['naming'] not in ('keep','identifier','identifier_title') or not isinstance(request['references'],dict):
        raise ValueError('Некорректный план назначений.')
    rows=request['rows']
    if not isinstance(rows,list) or not rows or len(rows)>2000 or any(not isinstance(r,dict) or set(r)-({'id'}|FIELDS) or 'id' not in r for r in rows):
        raise ValueError('Выберите материалы и корректные назначения.')
    if any(not isinstance(r['id'],str) or not re.fullmatch('[a-f0-9]{32}',r['id']) for r in rows):raise ValueError('Некорректный идентификатор материала назначения.')
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('Повтор материала в назначениях.')
    if any(v not in ('update','keep') for v in request['references'].values()):raise ValueError('Выберите обновление либо сохранение упоминания.')
    original=store.load(pid);p=deepcopy(original)
    from .schema import UPGRADES
    while p['schema']<4:p=UPGRADES[p['schema']](p)
    for row in rows:
        doc=store.document(p,row['id']);changes={k:v for k,v in row.items() if k!='id' and doc.get(k)!=v}
        if changes:store.update(p,doc['id'],changes,persist=False)
        if request['naming']!='keep' and doc.get('filename_mode')!='manual' and 'filename' not in changes and identifier(doc,p):
            title=re.sub(r'[<>:"/\\|?*\x00-\x1f]','-',doc['title']).strip(' .')[:90].rstrip(' .')
            name=identifier(doc,p)+(' — '+title if request['naming']=='identifier_title' and title else '')+'.pdf'
            if name!=doc['filename']:store.update(p,doc['id'],{'filename':name,'filename_mode':'generated'},persist=False)
    changes=[];old_docs={d['id']:d for d in original['documents']};new_docs={d['id']:d for d in p['documents']}
    for old in original['documents']:
        doc=new_docs[old['id']];before,after=snapshot(original,old),snapshot(p,doc)
        if before!=after:changes.append({'id':doc['id'],'title':doc['title'],'before':before,'after':after})
    conflicts=[v for v in store.validate(p) if v['code'] in ('duplicate_id','duplicate_path','path_hierarchy','source','main')]
    references=[];edits=[];known={r['key'] for r in original['references']}
    if set(request['references'])-known:raise ValueError('Строки сносок изменились. Повторите проверку назначений.')
    for ref in original['references']:
        if ref.get('keep_original') or not ref.get('target'):continue
        old,new=old_docs[ref['target']],new_docs[ref['target']]
        if identifier(old,original)==identifier(new,p) and stamp_label(effective_document(original,old)[0])==stamp_label(effective_document(p,new)[0]):continue
        decision=request['references'].get(ref['key'], 'update' if ref.get('managed') and not ref.get('scope_review') else None)
        result={'key':ref['key'],'footnote':ref['footnote'],'before':ref['mention'],'after':ref['mention'],'decision':decision}
        if decision=='keep':result['status']='kept'
        else:
            try:
                a,b,replacement=candidate(ref,old,new,original,p)
                after=ref['mention'][:a-ref['start']]+replacement+ref['mention'][b-ref['start']:]
                result['after']=after;result['status']='update' if decision=='update' else 'needs_decision'
                if decision=='update':edits.append({'ref':ref,'start':a,'end':b,'replacement':replacement})
                else:conflicts.append({'code':'reference_decision','message':f"Сноска {ref['footnote']}: выберите обновить или оставить текст."})
            except ValueError as exc:
                result.update(status='blocked',message=str(exc));conflicts.append({'code':'reference','message':f"Сноска {ref['footnote']}: {exc}"})
        references.append(result)
    derived=None
    if changes and original.get('main'):
        if not original.get('scanned'):conflicts.append({'code':'references','message':'Сначала сопоставьте ссылки основного DOCX.'})
        elif not any(v['code'] in ('reference','reference_decision') for v in conflicts):
            try:
                data=store.source(original,original['main'])
                count=len(original['references'])
                derived,all_updated,texts,events=rewrite(data,original['references']+original.get('excluded_references',[]),edits,pid)
                updated,exclusions=all_updated[:count],all_updated[count:]
                documents=[{**d,'identifier':identifier(d,p)} for d in p['documents']]
                found=word.scan(derived,documents,{r['key']:r for r in updated},excluded=exclusions,project_id=pid,
                                bindings=p.get('restored_word_bindings',{}).get(original['main']['sha256'],[]))
                p.update(found);p['excluded_references']=exclusions;p['scanned']=True;p['links_reviewed']=False
                p['volume_settings']['reference_pages']={};p['volume_settings']['accept_start_pages']=False
            except ValueError as exc:conflicts.append({'code':'rewrite','message':str(exc)})
    token=digest({'project':fingerprint(original),'request':request,'derived_sha256':sha256(derived).hexdigest() if derived else None})
    return p,derived,{'token':token,'changes':changes,'references':references,'conflicts':conflicts,
                     'word_pending':bool(edits and any(e['ref'].get('managed') for e in edits)),
                     'warnings':['Готовые PDF без обработки сохраняют внутреннее оформление. Новое обозначение не меняет их прежний штамп.',
                                 'После применения проверьте затронутые PDF и ссылки. Живой Word обновляется отдельным явным действием.']}


def apply(store,pid,request,token):
    with LOCK:
        p,derived,report=plan(store,pid,request)
        if token!=report['token']:raise ValueError('Подача или DOCX изменились. Повторите проверку назначений.')
        if report['conflicts']:raise ValueError('Назначения не применены: '+report['conflicts'][0]['message'])
        if not report['changes']:raise ValueError('Новых назначений нет.')
        if len(p['assignment_operations'])>=2000:raise ValueError('Достигнут предел истории: 2000 изменений назначений. Новые изменения не применены; подача и прежние комплекты сохранены.')
        original=store.load(pid)
        from .schema import upgrade
        upgraded=upgrade(store,original,target=4,persist=False)
        if upgraded.get('schema_backup') and original['schema']<4:backup_name=upgraded['schema_backup']
        else:
            folder=store.root/'backups';folder.mkdir(exist_ok=True)
            path=folder/f'before-assignments-{pid}-{uuid4().hex}.zip';backup.save_archive(store,pid,path)
            backup_name='backups/'+path.name
        new_blob=None
        try:
            if derived and original.get('main') and sha256(derived).hexdigest()!=original['main']['sha256']:
                path=store.folder(pid)/'inputs'/(sha256(derived).hexdigest()+'.docx');new_blob=None if path.exists() else path
                source=store.put(p,original['main']['name'],derived,'docx')
                if original['main']['blob'] not in {s['blob'] for s in p['assignment_sources']}:p['assignment_sources'].append(deepcopy(original['main']))
                p['main']=source
            p['schema_backup']=upgraded.get('schema_backup',p.get('schema_backup'))
            receipt={'id':uuid4().hex,'created_at':datetime.now(timezone.utc).isoformat(),'backup':backup_name,
                     'changed_ids':[v['id'] for v in report['changes']], 'original_main':(original.get('main') or {}).get('sha256'),
                     'derived_main':(p.get('main') or {}).get('sha256'),'word_pending':report['word_pending']}
            p['assignment_operations'].append(receipt);validate_project(p);store.save(p)
            return p
        except BaseException:
            if new_blob and new_blob.resolve().parent==(store.folder(pid)/'inputs').resolve():new_blob.unlink(missing_ok=True)
            raise
