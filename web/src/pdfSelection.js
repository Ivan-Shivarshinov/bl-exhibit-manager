export function parsePages(value, count) {
  const result=[];
  for (const token of value.split(',').map(x=>x.trim())) {
    const match = /^(\d+)(?:-(\d+))?$/.exec(token);
    if (!match) throw new Error('Укажите страницы, например 1, 3-5.');
    const a=Number(match[1]), b=Number(match[2] || match[1]);
    if (a<1 || b<a || b>count) throw new Error('Диапазон страниц вне документа.');
    for(let n=a;n<=b;n++) result.push({page:n});
  }
  return result;
}
