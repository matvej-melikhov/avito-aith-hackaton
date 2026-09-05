// Режим комментариев поверх интерфейса: карандаш вверху включает его,
// клик по любому блоку открывает окно для замечания, на блоке остаётся
// нумерованная метка. Комментарии живут в localStorage и копируются одним
// текстом, который удобно вставить в чат.
import { useCallback, useEffect, useRef, useState } from "react";

type Note = {
  id: string;
  route: string;
  screen: string;
  path: string;
  snippet: string;
  text: string;
  created: string;
};

const KEY = "ui-comments";
const SKIP_CLASS = /^(is-|has-|fb-)/;

function load(): Note[] {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Note[]) : [];
  } catch {
    return [];
  }
}
function save(notes: Note[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(notes));
  } catch {
    /* хранилище недоступно: комментарии живут до перезагрузки */
  }
}
function route() {
  return window.location.hash || "#/home";
}

/** Читаемый CSS-путь до элемента от <main>: теги, классы, порядковые номера. */
function describe(el: Element) {
  const parts: string[] = [];
  let node: Element | null = el;
  let depth = 0;
  while (
    node &&
    node.tagName !== "MAIN" &&
    node.tagName !== "BODY" &&
    depth < 8
  ) {
    const tag = node.tagName.toLowerCase();
    const classes = [...node.classList].filter((c) => !SKIP_CLASS.test(c));
    const shown = classes.slice(0, 2);
    let part = tag + shown.map((c) => `.${CSS.escape(c)}`).join("");
    const parent = node.parentElement;
    if (parent) {
      const current = node;
      const sameTag = [...parent.children].filter(
        (s) => s.tagName === current.tagName,
      );
      const sameLook = sameTag.filter((s) =>
        shown.every((c) => s.classList.contains(c)),
      );
      if (sameLook.length > 1)
        part += `:nth-of-type(${sameTag.indexOf(current) + 1})`;
    }
    parts.unshift(part);
    node = node.parentElement;
    depth++;
  }
  const path = `${node?.tagName === "MAIN" ? "main > " : ""}${parts.join(" > ")}`;
  const snippet = (el.textContent ?? "")
    .trim()
    .replace(/\s+/g, " ")
    .slice(0, 70);
  const screen = el.closest("[data-screen]")?.getAttribute("data-screen") ?? "";
  return { path, snippet, screen };
}

function exportText(notes: Note[]) {
  const date = new Date().toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
  });
  const lines = [`Комментарии к интерфейсу, ${date}, ${notes.length} шт.`, ""];
  notes.forEach((n, i) => {
    lines.push(
      `${i + 1}. ${n.screen ? `Экран ${n.screen}, ` : ""}маршрут ${n.route}`,
    );
    lines.push(`   Элемент: ${n.path}${n.snippet ? ` («${n.snippet}»)` : ""}`);
    lines.push(`   Правка: ${n.text}`);
    lines.push("");
  });
  return lines.join("\n").trim();
}

type Box = { top: number; left: number; width: number; height: number };
function box(el: Element): Box {
  const r = el.getBoundingClientRect();
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

export function Feedback() {
  const [on, setOn] = useState(false);
  const [notes, setNotes] = useState<Note[]>(load);
  const [current, setCurrent] = useState(route);
  const [hover, setHover] = useState<{ el: Element; box: Box } | null>(null);
  const [editing, setEditing] = useState<{
    el: Element | null;
    box: Box | null;
    note: Note;
  } | null>(null);
  const [panel, setPanel] = useState(false);
  const [copied, setCopied] = useState(false);
  const [pins, setPins] = useState<{ note: Note; box: Box; index: number }[]>(
    [],
  );
  const textRef = useRef<HTMLTextAreaElement>(null);

  const persist = useCallback((next: Note[]) => {
    setNotes(next);
    save(next);
  }, []);

  useEffect(() => {
    const update = () => {
      setCurrent(route());
      setEditing(null);
      setHover(null);
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  /* Метки на элементах текущего экрана: пересчитываются при прокрутке и
     изменении раскладки. */
  useEffect(() => {
    let frame = 0;
    const place = () => {
      frame = 0;
      const here = notes.filter((n) => n.route === current);
      setPins(
        here.flatMap((note) => {
          let el: Element | null = null;
          try {
            el = document.querySelector(note.path);
          } catch {
            el = null;
          }
          return el
            ? [{ note, box: box(el), index: notes.indexOf(note) + 1 }]
            : [];
        }),
      );
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(place);
    };
    place();
    const timer = window.setInterval(schedule, 600);
    window.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [notes, current]);

  /* Режим комментариев перехватывает наведение и клики поверх интерфейса. */
  useEffect(() => {
    if (!on) return;
    const pick = (x: number, y: number) => {
      const el = document.elementFromPoint(x, y);
      if (!el || el.closest(".fb")) return null;
      const target =
        el.closest(
          "button, a, input, textarea, select, .card, .kv, .acc, .tile, tr, th, td, h1, h2, h3, h4, p, li, .st, .pill, .field, .band, .topbar, .menu a, .callout, .dock, .seg, .tabs, .crumbs, .empty",
        ) ?? el;
      return target.closest("main, .login, .student, .app") ? target : el;
    };
    const move = (e: MouseEvent) => {
      if ((e.target as Element | null)?.closest?.(".fb")) {
        setHover(null);
        return;
      }
      const el = pick(e.clientX, e.clientY);
      setHover(el ? { el, box: box(el) } : null);
    };
    const click = (e: MouseEvent) => {
      if ((e.target as Element | null)?.closest?.(".fb")) return;
      e.preventDefault();
      e.stopPropagation();
      const el = pick(e.clientX, e.clientY);
      if (!el) return;
      const info = describe(el);
      setEditing({
        el,
        box: box(el),
        note: {
          id: crypto.randomUUID(),
          route: route(),
          screen: info.screen,
          path: info.path,
          snippet: info.snippet,
          text: "",
          created: new Date().toISOString(),
        },
      });
      setHover(null);
    };
    const swallow = (e: Event) => {
      if ((e.target as Element | null)?.closest?.(".fb")) return;
      e.stopPropagation();
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (editing) setEditing(null);
        else setOn(false);
      }
    };
    document.addEventListener("mousemove", move, true);
    document.addEventListener("click", click, true);
    document.addEventListener("mousedown", swallow, true);
    document.addEventListener("pointerdown", swallow, true);
    document.addEventListener("keydown", key, true);
    document.body.classList.add("fb-on");
    return () => {
      document.removeEventListener("mousemove", move, true);
      document.removeEventListener("click", click, true);
      document.removeEventListener("mousedown", swallow, true);
      document.removeEventListener("pointerdown", swallow, true);
      document.removeEventListener("keydown", key, true);
      document.body.classList.remove("fb-on");
    };
  }, [on, editing]);

  useEffect(() => {
    if (editing) textRef.current?.focus();
  }, [editing]);

  const here = notes.filter((n) => n.route === current);

  function submit() {
    if (!editing) return;
    const text = editing.note.text.trim();
    if (!text) {
      setEditing(null);
      return;
    }
    const exists = notes.some((n) => n.id === editing.note.id);
    persist(
      exists
        ? notes.map((n) => (n.id === editing.note.id ? { ...n, text } : n))
        : [...notes, { ...editing.note, text }],
    );
    setEditing(null);
  }
  function openNote(note: Note) {
    if (note.route !== current) {
      window.location.hash = note.route;
      return;
    }
    let el: Element | null = null;
    try {
      el = document.querySelector(note.path);
    } catch {
      el = null;
    }
    el?.scrollIntoView({ block: "center" });
    setEditing({ el, box: el ? box(el) : null, note });
    setPanel(false);
  }
  async function copyAll() {
    const text = exportText(notes);
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  const editorStyle = (() => {
    if (!editing?.box) return { top: 96, left: 24 };
    const width = 360;
    const left = Math.max(
      12,
      Math.min(editing.box.left, window.innerWidth - width - 12),
    );
    const below = editing.box.top + editing.box.height + 8;
    const top =
      below + 220 > window.innerHeight
        ? Math.max(12, editing.box.top - 228)
        : below;
    return { top, left };
  })();

  return (
    <div className="fb">
      <div
        className="fb-toggle"
        role="toolbar"
        aria-label="Комментарии к интерфейсу"
      >
        <button
          type="button"
          className={`fb-btn ${on ? "is-on" : ""}`}
          aria-pressed={on}
          title={
            on
              ? "Выйти из режима комментариев (Esc)"
              : "Комментировать интерфейс"
          }
          onClick={() => {
            setOn((v) => !v);
            setEditing(null);
            setHover(null);
          }}
        >
          <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
            <path
              d="M3 14.5V17h2.5L14.4 8.1l-2.5-2.5L3 14.5zm11.7-8.9 1.4-1.4a.8.8 0 0 0 0-1.1l-1.2-1.2a.8.8 0 0 0-1.1 0l-1.4 1.4 2.3 2.3z"
              fill="currentColor"
            />
          </svg>
          <span>{on ? "Комментирую" : "Комментарий"}</span>
        </button>
        {on && (
          <span className="fb-hint">
            Кликните по блоку или тексту · Esc — выйти
          </span>
        )}
        {notes.length > 0 && (
          <button
            type="button"
            className={`fb-btn fb-btn--count ${panel ? "is-on" : ""}`}
            aria-pressed={panel}
            title="Список комментариев"
            onClick={() => setPanel((v) => !v)}
          >
            {notes.length}
            {here.length !== notes.length ? ` · здесь ${here.length}` : ""}
          </button>
        )}
      </div>

      {on && hover && !editing && (
        <div
          className="fb-hover"
          style={{
            top: hover.box.top,
            left: hover.box.left,
            width: hover.box.width,
            height: hover.box.height,
          }}
        >
          <span className="fb-hover__tag">
            {describe(hover.el).path.split(" > ").slice(-2).join(" > ")}
          </span>
        </div>
      )}

      <div className="fb-pins" aria-hidden={!pins.length}>
        {pins.map(({ note, box: b, index }) => (
          <button
            key={note.id}
            type="button"
            className="fb-pin"
            style={{ top: b.top, left: b.left }}
            title={note.text}
            onClick={() => openNote(note)}
          >
            {index}
          </button>
        ))}
      </div>

      {editing && (
        <div
          className="fb-editor"
          style={editorStyle}
          role="dialog"
          aria-label="Комментарий"
        >
          {editing.box && (
            <div
              className="fb-outline"
              style={{
                top: editing.box.top,
                left: editing.box.left,
                width: editing.box.width,
                height: editing.box.height,
              }}
            />
          )}
          <div className="fb-editor__path">
            {editing.note.screen && <b>{editing.note.screen} · </b>}
            {editing.note.path.split(" > ").slice(-3).join(" > ")}
          </div>
          {editing.note.snippet && (
            <div className="fb-editor__snippet">«{editing.note.snippet}»</div>
          )}
          <textarea
            ref={textRef}
            className="fb-editor__text"
            placeholder="Что поменять или сделать здесь"
            value={editing.note.text}
            onChange={(e) =>
              setEditing({
                ...editing,
                note: { ...editing.note, text: e.target.value },
              })
            }
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit();
            }}
          />
          <div className="fb-editor__row">
            {notes.some((n) => n.id === editing.note.id) && (
              <button
                type="button"
                className="fb-link fb-link--danger"
                onClick={() => {
                  persist(notes.filter((n) => n.id !== editing.note.id));
                  setEditing(null);
                }}
              >
                Удалить
              </button>
            )}
            <span className="fb-editor__spacer" />
            <button
              type="button"
              className="fb-link"
              onClick={() => setEditing(null)}
            >
              Отмена
            </button>
            <button type="button" className="fb-btn is-on" onClick={submit}>
              Сохранить
            </button>
          </div>
        </div>
      )}

      {panel && (
        <div className="fb-panel" role="dialog" aria-label="Комментарии">
          <div className="fb-panel__head">
            <b>Комментарии</b>
            <span>{notes.length}</span>
            <span className="fb-editor__spacer" />
            <button
              type="button"
              className="fb-link"
              onClick={() => setPanel(false)}
            >
              Закрыть
            </button>
          </div>
          <div className="fb-panel__list">
            {notes.map((n, i) => (
              <div key={n.id} className="fb-item">
                <div className="fb-item__head">
                  <span className="fb-item__n">{i + 1}</span>
                  <span className="fb-item__where">
                    {n.screen ? `${n.screen} · ` : ""}
                    {n.route}
                  </span>
                </div>
                <div className="fb-item__text">{n.text}</div>
                <div className="fb-item__path">
                  {n.path.split(" > ").slice(-3).join(" > ")}
                  {n.snippet ? ` · «${n.snippet.slice(0, 40)}»` : ""}
                </div>
                <div className="fb-editor__row">
                  <button
                    type="button"
                    className="fb-link"
                    onClick={() => openNote(n)}
                  >
                    {n.route === current ? "Показать" : "Перейти"}
                  </button>
                  <button
                    type="button"
                    className="fb-link fb-link--danger"
                    onClick={() => persist(notes.filter((x) => x.id !== n.id))}
                  >
                    Удалить
                  </button>
                </div>
              </div>
            ))}
          </div>
          <div className="fb-panel__foot">
            <button type="button" className="fb-btn is-on" onClick={copyAll}>
              {copied ? "Скопировано" : "Скопировать всё для чата"}
            </button>
            <button
              type="button"
              className="fb-link fb-link--danger"
              onClick={() => {
                if (window.confirm("Удалить все комментарии?")) persist([]);
              }}
            >
              Очистить
            </button>
          </div>
          <textarea
            className="fb-panel__export"
            readOnly
            aria-label="Текст для копирования"
            value={exportText(notes)}
            onFocus={(e) => e.currentTarget.select()}
          />
        </div>
      )}
    </div>
  );
}
