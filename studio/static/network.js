/* Shared by the loopback studio and the separately hosted HTTPS frontend. */
(() => {
  const config = window.STUDIO_REMOTE;
  const origin = config?.apiOrigin || '';
  let csrf = '';
  const nativeFetch = window.fetch.bind(window);
  window.studioMedia = value => {
    const path = String(value || '');
    return origin && /^\/(api|auth|catalog|costume|poses|styles|uploads)\//.test(path) ? origin + path : path;
  };
  window.studioFetch = async (input, options = {}) => {
    const headers = new Headers(options.headers || {});
    if (origin && csrf && !['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase())) headers.set('X-Studio-CSRF', csrf);
    let response;
    try {
      response = await nativeFetch(window.studioMedia(input), {...options, headers, credentials: origin ? 'include' : 'same-origin'});
    } catch (error) {
      if (origin) window.dispatchEvent(new CustomEvent('studio:connection', {detail: 'offline'}));
      throw new Error(origin ? '生图电脑暂时无法连接，请检查电脑和远程连接是否在线。' : error.message);
    }
    if (origin && response.status === 401) window.dispatchEvent(new CustomEvent('studio:connection', {detail: 'login'}));
    else if (origin && response.ok) window.dispatchEvent(new CustomEvent('studio:connection', {detail: 'online'}));
    return response;
  };
  window.studioSetCSRF = value => {csrf = value || '';};
  if (origin) document.addEventListener('click', async event => {
    const link = event.target.closest('a[download]');
    if (!link || !link.getAttribute('href')?.startsWith(origin + '/api/')) return;
    event.preventDefault();
    try {
      const r = await window.studioFetch(link.href);
      if (!r.ok) throw Error('图片暂时无法下载，请稍后重试。');
      const objectUrl = URL.createObjectURL(await r.blob());
      const a = document.createElement('a'); a.href = objectUrl; a.download = link.download;
      document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
    } catch (error) {document.querySelector('#notice').textContent = error.message;}
  });
})();
