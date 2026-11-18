/* Shared local import controls for the existing player. */
(() => {
  'use strict';

  function mount() {
    const anchor = document.querySelector('#stagePane .st-track');
    if (!anchor || anchor.querySelector('.song-import')) return;
    const controls = document.createElement('div');
    controls.className = 'song-import';
    controls.innerHTML = `
      <button type="button" id="songImportOpen" title="导入本机歌曲">
        <svg width="16" height="16" aria-hidden="true"><use href="/assets/画册图标.svg#download"></use></svg>
        导入歌曲
      </button>
      <input type="file" id="songImportFiles" accept=".mp3,.wav,.m4a,.ogg,.flac,.opus" multiple hidden>
      <span id="songImportStatus" role="status"></span>`;
    anchor.append(controls);
    const button = controls.querySelector('button'), picker = controls.querySelector('input');
    const hint = controls.querySelector('[role=status]');
    const sizeLimit = 32 * 1024 * 1024;

    function playable(file) {
      const clip = new Audio();
      const url = URL.createObjectURL(file);
      return new Promise(resolve => {
        function finish(ok) {
          clearTimeout(timer);
          clip.onloadedmetadata = clip.onerror = null;
          clip.removeAttribute('src');
          clip.load();
          URL.revokeObjectURL(url);
          resolve(ok);
        }
        const timer = setTimeout(() => finish(false), 8000);
        clip.onloadedmetadata = () => finish(Number.isFinite(clip.duration) && clip.duration > 0);
        clip.onerror = () => finish(false);
        clip.preload = 'metadata';
        clip.src = url;
      });
    }

    function toBase64(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(',', 2)[1]);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
    }

    button.addEventListener('click', () => picker.click());
    picker.addEventListener('change', async () => {
      const files = [...picker.files];
      picker.value = '';
      if (!files.length || button.disabled) return;
      if (files.length > 10) {
        hint.textContent = '一次最多导入 10 首。';
        return;
      }
      const api = window.pywebview?.api;
      if (typeof api?.song_import !== 'function') {
        hint.textContent = '本机歌单暂时不可用。';
        return;
      }
      button.disabled = true;
      let saved = 0;
      const failed = [];
      for (const [index, file] of files.entries()) {
        hint.textContent = `正在导入 ${index + 1}/${files.length}…`;
        try {
          if (file.size > sizeLimit) throw new Error('单首歌曲不能超过 32 MB。');
          if (!(await playable(file))) throw new Error('这首歌在当前播放器中无法读取。');
          const result = await api.song_import(file.name, await toBase64(file));
          if (!result?.ok) throw new Error(result?.error || '歌曲未保存。');
          saved++;
        } catch (error) {
          failed.push(error.message || '音频文件无法读取。');
        }
      }
      try {
        if (saved) await 加载歌单(true);
        hint.textContent = [
          saved && `已导入 ${saved} 首。`,
          failed.length && (files.length === 1 ? failed[0] : `${failed.length} 首未导入：${failed[0]}`),
        ].filter(Boolean).join(' ');
      } catch {
        hint.textContent = '歌曲已保存，重新打开后可查看。';
      } finally {
        button.disabled = false;
      }
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
