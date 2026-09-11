import React, {useEffect, useState} from 'react';

export default function MainPdf({p, api, run, disabled}) {
  const [status,setStatus]=useState(null), [error,setError]=useState(''), [page,setPage]=useState(1), [image,setImage]=useState('');
  useEffect(()=>{let live=true; api(`/projects/${p.id}/main-pdf/status`).then(s=>{if(live)setStatus(s);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[p,api]);
  useEffect(()=>{
    if(!status?.ready){setImage('');return;}
    const controller=new AbortController();let url,live=true;setImage('');setError('');
    fetch(`/api/projects/${p.id}/main-pdf/preview?page=${page}&v=${status.key}`,{signal:controller.signal}).then(async r=>{if(!r.ok){const d=await r.json();throw new Error(d.detail);}return r.blob();}).then(b=>{if(live){url=URL.createObjectURL(b);setImage(url);}}).catch(e=>{if(live&&e.name!=='AbortError')setError(e.message);});
    return()=>{live=false;controller.abort();if(url)URL.revokeObjectURL(url);};
  },[p.id,status,page]);
  return <section className="main-pdf"><h2>Основной PDF</h2><p className="muted">{status?.ready?`Подготовлен через ${status.converted_with}. Страниц: ${status.pages}.`:status?.available?`Локальная конвертация: ${status.label}.`:status?.message||'Проверяем конвертер…'}</p>
    {error?<p role="alert" className="preview-error">{error}</p>:null}
    <button disabled={disabled||Boolean(p.issues.length)} onClick={()=>run('Готовим основной PDF…',async()=>{setError('');const s=await api(`/projects/${p.id}/main-pdf/prepare`,{});setPage(1);setStatus(s);})}>{status?.ready?'Подготовить PDF заново':'Подготовить и просмотреть PDF'}</button>
    <p className="muted">Сверьте переносы страниц и сноски. В ZIP войдут DOCX и PDF. Ссылки PDF открывают приложения из распакованной папки; переходы проверьте в desktop PDF-просмотрщике. Предпросмотр ниже показывает вёрстку.</p>
    <p className="muted">На Windows открытие приложений проверено в SumatraPDF 3.5.2. В PDF24 Reader 11.30.1 ссылки этого комплекта в подпапки не работают. Сохраняйте всю структуру распакованной папки.</p>
    {status?.ready?<><div className="actions"><button aria-label="Предыдущая страница основного PDF" disabled={page<=1} onClick={()=>setPage(page-1)}>←</button><span>Страница {page} из {status.pages}</span><button aria-label="Следующая страница основного PDF" disabled={page>=status.pages} onClick={()=>setPage(page+1)}>→</button><a className="button" href={`/api/projects/${p.id}/main-pdf/download`}>Скачать основной PDF</a></div>{image?<img className="main-pdf-page" src={image} alt={`Основной PDF, страница ${page}`}/>:!error?<p>Готовим страницу…</p>:null}</>:null}
  </section>;
}
