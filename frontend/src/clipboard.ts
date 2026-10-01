/** Uses a modal-local fallback so copying also works over LAN HTTP. */
export async function copyText(text: string, container: HTMLElement | null = null): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
  } catch { /* Some browsers disable the Clipboard API on an insecure origin. */ }
  const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const field = document.createElement('textarea');
  field.value = text; field.setAttribute('aria-hidden', 'true'); field.tabIndex = -1;
  Object.assign(field.style, { position: 'fixed', opacity: '0', width: '1px', height: '1px', top: '0', left: '0' });
  (container || document.body).appendChild(field);
  let copied = false;
  try { field.focus({ preventScroll: true }); field.select(); copied = document.execCommand('copy'); }
  finally { field.remove(); active?.focus({ preventScroll: true }); }
  if (!copied) throw new Error('浏览器未允许复制，请选中下方文字手动复制');
}
