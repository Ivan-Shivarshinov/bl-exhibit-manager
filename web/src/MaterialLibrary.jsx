import React, {useEffect, useRef, useState} from 'react';
import './material-library.css';
import {Comparison,ProjectVersions} from './LibraryVersions';
import LibraryArchive from './LibraryArchive';
import LibraryUpload from './LibraryUpload';
import LibraryUsage from './LibraryUsage';

const roleNames={original:'Оригинал',translation:'Подтверждённый перевод',ready:'Готовый PDF'};
const dateText=s=>new Date(s).toLocaleString('ru-RU');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

export default function MaterialLibrary({api,p,mode='browse',documentIds=[],onChanged,onClose,onNavigate}) {
  const [libraries,setLibraries]=useState([]),[lid,setLid]=useState(''),[newName,setNewName]=useState('');
  const [query,setQuery]=useState(''),[offset,setOffset]=useState(0),[catalog,setCatalog]=useState(null);
  const [sort,setSort]=useState('title');
  const [filters,setFilters]=useState({category:'',language:'',document_date:'',tag:'',translation:'',hidden:''});
  const [excluded,setExcluded]=useState([]),[metadataEdits,setMetadataEdits]=useState({});
  const [selected,setSelected]=useState(null),[vid,setVid]=useState(''),[version,setVersion]=useState(null);
  const [chosen,setChosen]=useState([]),[rows,setRows]=useState([]),[plan,setPlan]=useState(null);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true);
  const [job,setJob]=useState(null),[revision,setRevision]=useState(0),[comment,setComment]=useState(''),[asVersion,setAsVersion]=useState(false);
  const [originalOnly,setOriginalOnly]=useState(false),[allowDuplicate,setAllowDuplicate]=useState(false);
  const [role,setRole]=useState('original'),[page,setPage]=useState(1),[image,setImage]=useState(''),[imageError,setImageError]=useState('');
  const dialog=useRef(null),generation=useRef(0);
  useEffect(()=>{const previous=document.activeElement;dialog.current.showModal();return()=>previous?.focus();},[]);
  async function refreshLibraries(){const list=await api('/libraries');setLibraries(list);setLid(old=>list.some(l=>l.id===old)?old:list[0]?.id||'');}
  useEffect(()=>{refreshLibraries().catch(e=>setError(e.message)).finally(()=>setLoading(false));},[]);
  useEffect(()=>{setOffset(0);setSelected(null);setVersion(null);setChosen([]);setRows([]);setPlan(null);},[lid,query]);
  useEffect(()=>{
    const controller=new AbortController();let live=true;setCatalog(null);
    if(lid){setLoading(true);api(`/libraries/${lid}/materials?q=${encodeURIComponent(query)}&offset=${offset}&sort=${sort}&${new URLSearchParams(filters)}`,undefined,{signal:controller.signal})
      .then(value=>{if(live){setCatalog(value);setError('');}}).catch(e=>{if(live&&e.name!=='AbortError')setError(e.message);}).finally(()=>{if(live)setLoading(false);});}
    return()=>{live=false;controller.abort();};
  },[lid,query,offset,sort,revision,filters]);
  useEffect(()=>{
    let live=true;const controller=new AbortController();setVersion(null);
    if(selected&&vid)api(`/libraries/${lid}/materials/${selected.id}/versions/${vid}`,undefined,{signal:controller.signal}).then(v=>{if(live){setVersion(v);setRole(Object.keys(v.parts)[0]);setPage(1);}}).catch(e=>{if(live&&e.name!=='AbortError')setError(e.message);});
    return()=>{live=false;controller.abort();};
  },[lid,selected?.id,vid,revision]);
  useEffect(()=>{
    let live=true,url;const controller=new AbortController();setImage('');setImageError('');
    if(version&&role){api(`/libraries/${lid}/materials/${selected.id}/versions/${version.id}/preview?role=${role}&page=${page}`,undefined,{blob:true,signal:controller.signal})
      .then(blob=>{if(live){url=URL.createObjectURL(blob);setImage(url);}}).catch(e=>{if(live&&e.name!=='AbortError')setImageError(e.message);});}
    return()=>{live=false;controller.abort();if(url)URL.revokeObjectURL(url);};
  },[lid,version?.id,role,page]);
  async function run(action,args,after){
    if(busy)return;setBusy(true);setError('');setNotice('');const turn=++generation.current;
    try{
      const started=await api('/material-operations',{action,arguments:args});setJob({id:started.id,state:'queued',message:'Начинаем…'});
      while(turn===generation.current){
        const state=await api(`/material-operations/${started.id}`);setJob(state);
        if(state.state==='complete'){await after(state.result);break;}
        if(['error','cancelled','interrupted'].includes(state.state)){throw new Error(state.message);}
        await pause(300);
      }
    }catch(e){setError(e.message);}finally{setBusy(false);}
  }
  async function create(){setBusy(true);setError('');try{const lib=await api('/libraries',{name:newName});await refreshLibraries();setLid(lib.id);setNewName('');}catch(e){setError(e.message);}finally{setBusy(false);}}
  function select(item){setSelected(item);setVid(item.versions.at(-1));setPlan(null);}
  function assignments(){
    const occupied=new Set(p.documents.map(d=>d.identifier));let n=1;
    setRows(chosen.map(item=>{
      while(occupied.has(`Annex ${n}`))n++;const number=n++;occupied.add(`Annex ${number}`);
      return {library_id:lid,material_id:item.id,version_id:item.versions.at(-1),title:item.latest.metadata.title,number,designation:'Annex',prefix:'',filename:`Annex ${number}.pdf`,folder:'',mode:'prepare'};
    }));setPlan(null);
  }
  const blocked=busy||loading;
  const allSources=p?.documents.filter(d=>documentIds.includes(d.id))||[];
  const sourceDocs=allSources.filter(d=>!excluded.includes(d.id));
  return <dialog ref={dialog} className="library-dialog" aria-labelledby="library-title" onCancel={e=>{e.preventDefault();if(!busy)onClose();}}>
    <div className="section-heading"><div><h1 id="library-title">{mode==='save'?'Сохранить в библиотеку':'Библиотеки дел'}</h1><p className="muted">{p?`Подача: «${p.name}». `:''}Материалы копируются независимо; подачи продолжают работать без библиотеки.</p></div><button disabled={busy} onClick={onClose}>Вернуться к подаче</button></div>
    <div className="library-controls"><label className="field">Библиотека<select aria-label="Библиотека" disabled={blocked} value={lid} onChange={e=>setLid(e.target.value)}><option value="">Выберите библиотеку</option>{libraries.map(l=><option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
      <form className="actions" onSubmit={e=>{e.preventDefault();create();}}><input aria-label="Название новой библиотеки" placeholder="Название новой библиотеки" maxLength={120} required value={newName} onChange={e=>setNewName(e.target.value)} disabled={blocked}/><button disabled={blocked}>Создать библиотеку</button></form></div>
    {error&&<p className="error-note" role="alert">{error}</p>}{notice&&<p className="success-note" role="status">{notice}</p>}
    {busy&&<div className="library-progress" role="status"><p>{job?.message||'Проверяем…'} {job?.total?`${job.completed} из ${job.total}`:''}</p><progress {...(job?.total?{value:job.completed,max:job.total}:{})}/>{job?.id&&<button onClick={()=>api(`/material-operations/${job.id}/cancel`,{}).catch(e=>setError(e.message))}>Отменить операцию</button>}</div>}
    <LibraryArchive api={api} lid={lid} run={run} blocked={blocked} onRestored={async id=>{await refreshLibraries();setLid(id);setRevision(n=>n+1);}}/>
    {lid&&<LibraryUpload key={lid} api={api} lid={lid} run={run} blocked={blocked} onSaved={()=>setRevision(n=>n+1)}/>}
    {mode==='save'&&<section className="library-save"><h2>Материалы из подачи</h2><ul>{allSources.map(d=><li key={d.id}><label><input type="checkbox" disabled={blocked} checked={!excluded.includes(d.id)} onChange={e=>{setExcluded(e.target.checked?excluded.filter(id=>id!==d.id):[...excluded,d.id]);setPlan(null);}}/>Включить материал</label>{d.identifier} · {d.title} — {d.mode==='passthrough'?'готовый PDF без обработки':d.translation_confirmed?'оригинал и подтверждённый перевод':'оригинал; черновик перевода не переносится'}<details><summary>Реквизиты материала</summary>{['title','short_title','language','category','document_date','tags'].map(k=><label className="field" key={k}>{({title:'Название',short_title:'Краткое название',language:'Язык',category:'Категория',document_date:'Дата документа',tags:'Метки через запятую'})[k]}<input type={k==='document_date'?'date':'text'} value={metadataEdits[d.id]?.[k]??d[k]??''} onChange={e=>{setMetadataEdits({...metadataEdits,[d.id]:{...metadataEdits[d.id],[k]:e.target.value}});setPlan(null);}}/></label>)}</details></li>)}</ul>
      <label><input type="checkbox" checked={originalOnly} disabled={blocked} onChange={e=>{setOriginalOnly(e.target.checked);setPlan(null);}}/>Сохранить только оригинал, оставив черновик в подаче</label>
      <label><input type="checkbox" checked={asVersion} disabled={blocked||!selected||sourceDocs.length!==1} onChange={e=>{setAsVersion(e.target.checked);setPlan(null);}}/>Новая версия выбранного ниже материала {selected?.latest.metadata.title&&`«${selected.latest.metadata.title}»`}</label>
      <label className="field">Комментарий к версии<input value={comment} maxLength={1000} disabled={blocked} onChange={e=>{setComment(e.target.value);setPlan(null);}}/></label>
      <button className="primary" disabled={blocked||!lid||!sourceDocs.length||asVersion&&!selected} onClick={()=>run('save_preview',{lid,pid:p.id,rows:sourceDocs.map(d=>({document_id:d.id,metadata:{...metadataEdits[d.id],...(metadataEdits[d.id]?.tags!==undefined?{tags:metadataEdits[d.id].tags.split(",").map(s=>s.trim()).filter(Boolean)}:{})},original_only:originalOnly,allow_duplicate:allowDuplicate,comment,...(asVersion?{material_id:selected.id}:{})}))},setPlan)}>Проверить состав</button>
    </section>}
    {plan?.action==='save'&&<section className="library-plan"><h2>Состав перед сохранением</h2>{plan.warnings.map((w,i)=><p className="pending" key={i}>{w.message}</p>)}{plan.errors.map((e,i)=><p className="error-note" key={i}>{e.message}</p>)}
      <ul>{plan.rows.map((row,i)=><li key={i}><b>{row.version.metadata.title}</b> · {Object.keys(row.version.parts).map(r=>roleNames[r]).join(', ')} · {(Object.values(row.version.parts).reduce((s,p)=>s+p.size,0)/1e6).toFixed(1)} МБ{row.duplicates.length>0&&<p className="pending">Совпадает содержимое материала в этой библиотеке. Можно выбрать найденный материал ниже либо явно сохранить отдельно/новую версию.</p>}</li>)}</ul>
      {plan.rows.some(r=>r.duplicates.length)&&<label><input type="checkbox" disabled={blocked} checked={allowDuplicate} onChange={e=>{setAllowDuplicate(e.target.checked);setPlan(null);}}/>Разрешить сохранение одинакового содержания отдельным материалом/версией; затем повторить проверку</label>}
      <button className="primary" disabled={blocked||!!plan.errors.length||plan.rows.some(r=>r.duplicates.length&&!r.allow_duplicate)} onClick={()=>run('publish',{token:plan.token},()=>{setNotice('Материалы сохранены. Исходная подача не изменена.');setPlan(null);setRevision(n=>n+1);})}>Сохранить материалы</button>
    </section>}
    {lid&&<><details className="library-filters"><summary>Фильтры материалов</summary><div className="library-controls">{["category","language","document_date","tag"].map(k=><label className="field" key={k}>{({category:"Категория",language:"Язык",document_date:"Дата документа",tag:"Метка"})[k]}<input type={k==="document_date"?"date":"text"} value={filters[k]} onChange={e=>{setFilters({...filters,[k]:e.target.value});setOffset(0);}}/></label>)}<label className="field">Перевод<select value={filters.translation} onChange={e=>{setFilters({...filters,translation:e.target.value});setOffset(0);}}><option value="">С переводом и без</option><option value="true">С подтверждённым переводом</option><option value="false">Без перевода</option></select></label><label><input type="checkbox" checked={filters.hidden==="true"} onChange={e=>{setFilters({...filters,hidden:e.target.checked?"true":""});setOffset(0);setSelected(null);}}/>Показать скрытые материалы</label></div></details><label className="field">Поиск по названию или имени файла<input type="search" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Название, краткое название или файл" disabled={busy}/></label><label className="field">Порядок материалов<select aria-label="Порядок материалов" value={sort} disabled={busy} onChange={e=>{setSort(e.target.value);setOffset(0);}}><option value="title">По названию</option><option value="date">Сначала последние версии</option></select></label>
      {loading?<p role="status">Загружаем каталог…</p>:catalog&&<div className="library-layout"><section><h2>Материалы · {catalog.total}</h2>{!catalog.items.length&&<p>{query?'Ничего не найдено. Очистите поиск.':'Библиотека пока пуста. Сохраните материал из подачи.'}</p>}
        <div className="library-items">{catalog.items.map(item=><article className={selected?.id===item.id?'chosen-material':''} key={item.id}>
          {p&&mode!=='save'&&<label><input type="checkbox" disabled={blocked} checked={chosen.some(x=>x.id===item.id)} onChange={e=>{setChosen(e.target.checked?[...chosen,item]:chosen.filter(x=>x.id!==item.id));setRows([]);setPlan(null);}}/>Добавить в подачу</label>}
          <button className="title-button" disabled={busy} onClick={()=>select(item)}>{item.latest.metadata.title}</button><p className="muted">Версий: {item.version_count} · Сохранено {dateText(item.latest.created_at)}{item.latest.translated?' · С переводом':''}</p>
        </article>)}</div><div className="actions"><button disabled={blocked||offset===0} onClick={()=>setOffset(n=>Math.max(0,n-50))}>Назад</button><span>{catalog.total?offset+1:0}–{Math.min(offset+50,catalog.total)}</span><button disabled={blocked||offset+50>=catalog.total} onClick={()=>setOffset(n=>n+50)}>Далее</button></div>
        {p&&mode!=='save'&&<button className="primary" disabled={blocked||!chosen.length} onClick={assignments}>Добавить выбранные · {chosen.length}</button>}
      </section><section className="material-card">{selected&&<><h2>{selected.latest.metadata.title}</h2><label className="field">Версия<select value={vid} disabled={blocked} onChange={e=>setVid(e.target.value)}>{selected.versions.map((id,i)=><option key={id} value={id}>Версия {i+1}{id===selected.versions.at(-1)?' · последняя':''}</option>)}</select></label>
        {version?<><p>Сохранено {dateText(version.created_at)}. {version.comment}</p><p className="muted">Дата документа: {version.metadata.document_date||'не указана'}. Язык: {version.metadata.language||'не указан'}.</p><label className="field">Часть<select value={role} disabled={blocked} onChange={e=>{setRole(e.target.value);setPage(1);}}>{Object.keys(version.parts).map(r=><option key={r} value={r}>{roleNames[r]}</option>)}</select></label>
          {role==='ready'&&<p className="pending">Готовый PDF сохраняет прежний штамп {version.ready_identifier||'(обозначение не указано)'}. Новый номер требует подготовки из оригинала.</p>}
          <label className="field">Страница<input type="number" min="1" max={version.parts[role]?.pages||1} value={page} onChange={e=>setPage(Number(e.target.value)||1)}/></label>
          {imageError?<p className="error-note" role="alert">{imageError}</p>:image?<img className="material-preview" src={image} alt={`${roleNames[role]}, версия, страница ${page}`}/>:<p role="status">Готовим страницу…</p>}
        </>:<p role="status">Загружаем версию…</p>}</>}</section></div>}
    </>}
    {selected&&<><LibraryUsage key={selected.id} api={api} lid={lid} mid={selected.id} onNavigate={onNavigate} blocked={blocked}/><button disabled={blocked} onClick={async()=>{setBusy(true);setError(" ");try{await api(`/libraries/${lid}/materials/${selected.id}/hidden`,{hidden:!selected.hidden,revision:catalog.library.revision});setSelected(null);setRevision(n=>n+1);}catch(e){setError(e.message);setRevision(n=>n+1);}finally{setBusy(false);}}}>{selected.hidden?"Вернуть материал в каталог":"Скрыть материал из каталога"}</button><p className="muted">Скрытие сохраняет версии и действующие подачи.</p></>} 
    {selected&&<Comparison key={selected.id} api={api} lid={lid} mid={selected.id} versions={selected.versions} current={vid} run={run} blocked={blocked}/>} 
    {p&&mode==='updates'&&<ProjectVersions api={api} p={p} run={run} blocked={blocked} onChanged={onChanged}/>} 
    {rows.length>0&&<section><h2>Добавить в «{p.name}»</h2><p>Каждая строка получит независимую копию. Сохранённый перевод не запускается заново; результат с новым номером нужно просмотреть.</p><div className="table-scroll"><table className="library-assignment"><thead><tr><th>Материал / версия</th><th>Режим</th><th>Номер</th><th>Папка / имя PDF</th><th>Выбор</th></tr></thead><tbody>{rows.map((r,i)=><tr key={r.material_id}><td>{r.title}<select aria-label={`Версия ${r.title}`} value={r.version_id} onChange={e=>{setRows(rows.map((x,j)=>j===i?{...x,version_id:e.target.value}:x));setPlan(null);}}>{chosen[i].versions.map((v,n)=><option key={v} value={v}>Версия {n+1}</option>)}</select></td>
      <td><select aria-label={`Режим ${r.title}`} value={r.mode} onChange={e=>{setRows(rows.map((x,j)=>j===i?{...x,mode:e.target.value}:x));setPlan(null);}}><option value="prepare">Подготовить из оригинала</option><option value="passthrough">Готовый PDF без обработки</option></select></td>
      <td><label>Обозначение<select aria-label={`Обозначение ${r.title}`} value={r.designation} onChange={e=>{setRows(rows.map((x,j)=>j===i?{...x,designation:e.target.value}:x));setPlan(null);}}><option value="Annex">Annex</option><option value="Exhibit">Exhibit</option><option value="">Без обозначения</option></select></label><label>Префикс<input aria-label={`Префикс ${r.title}`} value={r.prefix} onChange={e=>{setRows(rows.map((x,j)=>j===i?{...x,prefix:e.target.value}:x));setPlan(null);}}/></label><label>Номер<input aria-label={`Номер ${r.title}`} type="number" min="1" value={r.number} onChange={e=>{setRows(rows.map((x,j)=>j===i?{...x,number:Number(e.target.value)}:x));setPlan(null);}}/></label></td>
      <td>{['folder','filename'].map(k=><input key={k} aria-label={`${k==='folder'?'Папка':'Имя PDF'} ${r.title}`} value={r[k]} onChange={e=>{setRows(rows.map((x,j)=>j===i?{...x,[k]:e.target.value}:x));setPlan(null);}}/>)}</td><td><button onClick={()=>{setRows(rows.filter((_,j)=>j!==i));setChosen(chosen.filter((_,j)=>j!==i));setPlan(null);}}>Исключить</button></td></tr>)}</tbody></table></div>
      <button disabled={blocked} onClick={()=>run('import_preview',{pid:p.id,rows:rows.map(({title,...r})=>r)},setPlan)}>Проверить назначения</button>
      {plan?.action==='import'&&<><ul>{plan.warnings.map((w,i)=><li className="pending" key={i}>{w}</li>)}</ul>{plan.errors.map((e,i)=><p className="error-note" role="alert" key={i}>{e.message}</p>)}{!plan.errors.length&&<p className="success-note">Назначения проверены. Добавится приложений: {plan.documents.length}.</p>}<button className="primary" disabled={blocked||!!plan.errors.length} onClick={()=>run('import_apply',{token:plan.token},async result=>{await onChanged(result);setRows([]);setChosen([]);setPlan(null);setNotice('Материалы добавлены. Вернитесь в подачу, просмотрите результат и подтвердите подготовку.');})}>Добавить в подачу</button></>}
    </section>}
  </dialog>;
}



