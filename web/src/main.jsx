import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import BatchDialog from './BatchDialog';
import MainPdf from './MainPdf';
import ReferenceScope from './ReferenceScope';
import TranslationDialog from './TranslationDialog';

async function api(path, body, options = {}) {
  let response;
  try { response = await fetch('/api' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-Exhibit-Local': '1', ...(body instanceof File ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : body instanceof File ? body : JSON.stringify(body), ...options,
  }); } catch { throw new Error('Нет связи с локальным приложением. Запустите Start.cmd (Windows) или Start.command (macOS), затем повторите действие. Сохранённые проекты остаются на компьютере.'); }
  if (!response.ok) {
    let message = `Ошибка ${response.status}`;
    try { const data = await response.json(); message = data.detail || message; } catch { /* non-JSON failure */ }
    throw new Error(typeof message === 'string' ? message : 'Некорректные данные запроса.');
  }
  return options.blob ? response.blob() : response.json();
}

function Upload({ children, accept = '.pdf', multiple = false, disabled, onFiles }) {
  return <label className={'upload button' + (disabled ? ' disabled' : '')}>{children}<input aria-label={children} type="file" accept={accept} multiple={multiple} disabled={disabled} onChange={e => { const files = [...e.target.files]; e.target.value = ''; if (files.length) onFiles(files); }}/></label>;
}
function Field({ label, children }) { return <label className="field"><span>{label}</span>{children}</label>; }
function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function App() {
  const [projects, setProjects] = useState([]), [p, setP] = useState(null), [selected, setSelected] = useState(null);
  const [tab, setTab] = useState('docs'), [search, setSearch] = useState(''), [busy, setBusy] = useState('');
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [docDirty, setDirty] = useState(false), [styleDirty, setStyleDirty] = useState(false);
  const dirty = docDirty || styleDirty;
  const [online, setOnline] = useState(null), [loaded, setLoaded] = useState(false), [retry, setRetry] = useState(0);
  const [creating, setCreating] = useState(false), [newName, setNewName] = useState('');
  const [translation, setTranslation] = useState(null);
  const [checked, setChecked] = useState([]), [sort, setSort] = useState('added'), [batch, setBatch] = useState(null);
  useEffect(() => { setChecked([]); setSearch(''); }, [p?.id]);
  const [revision, setRevision] = useState(0), [focusRef, setFocusRef] = useState(null);
  useEffect(() => { if (!dirty) return; const warn = e => { e.preventDefault(); e.returnValue = ''; }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn); }, [dirty]);
  const active = p?.documents.find(d => d.id === selected) || p?.documents[0];
  const orderedDocs = [...(p?.documents || [])];
  if (sort === 'title') orderedDocs.sort((a,b)=>a.title.localeCompare(b.title,'ru'));
  if (sort === 'number') orderedDocs.sort((a,b)=>(a.number===null)-(b.number===null) || a.prefix.localeCompare(b.prefix,'ru') || (a.number||0)-(b.number||0));
  const visibleDocs = orderedDocs.filter(d=>`${d.identifier} ${d.title} ${d.folder}`.toLowerCase().includes(search.toLowerCase()));
  function openBatch(action, ids=orderedDocs.filter(d=>checked.includes(d.id)).map(d=>d.id)) { setError(''); setBatch({action,ids}); }
  async function refreshList() { const items = await api('/projects'); setProjects(items); return items; }
  function accept(project) { setP(project); setRevision(r => r+1); try { localStorage.setItem('bl-exhibit-last-project-v1', project.id); } catch { /* Saving the project itself does not depend on browser storage. */ } }
  useEffect(() => {
    let live = true, previous = null, timer;
    const check = async () => {
      let ok = false;
      try { const r = await fetch('/api/health', {signal: AbortSignal.timeout(4000)}); ok = r.ok && (await r.json()).application === 'bl-exhibit-manager'; } catch { /* Server may be stopped. */ }
      if (!live) return;
      setOnline(ok);
      if (ok && previous === false) { setRevision(r => r+1); setNotice('Связь восстановлена. Можно продолжить работу.'); }
      previous = ok;
      timer = setTimeout(check, 5000);
    };
    check();
    return () => { live = false; clearTimeout(timer); };
  }, [retry]);
  async function run(label, fn) {
    if (busy || online !== true) return;
    setBusy(label); setError(''); setNotice('');
    try { return await fn(); } catch (e) { setError(e.message); } finally { setBusy(''); }
  }
  function guard(fn) { if (dirty) { setError('Сначала сохраните настройки или отмените изменения.'); return; } fn(); }
  useEffect(() => { if (p || online !== true) return; let live = true; (async () => {
    try {
      const items = await api('/projects'); if (!live) return; setProjects(items);
      let last; try { last = localStorage.getItem('bl-exhibit-last-project-v1'); } catch { /* Browser storage can be unavailable. */ }
      const id = items.find(x => x.id === last)?.id || items[0]?.id;
      if (id) { const project = await api('/projects/' + id); if (live) accept(project); }
      if (live) { setLoaded(true); setError(''); }
    } catch (e) { if (live) setError(e.message); }
  })(); return () => { live = false; }; }, [online, retry]);
  async function mutate(path, body = {}, message = 'Сохранено на этом компьютере') {
    return run('Сохраняем…', async () => { const project = await api(`/projects/${p.id}${path}`, body); accept(project); setDirty(false); setNotice(message); return project; });
  }
  async function upload(files, kind = 'original', did) {
    return run('Загружаем файлы…', async () => {
      let current;
      const failures = [];
      for (let i=0; i<files.length; i++) {
        setBusy(`Загрузка ${i+1} из ${files.length}…`);
        const query = new URLSearchParams({name: files[i].name, kind, ...(did ? {did} : {})});
        try { current = await api(`/projects/${p.id}/upload?${query}`, files[i]); accept(current); }
        catch (e) { failures.push(`${files[i].name}: ${e.message}`); }
      }
      if (failures.length) setError(failures.join(' · '));
      else setNotice('Файлы сохранены. Исходники не изменены.');
    });
  }
  const operationBusy = busy || (online !== true ? 'Нет связи' : '');
  const disabled = Boolean(operationBusy || dirty);
  return <>
    <div inert={Boolean(batch || creating || translation)}>
    <header><div className="brand">Buzko Legal <span>Приложения</span></div><div className="project-switch"><label htmlFor="project">Проект</label><select id="project" aria-label="Проект" value={p?.id || ''} disabled={disabled} onChange={e => run('Открываем…', async () => { accept(await api('/projects/'+e.target.value)); setSelected(null); setSearch(''); setTab('docs'); })}><option value="" disabled>Выберите подачу</option>{projects.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select><button disabled={disabled} onClick={() => setCreating(true)}>Новая</button></div><span className="local"><i/>{online === false ? 'Нет связи' : 'Локально'}</span></header>
    <nav aria-label="Этапы подготовки">{[['docs','Документы'],['links','Ссылки в Word'],['export','Проверка и экспорт']].map(([key, label]) => <button key={key} className={tab === key ? 'active' : ''} disabled={Boolean(busy)} onClick={() => guard(() => setTab(key))}>{label}{key==='export' && p?.issues.length ? <small>{p.issues.length}</small> : null}</button>)}<span className="save-state">{dirty ? 'Есть несохранённые настройки' : p ? 'Работа сохранена на компьютере' : 'Первый сквозной этап'}</span></nav>
    <div className={'message-strip ' + (error ? 'error' : '')} aria-live="polite">{busy || error || notice || 'PDF приложений и DOCX со сносками. Все исходники сохраняются отдельно.'}{error && !busy ? <button aria-label="Закрыть сообщение" onClick={() => setError('')}>×</button> : null}</div>
    {online === false ? <div className="connection-alert" role="alert"><b>Локальное приложение остановлено или недоступно.</b><p>Запустите Start.cmd на Windows или Start.command на macOS. Сохранённые проекты остаются на компьютере; эта страница автоматически восстановит связь.</p><button onClick={() => setRetry(r => r+1)}>Повторить подключение</button></div> : null}
    {!p && !loaded ? <main className="empty"><h1>{online === false ? 'Ожидаем запуск приложения' : error ? 'Не удалось открыть сохранённую работу' : 'Открываем сохранённую работу…'}</h1>{error && online ? <button onClick={() => setRetry(r => r+1)}>Повторить загрузку</button> : null}</main> : !p ? <main className="empty"><h1>Подготовьте первую подачу</h1><p>Создайте локальный проект или откройте вымышленные учебные документы для проверки полного процесса.</p><div className="actions"><button className="primary" disabled={Boolean(busy)} onClick={() => setCreating(true)}>Создать подачу</button><button disabled={Boolean(busy)} onClick={() => run('Создаём учебные документы…', async () => { accept(await api('/demo', {})); await refreshList(); })}>Открыть учебный комплект</button></div><p className="muted">Работа с приложениями доступна без основного Word-документа.</p></main> : <>
      {tab === 'docs' ? <main className="workbench">
        <section className="document-list"><div className="section-heading"><h1>Документы</h1><Upload multiple disabled={disabled} onFiles={upload}>Добавить PDF</Upload></div><input className="search" type="search" aria-label="Поиск документов" placeholder="Поиск по документам, папкам, номерам…" value={search} onChange={e => setSearch(e.target.value)}/>
          <div className="batch-toolbar"><label className="select-visible"><input type="checkbox" aria-label="Выбрать видимые документы" disabled={disabled||!visibleDocs.length} checked={visibleDocs.length>0&&visibleDocs.every(d=>checked.includes(d.id))} onChange={e=>setChecked(e.target.checked?[...new Set([...checked,...visibleDocs.map(d=>d.id)])]:checked.filter(id=>!visibleDocs.some(d=>d.id===id)))}/>Выбрано: {checked.length}</label><select aria-label="Сортировка документов" value={sort} disabled={disabled} onChange={e=>setSort(e.target.value)}><option value="added">Порядок загрузки</option><option value="number">По номеру</option><option value="title">По названию</option></select><button disabled={disabled||!checked.length} onClick={()=>openBatch('organize')}>Номера и имена</button><button disabled={disabled} onClick={()=>openBatch('format')}>Оформление</button>{checked.length?<button disabled={disabled} onClick={()=>setChecked([])}>Снять выбор</button>:null}{checked.some(id=>!visibleDocs.some(d=>d.id===id))?<small>Выбор включает документы вне текущего фильтра.</small>:null}</div>
          <div className="table-scroll"><table><thead><tr><th aria-label="Выбор"/><th>Номер</th><th>Документ</th><th>Папка</th><th>Статус</th></tr></thead><tbody>{visibleDocs.map(d => <tr key={d.id} className={active?.id === d.id ? 'selected' : ''}><td><input type="checkbox" aria-label={`Выбрать ${d.identifier||d.title}`} checked={checked.includes(d.id)} disabled={disabled} onChange={e=>setChecked(e.target.checked?[...checked,d.id]:checked.filter(id=>id!==d.id))}/></td><td><button className="row-button" disabled={Boolean(busy)} onClick={() => guard(() => setSelected(d.id))}>{d.identifier || '—'}</button></td><td><button className="title-button" disabled={Boolean(busy)} onClick={() => guard(() => setSelected(d.id))}>{d.title}</button><small>{d.translation ? 'Перевод прикреплён' : d.mode === 'passthrough' ? 'Без обработки' : 'Только оригинал'}</small></td><td>{d.folder || '—'}</td><td><span className={'status ' + (d.ready ? 'ready' : '')}>{d.ready ? 'Проверено' : 'Проверить'}</span></td></tr>)}</tbody></table>{!p.documents.length ? <p className="muted inset">Добавьте один или несколько PDF.</p> : search && !p.documents.some(d => `${d.identifier} ${d.title} ${d.folder}`.toLowerCase().includes(search.toLowerCase())) ? <p className="muted inset">По этому запросу ничего не найдено. <button onClick={() => setSearch('')}>Сбросить поиск</button></p> : null}</div>
          <div className="word-upload"><h2>Основной документ</h2><p>{p.main?.name || 'DOCX с существующими сносками'}</p><Upload accept=".docx" disabled={disabled} onFiles={f => upload(f, 'main')}>{p.main ? 'Заменить DOCX' : 'Загрузить DOCX'}</Upload><small>Необязателен для подготовки приложений.</small></div><footer>Всего документов: {p.documents.length}</footer>
        </section>
        {active ? <><Preview key={active.id+'-preview'} p={p} doc={active} revision={revision} disabled={disabled} onSave={changes => mutate(`/documents/${active.id}`, changes)} onError={setError}/><Inspector key={active.id+'-inspector'} p={p} doc={active} busy={operationBusy} onDirty={setDirty} mutate={mutate} upload={upload} onFormat={()=>guard(()=>openBatch('format',[active.id]))} onTranslation={()=>guard(()=>setTranslation(active))}/></> : <div className="preview-empty"><p>Выберите документ для просмотра и оформления.</p></div>}
      </main> : null}
      {tab === 'links' ? <Links error={error} clearError={()=>setError('')} p={p} busy={operationBusy} mutate={mutate} upload={upload} focusRef={focusRef}/> : null}
      {tab === 'export' ? <section className="export-page"><div><h1>Проверка перед сборкой</h1><p className="muted">В комплект войдут все загруженные приложения, в том числе не упомянутые в Word.</p></div>
        <div className="export-grid"><div><h2>{p.issues.length ? `Требуют внимания: ${p.issues.length}` : 'Блокирующих ошибок нет'}</h2>{p.issues.length ? <ul className="issues">{p.issues.map((issue, i) => <li key={i}><span className="issue-dot">!</span><div>{issue.message}<small>{p.documents.find(d => d.id === issue.document)?.title}</small></div><button disabled={Boolean(busy)} onClick={() => guard(() => { if (issue.document) { setSelected(issue.document); setTab('docs'); } else { setFocusRef(issue.reference); setTab('links'); } })}>Перейти</button></li>)}</ul> : <p className="success-note">Материалы проверены в инструменте. После экспорта проверьте комплект в Word и PDF-просмотрщике перед подачей.</p>}
          <button className="primary" disabled={Boolean(operationBusy || dirty || p.issues.length)} onClick={() => run('Собираем подачу…', async () => { const blob = await api(`/projects/${p.id}/export`, {}, {blob: true}); saveBlob(blob, 'Submission.zip'); setNotice('ZIP собран. Распакуйте весь комплект, сохранив структуру папок.'); })}>Собрать подачу · ZIP</button><p className="muted">{p.main?'Основной DOCX и PDF войдут в ZIP вместе с приложениями. Если PDF ещё не подготовлен, сборка создаст его автоматически.':'В ZIP войдут подготовленные приложения.'}</p>{p.main?<MainPdf key={p.id} p={p} api={api} run={run} disabled={Boolean(operationBusy||dirty)}/>:null}</div>
        <div><h2>Оформление подачи</h2><StyleForm style={p.style} onDirty={setStyleDirty} busy={operationBusy} onSave={style => mutate('/style', style)}/><p className="muted">В полях страницы штампы сохраняют размер и положение содержимого. Если места нет, измените отступы или выберите дополнительную полосу. После изменения оформления приложения требуют повторной проверки.</p><h2>Структура комплекта</h2><pre>Submission/{'\n'}{p.main ? '  Main document.docx\n  Main document.pdf\n' : ''}{p.documents.map(d => '  ' + (d.folder ? d.folder+'/' : '') + d.filename).join('\n')}</pre></div></div>
      </section> : null}
    </>}
    </div>
    {batch && p ? <BatchDialog p={p} ids={batch.ids} initialAction={batch.action} busy={operationBusy} error={error} onClose={()=>{setBatch(null);setError('');}} onPreview={request=>run('Проверяем изменения…',()=>api(`/projects/${p.id}/batch/preview`,request))} onApply={(request,token)=>run('Применяем изменения…',async()=>{accept(await api(`/projects/${p.id}/batch/apply`,{request,token}));setBatch(null);setChecked([]);setNotice('Массовые изменения сохранены. Проверьте изменённые результаты.');})}/> : null}
    {translation ? <TranslationDialog p={p} doc={translation} api={api} onClose={()=>setTranslation(null)} onApplied={project=>{accept(project);setTranslation(null);setNotice('Проверенный перевод прикреплён. Просмотрите результат и подтвердите подготовку.');}}/> : null}
    {creating ? <div className="modal-backdrop"><form className="modal" onSubmit={e => { e.preventDefault(); run('Создаём проект…', async () => { accept(await api('/projects', {name:newName})); await refreshList(); setSelected(null); setTab('docs'); setCreating(false); setNewName(''); }); }}><h2>Новая подача</h2><Field label="Название"><input autoFocus required maxLength={120} value={newName} onChange={e => setNewName(e.target.value)}/></Field><div className="actions"><button className="primary" disabled={Boolean(busy)}>Создать</button><button type="button" disabled={Boolean(busy)} onClick={() => setCreating(false)}>Отмена</button></div><p className="muted" style={{marginTop:24}}>Для повторной проверки можно создать свежую копию вымышленных документов.</p><button type="button" disabled={Boolean(busy)} onClick={() => run('Создаём учебные документы…', async () => { accept(await api('/demo', {})); await refreshList(); setSelected(null); setTab('docs'); setCreating(false); })}>Новый учебный комплект</button></form></div> : null}
  </>;
}

const FIELDS = ['title','prefix','number','designation','filename','folder','language','mode','original_label','translation_label','aliases','filename_mode'];
function draftFrom(doc) { return Object.fromEntries(FIELDS.map(k => [k, k === 'aliases' ? doc[k].join('\n') : ['designation','original_label','translation_label'].includes(k) ? doc.effective_format[k] : doc[k] ?? ''])); }
function Inspector({p, doc, busy, onDirty, mutate, upload, onFormat, onTranslation}) {
  const [draft, setDraft] = useState(() => draftFrom(doc)), [translationMode, setTranslationMode] = useState('manual');
  useEffect(() => { setDraft(draftFrom(doc)); onDirty(false); }, [doc]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftFrom(doc));
  function change(key, value) { const next = {...draft, [key]:value, ...(key==='filename'?{filename_mode:'manual'}:{})}; setDraft(next); onDirty(JSON.stringify(next) !== JSON.stringify(draftFrom(doc))); }
  const field = (label, key, type='text') => <Field label={label}><input type={type} value={draft[key]} min={type === 'number' ? 1 : undefined} onChange={e => change(key,e.target.value)}/></Field>;
  return <aside className="inspector"><h2>Настройки приложения</h2><form onSubmit={e => { e.preventDefault(); const original=draftFrom(doc);const changes=Object.fromEntries(FIELDS.filter(k=>draft[k]!==original[k]).map(k=>[k,draft[k]]));if('number' in changes)changes.number=draft.number===''?null:Number(draft.number);if('aliases' in changes)changes.aliases=draft.aliases.split('\n').map(x=>x.trim()).filter(Boolean);mutate(`/documents/${doc.id}`, changes); }}><fieldset disabled={Boolean(busy)}>
    {field('Название документа','title')}<div className="pair">{field('Префикс (необязателен)','prefix')}{field('Номер (необязателен)','number','number')}</div>
    <Field label="Обозначение"><select value={draft.designation} onChange={e=>change('designation',e.target.value)}><option>Exhibit</option><option>Annex</option><option value="">Без слова</option></select></Field>
    {field('Имя выходного файла','filename')}<label className="checkline"><input type="checkbox" checked={draft.filename_mode==='manual'} onChange={e=>change('filename_mode',e.target.checked?'manual':'source')}/>Сохранять это имя при массовом переименовании</label>{field('Папка','folder')}{field('Язык оригинала','language')}
    <Field label="Обработка"><select value={draft.mode} onChange={e=>change('mode',e.target.value)}><option value="prepare">Подготовить приложение</option><option value="passthrough">Использовать без обработки</option></select></Field>
    {draft.mode === 'prepare' ? <>{field('Левая пометка оригинала','original_label')}{doc.translation ? field('Левая пометка перевода','translation_label') : null}</> : <p className="muted">Файл будет скопирован байт в байт. Штампы и выбор страниц не применяются.</p>}
    <details><summary>Другие варианты упоминания в сносках</summary><Field label="Один вариант на строку"><textarea rows={2} value={draft.aliases} onChange={e=>change('aliases',e.target.value)}/></Field></details>
    {dirty ? <div className="actions"><button className="primary">Сохранить настройки</button><button type="button" onClick={()=>{setDraft(draftFrom(doc));onDirty(false);}}>Отменить</button></div> : null}
  </fieldset></form>
  <section className="format-origin"><h2>Оформление документа</h2><p className="muted">{doc.effective_format.font} · {doc.effective_format.size} pt · отступ {doc.effective_format.margin} pt</p><details><summary>Откуда взяты настройки</summary>{Object.entries({font:'Шрифт',size:'Размер',margin:'Отступ',top:'Отступ сверху',stamp_mode:'Размещение',designation:'Обозначение',original_label:'Пометка оригинала',translation_label:'Пометка перевода'}).map(([k,label])=><p className="muted" key={k}>{label}: {{project:'подача',group:'группа',document:'этот документ'}[doc.format_sources[k]]}</p>)}</details><button disabled={Boolean(busy||dirty)} onClick={onFormat}>Изменить оформление документа</button></section>
  <section className="translation"><h2>Перевод</h2>{doc.mode === 'prepare' ? <><Field label="Способ перевода"><select value={translationMode} onChange={e=>setTranslationMode(e.target.value)}><option value="manual">Прикрепить готовый PDF</option><option value="claude">Через свою подписку Claude / Codex</option></select></Field>{translationMode === 'claude' ? <div><p className="muted">Перевод через Claude или Codex. Перевод можно проверить и отредактировать перед созданием PDF.</p><button disabled={Boolean(busy||dirty)} onClick={onTranslation}>Открыть переводчик</button></div> : <>
    {doc.translation ? <><div className={'translation-state ' + (doc.translation_confirmed ? 'ready' : '')}><b>{doc.translation_confirmed ? 'Перевод проверен' : 'Перевод ожидает проверки'}</b><small>{doc.translation.name}</small></div><button disabled={Boolean(busy || dirty || doc.translation_confirmed)} onClick={()=>mutate(`/documents/${doc.id}/review/translation`)}>Подтвердить перевод</button></> : <p className="muted">Необязательно. Приложение можно собрать только с оригиналом.</p>}
    <div className="actions"><Upload disabled={Boolean(busy || dirty)} onFiles={f=>upload(f,'translation',doc.id)}>{doc.translation ? 'Заменить перевод' : 'Прикрепить PDF перевода'}</Upload>{doc.translation ? <button disabled={Boolean(busy || dirty)} onClick={()=>mutate(`/documents/${doc.id}/review/remove_translation`)}>Открепить</button> : null}</div></>}</> : <p className="muted">Готовый PDF используется целиком.</p>}</section>
  <div className="review-actions"><p className="muted">Просмотрите «Результат» перед подтверждением. Изменения потребуют новой проверки.</p><button className="primary full" disabled={Boolean(busy || dirty || doc.ready)} onClick={()=>mutate(`/documents/${doc.id}/review/document`, {}, 'Подготовка подтверждена')}>{doc.ready ? 'Подготовка подтверждена' : 'Подтвердить подготовку'}</button><Upload disabled={Boolean(busy || dirty)} onFiles={f=>upload(f,'original',doc.id)}>Заменить исходный PDF</Upload></div>
  </aside>;
}

function parsePages(value, count) {
  const result=[];
  for (const token of value.split(',').map(x=>x.trim())) {
    const match = /^(\d+)(?:-(\d+))?$/.exec(token);
    if (!match) throw new Error('Укажите страницы, например 1, 3-5.');
    const a=Number(match[1]), b=Number(match[2] || match[1]);
    if (a<1 || b<a || b>count) throw new Error('Диапазон страниц вне документа.');
    for(let n=a;n<=b;n++) result.push({page:n});
  }
  return result;
}
function Preview({p, doc, revision, disabled, onSave, onError}) {
  const [part, setPart] = useState('original'), [page, setPage] = useState(1), [imageUrl, setImageUrl] = useState('');
  const [imageError, setImageError] = useState(''), [loading,setLoading]=useState(false), [drawing,setDrawing]=useState(false), [zoom,setZoom]=useState('fit');
  const [rect,setRect]=useState(null), [range,setRange]=useState(''), [before,setBefore]=useState(true),[after,setAfter]=useState(true);
  const start=useRef(null), surface=useRef(null);
  const selectionKey = part === 'translation' ? 'translation_selection' : 'selection';
  const selection=doc[selectionKey], count=part === 'result' ? doc.mode === 'passthrough' ? doc.original.pages : doc.selection.length+(doc.translation ? doc.translation_selection.length : 0) : (doc[part]?.pages || 1);
  useEffect(()=>{setRange(selection.map(x=>x.page).join(', '));setRect(null);},[selection,part]);
  useEffect(()=>{ if(part === 'translation' && !doc.translation){setPart('original');setPage(1);} },[doc.translation,part]);
  useEffect(()=>{setPage(n=>Math.min(n,count));},[count]);
  useEffect(()=>{
    const controller=new AbortController(); let url, live=true;
    setLoading(true);setImageError('');setImageUrl('');
    fetch(`/api/projects/${p.id}/documents/${doc.id}/preview?part=${part}&page=${page}&v=${revision}`,{signal:controller.signal}).then(async r=>{if(!r.ok){let data=await r.json();throw new Error(data.detail || 'Предпросмотр недоступен');}return r.blob();}).then(blob=>{if(!live)return;url=URL.createObjectURL(blob);setImageUrl(url);setLoading(false);}).catch(e=>{if(live && e.name!=='AbortError'){setImageError(e instanceof TypeError ? 'Нет связи с приложением. Предпросмотр восстановится после запуска.' : e.message);setLoading(false);}});
    return()=>{live=false;controller.abort();if(url)URL.revokeObjectURL(url);};
  },[p.id,doc.id,part,page,revision]);
  function point(e){const b=surface.current.getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-b.left)/b.width)),Math.max(0,Math.min(1,(e.clientY-b.top)/b.height))];}
  function move(e){if(!start.current)return;const [x,y]=point(e),[sx,sy]=start.current;setRect([Math.min(x,sx),Math.min(y,sy),Math.max(x,sx),Math.max(y,sy)]);}
  const editable=part!=='result' && doc.mode==='prepare';
  return <section className="preview"><h2>Просмотр документа</h2><div className="preview-tabs">{[['original','Оригинал'],...(doc.translation ? [['translation','Перевод']] : []),['result','Результат']].map(([value,label])=><button key={value} className={part===value?'active':''} onClick={()=>{setPart(value);setPage(1);setDrawing(false);}}>{label}</button>)}</div>
    <div className="preview-toolbar"><button aria-label="Предыдущая страница" disabled={page<=1} onClick={()=>setPage(page-1)}>←</button><label>Стр. <input aria-label="Страница предпросмотра" type="number" min={1} max={count} value={page} onChange={e=>{const n=Number(e.target.value);if(n>=1&&n<=count)setPage(n);}}/> / {count}</label><button aria-label="Следующая страница" disabled={page>=count} onClick={()=>setPage(page+1)}>→</button><select aria-label="Масштаб предпросмотра" value={zoom} onChange={e=>setZoom(e.target.value)}><option value="fit">По ширине</option><option value="100">100%</option><option value="150">150%</option></select>{editable ? <button className={drawing?'active':''} disabled={disabled||loading} onClick={()=>{setDrawing(!drawing);setRect(null);}}>Выделить область</button> : <a className="button" href={`/api/projects/${p.id}/documents/${doc.id}/pdf`}>Скачать PDF</a>}</div>
    <div className="paper-scroll">{imageError ? <p role="alert" className="preview-error">{imageError}</p> : null}{loading ? <p className="muted inset">Готовим предпросмотр…</p> : null}{imageUrl ? <div ref={surface} className={'paper '+(drawing?'drawing':'')} style={zoom==='fit'?undefined:{width:(doc[part]?.sizes[page-1]?.[0] || 595)*Number(zoom)/100*96/72+'px'}} onPointerDown={e=>{if(!drawing)return;start.current=point(e);surface.current.setPointerCapture(e.pointerId);setRect(null);}} onPointerMove={move} onPointerUp={e=>{move(e);start.current=null;}} onPointerCancel={()=>{start.current=null;setRect(null);}}><img src={imageUrl} alt={`${part==='result'?'Результат':part==='translation'?'Перевод':'Оригинал'}, страница ${page}`} draggable="false"/>{rect ? <div className="crop-rect" style={{left:rect[0]*100+'%',top:rect[1]*100+'%',width:(rect[2]-rect[0])*100+'%',height:(rect[3]-rect[1])*100+'%'}}/> : null}</div> : null}</div>
    {editable ? <div className="fragments"><details open={drawing}><summary>Страницы и фрагменты · {selection.length} в результате</summary><p className="muted">Настройки {part==='translation'?'перевода':'оригинала'} независимы. Прямоугольник сохраняется как изображение 300 dpi без скрытого исходного текста.</p>
      <Field label="Целые страницы (заменяет текущий выбор)"><input aria-label="Диапазон страниц" value={range} onChange={e=>setRange(e.target.value)} placeholder="1, 3-5"/></Field><button disabled={disabled} onClick={()=>{try{onSave({[selectionKey]:parsePages(range,doc[part].pages)});}catch(e){onError(e.message);}}}>Применить страницы</button>
      {drawing ? <><p>Протяните прямоугольник по странице. Затем добавьте его к выбранным фрагментам.</p><div className="actions"><label><input type="checkbox" checked={before} onChange={e=>setBefore(e.target.checked)}/> [...] перед</label><label><input type="checkbox" checked={after} onChange={e=>setAfter(e.target.checked)}/> [...] после</label></div><button disabled={disabled||!rect||(rect[2]-rect[0])<.01||(rect[3]-rect[1])<.01} onClick={()=>onSave({[selectionKey]:[...selection,{page,rect,omission_before:before,omission_after:after}]})}>Добавить выделенную область</button></> : null}
      <ol className="selection-list">{selection.map((item,i)=><li key={i}><span>Стр. источника {item.page}{item.rect?' · область':' · целиком'}{item.omission_before||item.omission_after?' · [...]':''}</span><button aria-label={`Удалить фрагмент ${i+1}`} disabled={disabled||selection.length<=1} onClick={()=>onSave({[selectionKey]:selection.filter((_,n)=>n!==i)})}>×</button></li>)}</ol>
    </details>{doc[part]?.text_pages.some(x=>!x) ? <p className="pending">Есть страницы без доступного текста. OCR и автоматический перевод сканов пока не поддерживаются.</p> : null}</div> : <p className="muted inset">{part==='result'?'Порядок: перевод, затем оригинал. Номер здесь — страница сборки.':'Режим без обработки: сохранится весь исходный PDF.'}</p>}
  </section>;
}

function Links({p,busy,mutate,upload,focusRef,error,clearError}) {
  const [allSame,setAllSame]=useState(true),[para,setPara]=useState(''),[mention,setMention]=useState(''),[scope,setScope]=useState(null);
  const scoped=p.references.find(r=>r.key===scope);
  useEffect(()=>{if(focusRef)document.getElementById('ref-'+focusRef)?.scrollIntoView({block:'center'});},[focusRef]);
  return <section className="links-page"><div className="section-heading"><div><h1>Ссылки в существующих сносках</h1><p className="muted">{p.main?.name || 'Загрузите основной DOCX. Приложения могут быть уже оформлены.'}</p></div><div className="actions"><Upload accept=".docx" disabled={Boolean(busy)} onFiles={f=>upload(f,'main')}>{p.main?'Заменить DOCX':'Загрузить DOCX'}</Upload><button className="primary" disabled={Boolean(busy||!p.main)} onClick={()=>mutate('/scan',{},'Сопоставление обновлено. Проверьте все сноски.')}>Сопоставить ссылки</button></div></div>
    {p.scanned === false ? <p className="pending">Основной документ или реквизиты изменились. Повторите сопоставление.</p> : null}
    <label className="checkline"><input type="checkbox" checked={allSame} onChange={e=>setAllSame(e.target.checked)}/> Применять ручной выбор к одинаковым упоминаниям</label>
    <table className="links-table"><thead><tr><th>Сноска*</th><th>Текст гиперссылки</th><th>Соответствующий файл</th><th>Сопоставление</th></tr></thead><tbody>{p.references.map(r=><tr key={r.key} id={'ref-'+r.key} className={focusRef===r.key?'selected':''}><td>{r.footnote}</td><td><span>{r.link_text??r.mention}</span>{r.scope_review&&!r.keep_original?<small className="pending">Проверьте границу: уточнение страницы или пункта не распознано.</small>:null}<button disabled={Boolean(busy||!p.scanned||r.keep_original)} onClick={()=>{clearError();setScope(r.key);}}>Изменить границы</button></td><td><select aria-label={`Файл для ${r.mention} в сноске ${r.footnote}`} value={r.keep_original ? '__keep_original__' : r.target || ''} disabled={Boolean(busy||!p.scanned)} onChange={e=>mutate('/references',{key:r.key,target:e.target.value==='__keep_original__'?null:e.target.value||null,keep_original:e.target.value==='__keep_original__',all_same:allSame})}><option value="">{r.candidates.length>1?'Несколько кандидатов':'Не найден — выберите решение'}</option><option value="__keep_original__">Без файла — оставить как есть</option>{p.documents.map(d=><option key={d.id} value={d.id}>{d.identifier ? d.identifier+' · ' : ''}{d.title}</option>)}</select></td><td><span className={'status '+(r.target||r.keep_original?'ready':'problem')}>{r.keep_original?'Без файла · без изменений':!r.target?'Требует решения':r.manual?'Ручной выбор':'Точное совпадение'}</span></td></tr>)}</tbody></table>
    <p className="muted">*Порядок сносок в тексте. Нестандартная нумерация и перезапуск по разделам требуют проверки в Word.</p>
    <div className="links-bottom"><div><h2>Проверьте полный текст сносок</h2><p className="muted">Неизвестные названия нельзя надёжно обнаружить автоматически. Добавьте пропущенное упоминание ниже.</p>{p.footnotes.map(f=><p className="footnote" key={`${f.fid}:${f.paragraph}`}><b>{f.footnote}</b><span>{f.text}</span></p>)}
      {p.footnotes.length ? <form className="manual-reference" onSubmit={e=>{e.preventDefault();const f=p.footnotes[Number(para)];if(f)mutate('/references/add',{fid:f.fid,paragraph:f.paragraph,mention});}}><Field label="Сноска"><select required value={para} onChange={e=>setPara(e.target.value)}><option value="" disabled>Выберите сноску</option>{p.footnotes.map((f,i)=><option value={i} key={i}>{f.footnote} — {f.text.slice(0,55)}</option>)}</select></Field><Field label="Точный текст упоминания"><input required value={mention} onChange={e=>setMention(e.target.value)}/></Field><button disabled={Boolean(busy||!p.scanned)}>Добавить упоминание</button></form> : null}</div>
      <div><h2>Подтверждение</h2><p>Проверьте каждую сноску, включая упоминания без номера приложения. Для каждого упоминания выберите файл или «Без файла — оставить как есть». Например, для видео, на которое уже есть веб-ссылка в сноске.</p><button className="primary" disabled={Boolean(busy||!p.main||!p.scanned||p.references.some(r=>!r.target&&!r.keep_original)||p.links_reviewed)} onClick={()=>mutate('/references/confirm',{},'Сопоставление проверено')}>{p.links_reviewed?'Сопоставление подтверждено':'Все сноски проверены'}</button><p className="muted">Текст, состав и нумерация сносок не переписываются. Ссылки на выбранные файлы обновляются. При выборе «Без файла» исходный текст, оформление и существующие веб-ссылки сохраняются; новая ссылка не создаётся.</p>
      {p.name==='Учебная подача' ? <details><summary>Ненайденная ссылка в учебном комплекте</summary><p>RLA-99 отсутствует намеренно. Скачайте дополнительный учебный PDF и загрузите его в «Документы». Назначьте ему RLA и номер 99, проверьте подготовку и повторите сопоставление.</p><a href="/api/demo-missing" className="button">Скачать учебный RLA-99</a></details> : null}</div></div>
    {scoped?<ReferenceScope key={scoped.key} reference={scoped} text={p.footnotes.find(f=>f.fid===scoped.fid&&f.paragraph===scoped.paragraph)?.text??''} busy={busy} externalError={error} onClose={()=>setScope(null)} onSave={(start,end)=>mutate('/references/range',{key:scoped.key,start,end},'Границы сохранены. Проверьте сопоставление.')}/>:null}
  </section>;
}
function StyleForm({style,busy,onSave,onDirty}) {
  const [value,setValue]=useState(style);useEffect(()=>{setValue(style);onDirty(false);},[style]);
  function change(next) { setValue(next); onDirty(JSON.stringify(next) !== JSON.stringify(style)); }
  return <form onSubmit={e=>{e.preventDefault();onSave(value);}}><Field label="Шрифт"><select value={value.font} onChange={e=>change({...value,font:e.target.value})}><option value="DejaVu">DejaVu Sans (включая кириллицу)</option><option>Helvetica</option><option>Times-Roman</option></select></Field><div className="pair"><Field label="Размер, pt"><input type="number" min="6" max="24" required value={value.size} onChange={e=>change({...value,size:Number(e.target.value)})}/></Field><Field label="Отступ по горизонтали, pt"><input type="number" min="8" max="100" required value={value.margin} onChange={e=>change({...value,margin:Number(e.target.value)})}/></Field></div><Field label="Размещение штампов"><select value={value.stamp_mode??'band'} onChange={e=>change({...value,stamp_mode:e.target.value})}><option value="overlay">В полях исходной страницы</option><option value="band">Дополнительная полоса над страницей</option></select></Field><Field label="Отступ сверху, pt"><input type="number" min="8" max="150" required value={value.top??26} onChange={e=>change({...value,top:Number(e.target.value)})}/></Field><button disabled={Boolean(busy)}>Сохранить оформление</button><button type="button" disabled={Boolean(busy)} onClick={()=>{setValue(style);onDirty(false);}}>Отменить изменения</button></form>;
}

createRoot(document.getElementById('root')).render(<App/>);
