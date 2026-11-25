import React, {useRef, useState} from 'react';
import {parsePages} from './pdfSelection';

const operationNames = {rotate:'Повернуть страницы', merge:'Объединить PDF', split:'Разделить PDF'};
const downloadNames = {rotate:'Поворот.pdf', merge:'Объединённый PDF.pdf', split:'Части PDF.zip'};

export default function PdfDerivatives({project, api, disabled, saveBlob}) {
  const sources = project.documents.flatMap(doc => ['original','translation'].filter(part => doc[part]).map(part => ({
    key:`${doc.id}/${part}`, did:doc.id, part, source:doc[part].sha256, count:doc[part].pages,
    label:`${doc.title} · ${part === 'translation' ? 'перевод' : 'оригинал'}`,
  })));
  const [operation, setOperation] = useState('rotate');
  const [rows, setRows] = useState([{id:0, key:sources[0]?.key, pages:''}]);
  const [angle, setAngle] = useState('90');
  const [ranges, setRanges] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const id = useRef(1);
  const blocked = busy || disabled || !sources.length;

  function update(row, data) {
    setRows(rows.map(item => item.id === row.id ? {...item,...data} : item));
  }

  function choose(event) {
    const next = event.target.value;
    setOperation(next);
    setRows([{id:id.current++, key:sources[0]?.key, pages:''}, ...(next === 'merge' && sources[1] ? [{id:id.current++,key:sources[1].key,pages:''}] : [])]);
  }

  async function make(event) {
    event.preventDefault();
    if (blocked) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const chosen = rows.map(row => {
        const item = sources.find(source => source.key === row.key);
        if (!item) throw new Error('Выберите существующий PDF.');
        const {did, part, source, count} = item;
        const pages = operation === 'split' ? undefined : parsePages(row.pages.trim() || `1-${count}`, count).map(p => p.page);
        return {did, part, source, pages};
      });
      const count = sources.find(source => source.key === rows[0].key).count;
      const groups = operation === 'split' ? ranges.split(';').map(range => parsePages(range.trim(), count).map(p=>p.page)) : undefined;
      const result = await api(`/projects/${project.id}/pdf-derivatives`, {operation, sources:chosen, angle:Number(angle), groups}, {blob:true});
      saveBlob(result, downloadNames[operation]);
      setMessage('Производная скачана.');
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  }

  return <details className="pdf-derivatives">
    <summary>Сделать отдельную копию PDF</summary>
    <form onSubmit={make}>
      <label className="field">Операция<select aria-label="Операция с PDF" value={operation} disabled={blocked} onChange={choose}>{Object.entries(operationNames).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
      {rows.map((row, index)=><div className="pdf-derivative-source" key={row.id}>
        <label className="field">Исходный PDF {index+1}<select aria-label={`Исходный PDF ${index+1}`} value={row.key} disabled={blocked} onChange={event=>update(row,{key:event.target.value,pages:''})}>{sources.map(source=><option key={source.key} value={source.key}>{source.label}</option>)}</select></label>
        {operation !== 'split' && <label className="field">Страницы {operation === 'merge' ? `из PDF ${index+1}` : 'для поворота'}<input maxLength={500} disabled={blocked} value={row.pages} placeholder="Все страницы" onChange={event=>update(row,{pages:event.target.value})}/></label>}
        {operation === 'merge' && rows.length > 1 && <button type="button" disabled={blocked} onClick={()=>setRows(rows.filter(item=>item.id!==row.id))}>Убрать PDF {index+1}</button>}
      </div>)}
      {operation === 'merge' && <button type="button" disabled={blocked || rows.length >= 20} onClick={()=>setRows([...rows,{id:id.current++,key:sources[0].key,pages:''}])}>Добавить PDF в конец</button>}
      {operation === 'rotate' && <label className="field">Угол по часовой стрелке<select value={angle} disabled={blocked} onChange={event=>setAngle(event.target.value)}>{[90,180,270].map(value=><option key={value}>{value}</option>)}</select></label>}
      {operation === 'split' && <label className="field">Части по порядку<input required maxLength={500} value={ranges} disabled={blocked} placeholder="1-3; 4, 5; 6-8" onChange={event=>setRanges(event.target.value)}/><small>Разделяйте части точкой с запятой. Укажите каждую страницу один раз.</small></label>}
      <div className="actions"><button className="primary" disabled={blocked || (operation === 'merge' && rows.length < 2)}>Скачать производную</button></div>
      <p role="status">{busy ? 'Готовим PDF…' : message}</p>
      {error && <p role="alert" className="error-note">{error}</p>}
    </form>
  </details>;
}
