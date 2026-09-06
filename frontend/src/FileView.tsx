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

/* Значки типов и скачивания — Material Symbols Sharp, Google, Apache 2.0.
   Тот же набор, что у разделов панели: заливка, прямой угол.            */
const ICON_PATHS: Record<FileKind, string> = {
  // link
  link: "M440-280H280q-83 0-141.5-58.5T80-480q0-83 58.5-141.5T280-680h160v80H280q-50 0-85 35t-35 85q0 50 35 85t85 35h160v80ZM320-440v-80h320v80H320Zm200 160v-80h160q50 0 85-35t35-85q0-50-35-85t-85-35H520v-80h160q83 0 141.5 58.5T880-480q0 83-58.5 141.5T680-280H520Z",
  // description
  text: "M320-240h320v-80H320v80Zm0-160h320v-80H320v80ZM160-80v-800h400l240 240v560H160Zm360-520v-200H240v640h480v-440H520ZM240-800v200-200 640-640Z",
  // description
  markdown:
    "M320-240h320v-80H320v80Zm0-160h320v-80H320v80ZM160-80v-800h400l240 240v560H160Zm360-520v-200H240v640h480v-440H520ZM240-800v200-200 640-640Z",
  // picture_as_pdf
  pdf: "M360-460h40v-80h60l20-20v-80l-20-20H360v200Zm40-120v-40h40v40h-40Zm120 120h100l20-20v-160l-20-20H520v200Zm40-40v-120h40v120h-40Zm120 40h40v-80h40v-40h-40v-40h40v-40h-80v200ZM240-240v-640h640v640H240Zm80-80h480v-480H320v480ZM80-80v-640h80v560h560v80H80Zm240-720v480-480Z",
  // image
  image:
    "M240-280h480L570-480 450-320l-90-120-120 160ZM120-120v-720h720v720H120Zm80-80h560v-560H200v560Zm0 0v-560 560Z",
  // description
  doc: "M320-240h320v-80H320v80Zm0-160h320v-80H320v80ZM160-80v-800h400l240 240v560H160Zm360-520v-200H240v640h480v-440H520ZM240-800v200-200 640-640Z",
  // table_chart
  sheet:
    "M120-120v-720h720v720H120Zm80-520h560v-120H200v120Zm0 440h100v-360H200v360Zm460 0h100v-360H660v360Zm-280 0h200v-360H380v360Z",
  // folder_zip
  archive:
    "M640-480v-80h80v80h-80Zm0 80h-80v-80h80v80Zm0 80v-80h80v80h-80ZM447-640l-80-80H160v480h400v-80h80v80h160v-400H640v80h-80v-80H447ZM80-160v-640h320l80 80h400v560H80Zm80-80v-480 480Z",
  // draft
  file: "M160-80v-800h400l240 240v560H160Zm360-520v-200H240v640h480v-440H520ZM240-800v200-200 640-640Z",
};

const DOWNLOAD_PATH =
  "M480-320 280-520l56-58 104 104v-326h80v326l104-104 56 58-200 200ZM160-160v-200h80v120h480v-120h80v200H160Z";

function Glyph({ path }: { path: string }) {
  return (
    <svg
      className="fileicon"
      viewBox="0 -960 960 960"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d={path} />
    </svg>
  );
}

/** Значок типа с доступной подписью. */
export function FileIcon({ kind }: { kind: FileKind }) {
  return (
    <span className="fileicon-wrap" role="img" aria-label={KIND_LABEL[kind]}>
      <Glyph path={ICON_PATHS[kind]} />
    </span>
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
        <Glyph path={DOWNLOAD_PATH} />
      </Btn>
      {open && (
        <FilePreview name={name} url={href} close={() => setOpen(false)} />
      )}
    </>
  );
}
