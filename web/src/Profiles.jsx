import React,{useEffect,useState} from 'react';

const labels={format:'Оформление',style:'Шрифт и штампы',citation_style:'Ссылки Word',volume_settings:'PDF-тома'};
export default function Profiles({p,api,onSaved,onWorking,saveBlob,disabled}){
 const [list,setList]=useState([]),[selected,setSelected]=useState(''),[pkg,setPkg]=useState(null),[version,setVersion]=useState('');
 const [name,setName]=useState(''),[naming,setNaming]=useState('keep'),[folder,setFolder]=useState(''),[prefix,setPrefix]=useState(''),[useFolder,setUseFolder]=useState(false),[usePrefix,setUsePrefix]=useState(false);
 const [options,setOptions]=useState({reset_overrides:false,apply_paths:false,apply_prefix:false}),[preview,setPreview]=useState(null),[working,setWorking]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 useEffect(()=>{let live=true;api('/profiles').then(v=>{if(live)setList(v);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[api]);
 useEffect(()=>{onWorking(working);return()=>onWorking(false);},[working,onWorking]);
 useEffect(()=>{let live=true;setPreview(null);setPkg(null);setVersion('');if(selected)api('/profiles/'+selected).then(v=>{if(live){setPkg(v);setVersion(v.versions.at(-1).version_id);}}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[selected,api]);
 async function run(action){if(working)return;setWorking(true);setError('');setNotice('');try{await action();}catch(e){setError(e.message);}finally{setWorking(false);}}
 async function refresh(value){setList(await api('/profiles'));if(value){setSelected(value.profile_id);setPkg(value);setVersion(value.versions.at(-1).version_id);setPreview(null);}}
 async function create(existing=false){const v=await api('/profiles',{name,project_id:p.id,...(existing?{profile_id:selected}:{}),naming,folder:useFolder?folder:null,prefix:usePrefix?prefix:null});await refresh(v);setNotice('Версия профиля сохранена. Подачи не изменены.');}
 function request(){return {profile_id:selected,version_id:version,options};}
 function option(key,value){setOptions({...options,[key]:value});setPreview(null);}
 const blocked=disabled||working;
 return <section className="profile-section" aria-labelledby="profiles-heading" aria-busy={working}>
  <h2 id="profiles-heading">Профили оформления</h2>
  <p className="muted">Сохраните правила подачи для повторного использования. Документы и индивидуальные назначения в профиль не копируются.</p>
  {p.applied_profile&&<p className="success-note">Применён профиль «{p.applied_profile.name}». Эта подача хранит собственную копию выбранной версии.</p>}
  <details><summary>Сохранить правила этой подачи</summary><div className="volume-settings">
   <label>Название профиля<input aria-label="Название профиля" value={name} maxLength={120} disabled={blocked} onChange={e=>setName(e.target.value)}/></label>
   <label>Имена PDF<select aria-label="Имена в профиле" value={naming} disabled={blocked} onChange={e=>setNaming(e.target.value)}><option value="keep">Сохранять существующие</option><option value="identifier">Обозначение</option><option value="identifier_title">Обозначение и название</option></select></label>
   <label><input type="checkbox" checked={useFolder} disabled={blocked} onChange={e=>setUseFolder(e.target.checked)}/> Задать папку для явного применения<input aria-label="Папка в профиле" value={folder} disabled={blocked||!useFolder} onChange={e=>setFolder(e.target.value)}/></label>
   <label><input type="checkbox" checked={usePrefix} disabled={blocked} onChange={e=>setUsePrefix(e.target.checked)}/> Задать префикс для явного применения<input aria-label="Префикс в профиле" value={prefix} disabled={blocked||!usePrefix} onChange={e=>setPrefix(e.target.value)}/></label>
  </div><div className="actions"><button disabled={blocked||!name.trim()} onClick={()=>run(()=>create())}>Сохранить новый профиль</button><button disabled={blocked||!name.trim()||!selected} onClick={()=>run(()=>create(true))}>Сохранить новую версию выбранного профиля</button></div></details>
  <div className="actions"><label>Профиль<select aria-label="Выбрать профиль" value={selected} disabled={blocked} onChange={e=>setSelected(e.target.value)}><option value="">Выберите профиль</option>{list.map(v=><option value={v.id} key={v.id}>{v.name} · версий {v.versions}</option>)}</select></label>
  {pkg&&<label>Версия<select aria-label="Версия профиля" value={version} disabled={blocked} onChange={e=>{setVersion(e.target.value);setPreview(null);}}>{pkg.versions.map((v,i)=><option key={v.version_id} value={v.version_id}>{i+1} · {v.name}</option>)}</select></label>}
  <button disabled={blocked||!selected} onClick={()=>run(async()=>saveBlob(await api('/profiles/'+selected+'/export',undefined,{blob:true}),'Exhibit-profile.json'))}>Скачать профиль</button>
  <label className="upload button">Импортировать профиль<input aria-label="Импортировать профиль" type="file" accept=".json" disabled={blocked} onChange={e=>{const file=e.target.files[0];e.target.value='';if(file)run(async()=>{await refresh(await api('/profiles/import',file));setNotice('Профиль импортирован. Подача не изменена.');});}}/></label></div>
  {selected&&pkg&&<><p className="muted">По умолчанию местное оформление, номера и ручные имена сохраняются. Дополнительные изменения включаются явно и видны до применения.</p>
   <label><input type="checkbox" checked={options.reset_overrides} disabled={blocked} onChange={e=>option('reset_overrides',e.target.checked)}/> Явно сбросить индивидуальное оформление</label><br/>
   <label><input type="checkbox" checked={options.apply_paths} disabled={blocked} onChange={e=>option('apply_paths',e.target.checked)}/> Применить правила папок и автоматически заданных имён</label><br/>
   <label><input type="checkbox" checked={options.apply_prefix} disabled={blocked} onChange={e=>option('apply_prefix',e.target.checked)}/> Применить префикс профиля</label>
   <div className="actions"><button disabled={blocked||!version} onClick={()=>run(async()=>setPreview(await api(`/projects/${p.id}/profile/preview`,request())))}>Проверить применение профиля</button></div>
  </>}
  {error&&<p className="error-note" role="alert">{error}</p>}{notice&&<p className="success-note" role="status">{notice}</p>}
  {preview&&<div><h3>Изменения перед применением</h3>{preview.changes.length?<table><thead><tr><th>Материал</th><th>Было</th><th>Будет</th></tr></thead><tbody>{preview.changes.map(v=><tr key={v.id}><td>{v.title}</td><td>{v.before.identifier} · {v.before.path}</td><td>{v.after.identifier} · {v.after.path}</td></tr>)}</tbody></table>:<p>Местные обозначения и пути остаются прежними.</p>}
   <p>Обновляются: {preview.settings_changes.map(v=>labels[v.field]||v.field).join(', ')||'правила совпадают'}.</p>
   {preview.warnings.map((v,i)=><p className="muted" key={i}>{v}</p>)}{preview.conflicts.map((v,i)=><p className="error-note" key={i}>{v.message}</p>)}
   <button className="primary" disabled={blocked||!!preview.conflicts.length} onClick={()=>run(async()=>{onSaved(await api(`/projects/${p.id}/profile/apply`,{...request(),token:preview.token}));setPreview(null);setNotice('Профиль применён. Проверьте изменённые результаты и ссылки.');})}>Применить выбранную версию профиля</button>
  </div>}
 </section>;
}
