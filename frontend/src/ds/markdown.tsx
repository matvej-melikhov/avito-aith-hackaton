// Текст задания приходит как Markdown. Рендерим его сами, без библиотеки и без
// innerHTML: заголовки, списки, таблицы, код, цитаты, жирный, курсив, ссылки.
// Всё, что не распознано, остаётся обычным абзацем.
import { Fragment, type ReactNode } from "react";
import { cx } from "./controls";

export function Markdown({
  text,
  className,
}: {
  text: string;
  className?: string;
}) {
  return <div className={cx("md", className)}>{renderBlocks(text)}</div>;
}

type ListItem = { text: string; indent: number; ordered: boolean };

const HEADING = /^(#{1,6})\s+(.+?)\s*#*\s*$/;
const BULLET = /^(\s*)[-*+]\s+(.*)$/;
const NUMBERED = /^(\s*)\d+[.)]\s+(.*)$/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;

function renderBlocks(text: string): ReactNode[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  const key = () => `b${out.length}`;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    // Блок кода в ограде ```
    if (/^\s*```/.test(line)) {
      const lang = line.trim().slice(3).trim();
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1;
      out.push(
        <pre key={key()} className="md__code" data-lang={lang || undefined}>
          <code>{body.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      const level = heading[1].length;
      const Tag = `h${Math.min(level, 6)}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
      out.push(<Tag key={key()}>{inline(heading[2])}</Tag>);
      i += 1;
      continue;
    }
    if (RULE.test(line)) {
      out.push(<hr key={key()} />);
      i += 1;
      continue;
    }
    if (/^\s*>/.test(line)) {
      const quote: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        quote.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      out.push(
        <blockquote key={key()}>{renderBlocks(quote.join("\n"))}</blockquote>,
      );
      continue;
    }
    if (TABLE_ROW.test(line) && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && TABLE_ROW.test(lines[i])) {
        rows.push(cells(lines[i]));
        i += 1;
      }
      out.push(
        <div key={key()} className="md__scroll">
          <table className="md__table">
            <thead>
              <tr>
                {head.map((c, n) => (
                  <th key={n}>{inline(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, n) => (
                <tr key={n}>
                  {head.map((_, m) => (
                    <td key={m}>{inline(r[m] ?? "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (BULLET.test(line) || NUMBERED.test(line)) {
      const items: ListItem[] = [];
      while (i < lines.length) {
        const b = BULLET.exec(lines[i]);
        const n = b ? null : NUMBERED.exec(lines[i]);
        const m = b ?? n;
        if (!m) {
          // Продолжение пункта с отступом, без маркера.
          if (items.length && /^\s{2,}\S/.test(lines[i])) {
            items[items.length - 1].text += " " + lines[i].trim();
            i += 1;
            continue;
          }
          break;
        }
        items.push({ indent: m[1].length, text: m[2], ordered: !b });
        i += 1;
      }
      out.push(<Fragment key={key()}>{renderList(items)}</Fragment>);
      continue;
    }
    // Абзац: соседние строки склеиваются пробелом.
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !HEADING.test(lines[i]) &&
      !BULLET.test(lines[i]) &&
      !NUMBERED.test(lines[i]) &&
      !/^\s*```/.test(lines[i]) &&
      !/^\s*>/.test(lines[i]) &&
      !RULE.test(lines[i])
    ) {
      para.push(lines[i].trim());
      i += 1;
    }
    if (para.length) out.push(<p key={key()}>{inline(para.join(" "))}</p>);
    else i += 1;
  }
  return out;
}

function cells(row: string): string[] {
  return row
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
}

/** Вложенность списка по отступу: каждый уровень глубже предыдущего пункта. */
function renderList(items: ListItem[]): ReactNode {
  if (!items.length) return null;
  const base = items[0].indent;
  const Tag = items[0].ordered ? "ol" : "ul";
  const nodes: ReactNode[] = [];
  let n = 0;
  while (n < items.length) {
    const item = items[n];
    const children: ListItem[] = [];
    n += 1;
    while (n < items.length && items[n].indent > base) {
      children.push(items[n]);
      n += 1;
    }
    nodes.push(
      <li key={nodes.length}>
        {inline(item.text)}
        {children.length > 0 && renderList(children)}
      </li>,
    );
  }
  return <Tag>{nodes}</Tag>;
}

const INLINE =
  /(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*\s][^*]*\*)|(_[^_\s][^_]*_)|(\[[^\]]+\]\((https?:\/\/[^)\s]+)\))/g;

function inline(text: string): ReactNode {
  const parts: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0;
    if (at > last) parts.push(text.slice(last, at));
    const raw = m[0];
    if (m[1]) parts.push(<code key={k++}>{raw.slice(1, -1)}</code>);
    else if (m[2] || m[3]) parts.push(<b key={k++}>{inline(raw.slice(2, -2))}</b>);
    else if (m[4] || m[5]) parts.push(<i key={k++}>{inline(raw.slice(1, -1))}</i>);
    else if (m[6]) {
      const label = raw.slice(1, raw.indexOf("]("));
      parts.push(
        <a key={k++} href={m[7]} target="_blank" rel="noreferrer">
          {label}
        </a>,
      );
    }
    last = at + raw.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts.length === 1 ? parts[0] : parts;
}
