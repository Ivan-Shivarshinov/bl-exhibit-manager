// Presence is announced only by a ready Word host. No document text is sent.
export async function startWordConnection(info, {
  office = globalThis.Office, word = globalThis.Word, fetch = globalThis.fetch,
  location = globalThis.location, schedule = globalThis.setTimeout, cancel = globalThis.clearTimeout,
  onError = () => {}, onReady = () => {},
} = {}) {
  if (!office || info?.host !== office.HostType.Word || location.protocol !== 'https:') return () => {};
  const supported = office.context.requirements.isSetSupported('WordApi', '1.5');
  if (supported) {
    // Confirm the actual Word runtime, without reading or modifying document text.
    await word.run(async context => {context.document.load('saved'); await context.sync();});
  }
  let token = null, timer = null, stopped = false;
  async function request(body, keepalive = false) {
    const response = await fetch('/api/word/connection', {
      method: 'POST', headers: {'X-Exhibit-Local':'1', 'Content-Type':'application/json'},
      body: JSON.stringify(body), keepalive, signal: keepalive ? undefined : AbortSignal.timeout(4000),
    });
    if (!response.ok) {
      if (response.status === 400) token = null; // Expired after suspension or server restart.
      throw new Error('Не удалось подтвердить связь панели с приложением. Проверяем повторно…');
    }
    return response.json();
  }
  async function update() {
    if (stopped) return;
    try {
      const value = await request(token ? {action:'ping', token} : {action:'open', host:'Word', supported});
      if (value.token) token = value.token;
      if (!stopped) onReady();
      else if (token) await request({action:'close', token}, true);
    } catch (error) {if (!stopped) onError(error.message);}
    finally {if (!stopped) timer = schedule(update, 15000);}
  }
  await update();
  return () => {
    stopped = true; cancel(timer);
    if (token) request({action:'close', token}, true).catch(() => {});
  };
}
