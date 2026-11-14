import React,{useState,useEffect,useRef} from 'react';

export default function Volumes({p,api,onSaved,onDirty,onWorking,saveBlob,disabled}){
  const canonical=JSON.stringify(p.volume_settings);
  const [draft,setDraft]=useState(()=>JSON.parse(canonical)),[preview,setPreview]=useState(null),[job,setJob]=useState(null),[state,setState]=useState(null);
  const [error,setError]=useState(''),[saving,setSaving]=useState(false),[image,setImage]=useState(null),[imageBusy,setImageBusy]=useState(false);
  const live=useRef(true),flight=useRef(false),imageUrl=useRef(null);
  const dirty=JSON.stringify(draft)!==canonical,working=Boolean(job||saving);
  const stale=dirty||!preview||preview.stale||preview.preview_token!==p.submission_index_fingerprint;
  useEffect(()=>setDraft(JSON.parse(canonical)),[canonical]);
  useEffect(()=>{onDirty(dirty);},[dirty,onDirty]);
  useEffect(()=>{onWorking(working);},[working,onWorking]);
  useEffect(()=>{live.current=true;return()=>{live.current=false;onDirty(false);onWorking(false);if(imageUrl.current)URL.revokeObjectURL(imageUrl.current);};},[onDirty,onWorking]);
  useEffect(()=>{let current=true;api(`/projects/${p.id}/volumes/status`).then(s=>{if(current){if(s.preview)setPreview(s.preview);if(s.job&&['queued','running'].includes(s.job.state))setJob(s.job.id);else if(s.job&&['error','cancelled','interrupted'].includes(s.job.state))setError(s.job.message);}}).catch(e=>{if(current)setError(e.message);});return()=>{current=false;};},[p.id,api]);
  useEffect(()=>{
    if(!job)return;let current=true,timer;
    async function poll(){try{
      const next=await api('/volume-jobs/'+job);if(!current)return;setState(next);
      if(next.state==='complete'){
        if(next.result.download_token){const blob=await api(`/volume-jobs/${next.result.download_token}/download`,undefined,{blob:true});if(current){saveBlob(blob,'Submission.zip');onSaved(await api('/projects/'+p.id));}}
        else {setPreview(next.result);onSaved(await api('/projects/'+p.id));}
        if(current)setJob(null);return;
      }
      if(['error','cancelled','interrupted'].includes(next.state)){setError(next.message);setJob(null);return;}
      timer=setTimeout(poll,350);
    }catch(e){if(current){setError(e.message);setJob(null);}}}
    poll();return()=>{current=false;clearTimeout(timer);};
  },[job,api,p.id,saveBlob,onSaved]);
  // Detect edits in another window without claiming an old snapshot is current.
  useEffect(()=>{if(!preview||working)return;let current=true,timer;
    async function check(){try{const s=await api(`/projects/${p.id}/volumes/status`);if(current&&s.fingerprint!==preview.preview_token&&!preview.stale)setPreview({...preview,stale:true});}catch(e){if(current)setError(e.message);}if(current)timer=setTimeout(check,3000);}
    timer=setTimeout(check,3000);return()=>{current=false;clearTimeout(timer);};
  },[preview,working,p.id,api]);
  function change(values){setDraft({...draft,...values});setImage(null);setError('');}
  async function perform(action){if(flight.current)return;flight.current=true;setError('');try{await action();}catch(e){if(live.current)setError(e.message);}finally{flight.current=false;}}
  async function save(){setSaving(true);try{onSaved(await api(`/projects/${p.id}/volumes/settings`,draft));setPreview(null);}finally{if(live.current)setSaving(false);}}
  async function start(action){const r=await api(`/projects/${p.id}/volumes/${action}`,{preview_token:preview?.preview_token});setState({message:'Начинаем…',completed:0,total:0});setJob(r.id);}
  async function show(index,page=1){setImageBusy(true);try{
    const route=`/projects/${p.id}/volumes/${preview.preview_token}/${index}`;
    const [blob,navigation]=await Promise.all([api(`${route}/image?page=${page}`,undefined,{blob:true}),api(`${route}/navigation?page=${page}`)]);
    if(!live.current)return;
    if(imageUrl.current)URL.revokeObjectURL(imageUrl.current);imageUrl.current=URL.createObjectURL(blob);setImage({index,page,url:imageUrl.current,links:navigation.links});
  }finally{if(live.current)setImageBusy(false);}}
  function setReference(ref,page){change({reference_pages:{...draft.reference_pages,[ref.key]:{document_id:ref.document_id,revision:ref.revision,page:Number(page)}}});}
  function referenceSource(ref){const page=Number(draft.reference_pages[ref.key]?.page||ref.page),row=preview?.page_map.find(r=>r.document_id===ref.document_id&&r.prepared_page===page);return row?`${row.part==='translation'?'Перевод':'Оригинал'} · исходная стр. ${row.source_page}${row.rect?' · фрагмент':''} · том ${row.volume}, стр. ${row.local_page}`:'Такой подготовленной страницы нет';}
  function move(id,delta){const ids=draft.order==='custom'?[...draft.custom_order]:p.documents.map(d=>d.id);const i=ids.indexOf(id),j=i+delta;if(j<0||j>=ids.length)return;[ids[i],ids[j]]=[ids[j],ids[i]];change({order:'custom',custom_order:ids});}
  const blocked=disabled||working;
  return <section className="volume-section" aria-labelledby="volumes-heading" aria-busy={working}>
    <h2 id="volumes-heading">PDF-тома</h2>
    <p className="muted">Необязательно: приложения с перечнем, общей нумерацией и переходами. Отдельные исходные PDF останутся в комплекте. Подпись исходного файла не удостоверяет новый том.</p>
    <label><input type="checkbox" aria-label="Включить PDF-тома" checked={draft.enabled} disabled={blocked} onChange={e=>change({enabled:e.target.checked})}/> Включить PDF-тома</label>
    {draft.enabled&&<>
      <div className="volume-settings">
        <label><input type="checkbox" checked={draft.include_main} disabled={blocked||!p.main} onChange={e=>change({include_main:e.target.checked})}/> Включить основной документ · нужен конвертер</label>
        <label><input type="checkbox" checked={draft.dividers} disabled={blocked} onChange={e=>change({dividers:e.target.checked})}/> Разделители перед материалами</label>
        <label>Порядок<select aria-label="Порядок PDF-тома" value={draft.order} disabled={blocked} onChange={e=>change({order:e.target.value,custom_order:e.target.value==='custom'?p.documents.map(d=>d.id):[]})}><option value="identifier">По обозначению</option><option value="project">Как в подаче</option><option value="folder">По папкам</option><option value="custom">Мой порядок</option></select></label>
        <label>Начальный номер<input aria-label="Начальный номер тома" type="number" min="1" max="1000000" value={draft.start} disabled={blocked} onChange={e=>change({start:Number(e.target.value)})}/></label>
        <label>Подпись страницы<input aria-label="Подпись страницы тома" value={draft.number_format} disabled={blocked} onChange={e=>change({number_format:e.target.value})}/><small>Например: Стр. {'{page}'}</small></label>
        <label>Место номера<select value={draft.position} disabled={blocked} onChange={e=>change({position:e.target.value})}><option value="band">В отдельной верхней полосе</option><option value="top-right">Сверху справа · проверка свободного места</option><option value="bottom-right">Снизу справа · проверка свободного места</option><option value="bottom-center">Снизу по центру · проверка свободного места</option></select></label>
        <label>Страниц в одном томе<input aria-label="Предел страниц тома" type="number" min="0" value={draft.max_pages} disabled={blocked} onChange={e=>change({max_pages:Number(e.target.value)})}/><small>0 — без отдельного ограничения</small></label>
        <label>МБ в одном томе<input type="number" min="0" value={draft.max_mb} disabled={blocked} onChange={e=>change({max_mb:Number(e.target.value)})}/><small>0 — без отдельного ограничения</small></label>
      </div>
      {draft.order==='custom'&&<ol>{draft.custom_order.map((id,i)=>{const d=p.documents.find(d=>d.id===id);return <li key={id}>{d?.identifier} · {d?.title||'Материал недоступен'} <button disabled={blocked||i===0} onClick={()=>move(id,-1)}>Выше</button><button disabled={blocked||i===draft.custom_order.length-1} onClick={()=>move(id,1)}>Ниже</button></li>;})}</ol>}
      {!draft.include_main&&p.main&&<p className="muted">Основной DOCX сохранится отдельно; основной PDF в этом комплекте не создаётся.</p>}
    </>}
    <div className="actions"><button disabled={blocked||!dirty} onClick={()=>perform(save)}>Сохранить настройки томов</button>{draft.enabled&&<button disabled={blocked||dirty||!!p.issues.length} onClick={()=>perform(()=>start('preview'))}>Подготовить PDF-тома</button>}</div>
    {working&&<div role="status"><p>{state?.message||'Сохраняем…'}</p>{job&&<><progress max={Math.max(1,state?.total||1)} value={state?.completed||0}/><button onClick={()=>perform(()=>api(`/volume-jobs/${job}/cancel`,{}))}>Отменить сборку томов</button></>}</div>}
    {error&&<p role="alert" className="error-note">{error}</p>}
    {preview&&draft.enabled&&<div className="volume-preview">
      <h3>Состав и страницы томов</h3>{stale&&<p role="status" className="warning-note">Настройки или подача изменились. Сохраните и подготовьте тома заново.</p>}
      <div className="table-scroll"><table><thead><tr><th>Материал</th><th>Том</th><th>Страницы</th></tr></thead><tbody>{preview.rows.map(r=><tr key={r.id}><td>{r.identifier} · {r.title}</td><td>{r.volume+1}</td><td>{r.first}–{r.last}</td></tr>)}</tbody></table></div>
      <details><summary>Карта исходных и подготовленных страниц</summary><div className="table-scroll"><table><thead><tr><th>Материал</th><th>Источник</th><th>Подготовленная</th><th>Том / лист</th></tr></thead><tbody>{preview.page_map.map((r,i)=><tr key={i}><td>{p.documents.find(d=>d.id===r.document_id)?.identifier||'Основной документ'}</td><td>{r.part==='translation'?'Перевод':r.part==='main'?'DOCX':'Оригинал'} · {r.source_page}{r.rect?' · фрагмент':''}</td><td>{r.prepared_page}</td><td>{r.volume} / {r.local_page}</td></tr>)}</tbody></table></div></details>
      {!!preview.references.length&&<details open><summary>Страницы переходов из основного документа</summary><p>Укажите подготовленную страницу. Номер параграфа не считается номером страницы PDF.</p>{preview.references.map(ref=><label key={ref.key}>Сноска {ref.footnote} · {ref.mention}<input type="number" min="1" max={typeof ref.pages==='number'?ref.pages:ref.pages.length} value={draft.reference_pages[ref.key]?.page||ref.page} disabled={blocked} onChange={e=>setReference(ref,e.target.value)}/><button disabled={blocked} onClick={()=>setReference(ref,draft.reference_pages[ref.key]?.page||ref.page)}>Подтвердить страницу</button><small>{referenceSource(ref)}</small></label>)}<label><input type="checkbox" checked={draft.accept_start_pages} disabled={blocked} onChange={e=>change({accept_start_pages:e.target.checked})}/> Для ссылок без выбранной страницы явно использовать начало материала</label></details>}
      {preview.issues.map((issue,i)=><p className="warning-note" key={i}>{issue}</p>)}
      <div className="actions">{preview.volumes.map((v,i)=><button key={v.file} disabled={blocked||stale||imageBusy} onClick={()=>perform(()=>show(i))}>Просмотреть том {i+1} · {v.pages} стр.</button>)}<button className="primary" disabled={blocked||stale||!!preview.issues.length} onClick={()=>perform(()=>start('export'))}>Скачать ZIP с PDF-томами</button></div>
      {image&&<div className="volume-image"><div className="actions"><button disabled={blocked||imageBusy||stale||image.page===1} onClick={()=>perform(()=>show(image.index,image.page-1))}>Предыдущая страница</button><span>Том {image.index+1} · {image.page} / {preview.volumes[image.index].pages}</span><button disabled={blocked||imageBusy||stale||image.page>=preview.volumes[image.index].pages} onClick={()=>perform(()=>show(image.index,image.page+1))}>Следующая страница</button></div><div className="volume-canvas"><img src={image.url} alt={`Том ${image.index+1}, страница ${image.page}`}/>{image.links.map((link,i)=><button key={i} className="volume-hotspot" aria-label={link.label} title={link.label} style={{left:link.left+"%",top:link.top+"%",width:link.width+"%",height:link.height+"%"}} disabled={blocked||imageBusy||stale} onClick={()=>perform(()=>show(link.volume_index,link.page))}/>)}</div></div>}
    </div>}
  </section>;
}
