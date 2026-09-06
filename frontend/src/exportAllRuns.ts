import { WorkspaceClient, type W } from "./api/workspace";
import { exportFile } from "./exportFile";

export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [],
    cell = "",
    quoted = false;
  text = text.replace(/^\ufeff/, "");
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === '"') {
      if (quoted && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else quoted = !quoted;
    } else if (ch === "," && !quoted) {
      row.push(cell);
      cell = "";
    } else if ((ch === "\n" || ch === "\r") && !quoted) {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      rows.push([...row, cell]);
      row = [];
      cell = "";
    } else cell += ch;
  }
  if (quoted) throw new Error("Не удалось прочитать файл выгрузки.");
  if (row.length || cell) rows.push([...row, cell]);
  return rows;
}

export function combineRows(
  exports: { course: string; run: string; rows: string[][] }[],
) {
  const columns = [...new Set(exports.flatMap((item) => item.rows[0] ?? []))];
  const result: (string | number)[][] = [["Курс", "Поток", ...columns]];
  for (const item of exports) {
    const [header, ...rows] = item.rows;
    if (!header)
      throw new Error(`Выгрузка потока «${item.run}» не содержит заголовка.`);
    for (const row of rows) {
      if (row.length !== header.length)
        throw new Error(`Неполная строка в выгрузке «${item.run}».`);
      result.push([
        item.course,
        item.run,
        ...columns.map((column) => {
          const index = header.indexOf(column);
          const value = index === -1 ? "" : row[index];
          return (column === "Балл" ||
            column === "Попытка" ||
            column.startsWith("Критерий: ")) &&
            /^-?\d+(\.\d+)?$/.test(value)
            ? Number(value)
            : value;
        }),
      ]);
    }
  }
  return result;
}

async function pause(signal: AbortSignal) {
  await new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, 1000);
    signal.addEventListener("abort", abort, { once: true });
  });
}

export async function exportAllRuns(
  ws: WorkspaceClient,
  runs: W<"CourseRunView">[],
  courses: W<"CourseView">[],
  options: Omit<W<"ExportInput">, "course_run_id">,
  signal: AbortSignal,
  progress: (done: number, total: number) => void,
) {
  const files: { course: string; run: string; rows: string[][] }[] = [];
  for (const run of runs) {
    signal.throwIfAborted();
    let job = await ws.command("create_export", run.id, run.revision, {
      ...options,
      course_run_id: run.id,
      format: "csv",
    });
    const deadline = Date.now() + 10 * 60_000;
    while (["queued", "processing"].includes(job.status)) {
      if (Date.now() > deadline)
        throw new Error(
          `Выгрузка потока «${run.title}» ещё не готова. Повторите позже.`,
        );
      await pause(signal);
      job = await ws.export(job.id);
    }
    signal.throwIfAborted();
    if (job.status !== "succeeded" || !job.download)
      throw new Error(`Не удалось подготовить выгрузку потока «${run.title}».`);
    const response = await fetch(job.download.url, {
      signal,
      credentials: "omit",
    });
    if (!response.ok)
      throw new Error(`Не удалось скачать выгрузку потока «${run.title}».`);
    files.push({
      course:
        courses.find((c) => c.id === run.course_id)?.title ?? run.course_id,
      run: run.title,
      rows: parseCsv(await response.text()),
    });
    progress(files.length, runs.length);
  }
  signal.throwIfAborted();
  const rows = combineRows(files);
  return { blob: exportFile(rows, options.format), rows: rows.length - 1 };
}
