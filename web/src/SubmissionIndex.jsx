import React, {useEffect, useRef, useState} from 'react';

const titles={ru:'Перечень приложений',en:'List of Exhibits'};
const labels={ru:{identifier:'Обозначение',title:'Название',pages:'Страниц',path:'Файл'},en:{identifier:'Exhibit',title:'Title',pages:'Pages',path:'File'}};
export default function SubmissionIndex({p,api,onSaved,onDirty,preview,onPreview,disabled,onWorking}){
  const canonical=JSON.stringify(p.submission_index_settings);
  const [draft,setDraft]=useState(()=>JSON.parse(canonical)),[working,setWorking]=useState(''),[error,setError]=useState('');
  const [remoteStale,setRemoteStale]=useState(false);
  const inFlight=useRef(false),live=useRef(true);
  useEffect(()=>{setDraft(JSON.parse(canonical));},[canonical]);
  useEffect(()=>{live.current=true;return()=>{live.current=false;onDirty(false);onWorking(false);};},[onDirty,onWorking]);
  const dirty=JSON.stringify(draft)!==canonical;
  useEffect(()=>{onDirty(dirty);},[dirty,onDirty]);
  useEffect(()=>{
    if(!preview||!p.submission_index_settings.enabled)return;
    let current=true,timer;
    async function check(){
      try{const state=await api(`/projects/${p.id}/submission-index/status`);if(current){const changed=state.fingerprint!==p.submission_index_fingerprint;setRemoteStale(changed);if(changed&&!preview.stale)onPreview({...preview,stale:true});}}
      catch(e){if(current)setError(e.message);}
      if(current)timer=setTimeout(check,3000);
    }
    check();return()=>{current=false;clearTimeout(timer);};
  },[api,p.id,p.submission_index_fingerprint,p.submission_index_settings.enabled,preview,onPreview]);
  const stale=remoteStale||dirty||!preview||preview.stale||preview.pid!==p.id||preview.preview_token!==p.submission_index_fingerprint;
  const heading=labels[preview?.settings.language||draft.language];
  function change(next){setDraft(next);setError('');}
  function toggle(key,value){change({...draft,[key]:draft[key].includes(value)?draft[key].filter(v=>v!==value):[...draft[key],value]});}
  async function perform(label,action){
    if(inFlight.current)return;
    inFlight.current=true;setWorking(label);onWorking(true);setError('');
    try{await action();}catch(e){if(live.current)setError(e.message);}
    finally{inFlight.current=false;if(live.current){setWorking('');onWorking(false);}}
  }
  return <section className="submission-index" aria-labelledby="submission-index-heading" aria-busy={Boolean(working)}>
    <h2 id="submission-index-heading">Перечень приложений</h2>
    <p className="muted">Необязательный список всех PDF-приложений с названиями, страницами и ссылками. Основной документ в список не входит.</p>
    <details><summary>Как включить и передать перечень</summary><ol><li>Подготовьте приложения. Для готовых PDF Word и переводчик не нужны.</li><li>Отметьте «Добавить перечень приложений», выберите настройки и нажмите «Сохранить перечень».</li><li>Нажмите «Обновить перечень» и проверьте названия, пути и страницы.</li><li>Нажмите «Собрать подачу · ZIP». Распакуйте весь ZIP и передавайте папку Submission целиком, сохраняя вложенные папки.</li></ol><p>После правок обновите перечень. При конфликте имени измените путь приложения в «Документы» или «Папки Submission». Ссылки открывайте из распакованного комплекта в программе, поддерживающей переходы к другим файлам; просмотр внутри ZIP или браузера может их ограничивать.</p></details>
    <fieldset disabled={Boolean(disabled||working)}>
      <label className="index-check"><input type="checkbox" checked={draft.enabled} onChange={e=>change({...draft,enabled:e.target.checked})}/>Добавить перечень приложений</label>
      {draft.enabled?<div className="index-settings">
        <label className="field">Заголовок перечня<input maxLength={200} value={draft.title} onChange={e=>change({...draft,title:e.target.value})}/></label>
        <label className="field">Язык подписей<select value={draft.language} onChange={e=>{const language=e.target.value;change({...draft,language,title:draft.title===titles[draft.language]?titles[language]:draft.title});}}><option value="ru">Русский</option><option value="en">English</option></select></label>
        <label className="field">Порядок перечня<select value={draft.order} onChange={e=>change({...draft,order:e.target.value})}><option value="identifier">По обозначению и номеру</option><option value="project">По порядку документов проекта</option><option value="folder">По папкам</option></select></label>
        <fieldset><legend>Форматы перечня</legend>{['docx','pdf'].map(f=><label className="index-check" key={f}><input type="checkbox" checked={draft.formats.includes(f)} onChange={()=>toggle('formats',f)}/>{f.toUpperCase()}</label>)}</fieldset>
        <fieldset><legend>Дополнительные колонки</legend><p className="muted">Обозначение и название включены всегда.</p>{[['pages','Страниц'],['path','Файл']].map(([key,label])=><label className="index-check" key={key}><input type="checkbox" checked={draft.columns.includes(key)} onChange={()=>toggle('columns',key)}/>{label}</label>)}</fieldset>
      </div>:null}
      {dirty&&p.schema<3?<p className="warning">При сохранении создадим проверенную резервную копию и обновим формат проекта. Для возврата к приложению 0.5.1 понадобится этот архив. Простое открытие проекта формат не меняет.</p>:null}
      <div className="actions"><button disabled={!dirty} onClick={()=>perform('Сохраняем настройку…',async()=>{const saved=await api(`/projects/${p.id}/submission-index`,draft,{method:'PATCH'});if(live.current){onSaved(saved);onPreview(null);}})}>Сохранить перечень</button>
        <button disabled={!dirty} onClick={()=>{change(JSON.parse(canonical));onDirty(false);}}>Отменить настройки перечня</button>
        {draft.enabled?<button disabled={dirty||!p.documents.length||Boolean(p.issues.length)} onClick={()=>perform('Рассчитываем страницы и состав…',async()=>{const current=await api(`/projects/${p.id}`);if(live.current)onSaved(current);const result=await api(`/projects/${p.id}/submission-index/preview`,{});if(live.current){setRemoteStale(false);onPreview({...result,pid:p.id});}})}>Обновить перечень</button>:null}</div>
    </fieldset>
    <p role="status" aria-live="polite">{working||(draft.enabled?(!p.documents.length?'Добавьте PDF-приложения, чтобы построить перечень.':stale?'Перечень нужно обновить.':`Перечень проверен: ${preview.count} приложений · ${preview.settings.formats.join(' / ').toUpperCase()}`):'Перечень выключен.')}</p>
    {error?<p role="alert" className="error-note">{error}</p>:null}
    {draft.enabled&&preview&&preview.pid===p.id?<div className={'index-table-scroll'+(stale?' index-stale':'')} tabIndex={0} role="region" aria-label="Предпросмотр перечня приложений">
      <table><caption>{preview.settings.title} · {preview.count} приложений{stale?' · устаревшие данные':''}</caption><thead><tr><th scope="col">{heading.identifier}</th><th scope="col">{heading.title}</th>{preview.settings.columns.includes('pages')?<th scope="col">{heading.pages}</th>:null}{preview.settings.columns.includes('path')?<th scope="col">{heading.path}</th>:null}</tr></thead><tbody>{preview.rows.map(row=><tr key={row.id}><td>{row.identifier}</td><td>{row.title}</td>{preview.settings.columns.includes('pages')?<td>{row.pages}</td>:null}{preview.settings.columns.includes('path')?<td>{row.path}</td>:null}</tr>)}</tbody></table>
    </div>:null}
  </section>;
}
