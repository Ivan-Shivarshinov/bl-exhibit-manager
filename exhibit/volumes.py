"""Pure, bounded PDF volume composition with an auditable source/page map."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from collections import Counter
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from . import volume_settings, volume_pages
from .submission_index import natural

MAX_INPUT_BYTES=512_000_000
MAX_OUTPUT_BYTES=1_000_000_000
MAX_PAGES=10_000


def source_pages(document):
    rows=[]
    parts=[('original',[{'page':n+1} for n in range(document['original']['pages'])])] if document['mode']=='passthrough' else (
        ([('translation',document['translation_selection'])] if document.get('translation') else [])+[('original',document['selection'])])
    counts=Counter()
    for part,selection in parts:
        for item in selection:
            counts[(part,item['page'])]+=1
            rows.append({'part':part,'source_sha256':document[part]['sha256'],'source_page':item['page'],
                         'rect':deepcopy(item.get('rect')),'occurrence':counts[(part,item['page'])], 'prepared_page':len(rows)+1})
    return rows


def ordered(project,config):
    from .project import identifier
    docs=list(project['documents'])
    if config['order']=='identifier':docs.sort(key=lambda d:natural(identifier(d,project)))
    elif config['order']=='folder':docs.sort(key=lambda d:(natural(d['folder']),natural(identifier(d,project))))
    elif config['order']=='custom':
        ids=config['custom_order']
        if set(ids)!={d['id'] for d in docs}:raise ValueError('Порядок тома устарел. Выберите все приложения заново.')
        rank={did:i for i,did in enumerate(ids)};docs.sort(key=lambda d:rank[d['id']])
    return docs


def cancelled(stop):
    if stop():raise ValueError('Сборка PDF-томов отменена. Прежние комплекты сохранены.')


def layout(items,config,groups,toc_pages):
    rows=[];global_number=config['start'];locations={}
    for volume,group in enumerate(groups):
        local=toc_pages
        for item in group:
            if config['dividers']:local+=1
            locations[item['id']]={'volume':volume,'local_page':local,'global_number':global_number+local}
            rows.append({k:item[k] for k in ('id','title','identifier','path')}|{'volume':volume,'local_page':local,
                'pages':item['pages'],'first':global_number+local,'last':global_number+local+item['pages']-1})
            local+=item['pages']
        global_number+=local
    return rows,locations


def partition(items,config,toc_pages):
    groups=[];current=[];count=toc_pages
    maximum=config['max_pages'] or MAX_PAGES
    if toc_pages>=maximum:raise ValueError('Сам перечень занимает предел тома. Увеличьте число страниц.')
    for item in items:
        needed=item['pages']+int(config['dividers'])
        if needed+toc_pages>maximum:raise ValueError('Материал не помещается в предел страниц тома: '+item['title'])
        if current and count+needed>maximum:groups.append(current);current=[];count=toc_pages
        current.append(item);count+=needed
    if current:groups.append(current)
    return groups


def build(items,config,stop=lambda:False,progress=lambda *args:None):
    config=volume_settings.validate(config)
    if not items:raise ValueError('Добавьте материалы для PDF-тома.')
    if len(items)>2000:raise ValueError('Для PDF-томов доступно до 2000 материалов за одну сборку.')
    if len({i['id'] for i in items})!=len(items):raise ValueError('Повтор материала в составе тома.')
    if sum(len(i['data']) for i in items)>MAX_INPUT_BYTES:raise ValueError('Для томов доступно до 512 МБ подготовленных PDF за одну сборку.')
    for item in items:
        cancelled(stop);reader=PdfReader(BytesIO(item['data']))
        if reader.is_encrypted or not reader.pages:raise ValueError('Пустой или защищённый PDF: '+item['title'])
        root=reader.trailer['/Root'];form=root.get('/AcroForm');form=form.get_object() if form else {}
        widgets=sum(1 for p in reader.pages for a in p.get('/Annots',[]) if a.get_object().get('/Subtype')=='/Widget')
        names=root.get('/Names');names=names.get_object() if names else {}
        if root.get('/Perms') or form.get('/XFA') or root.get('/Collection') or names.get('/EmbeddedFiles') or widgets and not reader.get_fields():
            raise ValueError('Нельзя безопасно перенести ограничения подписи, XFA, вложения или несвязанные поля в PDF-том: '+item['title']+'. Исходник сохранён; используйте отдельный PDF.')
        item['pages']=len(reader.pages)
        if len(item['source_map'])!=item['pages']:raise ValueError('Карта источника не совпадает с PDF: '+item['title'])
    if sum(i['pages'] for i in items)>MAX_PAGES:raise ValueError('За одну сборку поддерживается до 10 000 страниц материалов.')
    toc_pages=1
    for _ in range(10):
        groups=partition(items,config,toc_pages);rows,locations=layout(items,config,groups,toc_pages)
        count=len(PdfReader(BytesIO(volume_pages.index_pdf(rows,'Перечень приложений',0)[0])).pages)
        if count==toc_pages:break
        toc_pages=count
    else:raise ValueError('Не удалось согласовать число страниц перечня.')
    # A bytes-bound split is monotone; never silently split a single material.
    for attempt in range(len(items)+1):
        cancelled(stop);rows,locations=layout(items,config,groups,toc_pages)
        output_pages=sum(i['pages'] for i in items)+len(groups)*toc_pages+int(config['dividers'])*len(items)
        if len(groups)>100 or output_pages>20_000:raise ValueError('Слишком много томов/страниц с повторяемым перечнем: предел 100 томов и 20 000 итоговых страниц. Увеличьте предел одного тома.')
        filenames=[f'Volume {n+1:02}.pdf' for n in range(len(groups))]
        outputs={};too_large=None;maps=[];global_start=config['start']
        for vi,group in enumerate(groups):
            cancelled(stop);progress(vi,len(groups),'Собираем PDF-том '+str(vi+1))
            toc,boxes=volume_pages.index_pdf(rows,'Перечень приложений',vi)
            writer=PdfWriter();writer.append(BytesIO(toc));writer.add_outline_item('Перечень приложений',0)
            page_owners=[None]*toc_pages;readers=[]
            for item in group:
                cancelled(stop)
                if config['dividers']:
                    writer.append(BytesIO(volume_pages.divider_pdf(item)));page_owners.append(None)
                first=len(writer.pages);reader=PdfReader(BytesIO(item['data']));readers.append(reader)
                volume_pages.namespace(reader,'m'+item['id']+'.')
                before_widgets=sum(1 for p in reader.pages for a in p.get('/Annots',[]) if a.get_object().get('/Subtype')=='/Widget')
                writer.append(reader,import_outline=True)
                after_widgets=sum(1 for p in writer.pages[first:] for a in p.get('/Annots',[]) if a.get_object().get('/Subtype')=='/Widget')
                if before_widgets!=after_widgets:raise ValueError('Не удалось сохранить поля PDF: '+item['title'])
                writer.add_outline_item((item['identifier']+' · '+item['title']).strip(' ·'),first)
                page_owners.extend([item]*item['pages'])
                for n,source in enumerate(item['source_map']):
                    maps.append({'document_id':item['id'],'revision':item['revision'],'prepared_sha256':sha256(item['data']).hexdigest(),
                        **source,'volume':vi+1,'file':filenames[vi],'local_page':first+n+1,'global_number':global_start+first+n})
            for box in boxes:
                annotation=volume_pages.link(box['rect'],'');target=locations[box['id']]
                volume_pages.jump(writer,annotation,target['volume'],target['local_page'],vi,filenames)
                writer.add_annotation(box['page'],annotation)
            for pi,page in enumerate(writer.pages):
                cancelled(stop)
                progress(pi,len(writer.pages),f'Том {vi+1}: оформление страницы {pi+1} из {len(writer.pages)}')
                owner=page_owners[pi]
                if owner:
                    for entry in page.get('/Annots',[]):
                        ann=entry.get_object();action=ann.get('/A')
                        if not action:continue
                        action=action.get_object();file=action.get('/F');name=str(file.get_object().get('/UF',file.get_object().get('/F',''))) if hasattr(file,'get_object') and isinstance(file.get_object(),dict) else str(file or '')
                        target=owner.get('targets',{}).get(name)
                        if target:
                            dest=locations[target['document_id']]
                            volume_pages.jump(writer,ann,dest['volume'],dest['local_page']+target['page']-1,vi,filenames)
                label=config['number_format'].replace('{page}',str(global_start+pi))
                try:rect=volume_pages.stamp_number(writer,pi,label,config['position'])
                except ValueError as exc:raise ValueError(f"{owner['title'] if owner else 'Перечень/разделитель'}, том {vi+1}, страница {pi+1}: {exc}. Выберите отдельную полосу или другое место номера.") from exc
                back=volume_pages.link(rect,'');volume_pages.jump(writer,back,vi,0,vi,filenames);writer.add_annotation(pi,back)
            writer.set_page_label(0,len(writer.pages)-1,style='/D',start=global_start)
            writer.add_metadata({'/Title':f'Подача · том {vi+1}','/Author':'BL Exhibit Manager'})
            out=BytesIO();writer.write(out);data=out.getvalue()
            if config['max_mb'] and len(data)>config['max_mb']*1_000_000:
                if len(group)==1:raise ValueError('Материал не помещается в предел размера тома: '+group[0]['title'])
                too_large=vi;break
            outputs[filenames[vi]]=data
            global_start+=len(writer.pages)
        if too_large is not None:
            vi=too_large;last=groups[vi].pop()
            if vi+1<len(groups):groups[vi+1].insert(0,last)
            else:groups.append([last])
            # Later group may now exceed its page bound; repartition without re-merging.
            for gi in range(vi+1,len(groups)):
                limit=config['max_pages'] or MAX_PAGES
                while toc_pages+sum(i['pages']+int(config['dividers']) for i in groups[gi])>limit:
                    moved=groups[gi].pop()
                    if not groups[gi]:raise ValueError('Материал не помещается в предел тома: '+moved['title'])
                    if gi+1==len(groups):groups.append([moved])
                    else:groups[gi+1].insert(0,moved)
            continue
        if sum(map(len,outputs.values()))>MAX_OUTPUT_BYTES:raise ValueError('Результат PDF-томов превышает 1 ГБ.')
        manifest={'format':1,'settings':config,'rows':rows,'pages':maps,
                  'volumes':[{'file':name,'pages':len(PdfReader(BytesIO(data)).pages),'bytes':len(data),'sha256':sha256(data).hexdigest()} for name,data in outputs.items()]}
        verify(outputs,manifest);progress(len(groups),len(groups),'PDF-тома проверены')
        return outputs,manifest
    raise ValueError('Не удалось согласовать ограничения размера томов.')


def verify(files,manifest):
    readers={name:PdfReader(BytesIO(data)) for name,data in files.items()}
    for meta in manifest['volumes']:
        if meta['file'] not in files or len(readers[meta['file']].pages)!=meta['pages'] or sha256(files[meta['file']]).hexdigest()!=meta['sha256']:
            raise ValueError('Готовый PDF-том не совпадает с картой.')
    for row in manifest['pages']:
        if row['file'] not in readers or not 1<=row['local_page']<=len(readers[row['file']].pages):raise ValueError('Страница карты вне PDF-тома.')
    for name,reader in readers.items():
        refs={p.indirect_reference.idnum for p in reader.pages}
        for page in reader.pages:
            for entry in page.get('/Annots',[]):
                ann=entry.get_object()
                if not ann.get('/BLVolumeLink'):continue
                action=ann['/A'];destination=action['/D']
                if action['/S']=='/GoTo':
                    if destination[0].idnum not in refs:raise ValueError('Внутренняя цель PDF-тома потеряна.')
                elif action['/S']=='/GoToR':
                    target=str(action['/F']['/UF'])
                    if target not in readers or not 0<=int(destination[0])<len(readers[target].pages):raise ValueError('Межтомовая цель потеряна.')
                else:raise ValueError('Неподдерживаемая цель PDF-тома.')
