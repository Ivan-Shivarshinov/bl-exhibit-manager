import React, {useEffect, useState} from 'react';

const active = job => job && ['queued', 'running'].includes(job.state);
const languageNames = {eng: 'Английский', rus: 'Русский'};

function pagesFromRange(value) {
  const pages = new Set();
  for (const item of value.split(',')) {
    const match = /^(\d+)(?:\s*-\s*(\d+))?$/.exec(item.trim());
    if (!match) throw new Error('Укажите страницы, например 1, 3-5.');
    const start = Number(match[1]);
    const end = Number(match[2] || match[1]);
    if (start < 1 || end < start || end > 1000 || end - start >= 100) throw new Error('Допустимо до 100 страниц из одного PDF.');
    for (let page = start; page <= end; page++) pages.add(page);
    if (pages.size > 100) throw new Error('Допустимо до 100 страниц за одну операцию.');
  }
  return [...pages];
}

export default function TextJobs({project, api, engine, disabled, onFinished}) {
  const [docId, setDocId] = useState(project.documents[0]?.id || '');
  const [part, setPart] = useState('original');
  const [range, setRange] = useState('');
  const [language, setLanguage] = useState('');
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const path = `/projects/${project.id}/text-job`;
  const doc = project.documents.find(item => item.id === docId) || project.documents[0];
  const role = doc?.[part] ? part : 'original';
  const selectedLanguage = engine?.languages.includes(language) ? language : engine?.languages[0] || '';
  const running = active(job);
  const blocked = disabled || busy || running;

  useEffect(() => {
    let live = true;
    api(path).then(data => {if (live) setJob(data);}).catch(error => {if (live) setError(error.message);});
    return () => {live = false;};
  }, [api, path]);

  useEffect(() => {
    if (!running) return undefined;
    let live = true;
    let timer;
    async function poll() {
      try {
        const data = await api(`/material-operations/${job.id}`);
        if (!live) return;
        setJob(data);
        if (!active(data)) {
          setStopping(false);
          await onFinished(data);
          return;
        }
      } catch (error) {
        if (live) setError(error.message);
      }
      if (live) timer = setTimeout(poll, 450);
    }
    timer = setTimeout(poll, 250);
    return () => {live = false;clearTimeout(timer);};
  }, [api, job?.id, onFinished, running]);

  async function launch(parameters) {
    setError('');
    setBusy(true);
    try {
      const handle = await api(path, parameters);
      setJob({id: handle.id, state: 'queued', message: 'Начинаем…', total: 0, completed: 0});
    } catch (error) {
      setError(error.message);
    } finally {
      setBusy(false);
    }
  }

  function recognize(event) {
    event.preventDefault();
    if (blocked || !doc || !selectedLanguage) return;
    try {
      const pages = pagesFromRange(range);
      launch({action:'ocr_pages', did:doc.id, part:role, source:doc[role].sha256, pages, language:selectedLanguage});
    } catch (error) {
      setError(error.message);
    }
  }

  async function stop() {
    setStopping(true);
    try {
      await api(`/material-operations/${job.id}/cancel`, {});
    } catch (error) {
      setStopping(false);
      setError(error.message);
    }
  }

  const retry = job?.arguments && (!running && (['error','interrupted','cancelled'].includes(job.state) || job.result?.failed?.length));
  return <details>
    <summary>Распознать несколько страниц или обновить индекс</summary>
    <form className="material-text-search actions" onSubmit={recognize}>
      <label>Документ<select aria-label="Документ очереди OCR" value={doc?.id || ''} disabled={blocked} onChange={event => {setDocId(event.target.value);setPart('original');setRange('');}}>{project.documents.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
      {doc?.translation && <label>Часть PDF<select value={role} disabled={blocked} onChange={event => {setPart(event.target.value);setRange('');}}><option value="original">Оригинал</option><option value="translation">Перевод</option></select></label>}
      <label>Страницы<input value={range} placeholder="1, 3-5" required maxLength={320} disabled={blocked} onChange={event => setRange(event.target.value)}/></label>
      <label>Язык OCR<select aria-label="Язык очереди OCR" value={selectedLanguage} disabled={blocked} onChange={event => setLanguage(event.target.value)}>{!selectedLanguage && <option value="">Движок недоступен</option>}{engine?.languages.map(code => <option key={code} value={code}>{languageNames[code]}</option>)}</select></label>
      <button disabled={blocked || !doc || !selectedLanguage}>Распознать выбранные страницы</button>
    </form>
    <button type="button" disabled={blocked || !project.documents.length} onClick={() => launch({action:'index_pages'})}>Индексировать исходные PDF</button>
    {error && <p role="alert" className="error-note">{error}</p>}
    {job && <section aria-label="Ход обработки текста">
      <p role="status">{job.message}{job.total ? ` · ${job.completed} из ${job.total}` : ''}</p>
      {running && <><progress aria-label="Ход обработки текста" value={job.completed} max={Math.max(1, job.total)}/><button disabled={stopping} onClick={stop}>{stopping ? 'Останавливаем…' : 'Остановить обработку'}</button></>}
      {job.result?.reused != null && <p>Ранее готовых страниц использовано: {job.result.reused}.</p>}
      {job.result?.unavailable > 0 && <p className="warning-note">Недоступно для индексации: {job.result.unavailable} стр.</p>}
      {job.result?.failed?.length > 0 && <ul>{job.result.failed.map(item => <li key={item.page}>Страница {item.page}. {item.message}</li>)}</ul>}
      {retry && <button type="button" disabled={blocked} onClick={() => launch({...job.arguments, action:job.action})}>Повторить обработку</button>}
    </section>}
  </details>;
}
