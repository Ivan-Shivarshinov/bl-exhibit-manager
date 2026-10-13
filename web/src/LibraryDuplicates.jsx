import React,{useEffect,useState} from 'react';

export const unresolvedDuplicates=(rows,decisions)=>rows.some(r=>(r.duplicates.length||(r.related?.length&&!r.version.parent))&&!r.allow_duplicate&&!decisions[r.version.id]);

export default function LibraryDuplicates({api,plan,decisions,onChange,onView,blocked}) {
  const [preview,setPreview]=useState(null),[limits,setLimits]=useState({});
  useEffect(()=>()=>{if(preview?.image)URL.revokeObjectURL(preview.image);},[preview?.image]);
  useEffect(()=>{setPreview(null);setLimits({});},[plan.token]);
  async function view(match){
    if(!match.batch){await onView(match);return;}
    setPreview({title:match.title,loading:true});
    try{const role=match.parts.original?'original':'ready';const blob=await api(`/material-previews/${plan.token}/${match.version_id}?role=${role}`,undefined,{blob:true});setPreview({title:match.title,image:URL.createObjectURL(blob)});}
    catch(e){setPreview({title:match.title,error:e.message});}
  }
  function decide(row,action,match){onChange({...decisions,[row.version.id]:{action,...(match?{version_id:match.version_id}:{})}});}
  return <section className="library-duplicates"><ul>{plan.rows.map(row=>{
    const matches=[...row.duplicates,...(row.related||[])].map(key=>plan.matches[key]),decision=decisions[row.version.id];
    return <li key={row.version.id} className="duplicate-row"><b>{row.version.metadata.title}</b> · {Object.keys(row.version.parts).map(k=>({original:'Оригинал',translation:'Подтверждённый перевод',ready:'Готовый PDF'})[k]).join(', ')}
      {matches.length>0&&<><p className="pending">{row.duplicates.length?'Найдено полное совпадение содержания. Выберите действие для этой строки.':'Совпадает оригинал, но состав перевода отличается. Это не полное совпадение.'}</p>
        <ul>{matches.slice(0,limits[row.version.id]||10).map(match=><li key={match.version_id} className="duplicate-match"><b>«{match.title}» · {match.batch?'в этой загрузке':`версия ${match.version_number}`}{match.hidden?' · скрытый материал':''}</b><p>{match.kind==='exact'?'Полное совпадение состава и байтов файлов.':'Одинаковый оригинал, другой перевод или состав частей.'}</p><div className="actions">
          <button disabled={blocked} onClick={()=>view(match)}>Посмотреть совпавшую версию</button>
          {match.kind==='exact'&&<button disabled={blocked} aria-pressed={decision?.action==='reuse'&&decision.version_id===match.version_id} onClick={()=>decide(row,'reuse',match)}>Использовать существующий материал</button>}
          <button disabled={blocked} aria-pressed={decision?.action==='version'&&decision.version_id===match.version_id} onClick={()=>decide(row,'version',match)}>Сохранить новую версию этого материала</button>
        </div></li>)}</ul>{matches.length>(limits[row.version.id]||10)&&<button disabled={blocked} onClick={()=>setLimits({...limits,[row.version.id]:(limits[row.version.id]||10)+10})}>Показать ещё совпадения · всего {matches.length}</button>}<button disabled={blocked} aria-pressed={decision?.action==='separate'} onClick={()=>decide(row,'separate')}>Сохранить отдельный материал</button>
        <p role="status">{decision?({reuse:'Будет использована найденная версия; новая копия в библиотеке не создаётся.',version:'Будет создана новая версия выбранного материала. Подачи не обновятся.',separate:'Будет создан отдельный материал.'})[decision.action]:'Решение ещё не выбрано.'}</p>
      </>}
    </li>;
  })}</ul>{plan.rows.some(r=>r.duplicates.length)&&<button disabled={blocked} onClick={()=>onChange({...decisions,...Object.fromEntries(plan.rows.filter(r=>r.duplicates.length).map(r=>[r.version.id,{action:'separate'}]))})}>Сохранить все совпадения отдельно</button>}
  {preview&&<section><h3>Просмотр: {preview.title} · в этой загрузке</h3><button onClick={()=>setPreview(null)}>Закрыть просмотр совпадения</button>{preview.loading?<p role="status">Готовим страницу…</p>:preview.error?<p role="alert">{preview.error}</p>:<img className="material-preview" src={preview.image} alt={`Совпадение: ${preview.title}`}/>}</section>}</section>;
}
