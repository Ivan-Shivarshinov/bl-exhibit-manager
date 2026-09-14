import React, {useState} from 'react';

const TITLES = {font:'Шрифт',size:'Размер, pt',margin:'Отступ по горизонтали, pt',top:'Отступ сверху, pt',stamp_mode:'Размещение штампов',designation:'Обозначение',original_label:'Пометка оригинала',translation_label:'Пометка перевода'};
const SOURCE = {project:'Подача',group:'Группа',document:'Документ'};
const keys = Object.keys(TITLES);

export default function BatchDialog({p, ids, initialAction, busy, error, onClose, onPreview, onApply}) {
  const docs = ids.map(id=>p.documents.find(d=>d.id===id));
  const [action,setAction]=useState(initialAction), [scope,setScope]=useState(ids.length?'selection':'project');
  const [folder,setFolder]=useState(docs[0]?.folder || ''), [move,setMove]=useState(false);
  const [number,setNumber]=useState(false), [prefix,setPrefix]=useState(''), [start,setStart]=useState(1), [names,setNames]=useState('keep');
  const [group,setGroup]=useState(docs[0]?.folder || p.documents[0]?.folder || '');
  const [values,setValues]=useState(docs[0]?.effective_format || p.format_defaults), [enabled,setEnabled]=useState([]);
  const [reset,setReset]=useState(false), [clearOverrides,setClearOverrides]=useState(false), [report,setReport]=useState(null);
  const groups=[...new Set(p.documents.map(d=>d.folder))];
  function change(fn) { setReport(null); fn(); }
  function defaults(nextScope, nextGroup=group) {
    return nextScope==='selection' ? docs[0]?.effective_format || p.format_defaults : nextScope==='group' ? p.group_formats?.[nextGroup] || p.format_defaults : p.format_defaults;
  }
  const request=action==='organize' ? {action,document_ids:ids,...(move?{folder}:{}),...(number?{numbering:{prefix,start:Number(start)}}:{}),names} :
    {action,document_ids:ids,scope,group,values:reset?{}:Object.fromEntries(enabled.map(k=>[k,values[k]])),reset,reset_overrides:scope!=='selection'&&clearOverrides};
  const canPreview=action==='organize' ? ids.length>0&&(move||number||names!=='keep') : reset||enabled.length>0||clearOverrides;
  function input(k) {
    const props={value:values[k],disabled:Boolean(busy||reset||!enabled.includes(k)),onChange:e=>change(()=>setValues({...values,[k]:['size','margin','top'].includes(k)?Number(e.target.value):e.target.value}))};
    if(k==='font')return <select aria-label="Массовый шрифт" {...props}><option value="DejaVu">DejaVu Sans</option><option>Helvetica</option><option>Times-Roman</option></select>;
    if(k==='stamp_mode')return <select aria-label="Размещение штампов" {...props}><option value="overlay">В полях исходной страницы</option><option value="band">Дополнительная полоса над страницей</option></select>;
    if(k==='designation')return <select aria-label="Массовое обозначение" {...props}><option>Exhibit</option><option>Annex</option><option value="">Без слова</option></select>;
    return <input aria-label={'Массовое: '+TITLES[k]} type={['size','margin','top'].includes(k)?'number':'text'} {...props}/>;
  }
  return <div className="modal-backdrop"><section className="batch-modal" role="dialog" aria-modal="true" aria-label="Массовые изменения">
    <div className="section-heading"><div><h2>Оформление и систематизация</h2><p className="muted">Выбрано документов: {ids.length}. Изменения сохраняются только после предварительной проверки.</p></div><button disabled={Boolean(busy)} onClick={onClose}>Закрыть</button></div>
    {error?<p role="alert" className="preview-error">{error}</p>:null}
    <label className="field"><span>Операция</span><select autoFocus aria-label="Массовая операция" disabled={Boolean(busy)} value={action} onChange={e=>change(()=>setAction(e.target.value))}><option value="organize" disabled={!ids.length}>Номера, имена и папка</option><option value="format">Оформление</option></select></label>
    {action==='organize'?<div className="batch-settings">
      <div><label className="checkline"><input type="checkbox" checked={number} disabled={Boolean(busy)} onChange={e=>change(()=>setNumber(e.target.checked))}/>Назначить номера выбранным документам без номера</label><div className="pair"><label className="field"><span>Префикс новых номеров (необязателен)</span><input aria-label="Префикс новых номеров" disabled={Boolean(busy||!number)} value={prefix} onChange={e=>change(()=>setPrefix(e.target.value))}/></label><label className="field"><span>Начальный номер</span><input aria-label="Начальный номер" type="number" min="1" disabled={Boolean(busy||!number)} value={start} onChange={e=>change(()=>setStart(e.target.value))}/></label></div><p className="muted">Оставьте префикс пустым для Annex 1 или Exhibit 1. Уже назначенные номера и префиксы сохраняются. Занятые номера будут пропущены. Нумерация новых документов следует порядку строк предпросмотра.</p></div>
      <div><label className="checkline"><input type="checkbox" checked={move} disabled={Boolean(busy)} onChange={e=>change(()=>setMove(e.target.checked))}/>Изменить папку выбранных документов</label><input aria-label="Общая папка" disabled={Boolean(busy||!move)} value={folder} onChange={e=>change(()=>setFolder(e.target.value))}/><p className="muted">Пустое значение — корень комплекта. Перемещение может изменить наследуемое оформление.</p><label className="field"><span>Имена файлов</span><select aria-label="Правило имён файлов" disabled={Boolean(busy)} value={names} onChange={e=>change(()=>setNames(e.target.value))}><option value="keep">Сохранить имена</option><option value="identifier">Обозначение.pdf</option><option value="identifier_title">Обозначение — название.pdf</option></select></label><p className="muted">Имена, защищённые в настройках документа, сохраняются. Для ненумерованных документов имя не генерируется.</p></div>
    </div>:<>
      <div className="pair"><label className="field"><span>Уровень оформления</span><select aria-label="Уровень оформления" value={scope} disabled={Boolean(busy)} onChange={e=>change(()=>{setScope(e.target.value);setValues(defaults(e.target.value));setEnabled([]);setReset(false);setClearOverrides(false);})}><option value="selection" disabled={!ids.length}>Выбранные документы — индивидуально</option><option value="group">Группа / папка</option><option value="project">Вся подача</option></select></label>{scope==='group'?<label className="field"><span>Группа</span><select aria-label="Группа оформления" disabled={Boolean(busy)} value={group} onChange={e=>change(()=>{setGroup(e.target.value);setValues(defaults(scope,e.target.value));})}>{groups.map(x=><option key={x} value={x}>{x||'Корень комплекта'}</option>)}</select></label>:null}</div>
      <p className="muted">Документ → группа → подача: индивидуальные параметры имеют приоритет. В группе меняются все её документы, включая не выбранные в таблице. В режиме без обработки PDF сохраняется без штампов.</p>
      <label className="checkline"><input type="checkbox" checked={reset} disabled={Boolean(busy)} onChange={e=>change(()=>setReset(e.target.checked))}/>{scope==='project'?'Вернуть стандартное оформление подачи':'Сбросить настройки этого уровня и наследовать общие'}</label>
      <div className="batch-format-fields">{keys.map(k=><div key={k}><label className="checkline"><input type="checkbox" checked={enabled.includes(k)} disabled={Boolean(busy||reset)} onChange={e=>change(()=>setEnabled(e.target.checked?[...enabled,k]:enabled.filter(x=>x!==k)))}/>{TITLES[k]}</label>{input(k)}</div>)}</div>
      {scope!=='selection'?<label className="checkline"><input type="checkbox" checked={clearOverrides} disabled={Boolean(busy)} onChange={e=>change(()=>setClearOverrides(e.target.checked))}/>Сбросить индивидуальное оформление затронутых документов</label>:null}
    </>}
    <div className="actions"><button className="primary" disabled={Boolean(busy||!canPreview)} onClick={async()=>{setReport(null);const next=await onPreview(request);if(next)setReport(next);}}>Проверить изменения</button><span aria-live="polite">{busy||''}</span></div>
    {report?<section className="batch-report"><h3>Предварительная проверка · изменится документов: {report.changes.length}</h3>{report.warnings.map((w,i)=><p className="muted" key={i}>{w}</p>)}{report.links_need_review?<p className="pending">После применения потребуется заново сопоставить и проверить ссылки Word.</p>:null}{report.conflicts.map((c,i)=><p role="alert" className="preview-error" key={i}>{c.message}</p>)}
      <div className="batch-table"><table><thead><tr><th>Документ</th><th>Было</th><th>Станет</th></tr></thead><tbody>{report.changes.map(c=><tr key={c.id}><td>{c.title}{c.requires_review?<small>Результат потребует проверки</small>:null}</td>{['before','after'].map(side=><td key={side}><b>{c[side].identifier||'Без номера'}</b><div>{c[side].path}</div>{keys.filter(k=>c.before.format[k]!==c.after.format[k]||c.before.sources[k]!==c.after.sources[k]).map(k=><small key={k}>{TITLES[k]}: {String(c[side].format[k])||'Без слова'} · {SOURCE[c[side].sources[k]]}</small>)}</td>)}</tr>)}</tbody></table></div>
      {!report.changes.length?<p className="muted">{report.settings_changed?'Настройки уровня сохранятся. Текущие документы используют параметры с более высоким приоритетом либо уже имеют такие значения.':'Изменений нет.'}</p>:null}
      <button className="primary" disabled={Boolean(busy||report.conflicts.length||!report.settings_changed)} onClick={()=>onApply(request,report.token)}>Применить проверенные изменения</button>
    </section>:null}
  </section></div>;
}
