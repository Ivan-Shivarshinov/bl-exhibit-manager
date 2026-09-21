import React, {useState} from 'react';

function foldersOf(paths) {
  const folders=new Set(['']);
  for(const path of paths) {
    const parts=path.split('/').filter(Boolean);
    for(let i=1;i<=parts.length;i++)folders.add(parts.slice(0,i).join('/'));
  }
  return [...folders].sort((a,b)=>a.localeCompare(b));
}

export function SubmissionTree({documents,main,folders=[],selected,onSelect}) {
  const paths=foldersOf([...folders,...documents.map(d=>d.folder)]);
  function node(path) {
    const children=paths.filter(x=>x&&x!==path&&x.split('/').slice(0,-1).join('/')===path);
    return <li key={path||'root'}><div className="folder-node">{onSelect?<button className={selected===path?'selected-folder':''} aria-pressed={selected===path} onClick={()=>onSelect(path)}>{path?path.split('/').at(-1):'Submission'}</button>:<strong>{path?path.split('/').at(-1):'Submission'}</strong>}</div><ul>
      {!path&&main?<><li>Main document.docx</li><li>Main document.pdf</li></>:null}
      {children.map(node)}
      {documents.filter(d=>d.folder===path).map(d=><li className="folder-file" key={d.id}>{d.filename}</li>)}
    </ul></li>;
  }
  return <ul className="submission-tree" aria-label="Структура Submission">{node('')}</ul>;
}

export default function SubmissionFolders({p,busy,error,onClose,onPreview,onApply}) {
  const [destinations,setDestinations]=useState(()=>Object.fromEntries(p.documents.map(d=>[d.id,d.folder])));
  const [created,setCreated]=useState([]),[folder,setFolder]=useState(''),[name,setName]=useState('');
  const [checked,setChecked]=useState([]),[search,setSearch]=useState(''),[report,setReport]=useState(null),[localError,setLocalError]=useState('');
  const docs=p.documents.map(d=>({...d,folder:destinations[d.id]}));
  const folders=foldersOf([...created,...docs.map(d=>d.folder)]);
  const visible=docs.filter(d=>`${d.title} ${d.filename} ${d.folder}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  const changed=Object.fromEntries(p.documents.filter(d=>d.folder!==destinations[d.id]).map(d=>[d.id,destinations[d.id]]));
  const request={action:'folders',destinations:changed};
  function edit() {setReport(null);setLocalError('');}
  function folderName() {
    const value=name.trim();
    if(!value||/[<>:"/\\|?*\x00-\x1f]/.test(value)||/[. ]$/.test(value)||/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(value)) {
      setLocalError('Введите одно название папки без / и других специальных символов.');return null;
    }
    return value;
  }
  function makeFolder(rename=false) {
    const value=folderName();if(!value)return;
    const parent=rename?folder.split('/').slice(0,-1).join('/'):folder;
    const path=[parent,value].filter(Boolean).join('/');
    if(folders.some(f=>f.toLocaleLowerCase()===path.toLocaleLowerCase())){setLocalError('Такая папка уже есть. Выберите её в дереве.');return;}
    edit();
    if(rename) {
      const replace=f=>f===folder||f.startsWith(folder+'/')?path+f.slice(folder.length):f;
      setDestinations(Object.fromEntries(Object.entries(destinations).map(([id,f])=>[id,replace(f)])));
      setCreated([...created.map(replace),path]);
    } else setCreated([...created,path]);
    setFolder(path);setName('');
  }
  return <div className="modal-backdrop"><section className="folders-modal" role="dialog" aria-modal="true" aria-label="Папки Submission">
    <div className="section-heading"><div><h2>Папки Submission</h2><p className="muted">Распределите приложения до сборки. Главный документ останется в корне, ссылки обновятся автоматически.</p></div><button disabled={Boolean(busy)} onClick={onClose}>Закрыть</button></div>
    <div className="folders-layout"><section><h3>Будущий комплект</h3><p className="muted">Выберите папку назначения. Для папки верхнего уровня выберите Submission.</p>
      <div className="folder-tree-scroll"><SubmissionTree documents={docs} main={p.main} folders={created} selected={folder} onSelect={f=>{setFolder(f);setLocalError('');}}/></div>
      <label className="field"><span>Название папки</span><input autoFocus value={name} disabled={Boolean(busy)} placeholder="Например, Exhibits" onChange={e=>setName(e.target.value)}/></label>
      <p className="muted">Новая папка внутри: Submission{folder?'/'+folder:''}</p>
      <div className="actions"><button disabled={Boolean(busy||!name.trim())} onClick={()=>makeFolder()}>Создать папку</button><button disabled={Boolean(busy||!folder||!name.trim())} onClick={()=>makeFolder(true)}>Переименовать выбранную папку</button></div>
    </section><section><h3>Документы</h3><input aria-label="Поиск документов для папок" type="search" value={search} placeholder="Название, номер или папка" onChange={e=>setSearch(e.target.value)}/>
      <label className="checkline"><input type="checkbox" disabled={Boolean(busy||!visible.length)} checked={visible.length>0&&visible.every(d=>checked.includes(d.id))} onChange={e=>setChecked(e.target.checked?[...new Set([...checked,...visible.map(d=>d.id)])]:checked.filter(id=>!visible.some(d=>d.id===id)))}/>Выбрать все найденные ({visible.length})</label>
      <div className="folder-documents">{visible.map(d=><label key={d.id} className="folder-document"><input type="checkbox" disabled={Boolean(busy)} checked={checked.includes(d.id)} onChange={e=>setChecked(e.target.checked?[...checked,d.id]:checked.filter(id=>id!==d.id))}/><span><b>{d.title}</b><small>Submission/{d.folder?d.folder+'/':''}{d.filename}</small></span></label>)}</div>
      <p>Выбрано: {checked.length}. Назначение: <b>Submission{folder?'/'+folder:''}</b></p>
      <button disabled={Boolean(busy||!checked.length)} onClick={()=>{edit();setDestinations({...destinations,...Object.fromEntries(checked.map(id=>[id,folder]))});setChecked([]);}}>Переместить выбранные сюда</button>
    </section></div>
    {localError||error?<p role="alert" className="preview-error">{localError||error}</p>:null}
    <p className="muted">Пока это план: исходные файлы не перемещаются. Пустые папки не попадут в ZIP. Если у папки задано своё оформление, предварительная проверка покажет его изменения.</p>
    <button className="primary" disabled={Boolean(busy||!Object.keys(changed).length)} onClick={async()=>{setLocalError('');setReport(null);const r=await onPreview(request);if(r)setReport(r);}}>Проверить структуру</button>
    {report?<section className="folder-report" aria-live="polite"><h3>Изменится документов: {report.changes.length}</h3>{report.conflicts.map((c,i)=><p role="alert" className="preview-error" key={i}>{c.message}</p>)}{report.links_need_review?<p className="pending">Изменилось обозначение приложения из настроек папки. Повторите сопоставление ссылок.</p>:null}
      <ul>{report.changes.map(c=><li key={c.id}><b>{c.title}</b>: {c.before.path} → {c.after.path}{c.requires_review?<strong> — оформление потребует проверки</strong>:null}</li>)}</ul>
      <button className="primary" disabled={Boolean(busy||report.conflicts.length||!report.settings_changed)} onClick={()=>onApply(request,report.token)}>Сохранить структуру</button>
    </section>:null}
  </section></div>;
}
