// 현장 도착 / 오늘 작업 끝: 누르는 순간에만 위치를 한 번 읽어 함께 보낸다 (백그라운드 추적 없음).
// 위치를 허용하지 않거나 10초 안에 못 읽으면 위치 없이 보낸다.
// 사진 올리기(upload.js)와 같은 폼에서도 쓰므로, 먼저 위치를 읽고 → 다시 제출해 upload.js가 이어받게 한다.
document.querySelectorAll('form.arrive-form, form.finish-form').forEach(form => {
  form.addEventListener('submit', ev => {
    if (form.dataset.located === '1' || !navigator.geolocation) return;   // 위치를 읽었으면 다음 단계로
    ev.preventDefault();
    ev.stopImmediatePropagation();                                        // 위치를 읽기 전에는 upload.js가 시작하지 않게
    const btn = ev.submitter || form.querySelector('button.primary, button.dark');
    if (btn) btn.disabled = true;
    const go = () => {
      form.dataset.located = '1';
      if (btn) btn.disabled = false;
      form.requestSubmit(btn && btn.form === form ? btn : undefined);
    };
    navigator.geolocation.getCurrentPosition(p => {
      form.querySelector('[name=lat]').value = p.coords.latitude;
      form.querySelector('[name=lng]').value = p.coords.longitude;
      form.querySelector('[name=accuracy]').value = p.coords.accuracy;
      go();
    }, go, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
  }, true);   // 캡처 단계: upload.js보다 먼저
});
