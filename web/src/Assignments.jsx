import React,{useState,useEffect} from 'react';

function values(p){return p.documents.map(d=>({id:d.id,designation:d.effective_format.designation,number:d.number,prefix:d.prefix,folder:d.folder,filename:d.filename}));}
export default function Assignments({p,api,onSaved,onDirty,onWorking,disabled}){
 const canonical=JSON.stringify(values(p));
 const [rows,setRows]=useState(()=>values(p)),[naming,setNaming]=useState('keep'),[decisions,setDecisions]=useState({}),[preview,setPreview]=useState(null),[checked,setChecked]=useState('');
 const [working,setWorking]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const dirty=JSON.stringify(rows)!==canonical||naming!=='keep';
 useEffect(()=>{setRows(JSON.parse(canonical));setNaming('keep');setPreview(null);setDecisions({});setChecked('');},[canonical]);
 useEffect(()=>{onDirty(dirty);return()=>onDirty(false);},[dirty,onDirty]);
 useEffect(()=>{onWorking(working);return()=>onWorking(false);},[working,onWorking]);
 function request(){const before=JSON.parse(canonical);return {rows:rows.map((r,i)=>Object.fromEntries(Object.entries(r).filter(([k,v])=>k==='id'||v!==before[i]?.[k]))),naming,references:decisions};}
 const matches=JSON.stringify(request())===checked;
 const blocked=disabled||working;
 function edit(i,key,value){setRows(rows.map((v,n)=>n===i?{...v,[key]:value}:v));setPreview(null);setError('');}
 async function run(action){if(working)return;setWorking(true);setError('');setNotice('');try{await action();}catch(e){setError(e.message);}finally{setWorking(false);}}
 return <section className="assignments-section" aria-labelledby="assignments-heading" aria-busy={working}>
  <h2 id="assignments-heading">Назначения подачи</h2>
  <details><summary>Изменить назначенные номера и пути</summary>
   <p className="muted">Можно обменять занятые номера. Сначала проверяется итоговая таблица и известные упоминания DOCX. Исходники и прежние комплекты сохраняются; другая подача не изменяется.</p>
   <div className="table-scroll"><table><thead><tr><th>Материал</th><th>Обозначение</th><th>Префикс</th><th>Номер</th><th>Папка</th><th>Имя PDF</th></tr></thead><tbody>{rows.map((r,i)=><tr key={r.id}><td>{p.documents.find(d=>d.id===r.id)?.title}</td>
    <td><select aria-label={`Обозначение назначения ${i+1}`} value={r.designation} disabled={blocked} onChange={e=>edit(i,'designation',e.target.value)}><option value="Exhibit">Exhibit</option><option value="Annex">Annex</option><option value="">Без слова</option></select></td>
    <td><input aria-label={`Префикс назначения ${i+1}`} value={r.prefix} disabled={blocked} onChange={e=>edit(i,'prefix',e.target.value)}/></td>
    <td><input aria-label={`Номер назначения ${i+1}`} type="number" min="1" value={r.number??''} disabled={blocked} onChange={e=>edit(i,'number',e.target.value===''?null:Number(e.target.value))}/></td>
    <td><input aria-label={`Папка назначения ${i+1}`} value={r.folder} disabled={blocked} onChange={e=>edit(i,'folder',e.target.value)}/></td>
    <td><input aria-label={`Имя PDF назначения ${i+1}`} value={r.filename} disabled={blocked} onChange={e=>edit(i,'filename',e.target.value)}/></td>
   </tr>)}</tbody></table></div>
   <label>Автоматические имена<select aria-label="Правило имён при перенумерации" value={naming} disabled={blocked} onChange={e=>{setNaming(e.target.value);setPreview(null);}}><option value="keep">Сохранить имена</option><option value="identifier">Обозначение.pdf</option><option value="identifier_title">Обозначение и название.pdf</option></select></label>
   <p className="muted">Ручное имя не меняется автоматически. Для изменения введите новое имя непосредственно в таблице.</p>
   <div className="actions"><button disabled={blocked||!dirty} onClick={()=>run(async()=>{const data=request();setPreview(await api(`/projects/${p.id}/assignments/preview`,{request:data}));setChecked(JSON.stringify(data));})}>Проверить новые назначения</button><button disabled={blocked||!dirty} onClick={()=>{setRows(JSON.parse(canonical));setNaming('keep');setPreview(null);setDecisions({});setNotice('Изменения отменены. Подача не менялась.');}}>Отменить изменения назначений</button></div>
   {working&&<p role="status">Проверяем и сохраняем согласованные назначения…</p>}
   {error&&<p className="error-note" role="alert">{error}</p>}{notice&&<p className="success-note" role="status">{notice}</p>}
   {preview&&<div><h3>План изменения назначений</h3><table><thead><tr><th>Материал</th><th>Было</th><th>Будет</th></tr></thead><tbody>{preview.changes.map(v=><tr key={v.id}><td>{v.title}</td><td>{v.before.identifier} · {v.before.path}</td><td>{v.after.identifier} · {v.after.path}</td></tr>)}</tbody></table>
    {!!preview.references.length&&<><h4>Упоминания основного DOCX</h4><button disabled={blocked} onClick={()=>setDecisions({...decisions,...Object.fromEntries(preview.references.filter(r=>r.status!=='blocked').map(r=>[r.key,'update']))})}>Обновить все однозначные упоминания</button>
     <table><thead><tr><th>Сноска</th><th>Было</th><th>Предложено</th><th>Решение</th></tr></thead><tbody>{preview.references.map(r=><tr key={r.key}><td>{r.footnote}</td><td>{r.before}</td><td>{r.after}{r.message&&<p>{r.message}</p>}</td><td><select aria-label={`Решение для сноски ${r.footnote}`} value={decisions[r.key]||r.decision||''} disabled={blocked} onChange={e=>setDecisions({...decisions,[r.key]:e.target.value})}><option value="" disabled>Выберите</option><option value="update">Обновить</option><option value="keep">Явно оставить текст</option></select></td></tr>)}</tbody></table></>}
    {!matches&&<p className="warning-note">Решения изменены. Нажмите «Проверить новые назначения» ещё раз.</p>}
    {preview.warnings.map((v,i)=><p className="muted" key={i}>{v}</p>)}{preview.conflicts.map((v,i)=><p className="error-note" key={i}>{v.message}</p>)}
    <button className="primary" disabled={blocked||!matches||!!preview.conflicts.length||!preview.changes.length} onClick={()=>run(async()=>{const result=await api(`/projects/${p.id}/assignments/apply`,{request:request(),token:preview.token});onSaved(result);setPreview(null);setNotice('Назначения сохранены. Проверьте изменённые PDF и сопоставления перед новым комплектом.');})}>Применить новые назначения</button>
    <p className="muted">При применении живой Word не изменяется. Если продолжаете писать в нём, выполните прежнюю команду проверки и обновления ссылок в панели; для обычной работы скачайте производный DOCX из комплекта.</p>
   </div>}
  </details>
 </section>;
}
