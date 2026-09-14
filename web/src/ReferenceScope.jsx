import React, {useRef, useState} from 'react';

export default function ReferenceScope({reference, text, busy, onSave, onClose, externalError}) {
  const area=useRef(null);
  const initial=[reference.link_start??reference.start,reference.link_end??reference.end];
  const [range,setRange]=useState(initial),[error,setError]=useState('');
  const chars=Array.from(text);
  const [draft,setDraft]=useState(chars.slice(...initial).join(''));
  function selectedText() {
    const selection=window.getSelection();
    if(!selection?.rangeCount||selection.isCollapsed)return;
    const selected=selection.getRangeAt(0),root=area.current;
    if(!root.contains(selected.startContainer)||!root.contains(selected.endContainer))return;
    const before=selected.cloneRange();before.selectNodeContents(root);before.setEnd(selected.startContainer,selected.startOffset);
    const start=Array.from(before.toString()).length,end=start+Array.from(selected.toString()).length;
    setRange([start,end]);setDraft(chars.slice(start,end).join(''));setError('');
  }
  return <div className="modal-backdrop"><section className="scope-modal" role="dialog" aria-modal="true" aria-label="Границы ссылки">
    <div className="section-heading"><h2>Границы ссылки · сноска {reference.footnote}</h2><button disabled={Boolean(busy)} onClick={onClose}>Закрыть</button></div>
    <p>Выделите в тексте ниже всю постоянную часть ссылки. Страницы, параграфы и статьи оставьте вне выделения.</p>
    <p ref={area} className="scope-text" onMouseUp={selectedText} onKeyUp={selectedText} tabIndex={0}>{chars.slice(0,initial[0]).join('')}<mark>{chars.slice(...initial).join('')}</mark>{chars.slice(initial[1]).join('')}</p>
    <p className="muted">Текущее сохранённое выделение отмечено жёлтым. Для точной правки с клавиатуры измените текст ниже; он должен буквально встречаться в этой сноске.</p>
    <label className="field"><span>Текст ссылки</span><textarea aria-label="Текст ссылки" autoFocus rows={3} value={draft} onChange={e=>{
      const value=e.target.value;setDraft(value);const first=text.indexOf(value);
      if(value&&first>=0&&text.indexOf(value,first+1)<0){const start=Array.from(text.slice(0,first)).length;setRange([start,start+Array.from(value).length]);setError('');}
      else setError('Выделите нужное вхождение мышью: текст не найден или встречается несколько раз.');
    }}/></label>
    {error||externalError?<p role="alert" className="preview-error">{error||externalError}</p>:null}
    <p className="muted">Найденное упоминание «{reference.mention}» должно целиком входить в ссылку. Другие ссылки не должны пересекаться с ней.</p>
    <div className="actions"><button disabled={Boolean(busy)} onClick={()=>{setRange([reference.start,reference.end]);setDraft(reference.mention);setError('');}}>Только найденное упоминание</button><button className="primary" disabled={Boolean(busy||error||!draft)} onClick={async()=>{try{if(await onSave(...range))onClose();}catch(e){setError(e.message);}}}>Сохранить границы</button></div>
  </section></div>;
}
