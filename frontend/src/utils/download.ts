// Generic browser download trigger -- no domain knowledge, reusable
// anywhere a Blob needs to become a saved file.
export function download(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(href);
}

// Flat records -> CSV text. Column order is the first row's own key order,
// so callers control layout by controlling the row shape, not this function.
export function rowsToCsv(rows: Record<string, unknown>[]): string {
  if (!rows.length) return "";
  const cols = Object.keys(rows[0]);
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [cols.map(esc).join(",")];
  for (const r of rows) lines.push(cols.map((c) => esc(r[c])).join(","));
  return lines.join("\n");
}

// Flat records -> an HTML table saved with a .xls extension -- Excel opens
// this natively (it sniffs the <table> markup, not the file extension) and
// it's a well-worn zero-dependency substitute for real .xlsx generation.
// The npm `xlsx` package (SheetJS) was considered and rejected: it carries
// an unpatched high-severity prototype-pollution/ReDoS advisory with no fix
// available, not worth pulling in just to write a file we fully control the
// content of ourselves.
export function rowsToExcelHtml(rows: Record<string, unknown>[]): string {
  if (!rows.length) return "";
  const cols = Object.keys(rows[0]);
  const esc = (v: unknown) =>
    String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const head = `<tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>`;
  const body = rows
    .map((r) => `<tr>${cols.map((c) => `<td>${esc(r[c])}</td>`).join("")}</tr>`)
    .join("");
  return (
    `<html xmlns:o="urn:schemas-microsoft-com:office:office" ` +
    `xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40">` +
    `<head><meta charset="utf-8"><!--[if gte mso 9]><xml><x:ExcelWorkbook><x:ExcelWorksheets>` +
    `<x:ExcelWorksheet><x:Name>Sheet1</x:Name><x:WorksheetOptions>` +
    `<x:DisplayGridlines/></x:WorksheetOptions></x:ExcelWorksheet>` +
    `</x:ExcelWorksheets></x:ExcelWorkbook></xml><![endif]--></head>` +
    `<body><table border="1">${head}${body}</table></body></html>`
  );
}
