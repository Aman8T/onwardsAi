/**
 * Copying and sharing, with the fallbacks a real browser needs: the async clipboard where there is one, a hidden
 * textarea and `execCommand('copy')` where there is not (an older WebView, a page without a secure context), and the
 * system share sheet only where the browser offers it. None of it throws; each says whether it worked.
 */

/** Put `text` on the clipboard. True when it got there. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // refused (permissions, an unfocused document): try the older way
  }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.setAttribute('aria-hidden', 'true');
    area.style.position = 'fixed';
    area.style.top = '0';
    area.style.left = '-9999px';
    const keep = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(area);
    keep?.focus({ preventScroll: true });
    return ok;
  } catch {
    return false;
  }
}

export interface ShareData {
  title: string;
  text?: string;
  url: string;
}

/** Whether this browser can open its share sheet for `data`. */
export function canShare(data: ShareData): boolean {
  if (typeof navigator === 'undefined' || typeof navigator.share !== 'function') return false;
  try {
    return typeof navigator.canShare === 'function' ? navigator.canShare(data) : true;
  } catch {
    return false;
  }
}

/** Open the share sheet. `'cancelled'` when the person closed it; `'failed'` when the browser refused. */
export async function shareData(data: ShareData): Promise<'shared' | 'cancelled' | 'failed'> {
  try {
    await navigator.share(data);
    return 'shared';
  } catch (err) {
    return err instanceof DOMException && err.name === 'AbortError' ? 'cancelled' : 'failed';
  }
}
