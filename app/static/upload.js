// 첨부 업로드: 사진은 휴대폰에서 압축한 뒤 올린다.
// - 저장소 직접 업로드(data-direct="1"): 서버에서 1회용 주소를 받아 R2로 바로 올리고, 폼에는 업로드 결과만 보낸다.
// - 그 외: 압축한 파일로 바꿔서 폼을 그대로 제출한다.
(function () {
  const MAX_SIDE = 2000;      // 긴 변 최대 픽셀
  const QUALITY = 0.8;        // JPEG 품질
  const KEEP_UNDER = 800 * 1024; // 이 크기 이하의 작은 JPEG는 그대로 둔다

  function guessType(file) {
    if (file.type) return file.type;
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    return { heic: 'image/heic', heif: 'image/heif', jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png',
             webp: 'image/webp', mov: 'video/quicktime', mp4: 'video/mp4', pdf: 'application/pdf' }[ext] || '';
  }

  async function decode(file) {
    try {
      return await createImageBitmap(file, { imageOrientation: 'from-image' });
    } catch (e) {
      // 일부 브라우저(사파리의 HEIC 등)는 img 태그로만 읽힌다
      return await new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
        img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('decode')); };
        img.src = url;
      });
    }
  }

  async function compress(file) {
    const type = guessType(file);
    if (!type.startsWith('image/') || type === 'image/gif') return file;
    let img;
    try { img = await decode(file); } catch (e) { return file; } // 못 읽으면 원본 그대로
    const w = img.width, h = img.height;
    if (type === 'image/jpeg' && file.size <= KEEP_UNDER && Math.max(w, h) <= MAX_SIDE) return file;
    const scale = Math.min(1, MAX_SIDE / Math.max(w, h));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(h * scale);
    canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
    if (img.close) img.close();
    const blob = await new Promise(r => canvas.toBlob(r, 'image/jpeg', QUALITY));
    if (!blob) return file;
    // 다시 저장했더니 더 커졌고 원본이 웹에서 바로 보이는 형식이면 원본 유지
    if (blob.size >= file.size && ['image/jpeg', 'image/png', 'image/webp'].includes(type)) return file;
    const name = file.name.replace(/\.[^.]+$/, '') + '.jpg';
    return new File([blob], name, { type: 'image/jpeg', lastModified: file.lastModified });
  }

  async function putWithRetry(url, file, type, tries) {
    for (let i = 1; ; i++) {
      try {
        const r = await fetch(url, { method: 'PUT', headers: { 'Content-Type': type }, body: file });
        if (r.ok) return;
        if (i >= tries) throw new Error('HTTP ' + r.status);
      } catch (e) {
        if (i >= tries) throw e;
      }
      await new Promise(r => setTimeout(r, 1000 * i));
    }
  }

  document.querySelectorAll('form[data-upload]').forEach(form => {
    const input = form.querySelector('input[type=file][name=files]');
    const hidden = form.querySelector('input[name=uploaded]');
    const button = form.querySelector('button.primary');
    const msg = input ? input.dataset : {};
    const label = button ? button.textContent : '';

    const setBusy = text => { if (button) { button.disabled = true; button.textContent = text; } };
    const reset = () => { if (button) { button.disabled = false; button.textContent = label; } if (input) input.disabled = false; };

    form.addEventListener('submit', async ev => {
      if (!input || !input.files.length) { setBusy(msg.saving || label); return; }
      ev.preventDefault();
      try {
        setBusy(msg.compressing);
        const files = [];
        for (const f of input.files) files.push(await compress(f));

        if (form.dataset.direct === '1') {
          const res = await fetch(form.dataset.uploadUrl, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ files: files.map(f => ({ name: f.name, type: guessType(f), size: f.size })) }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw Object.assign(new Error(data.error || msg.failed), { friendly: true });
          const done = [];
          for (let i = 0; i < files.length; i++) {
            setBusy(msg.uploading.replace('{i}', i + 1).replace('{n}', files.length));
            const u = data.uploads[i];
            await putWithRetry(u.url, files[i], u.type, 3);
            done.push({ key: u.key, name: files[i].name });
          }
          hidden.value = JSON.stringify(done);
          input.disabled = true; // 파일은 이미 올렸으므로 폼으로 다시 보내지 않는다
        } else {
          const dt = new DataTransfer();
          files.forEach(f => dt.items.add(f));
          input.files = dt.files;
        }
        setBusy(msg.saving);
        form.submit();
      } catch (e) {
        reset();
        alert(e && e.friendly ? e.message : msg.failed); // 서버가 준 안내 문구만 그대로, 나머지는 공통 안내
      }
    });
  });
})();
