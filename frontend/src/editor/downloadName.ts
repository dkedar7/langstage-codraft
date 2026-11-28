/** The file name a Content-Disposition header suggests: RFC 5987 `filename*` first, so a
 *  title in any script keeps its letters (gh #79), then the ASCII `filename`. */
export function downloadName(disp: string): string | null {
  const star = disp.match(/filename\*\s*=\s*UTF-8''([^;]+)/i)?.[1];
  if (star) {
    try {
      return decodeURIComponent(star.trim());
    } catch {
      // a malformed escape: fall back to the plain name
    }
  }
  return disp.match(/filename\s*=\s*"([^"]+)"/i)?.[1] || null;
}
