import React, {useEffect, useRef, useState} from 'react';
import TranslationSource from './TranslationSource';

const roleLabels={body:'Основной текст',footnotes:'Сноски',header:'Верхний колонтитул',footer:'Нижний колонтитул'};
const partLabel=x=>roleLabels[x?.role]||'Текст';

const labels = {running:'Перевод выполняется', complete:'Черновик готов к проверке', cancelled:'Перевод остановлен', error:'Перевод прерван', interrupted:'Перевод прерван при перезапуске', partial:'Заполнены не все части'};

export default function TranslationDialog({p, doc, api, onClose, onApplied}) {
  const base = `/projects/${p.id}/documents/${doc.id}/translation`;
  const [state,setState]=useState(null), [source,setSource]=useState(null), [texts,setTexts]=useState([]);
  const [choiceDirty,setChoiceDirty]=useState(false);
  const [sourceOptions,setSourceOptions]=useState({selection:Array.from({length:doc.original.pages},(_,i)=>({page:i+1})),top:60,bottom:60,placement:'preserve',notes:[]});
  const sourceChanged=!source||JSON.stringify(sourceOptions)!==JSON.stringify(source.options);
  const [provider,setProvider]=useState('claude'), [target,setTarget]=useState('English'), [connection,setConnection]=useState(null);
  const [part,setPart]=useState(0), [checked,setChecked]=useState(false), [replace,setReplace]=useState(false), [confirmed,setConfirmed]=useState(false);
  const [loading,setLoading]=useState(true), [busy,setBusy]=useState(''), [error,setError]=useState(''), [sourceError,setSourceError]=useState(''), [notice,setNotice]=useState('');
  const [view,setView]=useState('text'), [pdfPage,setPdfPage]=useState(1), [pdfCount,setPdfCount]=useState(1), [pdfUrl,setPdfUrl]=useState(''), [pdfError,setPdfError]=useState('');
  const dialog=useRef(null), generation=useRef(0), sourceResult=useRef(null);
  const [previewRequest,setPreviewRequest]=useState(0);
  const running=state?.status==='running';
  const dirty=Boolean(state && JSON.stringify(texts)!==JSON.stringify(state.parts.map(x=>x.translation)));
  const parts=state?.parts || source?.parts || [];
  const all=Boolean(state && texts.length && texts.every(x=>x.trim()));
  function receive(s) {setState(s);setTexts(s?.parts.map(x=>x.translation)||[]);setConfirmed(false);}
  useEffect(()=>{const el=dialog.current;el?.focus();},[]);
  useEffect(()=>{if(!previewRequest)return;const el=sourceResult.current;if(el){el.focus({preventScroll:true});el.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});}},[previewRequest]);
  useEffect(()=>{
    let live=true;
    api(base).then(async s=>{
      if(!live)return;receive(s);if(s){setProvider(s.provider);setTarget(s.target);}
      const options=s?.source_options;
      if(options)setSourceOptions(options);
      const src=await api(base+'/source-preview',{options}).catch(e=>{if(live)setSourceError(e.message);return null;});
      if(!live)return;setSource(src);if(src)setSourceOptions(src.options);
    }).catch(e=>{if(live)setError(e.message);}).finally(()=>{if(live)setLoading(false);});
    return()=>{live=false;};
  },[base,api]);
  useEffect(()=>{
    let live=true; const id=++generation.current;setConnection(null);
    api('/translation/providers/'+provider).then(s=>{if(live&&id===generation.current)setConnection(s);}).catch(e=>{if(live)setConnection({ready:false,message:e.message});});
    return()=>{live=false;};
  },[provider,api]);
  useEffect(()=>{
    if(!running)return;
    let live=true,timer;
    async function poll(){try{const s=await api(base);if(live){receive(s);setError('');}}catch(e){if(live)setError(e.message);}if(live)timer=setTimeout(poll,1500);}
    timer=setTimeout(poll,1000);return()=>{live=false;clearTimeout(timer);};
  },[running,base,api]);
  useEffect(()=>{
    if(!dirty&&!choiceDirty)return;
    const warn=e=>{e.preventDefault();e.returnValue='';};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);
  },[dirty,choiceDirty]);
  useEffect(()=>{
    if(view!=='pdf'||!state||dirty)return;
    const controller=new AbortController();let url,live=true;setPdfUrl('');setPdfError('');
    fetch('/api'+base+`/preview?revision=${state.revision}&page=${pdfPage}`,{signal:controller.signal}).then(async r=>{if(!r.ok)throw new Error((await r.json()).detail);const count=Number(r.headers.get('X-Page-Count'));const blob=await r.blob();if(live){url=URL.createObjectURL(blob);setPdfUrl(url);setPdfCount(count);}}).catch(e=>{if(live&&e.name!=='AbortError')setPdfError(e.message);});
    return()=>{live=false;controller.abort();if(url)URL.revokeObjectURL(url);};
  },[view,base,state?.revision,pdfPage,dirty]);
  async function action(label,fn){if(busy)return;setBusy(label);setError('');setNotice('');try{await fn();}catch(e){setError(e.message);}finally{setBusy('');}}
  function close(){if(!busy&&(!(dirty||choiceDirty)||window.confirm('Закрыть без сохранения правок или выбора для перевода?')))onClose();}
  function keydown(e){if(e.key==='Escape'){e.preventDefault();close();}if(e.key==='Tab'){const focus=[...dialog.current.querySelectorAll('button,input,select,textarea,a[href]')].filter(x=>!x.disabled&&x.offsetParent!==null);const first=focus[0],last=focus.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}}
  async function start(resume){await action('Запускаем перевод…',async()=>{const s=await api(base+'/start',{provider,target,options:source.options,token:source?.token,reviewed_source:checked,revision:state?.revision,resume,replace_draft:replace});receive(s);setView('text');setPart(0);setReplace(false);});}
  const disabled=Boolean(loading||busy||running);
  const startDisabled=disabled||dirty||!source||sourceChanged||!checked||!connection?.ready;
  return <div className="modal-backdrop"><section className="translation-dialog" role="dialog" aria-modal="true" aria-labelledby="translation-title" ref={dialog} tabIndex={-1} onKeyDown={keydown}>
    <div className="translation-heading"><div><h1 id="translation-title">Перевод документа</h1><p>{doc.identifier ? doc.identifier+' · ':''}{doc.title}</p></div><button disabled={Boolean(busy)} onClick={close}>Закрыть</button></div>
    {loading ? <p>Открываем черновик и извлекаем текст…</p> : <>
      <div className="translation-setup"><label className="field"><span>Переводчик</span><select value={provider} disabled={disabled} onChange={e=>{setProvider(e.target.value);setChecked(false);}}><option value="claude">Claude</option><option value="codex">Codex</option></select></label><label className="field"><span>Язык перевода</span><select value={target} disabled={disabled} onChange={e=>{setTarget(e.target.value);setChecked(false);}}>{['English','Русский','Français','Deutsch','Español'].map(x=><option key={x}>{x}</option>)}</select></label><div className="translation-connection"><b>{connection ? connection.ready?'Вход найден':'Нужно подключение':'Проверяем официальный CLI…'}</b><small>{connection?.version} {connection?.message}</small><button disabled={disabled} onClick={()=>action('Проверяем вход…',async()=>setConnection(await api('/translation/providers/'+provider)))}>Проверить подключение</button></div></div>
      <details className="translation-help"><summary>Как подключить личную подписку</summary><p>Установите официальный <a href="https://code.claude.com/docs/en/quickstart" target="_blank" rel="noreferrer">Claude Code</a> или <a href="https://developers.openai.com/codex/cli" target="_blank" rel="noreferrer">Codex CLI</a>. В Терминале Windows или macOS выполните <code>{provider==='claude'?'claude auth login':'codex login'}</code> и завершите вход на странице провайдера. Затем нажмите «Проверить подключение».</p><p>Выбирайте вход через подписку. Ключи API здесь не используются. Приложение не получает пароль и токены. Наличие входа ещё не подтверждает доступ к переводу; он зависит от подписки и её лимитов.</p></details>
      <TranslationSource p={p} doc={doc} options={sourceOptions} compact={Boolean(state)} onChange={next=>{setSourceOptions(next);setChoiceDirty(true);setChecked(false);setSourceError('');}} disabled={disabled||dirty} source={source} changed={sourceChanged} onPreview={()=>action('Извлекаем выбранный текст…',async()=>{setSource(null);setChecked(false);setSourceError('');try{const src=await api(base+'/source-preview',{options:sourceOptions});setSource(src);setSourceOptions(src.options);setChoiceDirty(false);setPart(0);}catch(e){setSourceError(e.message);}finally{setPreviewRequest(n=>n+1);}})}/>
      {sourceError ? <p ref={sourceResult} tabIndex={-1} role="alert" className="pending source-result">{sourceError}</p> : <p className="muted">{sourceOptions.placement==='preserve'?'Выбранный текст переводится по отдельным зонам: основной текст, сноски и колонтитулы. В PDF они остаются на соответствующей странице.':'Отправляются выбранные страницы и области, без серых исключённых зон.'} Изображения и рукописный текст не распознаются. До 120 000 знаков на запрос перевода.</p>}
      {state?.stale ? <p role="alert" className="pending">Оригинал заменён. Показан черновик старого файла; его применение заблокировано. Для нового файла начните заново.</p> : null}
      {source && !sourceChanged ? <section ref={sourceResult} tabIndex={-1} className="source-disclosure source-result" aria-label="Текст для отправки"><h2>Текст для отправки готов · частей: {source.parts.length}</h2><p className="muted">Проверьте текст ниже. Он будет отправлен только после вашего согласия и нажатия «Начать перевод».</p>{source.parts.map((x,i)=><section key={i}><b>Страница {x.page} · {partLabel(x)} · часть {i+1}</b><pre>{x.source}</pre></section>)}</section> : null}
      <label className="checkline"><input type="checkbox" checked={checked} disabled={disabled||sourceChanged} onChange={e=>setChecked(e.target.checked)}/>Я проверил(а) извлечённый текст и разрешаю отправить его {provider==='claude'?'Claude':'Codex'} через мою подписку.</label>
      {state ? <label className="checkline"><input type="checkbox" checked={replace} disabled={disabled||dirty} onChange={e=>setReplace(e.target.checked)}/>Заменить текущий черновик новым переводом. Прикреплённый PDF останется до отдельного подтверждения.</label> : null}
      <div className="actions"><button className="primary" disabled={startDisabled||Boolean(state&&!replace)} onClick={()=>start(false)}>{state?'Перевести заново':'Начать перевод'}</button>{state && !all ? <button disabled={startDisabled||state.stale||state.provider!==provider||state.target!==target} onClick={()=>start(true)}>Продолжить незаполненные части</button> : null}{running ? <button disabled={Boolean(busy)} onClick={()=>action('Останавливаем…',async()=>receive(await api(base+'/cancel',{})))}>Остановить перевод</button> : null}</div>
      {state ? <div className="translation-progress" role="status"><b>{labels[state.status]}</b><span>{state.parts.filter(x=>x.translation.trim()).length} / {state.parts.length} частей сохранено</span>{running ? <p>Можно закрыть это окно: перевод продолжится, пока работает локальное приложение.</p> : null}{state.error ? <p className="pending">{state.error}</p> : null}</div> : null}
      {parts.length ? <><div className="translation-editor-toolbar"><label>Часть <select aria-label="Часть перевода" value={part} onChange={e=>setPart(Number(e.target.value))}>{parts.map((x,i)=><option key={i} value={i}>{i+1} · стр. {x.page} · {partLabel(x)}</option>)}</select></label><div className="actions"><button className={view==='text'?'active':''} onClick={()=>setView('text')}>Текст и оригинал</button><button disabled={!all||dirty||running||state?.stale} className={view==='pdf'?'active':''} onClick={()=>{setPdfPage(1);setView('pdf');}}>Проверить PDF перевода</button></div></div>
        {view==='text' ? <div className="translation-columns"><section><h2>Оригинал · страница {parts[part]?.page}</h2>{!state?.stale ? <img alt={`Оригинал для перевода, страница ${parts[part]?.page}`} src={`/api/projects/${p.id}/documents/${doc.id}/preview?part=original&page=${parts[part]?.page}&v=${doc.original.sha256}`}/> : <pre>{parts[part]?.source}</pre>}</section><section><h2>{partLabel(parts[part])} · {state?.target||target}</h2><textarea aria-label="Текст перевода" placeholder="Здесь появится перевод этой части" disabled={!state||running||Boolean(busy)||state.stale} value={texts[part]||''} onChange={e=>{const next=[...texts];next[part]=e.target.value;setTexts(next);setConfirmed(false);}}/><p className="muted">Проверьте имена, суммы, даты, ссылки, нумерацию и полноту. Сохраните номера сносок и их упоминания в основном тексте. Переносы строк сохранятся в PDF.</p></section></div> : <div className="translation-pdf"><div className="actions"><button disabled={pdfPage<=1} onClick={()=>setPdfPage(n=>n-1)}>Предыдущая страница PDF</button><span>{pdfPage} / {pdfCount}</span><button disabled={pdfPage>=pdfCount} onClick={()=>setPdfPage(n=>n+1)}>Следующая страница PDF</button><a href={'/api'+base+`/pdf?revision=${state.revision}`} className="button">Скачать PDF перевода</a></div>{pdfError ? <p role="alert">{pdfError}</p> : pdfUrl ? <img src={pdfUrl} alt={`PDF перевода, страница ${pdfPage}`}/> : <p>Готовим PDF…</p>}</div>}
      </> : null}
      {state ? <div className="translation-save"><div className="actions"><button disabled={disabled||!dirty||state.stale} onClick={()=>action('Сохраняем правки…',async()=>{receive(await api(base+'/save',{revision:state.revision,texts}));setNotice('Правки сохранены. Прикреплённый PDF обновится только после подтверждения.');})}>Сохранить правки</button><span>{dirty?'Есть несохранённые правки':'Черновик сохранён на компьютере'}</span></div><label className="checkline"><input type="checkbox" checked={confirmed} disabled={disabled||dirty||!all||state.stale} onChange={e=>setConfirmed(e.target.checked)}/>Я сверил(а) перевод с оригиналом и проверил(а) его PDF.</label><button className="primary" disabled={disabled||dirty||!all||!confirmed||state.stale} onClick={()=>action('Прикрепляем проверенный перевод…',async()=>{onApplied(await api(base+'/apply',{revision:state.revision,confirmed}));})}>{doc.translation?'Заменить прикреплённый PDF проверенным переводом':'Прикрепить проверенный перевод'}</button><p className="muted">После прикрепления можно независимо выбрать страницы перевода и оригинала, затем проверить результат приложения.</p></div> : null}
    </>}
    <div className={'translation-message '+(error?'error':'')} role={error?'alert':'status'}>{busy||error||notice}</div>
  </section></div>;
}
