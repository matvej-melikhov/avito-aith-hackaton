import { useState } from "react";
import { WorkspaceClient } from "../api/workspace";
import { Empty, Resource, Status, useResource } from "../ui";
import { ScreenTitle } from "../workspace-ui";

export function StudentWorks({ ws }: { ws: WorkspaceClient }) {
  const [state, setState] = useState("");
  const [offset, setOffset] = useState(0);
  const params = { state, offset, limit: 20 };
  const r = useResource(() => ws.studentWorks(params), JSON.stringify(params));
  return (
    <>
      <ScreenTitle code="С4" title="Мои домашки">
        <div className="seg" aria-label="Статус домашних работ">
          {[
            ["", "Все"],
            ["in_progress", "В работе"],
            ["completed", "Завершённые"],
          ].map(([value, label]) => (
            <button
              key={value}
              className={state === value ? "is-on" : undefined}
              aria-pressed={state === value}
              onClick={() => {
                setState(value);
                setOffset(0);
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </ScreenTitle>
      <Resource value={r}>
        {r.data && (
          <>
            <div className="card">
              <div className="card__body card__body--flush">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Задание</th>
                      <th>Курс</th>
                      <th className="n">Дедлайн</th>
                      <th className="n">Попытка</th>
                      <th className="n r">Балл</th>
                      <th className="r">Статус</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.data.items.map((item) => {
                      const href =
                        item.submission_id &&
                        !["draft", "needs_changes"].includes(item.status)
                          ? `#/submissions/${item.submission_id}`
                          : `#/prepare/${item.publication_id}`;
                      return (
                        <tr
                          key={item.publication_id}
                          className={
                            ["passed", "failed", "published"].includes(
                              item.status,
                            )
                              ? "is-done"
                              : undefined
                          }
                        >
                          <td>
                            <a
                              className="who"
                              href={href}
                              style={{ color: "inherit", border: 0 }}
                            >
                              {item.title}
                            </a>
                          </td>
                          <td>{item.course_title}</td>
                          <td className="n">
                            {new Date(item.submission_deadline).toLocaleString(
                              "ru-RU",
                              {
                                day: "2-digit",
                                month: "2-digit",
                                hour: "2-digit",
                                minute: "2-digit",
                              },
                            )}
                          </td>
                          <td className="n">{item.attempt}</td>
                          <td className="n r">
                            {item.score === null
                              ? "—"
                              : item.score.toLocaleString("ru-RU", {
                                  minimumFractionDigits: 1,
                                  maximumFractionDigits: 2,
                                })}
                          </td>
                          <td className="r">
                            <Status
                              value={item.status}
                              attempt={item.attempt}
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {r.data.items.length === 0 && (
                  <Empty>Домашних работ с таким статусом пока нет.</Empty>
                )}
              </div>
            </div>
            {r.data.total > r.data.limit && (
              <div className="pagination">
                <button
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - r.data!.limit))}
                >
                  Назад
                </button>
                <span>
                  {r.data.offset + 1}–{r.data.offset + r.data.items.length} из{" "}
                  {r.data.total}
                </span>
                <button
                  disabled={r.data.offset + r.data.items.length >= r.data.total}
                  onClick={() => setOffset(offset + r.data!.limit)}
                >
                  Далее
                </button>
              </div>
            )}
          </>
        )}
      </Resource>
    </>
  );
}
