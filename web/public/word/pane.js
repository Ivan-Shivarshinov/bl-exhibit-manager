import {insertCitation, inspectUpdates, applyUpdates, currentDocx} from './office.js';

const $ = id => document.getElementById(id);
let catalog = null, selected = '', plan = null, busy = false;
const messages = {current:'Актуальна', update:'Можно обновить', manual:'Текст исправлен вручную — проверьте в Word', missing:'Приложение отсутствует в подаче', other_project:'Ссылка из другой подачи'};

async function api(path, body, binary=false, headers={}) {
  const response = await fetch('/api'+path, {method: body === undefined ? 'GET' : 'POST', headers: {'X-Exhibit-Local':'1', ...(binary ? {} : {'Content-Type':'application/json'}), ...headers}, body: body === undefined ? undefined : binary ? body : JSON.stringify(body)});
  if (!response.ok) { let result; try { result=await response.json(); } catch {} throw new Error(result?.detail || `Ошибка ${response.status}`); }
  return response.json();
}
async function run(task) {
  if (busy) return; busy=true; $('error').hidden=true;
  const controls=[...document.querySelectorAll('button,input,select')]; const states=controls.map(c=>c.disabled); controls.forEach(c=>c.disabled=true);
  try { await task(); } catch(e) {
    $('error').textContent=e.message || String(e); $('error').hidden=false;
    $('error').scrollIntoView({block:'nearest'});
  }
  finally { controls.forEach((c,i)=>c.disabled=states[i]); busy=false; render(); }
}
function clearPlan() { plan=null; $('updates').replaceChildren(); $('apply').hidden=true; }
function preview() {
  const doc=catalog?.documents.find(d=>d.id===selected); $('selected').textContent=doc?.title || 'Выберите приложение выше.';
  $('citation-preview').replaceChildren(); if(!doc)return;
  const text=doc[$('form').value], ident=doc.identifier;
  if(ident && (text===ident || text.startsWith(ident+','))) {
    const strong=document.createElement('strong');strong.textContent=ident;$('citation-preview').append(strong,document.createTextNode(text.slice(ident.length)));
  } else $('citation-preview').append(document.createTextNode(text));
  if($('pinpoint').value.trim())$('citation-preview').append(document.createTextNode(catalog.style.locator_separator+$('pinpoint').value.trim()));
}
function render() {
  $('documents').replaceChildren(); const needle=$('search').value.toLocaleLowerCase();
  for(const doc of catalog?.documents || []) {
    if(!`${doc.identifier} ${doc.title} ${doc.short_title}`.toLocaleLowerCase().includes(needle))continue;
    const button=document.createElement('button');button.textContent=`${doc.full} · ${doc.ready?'готово':'ожидает проверки'}`;button.className=doc.id===selected?'active':'';
    button.disabled=busy;button.onclick=()=>{selected=doc.id;render();};$('documents').append(button);
  }
  $('insert').disabled=$('append').disabled=busy || !selected;
  $('check').disabled=$('send').disabled=busy || !catalog;
  preview();
}
async function loadProject() {
  clearPlan(); selected=''; const id=$('project').value;
  catalog=id?await api('/word/projects/'+id):null;
  if(catalog)$('form').value=catalog.style.name_form;
  render();
}
async function refresh() {
  const prior=$('project').value, projects=await api('/projects');
  $('project').replaceChildren(new Option('Выберите подачу',''));
  projects.forEach(p=>$('project').append(new Option(p.name,p.id)));
  if(projects.some(p=>p.id===prior))$('project').value=prior;
  await loadProject();
}
function showPlan() {
  $('updates').replaceChildren();
  if(!plan.rows.length)$('updates').textContent='Ссылок, вставленных этой панелью, пока нет. Обычные сноски сопоставляйте в основном приложении.';
  for(const row of plan.rows) {
    const article=document.createElement('article'),label=document.createElement('label');
    if(row.status==='update'){const input=document.createElement('input');input.type='checkbox';input.checked=true;input.value=String(row.index);label.append(input);}
    label.append(document.createTextNode(`Сноска ${row.footnote}: ${messages[row.status]}`));article.append(label);
    const old=document.createElement('p');old.textContent=row.old;article.append(old);
    if(row.status==='update'){const next=document.createElement('strong');next.textContent='→ '+row.next;article.append(next);}
    $('updates').append(article);
  }
  $('apply').hidden=!plan.rows.some(r=>r.status==='update');
}
$('project').onchange=()=>run(loadProject);$('refresh').onclick=()=>run(refresh);$('search').oninput=render;
$('form').onchange=preview;$('pinpoint').oninput=preview;
for(const [id,existing] of [['insert',false],['append',true]])$(id).onclick=()=>run(async()=>{
  catalog=await api('/word/projects/'+catalog.id);
  await insertCitation(catalog,selected,$('form').value,$('pinpoint').value,existing);clearPlan();$('status').textContent='Ссылка вставлена. Сохраните документ в Word.';
});
$('check').onclick=()=>run(async()=>{catalog=await api('/word/projects/'+catalog.id);plan=await inspectUpdates(catalog);showPlan();});
$('apply').onclick=()=>run(async()=>{
  const fresh=await api('/word/projects/'+catalog.id),indices=[...$('updates').querySelectorAll('input:checked')].map(i=>Number(i.value));
  const count=await applyUpdates(fresh,plan,indices);catalog=fresh;clearPlan();$('status').textContent=`Обновлено ссылок: ${count}. Сохраните Word.`;
});
$('send').onclick=()=>run(async()=>{
  const docx=await currentDocx();
  await api(`/projects/${catalog.id}/upload?kind=main&name=Main%20document.docx`,docx,true,{'X-Exhibit-Main-Sha':catalog.main_sha});
  catalog=await api('/word/projects/'+catalog.id);
  $('status').textContent='Редакция передана. Откройте подачу, проверьте сопоставления и соберите комплект.';
});
if(!globalThis.Office) { $('status').textContent='Панель открывается как надстройка в Microsoft Word. Настройте подключение по инструкции проекта.'; }
else Office.onReady(info=>{
  if(info.host!==Office.HostType.Word || !Office.context.requirements.isSetSupported('WordApi','1.5')){ $('status').textContent='Нужен Microsoft Word с поддержкой WordApi 1.5. Обновите Microsoft 365.';return; }
  $('workspace').hidden=false;$('status').textContent='Подключено к Word. Выберите подачу.';run(refresh);
});
