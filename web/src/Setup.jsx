import React, {useEffect, useRef, useState} from 'react';

const labels={available:'Доступен',missing:'Не установлен',needs_login:'Нужен вход',error:'Проверьте настройку',unchecked:'Не проверен',not_configured:'Не подключена',stopped:'Остановлено',running:'HTTPS запущен',trusted:'HTTPS проверен',needs_trust:'Проверьте доверие'};

export function WordSetup({api}) {
  const [status,setStatus]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[port,setPort]=useState('8769');
  useEffect(()=>{let live=true;api('/setup').then(s=>{if(live)setStatus(s.components.word);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[]);
  async function action(path, body={}) {
    setBusy(true);setError('');
    try {setStatus(await api(path,body));} catch(e){setError(e.message);} finally{setBusy(false);}
  }
  return <section className="setup-card"><h2>Панель Word · по желанию</h2><p>Для создания сносок прямо в Microsoft Word. Загрузка DOCX, подготовка PDF и сборка комплекта работают без панели.</p>
    {error&&<p role="alert" className="error-note">{error}</p>}
    <p role="status">{busy?'Подготавливаем подключение…':status?.message||'Проверяем настройки…'}</p>
    {status?.state==='not_configured'&&<><p>Приложение создаст сертификат только для этого компьютера. Доверие к нему вы подтвердите отдельно в настройках системы.</p><label className="field">HTTPS-порт<input type="number" min="1024" max="65535" value={port} onChange={e=>setPort(e.target.value)}/></label><button className="primary" disabled={busy||!port} onClick={()=>action('/setup/word',{port:Number(port)})}>Подготовить подключение Word</button></>}
    {status?.origin&&<>
      <ol className="setup-steps"><li><b>Подключение на этом компьютере.</b> <a href={status.origin+'/word/index.html'} target="_blank" rel="noopener">Открыть адрес панели</a>. Не обходите предупреждение о сертификате; сначала выполните следующий шаг.
        {status.state==='stopped'&&<p><button disabled={busy} onClick={()=>action('/setup/word')}>Запустить подключение</button></p>}</li>
        <li><b>Доверие сертификату.</b> {status.generated?<><a className="button" href="/api/setup/word/certificate">Скачать сертификат</a><p>Откройте скачанный файл и выполните шаги для своей системы в инструкции ниже. Установка меняет доверие на вашем компьютере и может потребовать пароль. Можно отказаться и продолжить без панели.</p><details><summary>Сверить сертификат перед установкой</summary><p>Имя: BL Exhibit Manager localhost. Действует до {status.expires}.</p><p className="fingerprint">SHA-256: {status.fingerprint}</p></details></>:<p>Используется прежний сертификат. Если доверие уже настроено, переустановка не нужна. Иначе восстановите доверие по инструкции поставщика этого сертификата.</p>}</li>
        <li><b>Проверка соединения.</b><p><button disabled={busy} onClick={()=>action('/setup/check/word')}>Проверить HTTPS</button></p><p>Проверка соединения приложения не заменяет открытие панели в Word. На Mac доверие также проверьте, открыв адрес панели в Safari.</p></li>
        <li><b>Подключение в Word.</b> <a className="button" href="/api/word/manifest">Скачать манифест Word</a><p>Это локальное подключение, без магазина надстроек. Установите манифест по инструкции для Windows или Mac. Политика вашей организации может ограничивать установку.</p></li>
      </ol>
    </>}
    <a className="button" href="/api/word/instructions" target="_blank" rel="noopener">Инструкция: доверие и подключение в Word</a>
  </section>;
}

export default function Setup({api,onClose}) {
  const [data,setData]=useState(null),[pending,setPending]=useState({}),[error,setError]=useState(''),[report,setReport]=useState(''),[copied,setCopied]=useState(false);
  const dialog=useRef(null);
  useEffect(()=>{const previous=document.activeElement;dialog.current?.showModal();return()=>{previous?.focus();};},[]);
  useEffect(()=>{let live=true;api('/setup').then(d=>{if(live)setData(d);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[]);
  async function check(name) {
    setPending(p=>({...p,[name]:true}));setError('');setReport('');
    try {const value=await api('/setup/check/'+name,{});setData(d=>({...d,components:{...d.components,[name]:value}}));}
    catch(e){setError(e.message);}finally{setPending(p=>({...p,[name]:false}));}
  }
  async function showReport(){
    setError('');setCopied(false);
    try {const fresh=await api('/setup');const body={...data.components,word:fresh.components.word};const r=await api('/setup/report',body);setReport(JSON.stringify(r,null,2));}
    catch(e){setError(e.message);}
  }
  return <dialog ref={dialog} className="setup-dialog" aria-labelledby="setup-title" onCancel={e=>{e.preventDefault();onClose();}}>
    <div className="setup-heading"><h1 id="setup-title">Помощь и настройка</h1><button autoFocus onClick={onClose}>Вернуться к работе</button></div>
    <p>Подключайте только то, что нужно для вашей задачи. Все проекты доступны без обязательного мастера настройки.</p>
    {error&&<p role="alert" className="error-note">{error}</p>}
    {!data?<p role="status">Проверяем компоненты…</p>:<>
      <p className="muted">Версия {data.report.version} · {data.report.platform} · {data.report.architecture}</p>
      <div className="setup-grid">{['converter','claude','codex'].map(name=><section className="setup-card" key={name}>
        <h2>{name==='converter'?'Основной PDF':name==='claude'?'Перевод через Claude':'Перевод через Codex'}</h2>
        <p role="status"><b>{pending[name]?'Проверяем…':labels[data.components[name].state]}</b></p>
        <p>{data.components[name].message||(name==='converter'?'Нужен при преобразовании DOCX в PDF.':'Для перевода через личную подписку нужен официальный CLI и вход в аккаунт. Проверка не отправляет текст и не обращается к модели.')}</p>
        {name==='converter'?<a href="https://www.libreoffice.org/download/download-libreoffice/" target="_blank" rel="noopener">Скачать LibreOffice</a>:<p><a href={name==='claude'?'https://code.claude.com/docs/en/quickstart':'https://developers.openai.com/codex/cli'} target="_blank" rel="noopener">Установить официальный CLI</a>. Затем войдите командой <code>{name==='claude'?'claude auth login':'codex login'}</code>.</p>}
        <p><button disabled={pending[name]} onClick={()=>check(name)}>{name==='converter'?'Повторить проверку':`Проверить ${name==='claude'?'Claude':'Codex'}`}</button></p>{pending[name]&&name!=='converter'&&<p className="muted">Проверка может занять до 50 секунд.</p>}
      </section>)}</div>
      <WordSetup api={api}/>
      <section className="setup-card"><h2>Сообщить об ошибке</h2><p>Опишите действие, ожидаемый результат и что произошло. Добавьте сводку ниже. Не прикладывайте рабочие документы: используйте вымышленный пример.</p>
        <button disabled={Object.values(pending).some(Boolean)} onClick={showReport}>Показать сводку диагностики</button>
        {report&&<><label className="field">Сводка для копирования<textarea readOnly rows="12" value={report}/></label><button onClick={async()=>{try{await navigator.clipboard.writeText(report);setCopied(true);}catch{setError('Выделите текст сводки и скопируйте вручную.');}}}>Копировать сводку</button>{copied&&<p role="status">Скопировано</p>}</>}
        <p className="muted">Только версия, система и состояния компонентов. Документы, имена проектов, пути и данные входа не включаются. Ничего не отправляется автоматически.</p>
        <a href="https://github.com/Ivan-Shivarshinov/bl-exhibit-manager/issues/new/choose" target="_blank" rel="noopener">Открыть обращение на GitHub</a>
      </section>
    </>}
  </dialog>;
}
