import { Inp, Srch, Pop } from "../ds";
import { useEffect, useId, useRef, useState } from "react";
import { WorkspaceClient, type W } from "../api/workspace";
import { Status } from "../ui";

export function WorkspaceSearch({ ws }: { ws: WorkspaceClient }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<W<"WorkspaceSearchView">>();
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState(0);
  const root = useRef<HTMLSpanElement>(null);
  const popupId = useId();
  const students = result?.students ?? [];
  const homeworks = result?.homeworks ?? [];
  const destinations = [
    ...students.map((work) =>
      work.submission_id &&
      work.review_iteration_id &&
      work.review_submission_version_id === work.submission_version_id
        ? `#/reviews/${work.review_iteration_id}`
        : work.submission_id
          ? `#/submissions/${work.submission_id}`
          : `#/registry?run=${work.course_run_id}&draft=${work.draft_id}`,
    ),
    ...homeworks.map(
      (homework) => `#/homework/${homework.id}?run=${homework.course_run_id}`,
    ),
  ];
  useEffect(() => {
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !root.current?.contains(event.target))
        setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, []);
  useEffect(() => {
    let cancelled = false;
    setResult(undefined);
    setError(false);
    setSelected(0);
    if (!query.trim()) {
      setBusy(false);
      return;
    }
    setBusy(true);
    const timer = window.setTimeout(() => {
      void ws
        .search(query.trim())
        .then((value) => {
          if (!cancelled) setResult(value);
        })
        .catch(() => {
          if (!cancelled) setError(true);
        })
        .finally(() => {
          if (!cancelled) setBusy(false);
        });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query, ws]);
  return (
    <Srch ref={root} data-screen="К1а">
      <Inp
        className="inp inp--s"
        style={{ width: 280, maxWidth: "100%" }}
        aria-label="ID студента или название задания"
        placeholder="ID студента или название задания"
        value={query}
        aria-expanded={open && !!query.trim()}
        aria-controls={popupId}
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setOpen(true);
            setSelected((index) =>
              destinations.length
                ? (index +
                    (event.key === "ArrowDown" ? 1 : -1) +
                    destinations.length) %
                  destinations.length
                : 0,
            );
          }
          if (event.key === "Enter" && open && destinations[selected]) {
            event.preventDefault();
            window.location.hash = destinations[selected];
            setOpen(false);
          }
        }}
      />
      {open && !!query.trim() && (
        <Pop
          id={popupId}
          style={{
            top: "100%",
            left: 0,
            marginTop: 4,
            width: "min(560px, calc(100vw - 40px))",
          }}
          aria-label="Результаты поиска"
        >
          <div className="pop__g">
            Студенты{students.length ? ` · ${students.length} работ` : ""}
          </div>
          {students.map((work, index) => (
            <a
              className={`pop__row ${selected === index ? "is-on" : ""}`}
              href={destinations[index]}
              key={work.submission_id ?? work.draft_id}
              onMouseEnter={() => setSelected(index)}
              onClick={() => setOpen(false)}
            >
              <span className="t">
                <span>{work.title}</span>
                <span className="s">
                  {work.student_id} · {work.course_run_title}
                </span>
              </span>
              <Status value={work.status} />
            </a>
          ))}
          {!students.length && (
            <div className="pop__empty">
              {busy
                ? "Ищем работы студента…"
                : error
                  ? "Не удалось загрузить работы."
                  : "Работ с таким ID студента нет."}
            </div>
          )}
          <div className="pop__g">Задания</div>
          {homeworks.map((homework, index) => (
            <a
              className={`pop__row ${selected === students.length + index ? "is-on" : ""}`}
              key={`${homework.id}:${homework.course_run_id}`}
              href={destinations[students.length + index]}
              onMouseEnter={() => setSelected(students.length + index)}
              onClick={() => setOpen(false)}
            >
              <span className="t">{homework.title}</span>
            </a>
          ))}
          {!homeworks.length && (
            <div className="pop__empty">
              {busy
                ? "Ищем задания…"
                : error
                  ? "Не удалось загрузить задания."
                  : "Заданий с таким названием нет."}
            </div>
          )}
          <div className="pop__foot">
            <span>
              Enter открывает выбранную строку, Escape закрывает поиск
            </span>
            {students.length > 0 && (
              <a
                href={`#/registry?q=${encodeURIComponent(query.trim())}`}
                onClick={() => setOpen(false)}
              >
                Все работы студента
              </a>
            )}
          </div>
        </Pop>
      )}
    </Srch>
  );
}
