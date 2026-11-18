/* Browser transport uses the same API surface as the optional desktop window. */
(() => {
  if (new URLSearchParams(location.search).get('desktop') === '1') return;
  if (window.pywebview && window.pywebview.api) return;
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.tb-btn').forEach(button => { button.style.display = 'none'; });
  });
  let bootstrap;
  const ready = fetch('/api/bootstrap', {cache: 'no-store'}).then(response => {
    if (!response.ok) throw new Error('面板连接失败');
    return response.json();
  }).then(value => { bootstrap = value; return value; });
  const api = new Proxy({}, {
    get: (_target, method) => {
      if (method === 'then') return undefined;
      if (String(method).startsWith('win_')) return async () => ({ok: true});
      return async (...arguments_) => {
        await ready;
        const response = await fetch('/api/' + encodeURIComponent(method), {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'X-Panel-Token': bootstrap.session_token},
          body: JSON.stringify(arguments_)
        });
        const result = await response.json();
        if (result.ok && result.download) {
          const link = document.createElement('a');
          link.href = result.download;
          link.download = result.filename || '';
          document.body.append(link);
          link.click();
          link.remove();
        }
        return result;
      };
    }
  });
  window.pywebview = {api};
  ready.then(() => {
    document.dispatchEvent(new Event('panelready'));
    window.dispatchEvent(new Event('pywebviewready'));
  }).catch(error => console.error(error.message));
})();
