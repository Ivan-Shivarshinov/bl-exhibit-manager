import React, {useCallback, useEffect, useRef, useState} from 'react';
import {PrivateImage} from './PrivateResources';
import TextJobs from './TextJobs';

const partNames = {original: 'Оригинал', translation: 'Перевод', ready: 'Готовый PDF'};
const methods = {embedded: 'Текст PDF', ocr: 'OCR, ожидает сверки', 'confirmed-ocr': 'Проверенный OCR', docx: 'Основной DOCX'};
const languages = {eng: 'Английский', rus: 'Русский'};

export default function MaterialText({project, api, onDirty, onProject, saveBlob}) {
  const [query, setQuery] = useState('');
  const [part, setPart] = useState('all');
  const [scope, setScope] = useState('source');
  const [matchCase, setMatchCase] = useState(false);
  const [result, setResult] = useState(null);
  const [target, setTarget] = useState(null);
  const [status, setStatus] = useState(null);
  const [engine, setEngine] = useState(null);
  const [language, setLanguage] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [edited, setEdited] = useState(false);
  const [notice, setNotice] = useState('');
  const [volumeShown, setVolumeShown] = useState(false);
  const heading = useRef(null);
  const mounted = useRef(false);
  const base = `/projects/${project.id}`;
  const onTextJobFinished = useCallback(async job => {
    if (job.action !== 'ocr_pages') return;
    const updated = await api(base);
    if (mounted.current) onProject(updated);
  }, [api, base, onProject]);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    api('/text/ocr', undefined, {signal: controller.signal}).then(value => {
      if (!controller.signal.aborted) {
        setEngine(value);
        setLanguage(value.languages[0] || '');
      }
    }).catch(() => {
      if (!controller.signal.aborted) setEngine({available: false, languages: [], message: 'Не удалось проверить доступность OCR.'});
    });
    return () => {mounted.current = false;controller.abort();};
  }, [api]);

  async function action(label, task) {
    setBusy(label);
    setError('');
    setNotice('');
    try {
      await task();
    } catch (error) {
      setError(error.message);
    } finally {
      setBusy('');
    }
  }

  function search(criteria, cursor) {
    const offset = cursor?.next_offset || 0;
    action('Ищем в материалах…', async () => {
      if (!offset) setResult(null);
      const response = await api(`${base}/text-search`, {...criteria, offset, snapshot: cursor?.snapshot});
      setResult({...response, criteria, hits: offset ? [...cursor.hits, ...response.hits] : response.hits,
        checked: response.checked + offset, without_text: response.without_text + (cursor?.without_text || 0),
        unavailable: response.unavailable + (cursor?.unavailable || 0)});
    });
  }

  function open(page) {
    if (page.part === 'main') {
      const params = new URLSearchParams({source: page.source, location: page.location});
      action('Открываем место в DOCX…', async () => {
        const original = await api(`${base}/text-docx?${params}`);
        setTarget({...page, ...original});
        setStatus(null);
      });
      return;
    }
    const params = new URLSearchParams({page: page.page, part: page.part, source: page.source});
    action('Открываем страницу…', async () => {
      const route = `${base}/documents/${page.document}/text-page`;
      setVolumeShown(false);
      if (page.prepared_page) {
        params.set('prepared', page.prepared_page);
        params.set('rect', JSON.stringify(page.rect));
        setStatus(null);
        setTarget({...page, image: `/api${route}/image?${params}`});
        return;
      }
      const response = await api(`${route}?${params}`);
      setStatus(response);
      setTarget({...page, route, image: `/api${route}/image?${params}`});
    });
  }

  useEffect(() => {
    if (target) heading.current?.focus();
  }, [target]);

  function setDirty(value) {
    setEdited(value);
    onDirty(value);
  }

  function save(operation, payload) {
    action(operation === 'ocr' ? 'Распознаём страницу…' : 'Сохраняем проверенный текст…', async () => {
      const record = await api(`${target.route}/${operation}`, {page: target.page, part: target.part, source: target.source, ...payload});
      if (!mounted.current) return;
      setStatus({...status, record});
      setDirty(false);
      setResult(null);
      setNotice(operation === 'ocr' ? 'Распознавание завершено. Сверьте текст со страницей.' : 'Проверенный текст сохранён и доступен для поиска.');
      if (operation === 'ocr' && project.schema < 5) {
        const updated = await api(base);
        if (mounted.current) onProject(updated);
      }
    });
  }

  function downloadCopy() {
    const params = new URLSearchParams({part: target.part, source: target.source});
    action('Готовим поисковую копию…', async () => {
      const blob = await api(`${base}/documents/${target.document}/searchable-copy?${params}`, undefined, {blob: true});
      if (!mounted.current) return;
      saveBlob(blob, `${target.title} — поисковая копия.pdf`);
      setNotice('Поисковая копия скачана.');
    });
  }

  const actionsLocked = Boolean(busy || edited);
  return <main className="export-page material-text">
    <h1>Поиск по тексту</h1>
    <form className="material-text-search actions" onSubmit={event => {event.preventDefault(); if (!actionsLocked) search({query, scope, part, match_case: matchCase});}}>
      <label>Слово или фраза<input type="search" required maxLength={160} value={query} onChange={event => setQuery(event.target.value)} placeholder={'Например, "срок поставки"'}/></label>
      <label>Область<select aria-label="Область поиска" value={scope} onChange={event => setScope(event.target.value)}><option value="source">Исходные PDF</option><option value="prepared">Подготовленные страницы и фрагменты</option>{project.main && <option value="docx">Основной DOCX</option>}</select></label>
      {scope !== 'docx' && <label>Источник<select value={part} onChange={event => setPart(event.target.value)}><option value="all">Оригиналы и переводы</option><option value="original">Оригиналы</option><option value="translation">Переводы</option></select></label>}
      <label className="checkline"><input type="checkbox" checked={matchCase} onChange={event => setMatchCase(event.target.checked)}/>Учитывать регистр</label>
      <button className="primary" disabled={actionsLocked || (scope !== 'docx' && !project.documents.length)}>Найти</button>
    </form>
    <p className="muted">Фразу заключайте в кавычки.</p>
    <div role="status">{busy || notice}</div>
    {error && <p role="alert" className="error-note">{error}</p>}
    {result && <SearchResults result={result} disabled={actionsLocked} onOpen={open} onMore={() => search(result.criteria, result)}/>}
    {project.documents.length > 0 && <PagePicker key={project.id} documents={project.documents} disabled={actionsLocked} onOpen={open}/>}
    {project.documents.length > 0 && <TextJobs project={project} api={api} engine={engine} disabled={actionsLocked} onFinished={onTextJobFinished}/>}
    {target && <section className="material-text-review" aria-labelledby="ocr-page-heading">
      <h2 id="ocr-page-heading" tabIndex={-1} ref={heading}>{hitLabel(target)}</h2>
      {target.part === 'main' ? <DocxParagraph target={target}/>
      : target.prepared_page ? <>
        <PagePicture key={target.image} target={target}/>
        {target.volume && <div className="actions"><button type="button" onClick={() => setVolumeShown(!volumeShown)}>{volumeShown ? 'Скрыть том' : `Открыть том ${target.volume.number}, стр. ${target.volume.page}`}</button></div>}
        {volumeShown && <PrivateImage className="text-volume-preview" src={`/api${base}/volumes/${target.volume.token}/${target.volume.number - 1}/image?page=${target.volume.page}`} alt={`Том ${target.volume.number}, страница ${target.volume.page}`}/>}
      </> : <div className="material-text-columns">
        <PagePicture key={target.image} target={target}/>
        <div>
          {status.record ? <Review key={status.record.revision} record={status.record} onDirty={setDirty} busy={Boolean(busy)} onSave={(revision, lines) => save('confirm', {revision, lines})}/>
            : status.embedded ? <p>У страницы есть встроенный текст. Он доступен для поиска.</p>
              : <div><p>У страницы нет встроенного текста. Её можно распознать локально.</p>
                {engine?.available && <div className="actions">
                  <label>Язык<SelectLanguage language={language} onChange={setLanguage} engine={engine} disabled={Boolean(busy)}/></label>
                  <button className="primary" disabled={Boolean(busy) || !language} onClick={() => save('ocr', {language})}>Распознать страницу</button>
                </div>}
                {engine?.message && <p className="warning-note">{engine.message}</p>}
              </div>}
          {status.record?.confirmed && <div className="actions"><button disabled={actionsLocked} onClick={downloadCopy}>Скачать PDF с поисковым слоем</button></div>}
        </div>
      </div>}
    </section>}
  </main>;
}

function DocxParagraph({target}) {
  const text = Array.from(target.text);
  const [start, end] = target.span;
  return <p className="text-source-paragraph">{text.slice(0, start).join('')}<mark>{text.slice(start, end).join('')}</mark>{text.slice(end).join('')}</p>;
}

export function hitLabel(hit) {
  if (hit.part === 'main') return `${hit.title} · ${hit.label}`;
  const identifier = hit.identifier ? `${hit.identifier} · ` : '';
  const version = hit.version_number ? ` · версия ${hit.version_number}${hit.hidden ? ', скрытый материал' : ''}` : '';
  const prepared = hit.prepared_page ? ` · подготовленная стр. ${hit.prepared_page}${hit.rect ? ' (фрагмент)' : ''}` : '';
  return `${identifier}${hit.title}${version} · ${partNames[hit.part]}, стр. ${hit.page}${prepared}`;
}

export function SearchResults({result, disabled, onOpen, onMore}) {
  const unit = result.criteria.scope === 'docx' ? 'абзацев' : 'стр.';
  return <section aria-label="Результаты поиска">
    <p>Проверено {result.checked} из {result.pages} {unit} · найдено: {result.hits.length}.</p>
    {Boolean(result.without_text || result.unavailable || result.next_offset) && <p className="warning-note">{result.without_text ? `Страниц без доступного текста: ${result.without_text}. ` : ''}{result.unavailable ? `Страниц с ошибкой чтения: ${result.unavailable}. ` : ''}{result.next_offset ? 'Выдача пока неполная.' : ''}</p>}
    {result.hits.length > 0 && <ul className="material-text-results">{result.hits.map(hit => <li key={JSON.stringify([hit.document, hit.version, hit.part, hit.prepared_page, hit.page, hit.location])}>
      <button disabled={disabled} onClick={() => onOpen(hit)}>{hitLabel(hit)}</button>
      <small>{methods[hit.method]}</small><p>{hit.context}</p>
    </li>)}</ul>}
    {result.next_offset !== null && <button disabled={disabled} onClick={onMore}>Продолжить поиск</button>}
  </section>;
}

function SelectLanguage({language, onChange, engine, disabled}) {
  return <select aria-label="Язык распознавания" value={language} onChange={event => onChange(event.target.value)} disabled={disabled}>
    {Object.entries(languages).map(([code, label]) => {
      const available = engine.languages.includes(code);
      return <option key={code} value={code} disabled={!available}>{label}{available ? '' : ' · пакет не установлен'}</option>;
    })}
  </select>;
}

function PagePicker({documents, disabled, onOpen}) {
  const [id, setId] = useState(documents[0].id);
  const [part, setPart] = useState('original');
  const [page, setPage] = useState('1');
  const doc = documents.find(value => value.id === id) || documents[0];
  const role = doc[part] ? part : 'original';
  const source = doc[role];
  return <details>
    <summary>Открыть страницу для распознавания</summary>
    <form className="material-text-search actions" onSubmit={event => {event.preventDefault();onOpen({document: doc.id, title: doc.title, part: role, page: Number(page), source: source.sha256, boxes: []});}}>
      <label>Документ<select aria-label="Документ для распознавания" value={doc.id} disabled={disabled} onChange={event => {setId(event.target.value);setPart('original');setPage('1');}}>{documents.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
      <label>Часть PDF<select aria-label="Часть PDF" value={role} disabled={disabled} onChange={event => {setPart(event.target.value);setPage('1');}}><option value="original">Оригинал</option>{doc.translation && <option value="translation">Перевод</option>}</select></label>
      <label>Номер страницы<input type="number" required min={1} max={source.pages} value={page} onChange={event => setPage(event.target.value)} disabled={disabled}/></label>
      <button disabled={disabled}>Открыть страницу</button>
    </form>
  </details>;
}

export function PagePicture({target}) {
  const [loaded, setLoaded] = useState(false);
  return <div className="material-text-picture">
    <PrivateImage src={target.image} alt={`${target.title}, страница ${target.page}`} onLoad={() => setLoaded(true)}/>
    {loaded && target.boxes.map(([left, top, right, bottom], index) => <span className="material-text-hit" aria-hidden="true" key={index} style={{left: `${left * 100}%`, top: `${top * 100}%`, width: `${(right - left) * 100}%`, height: `${(bottom - top) * 100}%`}}/>)}
  </div>;
}

function Review({record, busy, onDirty, onSave}) {
  const initial = record.lines.map(line => line.text);
  const [lines, setLines] = useState(initial);
  const isDirty = lines.some((line, index) => line !== initial[index]);
  return <form onSubmit={event => {event.preventDefault();onSave(record.revision, lines);}}>
    <h3>{record.confirmed ? 'Проверенный текст' : 'Распознанный текст'}</h3>
    <p className="muted">{languages[record.language]}. Сверьте строки со страницей.</p>
    <div className="material-ocr-lines">{lines.map((line, index) => <label key={index}>Строка {index + 1}{record.lines[index].confidence < 70 ? ' · проверьте распознавание' : ''}
      <input maxLength={1000} value={line} disabled={busy} onChange={event => { const next = lines.with(index, event.target.value);setLines(next);onDirty(next.some((value, i) => value !== initial[i])); }}/>
    </label>)}</div>
    <div className="actions">
      <button className="primary" disabled={busy || !lines.some(line => line.trim()) || (record.confirmed && !isDirty)}>Сохранить проверенный текст</button>
      {isDirty && <button type="button" disabled={busy} onClick={() => {setLines(initial);onDirty(false);}}>Отменить правки</button>}
    </div>
  </form>;
}
