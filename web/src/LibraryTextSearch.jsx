import React, {useEffect, useRef, useState} from 'react';
import {PagePicture, SearchResults, hitLabel} from './MaterialText';

export default function LibraryTextSearch({api, lid, disabled, onVersion}) {
  const [query, setQuery] = useState('');
  const [history, setHistory] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [matchCase, setMatchCase] = useState(false);
  const [tag, setTag] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [target, setTarget] = useState(null);
  const heading = useRef(null);

  useEffect(() => {
    if (target) heading.current?.focus();
  }, [target]);

  async function search(criteria, cursor) {
    if (disabled || pending) return;
    setPending(true);
    setError('');
    if (!cursor) {
      setResult(null);
      setTarget(null);
    }
    const offset = cursor?.next_offset || 0;
    try {
      const data = await api(`/libraries/${lid}/text-search`, {...criteria, offset, snapshot: cursor?.snapshot});
      setResult({...data, criteria, hits: [...(cursor?.hits || []), ...data.hits], checked: offset + data.checked,
        unavailable: (cursor?.unavailable || 0) + data.unavailable, without_text: (cursor?.without_text || 0) + data.without_text});
    } catch (error) {
      setError(error.message);
    } finally {
      setPending(false);
    }
  }

  function open(hit) {
    const params = new URLSearchParams({role: hit.part, page: hit.page});
    setTarget({...hit, image: `/api/libraries/${lid}/materials/${hit.document}/versions/${hit.version}/preview?${params}`});
  }

  return <details>
    <summary>Поиск по содержимому PDF</summary>
    <form className="material-text-search actions" onSubmit={event => {event.preventDefault();search({query, history, hidden, tag, match_case: matchCase});}}>
      <label>Слово или фраза<input type="search" value={query} maxLength={160} required onChange={event => setQuery(event.target.value)}/></label>
      <label>Метка<input value={tag} maxLength={80} onChange={event => setTag(event.target.value)}/></label>
      <label className="checkline"><input type="checkbox" checked={history} onChange={event => setHistory(event.target.checked)}/>Включить старые версии</label>
      <label className="checkline"><input type="checkbox" checked={hidden} onChange={event => setHidden(event.target.checked)}/>Включить скрытые материалы</label>
      <label className="checkline"><input type="checkbox" checked={matchCase} onChange={event => setMatchCase(event.target.checked)}/>Учитывать регистр</label>
      <button className="primary" disabled={disabled || pending}>Найти в библиотеке</button>
    </form>
    <p className="muted">Поиск по тексту PDF. Фразу заключайте в кавычки.</p>
    {pending && <p role="status">Ищем в библиотеке…</p>}
    {error && <p role="alert" className="error-note">{error}</p>}
    {result && <SearchResults result={result} disabled={disabled || pending} onOpen={open} onMore={() => search(result.criteria, result)}/>}
    {target && <section>
      <h3 ref={heading} tabIndex={-1}>{hitLabel(target)}</h3>
      <PagePicture key={target.image} target={target}/>
      <button type="button" disabled={disabled} onClick={() => onVersion({material_id: target.document, version_id: target.version})}>Открыть карточку версии</button>
    </section>}
  </details>;
}
