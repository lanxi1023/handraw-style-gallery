(() => {
  const gate = document.querySelector('#remoteGate'), form = document.querySelector('#remoteLogin');
  const status = document.querySelector('#remoteStatus'), submit = document.querySelector('#remoteSubmit');
  const shell = document.querySelector('main'), nav = document.querySelector('header nav');
  let loaded = false;
  // URL fragments are not sent to Pages, the tunnel, or HTTP access logs.
  let quickAccess = null;
  function readQuickAccess() {
    const value = new URLSearchParams(location.hash.slice(1)).get('access');
    if (value === null) return false;
    quickAccess = value;
    history.replaceState(null, '', location.pathname + location.search);
    return true;
  }
  readQuickAccess();
  const authChannel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('studio-auth') : null;
  if (authChannel) authChannel.onmessage = event => {
    if (loaded && event.data?.userId !== window.STUDIO_USER?.id) location.reload();
  };
  const loadingScripts = new Map();
  const script = src => {
    if (loadingScripts.has(src)) return loadingScripts.get(src);
    const pending = new Promise((resolve, reject) => {
      const s = document.createElement('script'); s.src = src;
      if (src.startsWith(window.STUDIO_REMOTE.apiOrigin)) s.crossOrigin = 'use-credentials';
      s.onload = resolve;
      s.onerror = () => {loadingScripts.delete(src); s.remove(); reject(Error('素材载入失败，请重新连接。'));};
      document.body.append(s);
    });
    loadingScripts.set(src, pending);
    return pending;
  };
  async function reveal(user) {
    if (!user?.id) throw Error('工作台正在更新，请刷新网页后登录。');
    if ((loaded || loadingScripts.size) && window.STUDIO_USER?.id !== user.id) { location.reload(); return; }
    window.STUDIO_USER = user;
    document.querySelector('#remoteAccount').textContent = '账号 ' + user.username;
    if (!loaded) {
      status.textContent = '正在载入素材和作品…';
      await script(studioMedia('/catalog/data.js'));
      await script(studioMedia('/catalog/extra-data.js'));
      await script('./static/redesign.js?v=20261103quickLink2');
      loaded = true;
    }
    gate.hidden = true; shell.hidden = false; nav.hidden = false;
    document.querySelector('#remoteLogout').hidden = false;
    document.querySelector('#remoteConnection').textContent = '已连接生图电脑';
  }
  async function connect() {
    submit.disabled = true;
    status.textContent = '正在连接生图电脑…';
    try {
      if (quickAccess !== null) {
        status.textContent = '正在通过专用链接进入工作台…';
        const r = await studioFetch('/auth/shortcut', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token:quickAccess})});
        const data = await r.json();
        if (!r.ok) throw Error(data.detail || '快捷链接暂时无法使用，请重试或使用账号登录。');
        quickAccess = null; studioSetCSRF(data.csrf);
        authChannel?.postMessage({userId:data.user?.id}); await reveal(data.user);
        return;
      }
      const r = await studioFetch('/auth/session');
      if (!r.ok) throw Error('远程连接尚未就绪，请在生图电脑上完成配置。');
      const data = await r.json(); studioSetCSRF(data.csrf);
      if (data.authenticated) await reveal(data.user); else status.textContent = '输入你的账号和密码，仅查看自己的作品与素材。';
    } catch (error) {status.textContent = error.message;}
    finally {submit.disabled = false;}
  }
  form.onsubmit = async event => {
    event.preventDefault(); submit.disabled = true;
    try {
      const r = await studioFetch('/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: form.elements.username.value.trim(), password: form.elements.password.value})});
      const data = await r.json();
      if (!r.ok) throw Error(data.detail || '登录失败，请稍后重试。');
      form.elements.password.value = ''; quickAccess = null; studioSetCSRF(data.csrf);
      authChannel?.postMessage({userId: data.user?.id}); await reveal(data.user);
    } catch (error) {status.textContent = error.message;}
    finally {submit.disabled = false;}
  };
  document.querySelector('#remoteReconnect').onclick = connect;
  // Reopening a bookmark in this tab may change only its fragment, without a reload.
  window.addEventListener('hashchange', () => { if (readQuickAccess()) connect(); });
  document.querySelector('#remoteLogout').onclick = async () => {
    try {const r=await studioFetch('/auth/logout', {method: 'POST'}); if(!r.ok)throw Error('退出暂时未完成，请重试。'); authChannel?.postMessage({userId: null}); location.reload();}
    catch (error) {document.querySelector('#remoteConnection').textContent = error.message;}
  };
  window.addEventListener('studio:connection', event => {
    if (event.detail === 'account_changed' || (event.detail === 'login' && loaded)) { location.reload(); return; }
    document.querySelector('#remoteConnection').textContent = event.detail === 'online' ? '已连接生图电脑' : event.detail === 'login' ? '登录已过期，请重新登录' : '生图电脑暂时离线';
    if (event.detail === 'login') {
      shell.hidden = true; nav.hidden = true; gate.hidden = false; status.textContent = '登录已过期，请重新登录。';
    }
  });
  if (window.STUDIO_REMOTE.enabled) connect();
  else {
    status.textContent = '远程入口配置中。连接开通后，可在这里登录并使用本机号池。';
    document.querySelector('#remoteConnection').textContent = '远程连接待开通';
    form.querySelectorAll('input, button').forEach(element => element.disabled = true);
  }
})();
