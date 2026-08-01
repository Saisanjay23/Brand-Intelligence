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
