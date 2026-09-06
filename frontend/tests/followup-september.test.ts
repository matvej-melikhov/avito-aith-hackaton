import { expect, it, vi } from "vitest";
import { parseCsv, combineRows, exportAllRuns } from "../src/exportAllRuns";
import { exportFile } from "../src/exportFile";
import { reviewerAvailability } from "../src/pages/People";
import { ApiError, errorMessage, ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import { createDemoTransport } from "../src/mocks/transport";

it("distinguishes a current vacation, future vacation, expired dates and missing API data", () => {
  const now = Date.parse("2026-09-06T12:00:00Z");
  expect(reviewerAvailability({}, now)).toBe("Нет данных");
  expect(
    reviewerAvailability({ absent_from: null, absent_until: null }, now),
  ).toBe("Доступен");
  expect(
    reviewerAvailability(
      {
        absent_from: "2026-08-31T21:00:00Z",
        absent_until: "2026-09-16T20:59:59Z",
      },
      now,
    ),
  ).toBe("В отпуске до 16 сентября");
  expect(
    reviewerAvailability(
      {
        absent_from: "2026-09-10T21:00:00Z",
        absent_until: "2026-09-16T20:59:59Z",
      },
      now,
    ),
  ).toContain("Отпуск с 11 сентября");
  expect(
    reviewerAvailability(
      {
        absent_from: "2026-08-01T00:00:00Z",
        absent_until: "2026-08-10T00:00:00Z",
      },
      now,
    ),
  ).toBe("Доступен");
});

it("translates an unconfigured integration without hiding other failures", () => {
  expect(
    errorMessage(
      new ApiError(
        503,
        "service_unavailable",
        "invitation email boundary is not configured",
        "configure_service",
      ),
    ),
  ).toBe("Интеграция пока не настроена.");
  expect(
    errorMessage(new ApiError(422, "invalid_input", "Введите email")),
  ).toBe("Введите email");
});

it("combines different criterion headers and parses commas, multiline feedback and quotes", () => {
  const rows = parseCsv(
    '\ufeffID студента,Балл,Отзыв\r\n007,8.5,"Строка 1, тест\nСтрока ""2"""\r\n',
  );
  expect(rows[1]).toEqual(["007", "8.5", 'Строка 1, тест\nСтрока "2"']);
  const combined = combineRows([
    { course: "A", run: "1", rows },
    {
      course: "B",
      run: "2",
      rows: [
        ["ID студента", "Критерий: API"],
        ["008", "3.5"],
      ],
    },
  ]);
  expect(combined[0]).toEqual([
    "Курс",
    "Поток",
    "ID студента",
    "Балл",
    "Отзыв",
    "Критерий: API",
  ]);
  expect(combined[1][2]).toBe("007");
  expect(combined[1][3]).toBe(8.5);
  expect(combined[2]).toEqual(["B", "2", "008", "", "", 3.5]);
  expect(() => parseCsv('a,"unterminated')).toThrow();
});

function bytes(blob: Blob) {
  return new Promise<string>((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = reject;
    r.readAsText(blob);
  });
}
it("writes XLSX columns after Z and keeps formula-like strings as text", async () => {
  const text = await bytes(
    exportFile(
      [Array.from({ length: 28 }, (_, i) => (i === 26 ? "=1+1" : "x"))],
      "xlsx",
    ),
  );
  expect(text).toContain('r="AA1" t="inlineStr"');
  expect(text).toContain('r="AB1"');
  expect(text).not.toContain("<f>");
  expect(await bytes(exportFile([["=1+1"]], "csv"))).toContain("'=1+1");
});

it("exports every run using the server policy and returns no partial file on failure", async () => {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const catalog = await ws.catalog();
  const command = vi.spyOn(ws, "command").mockResolvedValue({
    id: "job",
    status: "succeeded",
    rows: 1,
    download: {
      url: "https://example.test/result.csv",
      filename: "result.csv",
      expires_at: "2026-10-01T00:00:00Z",
    },
    error: null,
  });
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response("ID студента,Балл\r\ns1,8\r\n"));
  // Each fetch consumes its own response stream.
  fetcher.mockImplementation(
    async () => new Response("ID студента,Балл\r\ns1,8\r\n"),
  );
  const options = {
    audience: "students" as const,
    columns: ["student_id", "score"] as ("student_id" | "score")[],
    include_unpublished: false,
    format: "xlsx" as const,
  };
  const result = await exportAllRuns(
    ws,
    catalog.course_runs,
    catalog.courses,
    options,
    new AbortController().signal,
    vi.fn(),
  );
  expect(result.rows).toBe(catalog.course_runs.length);
  expect(command).toHaveBeenCalledTimes(catalog.course_runs.length);
  expect(
    command.mock.calls.every(
      (c) =>
        "audience" in c[3] &&
        "format" in c[3] &&
        c[3].audience === "students" &&
        c[3].format === "csv",
    ),
  ).toBe(true);
  command.mockResolvedValueOnce({
    id: "bad",
    status: "failed",
    rows: 0,
    download: null,
    error: "failure",
  });
  await expect(
    exportAllRuns(
      ws,
      catalog.course_runs,
      catalog.courses,
      options,
      new AbortController().signal,
      vi.fn(),
    ),
  ).rejects.toThrow("Не удалось подготовить");
});
