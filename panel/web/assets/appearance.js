/* Editor shared by the installable panel and the local trial. */
(() => {
  'use strict';

  window.applyProfileAppearance = payload => {
    const profiles = {me: payload.me, peer: payload.peer};
    window.panelDisplayNames = {me: profiles.me?.display_name, peer: profiles.peer?.display_name};
    document.querySelectorAll('.hist-row[data-profile-role]').forEach(row => {
      const profile = profiles[row.dataset.profileRole];
      if (!profile) return;
      const name = row.querySelector('.hist-whom b');
      const avatar = row.querySelector('.hist-av');
      if (name) name.textContent = profile.display_name;
      if (avatar && profile.avatar && avatar.getAttribute('src') !== profile.avatar) {
        avatar.hidden = false;
        avatar.src = profile.avatar;
      }
    });
    const rail = document.querySelector('#meAvatar');
    const avatar = rail?.querySelector('img');
    if (rail) rail.title = '我的资料';
    if (avatar && profiles.me?.display_name) avatar.alt = profiles.me.display_name;
  };

  window.createProfileAppearance = ({anchor, onSaved, onEdit = () => {}}) => {
    const section = document.createElement('div');
    section.className = 'appearance-editor';
    section.hidden = true;
    section.innerHTML = `
      <div class="appearance-actions">
        <button id="profileEdit" type="button">编辑资料</button>
      </div>
      <form id="profileForm" hidden>
        <div class="appearance-avatar">
          <img id="profileAvatarPreview" alt="">
          <div>
            <button id="profileAvatarPick" type="button">
              <svg width="15" height="15" aria-hidden="true"><use href="/assets/画册图标.svg#image-plus"></use></svg>更换头像
            </button>
            <input id="profileAvatarFile" type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/bmp" hidden>
          </div>
        </div>
        <label>昵称<input id="profileNickname" type="text" maxlength="40" required autocomplete="off"></label>
        <label>个性签名<textarea id="profileBio" maxlength="160" rows="3"></textarea></label>
        <div class="appearance-actions">
          <button id="profileReset" type="button" hidden>恢复默认</button>
          <button id="profileCancel" type="button">取消</button>
          <button id="profileSave" type="submit" class="appearance-primary">保存</button>
        </div>
      </form>
      <p id="profileAppearanceHint" role="status" hidden></p>`;
    anchor.after(section);
    const find = id => section.querySelector('#profile' + id);
    const form = find('Form'), edit = find('Edit'), file = find('AvatarFile'), pick = find('AvatarPick');
    const name = find('Nickname'), bio = find('Bio'), preview = find('AvatarPreview');
    const save = find('Save'), cancel = find('Cancel'), reset = find('Reset'), hint = find('AppearanceHint');
    const maxUpload = 10 * 1024 * 1024;
    let role, current, stored = false, pending = false, sequence = 0, selected, previewUrl;

    function say(text) {
      hint.textContent = text;
      hint.hidden = !text;
    }
    function setBusy(busy) {
      pending = busy;
      for (const control of [name, bio, pick, reset, cancel, save]) control.disabled = busy;
    }
    function closeForm() {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = selected = undefined;
      file.value = '';
      preview.removeAttribute('src');
      edit.hidden = false;
      form.hidden = true;
      onEdit(false);
      setBusy(false);
    }
    function hide() {
      sequence++;
      section.hidden = true;
      closeForm();
      say('');
    }
    function show(next, payload) {
      hide();
      role = next;
      current = payload.profile;
      stored = payload.appearance_customized?.[role] === true;
      section.hidden = false;
    }
    function cancelEdit() {
      if (form.hidden) return false;
      if (!pending) {
        closeForm();
        say('');
        edit.focus();
      }
      return true;
    }
    edit.addEventListener('click', () => {
      name.value = current.display_name || '';
      bio.value = current.signature || '';
      preview.src = current.avatar;
      reset.hidden = !stored;
      edit.hidden = true;
      say('');
      form.hidden = false;
      onEdit(true);
      name.focus();
    });
    cancel.addEventListener('click', cancelEdit);
    pick.addEventListener('click', () => file.click());
    file.addEventListener('change', () => {
      const chosen = file.files[0];
      file.value = '';
      if (!chosen) return;
      if (chosen.size <= 0 || chosen.size > maxUpload) {
        say('请选择一张不超过 10 MB 的图片。');
        return;
      }
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      selected = chosen;
      previewUrl = URL.createObjectURL(chosen);
      preview.src = previewUrl;
      say('');
    });

    async function update(resetRequested = false) {
      if (pending) return;
      const revision = sequence, target = role;
      setBusy(true);
      say('');
      try {
        const patch = resetRequested ? {reset: true} : {};
        if (!resetRequested) {
          if (name.value !== (current.display_name || '')) patch.display_name = name.value;
          if (bio.value !== (current.signature || '')) patch.signature = bio.value;
          if (selected) {
            patch.avatar = await new Promise((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () => resolve(String(reader.result).split(',', 2)[1]);
              reader.onerror = reject;
              reader.readAsDataURL(selected);
            });
            if (revision !== sequence) return;
          }
        }
        if (!Object.keys(patch).length) {
          closeForm();
          say('资料没有改动。');
          edit.focus();
          return;
        }
        const result = await window.pywebview.api.profile_appearance(target, patch);
        if (!result?.ok) throw new Error(result?.error);
        if (typeof 拉资料 === 'function') await 拉资料();
        if (revision === sequence) await onSaved(target, resetRequested ? '已恢复默认。' : '已保存。');
      } catch (error) {
        if (revision === sequence) say(error.message || '这次没能保存，请再试一次。');
      } finally {
        if (revision === sequence) setBusy(false);
      }
    }
    form.addEventListener('submit', event => {
      event.preventDefault();
      update();
    });
    reset.addEventListener('click', () => update(true));
    return {hide, show, cancelEdit, say, focusEdit: () => edit.focus()};
  };
})();
