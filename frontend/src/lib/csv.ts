/**
 * RFC 4180 compliant CSV export helper.
 */
export function exportToCsv(
  filename: string,
  headers: string[],
  rows: (string | number | boolean | null | undefined)[][],
) {
  function escapeCell(val: string | number | boolean | null | undefined): string {
    if (val === null || val === undefined) return "";
    const str = String(val);
    if (str.includes(",") || str.includes('"') || str.includes("\n") || str.includes("\r")) {
      return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
  }

  const csvLines: string[] = [
    headers.map(escapeCell).join(","),
    ...rows.map((row) => row.map(escapeCell).join(",")),
  ];

  const blob = new Blob(["\uFEFF" + csvLines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.setAttribute("href", url);
  link.setAttribute("download", filename.endsWith(".csv") ? filename : `${filename}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
