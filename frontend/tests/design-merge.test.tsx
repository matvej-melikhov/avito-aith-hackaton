import { useState } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Aside, Field } from "../src/ds";
import { FileActions, FilePreview } from "../src/FileView";
import { MarkdownArea } from "../src/MarkdownArea";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

it("collapses from the keyboard, preserves accessible links, and restores labels on mobile", () => {
  let sync: () => void = () => {};
  const query = {
    matches: true,
    addEventListener: (_: string, fn: () => void) => {
      sync = fn;
    },
    removeEventListener: vi.fn(),
  };
  vi.stubGlobal("matchMedia", () => query);
  localStorage.setItem("aside-width", "190");
  const { container } = render(
    <div className="app-shell">
      <Aside
        menu={[
          { href: "#/works", label: "Мои проверки", icon: "works", count: 3 },
        ]}
      />
    </div>,
  );
  fireEvent.keyDown(screen.getByRole("separator"), { key: "ArrowLeft" });
  expect(container.firstChild).toHaveClass("app--rail");
  expect(screen.getByRole("link", { name: "Мои проверки" })).toHaveAttribute(
    "title",
    "Мои проверки",
  );
  fireEvent.keyDown(screen.getByRole("separator"), { key: "ArrowRight" });
  expect(screen.getByRole("separator")).toHaveAttribute("aria-valuenow", "190");
  fireEvent.keyDown(screen.getByRole("separator"), { key: "Enter" });
  act(() => {
    query.matches = false;
    sync();
  });
  expect(container.firstChild).not.toHaveClass("app--rail");
  expect(screen.queryByRole("separator")).toBeNull();
  expect(screen.getByRole("link", { name: "Мои проверки 3" })).toBeVisible();
});

it("keeps field associations and the selected text while inserting Markdown", async () => {
  function Editor() {
    const [value, change] = useState("Текст условия");
    return (
      <Field label="Условие" hint="Markdown">
        <MarkdownArea value={value} onChange={change} />
      </Field>
    );
  }
  render(<Editor />);
  const input = screen.getByRole("textbox", {
    name: "Условие",
  }) as HTMLTextAreaElement;
  expect(input).toHaveAccessibleDescription("Markdown");
  input.focus();
  input.setSelectionRange(0, 5);
  fireEvent.click(screen.getByRole("button", { name: "Жирный" }));
  expect(input).toHaveValue("**Текст** условия");
  await waitFor(() => expect(input.selectionStart).toBe(2));
  expect(input.selectionEnd).toBe(7);
});

it("aborts old text previews and replaces their content when the URL changes", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response("Первая версия"))
    .mockResolvedValueOnce(new Response("Вторая версия"));
  vi.stubGlobal("fetch", fetcher);
  const { rerender, unmount } = render(
    <FilePreview
      name="Работа.md"
      url="https://files.example/first"
      close={() => {}}
    />,
  );
  await screen.findByText("Первая версия");
  const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
  rerender(
    <FilePreview
      name="Работа.md"
      url="https://files.example/second"
      close={() => {}}
    />,
  );
  expect(signal.aborted).toBe(true);
  await screen.findByText("Вторая версия");
  expect(screen.queryByText("Первая версия")).toBeNull();
  unmount();
  expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
});

it("does not fetch or embed an unsafe file URL", () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const { container } = render(
    <FilePreview
      name="Работа.pdf"
      url="javascript:alert(1)"
      close={() => {}}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Ссылка на файл недоступна",
  );
  expect(container.querySelector("iframe")).toBeNull();
  expect(fetcher).not.toHaveBeenCalled();
});

it("downloads cross-origin bytes under the original UTF-8 filename", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response("Содержимое"));
  vi.stubGlobal("fetch", fetcher);
  const create = vi.fn(() => "blob:http://localhost/test");
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = create;
      static revokeObjectURL = vi.fn();
    },
  );
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe("Ответ студента.md");
      expect(this.href).toBe("blob:http://localhost/test");
    });
  render(
    <FileActions
      name="Ответ студента.md"
      url="https://files.example/signed?token=sample"
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Скачать Ответ студента.md" }),
  );
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(fetcher).toHaveBeenCalledWith(
    "https://files.example/signed?token=sample",
  );
});
