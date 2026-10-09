import React, {useEffect, useRef, useState} from 'react';

const labels={available:'Доступен',missing:'Не установлен',needs_login:'Нужен вход',error:'Проверьте настройку',unchecked:'Не проверен',not_configured:'Не подключена',stopped:'Остановлено',running:'HTTPS запущен',trusted:'HTTPS проверен',needs_trust:'Проверьте доверие'};

export function WordSetup({api}) {
  const [status,setStatus]=useState(null),[busy,setBusy]=useState(''),[error,setError]=useState(''),[port,setPort]=useState('');
  const [downloadState,setDownloadState]=useState(null);
  const [refreshError,setRefreshError]=useState('');
  const [confirmation,setConfirmation]=useState('');
  const refreshVersion=useRef(0);
  const actionPending=useRef(false);
  useEffect(()=>{
    let live=true,timer,delay=1000;
    async function refresh() {
      const version=refreshVersion.current;
      try {if(actionPending.current)return;const s=await api('/setup');if(live&&version===refreshVersion.current){setStatus(s.components.word);setRefreshError('');delay=s.components.word.installation?.busy?1000:5000;}}
      catch(e){if(live&&version===refreshVersion.current){setRefreshError(e.message);setStatus(null);}}
      finally {if(live)timer=setTimeout(refresh,delay);}
    }
    refresh();return()=>{live=false;clearTimeout(timer);};
  },[]);
  async function action(path, body={}) {
    refreshVersion.current++;
    actionPending.current=true;
    setConfirmation('');setBusy(path==='/setup/check/word'?'Проверяем HTTPS…':'Выполняем действие…');setError('');
    try {const value=await api(path,body);setStatus(value);if(path==='/setup/check/word')window.alert(value.message);}
    catch(e){setError(e.message);if(path==='/setup/check/word')window.alert(e.message);} finally{setBusy('');actionPending.current=false;refreshVersion.current++;}
  }
  async function download(path,filename) {
    setBusy('Загружаем '+filename+'…');setError('');setDownloadState({path,state:'loading'});
    try {
      const blob=await api(path,undefined,{blob:true});
      const url=URL.createObjectURL(blob),link=document.createElement('a');
      link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();
      setTimeout(()=>URL.revokeObjectURL(url),60000);
      setDownloadState({path,state:'done',message:`Файл ${filename} передан браузеру. Найдите его в загрузках.`});
    } catch(e){setDownloadState({path,state:'error',message:'Не удалось скачать файл. '+e.message+' Повторите скачивание.'});}
    finally{setBusy('');}
  }
  function downloadControl(path,filename,label) {
    const current=downloadState?.path===path?downloadState:null;
    return <><p><button disabled={!!busy||!!status?.installation?.busy} aria-busy={current?.state==='loading'} onClick={()=>download(path,filename)}>{current?.state==='loading'?'Скачиваем…':label}</button></p>
      {current?.message&&<p role={current.state==='error'?'alert':'status'} className={current.state==='error'?'error-note':'muted'}>{current.message}</p>}</>;
  }
  const installation=status?.installation;
  const blocked=!!busy||!!installation?.busy;
  const installationMessage=installation?.phase==='waiting_word'&&status?.panel?.state==='connected'?'Настройка выполнена. Панель подключена и отвечает в Word.':installation?.message;
  return <section className="setup-card"><h2>Панель Word · по желанию</h2>
    <p>Создавайте сноски прямо в Word. Обычная загрузка DOCX/PDF и сборка комплекта доступны без панели.</p>
    <p className="muted">Экспериментальное локальное подключение для пилота.</p>
    {error&&<p role="alert" className="error-note">{error}</p>}
    {refreshError&&<p role="alert" className="error-note">Связь с приложением потеряна. {refreshError} Проверяем повторно…</p>}
    <p role={installation?.phase==='error'?'alert':'status'}>{busy||installationMessage||'Проверяем настройки…'}</p>
    {status?.origin&&<p><b>Защищённое соединение:</b> {labels[status.state]||status.message}.</p>}
    {installation?.registration_message&&installation.registration_message!==installation.message&&<p>{installation.registration_message}</p>}
    {status?.panel&&<p role="status"><b>Панель в Word: {({connected:'подключена',unsupported:'версия не поддерживает нужные функции',disconnected:'связь потеряна',waiting:'ожидает открытия',unavailable:'соединение не запущено',setup_required:'настройка не завершена'})[status.panel.state]}.</b> {status.panel.message}</p>}
    {!confirmation&&status&&<div className="actions">
      {installation?.installed?<button className="primary" disabled={blocked} onClick={()=>action('/setup/word/open')}>Открыть новый учебный документ в Word</button>:<button className="primary" disabled={blocked||installation?.registration_blocked} onClick={()=>setConfirmation({token:installation?.replacement_token})}>{installation?.replacement_token?'Обновить подключение':'Подключить панель'}</button>}
      {installation?.busy&&installation.phase!=='removing'&&<button disabled={!!busy||installation.phase==='cancelling'} onClick={()=>action('/setup/word/cancel')}>Отменить настройку</button>}
    </div>}
    {confirmation&&confirmation!=='remove'&&<div className="setup-consent">
      {confirmation.token&&<p>Word сейчас подключён к другой копии BL Exhibit Manager. Обновление переключит панель на это приложение. Прежняя регистрация будет сохранена и восстановлена при отмене незавершённой настройки, ошибке установки или удалении нового подключения. Прежние файлы и сертификаты останутся без изменений.</p>}
      <p>Приложение добавит доверие к своему сертификату для защищённого соединения только с этим компьютером, зарегистрирует панель в Word и откроет новый учебный документ. Действующие настройки сохраняются. Система может попросить подтверждение или пароль.</p>
      <p>Рабочие документы остаются без изменений. Можно отказаться и продолжить без панели.</p>
      <button className="primary" disabled={blocked} onClick={()=>action('/setup/word/install',{consent:true,...(confirmation.token?{replacement_token:confirmation.token}:{})})}>{confirmation.token?'Разрешить и обновить':'Разрешить и подключить'}</button> <button disabled={blocked} onClick={()=>setConfirmation('')}>Отмена</button>
    </div>}
    {confirmation==='remove'&&<div className="setup-consent"><p>Удалить созданную приложением регистрацию панели и установленное им доверие? Если обновлялось прежнее подключение, его регистрация будет восстановлена. Документы, проекты и прежние настройки сохранятся. Word останется открытым.</p><button disabled={blocked} onClick={()=>action('/setup/word/remove',{consent:true})}>Удалить подключение</button> <button disabled={blocked} onClick={()=>setConfirmation('')}>Отмена</button></div>}
    {installation?.installed&&installation?.phase!=='word_restart_required'&&status?.panel?.state!=='connected'&&<p className="muted">Если панель не появилась, сохраните открытые документы и закройте все окна Word. Затем нажмите «Открыть новый учебный документ в Word»; приложение запустит Word заново.</p>}
    <details><summary>Диагностика и дополнительные действия</summary>
      {status?.message&&<p>{status.message}</p>}
      {status?.fingerprint&&<><p>Сертификат BL Exhibit Manager localhost, действует до {status.expires}.</p><p className="fingerprint">SHA-256: {status.fingerprint}</p></>}
      {installation?.installed&&<p><button disabled={blocked} onClick={()=>setConfirmation({})}>Повторить настройку</button></p>}
      {installation?.can_remove&&<p><button disabled={blocked} onClick={()=>setConfirmation('remove')}>Удалить созданное подключение</button></p>}
      {status?.origin&&<><p><button disabled={blocked} onClick={()=>action('/setup/check/word')}>Проверить HTTPS</button></p><p><a href={status.origin+'/word/index.html'} target="_blank" rel="noopener">Открыть адрес панели в браузере</a></p><p className="muted">Страница в браузере проверяет доступность адреса; сноски создаются внутри Word.</p></>}
      <details><summary>Технические файлы для диагностики</summary>
        <p>Эти действия не нужны для обычного подключения.</p>
        {!status?.origin&&<><label className="field">HTTPS-порт (необязательно)<input type="number" min="1024" max="65535" value={port} onChange={e=>setPort(e.target.value)}/></label><button disabled={blocked} onClick={()=>action('/setup/word',port?{port:Number(port)}:{})}>Подготовить технические файлы</button></>}
        {status?.origin&&<>{status.generated&&downloadControl('/setup/word/certificate','BL-Exhibit-localhost.crt','Скачать сертификат')}{downloadControl('/word/manifest','BLExhibitManager.Word.xml','Скачать манифест Word')}</>}
      </details>
      <a className="button" href="/api/word/instructions" target="_blank" rel="noopener">Инструкция по подключению</a>
    </details>
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
    catch(e){setData(d=>({...d,components:{...d.components,[name]:{state:'error',message:e.message}}}));}finally{setPending(p=>({...p,[name]:false}));}
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
        {!pending[name]&&data.components[name].state==='available'?<p className="muted">{name==='converter'?'Можно подготовить основной PDF в разделе «Проверка и экспорт».':'Для перевода выберите документ и нажмите «Открыть переводчик». Доступ к модели проверяется при запуске перевода с вашего согласия.'}</p>:name==='converter'?<a href="https://www.libreoffice.org/download/download-libreoffice/" target="_blank" rel="noopener">Скачать LibreOffice</a>:!pending[name]&&<p>{data.components[name].state!=='needs_login'&&<><a href={name==='claude'?'https://code.claude.com/docs/en/quickstart':'https://developers.openai.com/codex/cli'} target="_blank" rel="noopener">Установить официальный CLI</a>. </>}Войдите командой <code>{name==='claude'?'claude auth login':'codex login'}</code>.</p>}
        <p><button disabled={pending[name]} aria-busy={!!pending[name]} onClick={()=>check(name)}>{pending[name]?'Проверяем…':name==='converter'?'Повторить проверку':`Проверить ${name==='claude'?'Claude':'Codex'}`}</button></p>{pending[name]&&name!=='converter'&&<p className="muted">Проверка может занять до 50 секунд.</p>}
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
