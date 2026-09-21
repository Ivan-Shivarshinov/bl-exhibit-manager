import React, {useRef, useState} from 'react';

export default function ReferenceAdd({footnotes, documents, initial, busy, error, onClose, onSave}) {
  const [index,setIndex]=useState(initial),[range,setRange]=useState(null),[draft,setDraft]=useState(''),[target,setTarget]=useState('');
  const area=useRef(null), note=footnotes[index];
  function selectedText() {
    const selection=window.getSelection();
    if(!selection?.rangeCount||selection.isCollapsed)return;
    const selected=selection.getRangeAt(0),root=area.current;
    if(!root.contains(selected.startContainer)||!root.contains(selected.endContainer))return;
    const before=selected.cloneRange();before.selectNodeContents(root);before.setEnd(selected.startContainer,selected.startOffset);
    const start=Array.from(before.toString()).length,end=start+Array.from(selected.toString()).length;
    setRange([start,end]);setDraft(Array.from(note.text).slice(start,end).join(''));
  }
  function typed(value) {
    setDraft(value);
    const start=note.text.indexOf(value);
    setRange(value&&start>=0&&note.text.indexOf(value,start+1)<0 ? [Array.from(note.text.slice(0,start)).length,Array.from(note.text.slice(0,start)+value).length] : null);
  }
  return <div className="modal-backdrop"><section className="scope-modal" role="dialog" aria-modal="true" aria-label="Добавить ссылку">
    <div className="section-heading"><h2>Добавить ссылку из сноски</h2><button disabled={Boolean(busy)} onClick={onClose}>Закрыть</button></div>
    <label className="field"><span>Сноска</span><select autoFocus value={index} onChange={e=>{setIndex(Number(e.target.value));setRange(null);setDraft('');}}>{footnotes.map((f,i)=><option key={`${f.fid}:${f.paragraph}`} value={i}>{f.footnote} — {f.text.slice(0,65)}</option>)}</select></label>
    <p>Выделите название в тексте и выберите файл. Слова Annex или Exhibit не обязательны. Страницы и параграфы оставьте за пределами ссылки.</p>
    <p className="scope-text" tabIndex={0} ref={area} onMouseUp={selectedText} onKeyUp={selectedText}>{note.text}</p>
    <label className="field"><span>Текст новой ссылки</span><textarea rows={2} value={draft} onChange={e=>typed(e.target.value)}/></label>
    {draft&&!range?<p role="alert">Текст не найден или повторяется. Выделите нужное вхождение в тексте сноски.</p>:null}
    <label className="field"><span>Файл новой ссылки</span><select value={target} onChange={e=>setTarget(e.target.value)}><option value="">Выберите файл</option>{documents.map(d=><option key={d.id} value={d.id}>{d.identifier?d.identifier+' · ':''}{d.title}</option>)}</select></label>
    {error?<p role="alert" className="preview-error">{error}</p>:null}
    <button className="primary" disabled={Boolean(busy||!range||!target)} onClick={async()=>{if(await onSave({fid:note.fid,paragraph:note.paragraph,mention:draft,start:range[0],end:range[1],target}))onClose();}}>Создать ссылку</button>
  </section></div>;
}
