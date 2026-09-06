/* Работа студента как файл: иконка по типу, просмотр в модалке и скачивание.
   Ссылка на репозиторий остаётся ссылкой, у неё только «Открыть».            */
import { useEffect, useState } from "react";
import { Btn, BtnRow, Modal, Skel } from "./ds";
import { safeUrl } from "./ui";

export type FileKind =
  | "link"
  | "text"
  | "markdown"
  | "pdf"
  | "image"
  | "doc"
  | "sheet"
  | "archive"
  | "file";

const BY_EXT: Record<string, FileKind> = {
  md: "markdown",
  markdown: "markdown",
  txt: "text",
  log: "text",
  csv: "text",
  json: "text",
  yaml: "text",
  yml: "text",
  go: "text",
  py: "text",
  ts: "text",
  tsx: "text",
  js: "text",
  jsx: "text",
  sql: "text",
  sh: "text",
  pdf: "pdf",
  png: "image",
  jpg: "image",
  jpeg: "image",
  gif: "image",
  webp: "image",
  svg: "image",
  doc: "doc",
  docx: "doc",
  rtf: "doc",
  odt: "doc",
  xls: "sheet",
  xlsx: "sheet",
  ods: "sheet",
  zip: "archive",
  gz: "archive",
  tar: "archive",
};

/** Тип по имени файла. Ссылка распознаётся по схеме, а не по расширению. */
export function fileKind(name: string | null | undefined): FileKind {
  const value = (name ?? "").trim();
  if (!value) return "file";
  if (/^https?:\/\//i.test(value)) return "link";
  const ext = value.split("?")[0].split("#")[0].split(".").pop()?.toLowerCase();
  return (ext && BY_EXT[ext]) || "file";
}

const KIND_LABEL: Record<FileKind, string> = {
  link: "Ссылка",
  text: "Текстовый файл",
  markdown: "Файл Markdown",
  pdf: "Файл PDF",
  image: "Изображение",
  doc: "Документ",
  sheet: "Таблица",
  archive: "Архив",
  file: "Файл",
};

/* Иконки нарисованы разметкой в одном ключе с лупой поиска: контур в
   currentColor, сетка 16, без заливки.                                     */
function Glyph({ kind }: { kind: FileKind }) {
  const common = {
    className: "fileicon",
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.4,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  if (kind === "link")
    return (
      <svg {...common}>
        <path d="M6.6 9.4a2.6 2.6 0 0 0 3.7 0l2-2a2.6 2.6 0 0 0-3.7-3.7l-.8.8" />
        <path d="M9.4 6.6a2.6 2.6 0 0 0-3.7 0l-2 2a2.6 2.6 0 0 0 3.7 3.7l.8-.8" />
      </svg>
    );
  if (kind === "image")
    return (
      <svg {...common}>
        <rect x="2" y="3" width="12" height="10" rx="1.5" />
        <circle cx="5.6" cy="6.4" r="1" />
        <path d="M3 11.4 6.4 8.6l2.2 1.8L10.8 8 13 10.2" />
      </svg>
    );
  if (kind === "archive")
    return (
      <svg {...common}>
        <rect x="3" y="2" width="10" height="12" rx="1.5" />
        <path d="M7.4 2v2M8.6 4v2M7.4 6v2M8.6 8v2" />
      </svg>
    );
  if (kind === "sheet")
    return (
      <svg {...common}>
        <rect x="2.5" y="2.5" width="11" height="11" rx="1.5" />
        <path d="M2.5 6.5h11M6.5 6.5v7" />
      </svg>
    );
  /* Лист с загнутым углом: общая основа для текста, markdown, pdf и docx. */
  const mark =
    kind === "pdf"
      ? "PDF"
      : kind === "markdown"
        ? "MD"
        : kind === "doc"
          ? "W"
          : "";
  return (
    <svg {...common}>
      <path d="M9 1.8H4.6a1.4 1.4 0 0 0-1.4 1.4v9.6a1.4 1.4 0 0 0 1.4 1.4h6.8a1.4 1.4 0 0 0 1.4-1.4V5.4z" />
      <path d="M9 1.8v3.6h3.8" />
      {mark ? (
        <text
          x="8"
          y="12"
          textAnchor="middle"
          stroke="none"
          fill="currentColor"
          fontSize="4.2"
          fontWeight="600"
        >
          {mark}
        </text>
      ) : (
        <path d="M5.4 8.6h5.2M5.4 11h3.4" />
      )}
    </svg>
  );
}

/** Иконка типа с доступной подписью. */
export function FileIcon({ kind }: { kind: FileKind }) {
  return (
    <span className="fileicon-wrap" role="img" aria-label={KIND_LABEL[kind]}>
      <Glyph kind={kind} />
    </span>
  );
}

function DownloadGlyph() {
  return (
    <svg
      className="fileicon"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M8 2.6v7.2M5.2 7.2 8 10l2.8-2.8M3 12.4h10" />
    </svg>
  );
}

/** Просмотр файла поверх страницы: по типу — текст, PDF или картинка. */
export function FilePreview({
  name,
  url,
  close,
}: {
  name: string;
  url: string;
  close: () => void;
}) {
  const kind = fileKind(name);
  const textual = kind === "text" || kind === "markdown";
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!textual) return;
    let alive = true;
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.text();
      })
      .then((body) => alive && setText(body))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [textual, url]);
  return (
    <Modal
      title={name}
      close={close}
      wide
      foot={
        <BtnRow>
          <Btn variant="quiet" onClick={close}>
            Закрыть
          </Btn>
          <Btn variant="pri" href={url} download={name}>
            Скачать
          </Btn>
        </BtnRow>
      }
    >
      {textual ? (
        failed ? (
          <p className="small dim">
            Не удалось прочитать файл в браузере. Скачайте его, чтобы
            посмотреть.
          </p>
        ) : text === null ? (
          <Skel lines={4} label="Читаем файл…" />
        ) : (
          <pre className="file-preview__text">{text}</pre>
        )
      ) : kind === "pdf" ? (
        <iframe className="file-preview__frame" src={url} title={name} />
      ) : kind === "image" ? (
        <img className="file-preview__img" src={url} alt={name} />
      ) : (
        <p className="small dim">
          {KIND_LABEL[kind]} не показывается в браузере. Скачайте файл, чтобы
          посмотреть.
        </p>
      )}
    </Modal>
  );
}

/**
 * Строка работы: иконка типа и действия. У ссылки одно действие «Открыть»,
 * у файла — просмотр в модалке и скачивание.
 */
export function FileActions({
  name,
  url,
  size = "s",
}: {
  name: string;
  url: string | null | undefined;
  size?: "s" | "l";
}) {
  const kind = fileKind(name);
  const [open, setOpen] = useState(false);
  const href = url ? safeUrl(url) : undefined;
  if (!href) return null;
  if (kind === "link")
    return (
      <Btn
        size={size}
        variant="quiet"
        href={href}
        target="_blank"
        rel="noreferrer"
      >
        Открыть ↗
      </Btn>
    );
  return (
    <>
      <Btn size={size} variant="quiet" onClick={() => setOpen(true)}>
        Открыть
      </Btn>
      <Btn
        size={size}
        variant="quiet"
        icon
        href={href}
        download={name}
        aria-label={`Скачать ${name}`}
        title="Скачать"
      >
        <DownloadGlyph />
      </Btn>
      {open && (
        <FilePreview name={name} url={href} close={() => setOpen(false)} />
      )}
    </>
  );
}
