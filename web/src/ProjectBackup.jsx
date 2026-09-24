import React, {useState} from 'react';

export default function ProjectBackup({p, api, onRestored, onClose}) {
  const [preview, setPreview] = useState(null), [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  async function run(label, action) {
    setBusy(label); setError(''); setNotice('');
    try { await action(); } catch (e) { setError(e.message); } finally { setBusy(''); }
  }
  async function cancel() {
    if (preview) await api(`/backups/${preview.token}/cancel`, {});
    setPreview(null);
  }
  return <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="backup-title">
    <h2 id="backup-title">Архив проекта</h2>
    <p>Сохраните всю работу для резервной копии или переноса на другой компьютер: исходники, настройки, решения по ссылкам, переводы и историю комплектов.</p>
    <p className="muted">Это архив для продолжения работы в приложении. Готовую подачу для отправки можно скачать в разделе «Проверка и экспорт».</p>
    {busy ? <p role="status">{busy}</p> : null}
    {error ? <p className="error-note" role="alert">{error}</p> : null}
    {notice ? <p className="success-note" role="status">{notice}</p> : null}
    {!preview ? <>
      {p ? <button disabled={Boolean(busy)} onClick={() => run('Сохраняем и проверяем архив…', async () => {
        const result = await api(`/projects/${p.id}/backup`, {});
        const a = document.createElement('a'); a.href = `/api/backups/${result.token}/download`; a.download = 'Exhibit-project.zip'; a.click();
        setNotice('Архив подготовлен для скачивания. Сохраните его в надёжном месте.');
      })}>Сохранить проект «{p.name}»</button> : null}
      <label className={'upload button' + (busy ? ' disabled' : '')}>Восстановить проект<input aria-label="Восстановить проект" type="file" accept=".zip" disabled={Boolean(busy)} onChange={e => {
        const file = e.target.files[0]; e.target.value = '';
        if (file) run('Загружаем и проверяем архив…', async () => setPreview(await api('/backups/preview', file)));
      }}/></label>
      <p className="muted">До 8 ГБ и 20 000 файлов. Активный перевод нужно завершить или остановить перед сохранением. Настройки подключения и вход в сервисы не переносятся.</p>
    </> : <>
      <h3>{preview.name}</h3>
      <p>Документов: {preview.summary.documents}; исходных файлов и редакций: {preview.summary.inputs}; черновиков перевода: {preview.summary.translations}; готовых комплектов: {preview.summary.exports}.</p>
      <p>Размер данных: {(preview.summary.bytes / 1_000_000).toFixed(1)} МБ. Целостность проверена.</p>
      {preview.conflict ? <p className="warning">Этот проект уже есть на компьютере. Можно восстановить независимую копию с другим именем.</p> : <p>Проект будет добавлен в список. После восстановления можно продолжить работу.</p>}
      <div className="actions"><button className="primary" disabled={Boolean(busy)} onClick={() => run('Восстанавливаем проект…', async () => {
        const restored = await api(`/backups/${preview.token}/restore`, {copy: preview.conflict});
        setPreview(null); await onRestored(restored);
      })}>{preview.conflict ? 'Восстановить копию' : 'Восстановить'}</button>
      <button disabled={Boolean(busy)} onClick={() => run('Отменяем…', cancel)}>Отменить восстановление</button></div>
      <p className="muted">Для копии с управляемыми ссылками выберите её в панели Word и нажмите «Проверить ссылки» перед обновлением.</p>
    </>}
    <div className="actions"><button disabled={Boolean(busy)} onClick={() => run('Закрываем…', async () => { await cancel(); onClose(); })}>Закрыть</button></div>
  </section></div>;
}
