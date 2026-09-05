// Small, dependency-free export fixture. The real endpoint uses the backend exporter.
const encoder = new TextEncoder();
function crc32(bytes: Uint8Array) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++)
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function zip(files: Record<string, string>) {
  const parts: Uint8Array[] = [],
    central: Uint8Array[] = [];
  let offset = 0,
    size = 0;
  for (const [name, text] of Object.entries(files)) {
    const filename = encoder.encode(name),
      data = encoder.encode(text),
      crc = crc32(data);
    const header = new Uint8Array(30 + filename.length),
      h = new DataView(header.buffer);
    h.setUint32(0, 0x04034b50, true);
    h.setUint16(4, 20, true);
    h.setUint32(14, crc, true);
    h.setUint32(18, data.length, true);
    h.setUint32(22, data.length, true);
    h.setUint16(26, filename.length, true);
    header.set(filename, 30);
    const entry = new Uint8Array(46 + filename.length),
      e = new DataView(entry.buffer);
    e.setUint32(0, 0x02014b50, true);
    e.setUint16(4, 20, true);
    e.setUint16(6, 20, true);
    e.setUint32(16, crc, true);
    e.setUint32(20, data.length, true);
    e.setUint32(24, data.length, true);
    e.setUint16(28, filename.length, true);
    e.setUint32(42, offset, true);
    entry.set(filename, 46);
    parts.push(header, data);
    central.push(entry);
    offset += header.length + data.length;
    size += entry.length;
  }
  const end = new Uint8Array(22),
    d = new DataView(end.buffer);
  d.setUint32(0, 0x06054b50, true);
  d.setUint16(8, central.length, true);
  d.setUint16(10, central.length, true);
  d.setUint32(12, size, true);
  d.setUint32(16, offset, true);
  const all = [...parts, ...central, end],
    bytes = new Uint8Array(offset + size + end.length);
  let position = 0;
  for (const part of all) {
    bytes.set(part, position);
    position += part.length;
  }
  return bytes;
}
export function fixtureExport(
  rows: (string | number)[][],
  format: "csv" | "xlsx",
) {
  if (format === "csv")
    return new Blob(
      [
        "\ufeff" +
          rows
            .map((row) =>
              row
                .map((value) => {
                  const text = String(value);
                  const safe = /^[=+\-@\t\r]/.test(text) ? "'" + text : text;
                  return '"' + safe.replaceAll('"', '""') + '"';
                })
                .join(","),
            )
            .join("\r\n"),
      ],
      { type: "text/csv;charset=utf-8" },
    );
  const xml = (value: string) =>
    value
      .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  const sheet = rows
    .map(
      (row, index) =>
        '<row r="' +
        (index + 1) +
        '">' +
        row
          .map((value, col) => {
            const ref = String.fromCharCode(65 + col) + (index + 1);
            return typeof value === "number"
              ? `<c r="${ref}"><v>${value}</v></c>`
              : `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${xml(value)}</t></is></c>`;
          })
          .join("") +
        "</row>",
    )
    .join("");
  return new Blob(
    [
      zip({
        "[Content_Types].xml":
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels":
          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Результаты" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml":
          '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' +
          sheet +
          "</sheetData></worksheet>",
      }),
    ],
    {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
  );
}
