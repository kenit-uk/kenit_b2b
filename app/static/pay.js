// 결제 수정 창: 총액 − 계약금 − 중도금 = 잔금을 바로 보여 주고, 넘치면 저장을 막는다
document.querySelectorAll('.pay-form').forEach(f => {
  const n = el => parseFloat((el.value || '').replace(/[£,\s]/g, '')) || 0;
  const out = f.querySelector('[data-balance]');
  const fmt = v => '£' + v.toLocaleString('en-GB', {maximumFractionDigits: 2});
  const sync = () => {
    const total = f.total.value.trim() ? n(f.total) : null;
    const bal = total === null ? null : total - n(f.deposit_amount) - n(f.interim_amount);
    out.textContent = bal === null ? '—' : fmt(bal);
    out.classList.toggle('bad-text', bal !== null && bal < 0);
  };
  ['total', 'deposit_amount', 'interim_amount'].forEach(k => f[k].addEventListener('input', sync));
  sync();
});
