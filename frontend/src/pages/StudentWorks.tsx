import { useState } from "react";
import { WorkspaceClient } from "../api/workspace";
import { useMenuCounts } from "../App";
import {
  Btn,
  BtnRow,
  Card,
  CardBody,
  CardFoot,
  Empty,
  Main,
  Seg,
  St,
  cx,
  dayNum,
  isClosed,
} from "../ds";
import { Resource, go, useResource } from "../ui";
import { useEffect } from "react";

const LIMIT = 20;

function score(value: number | null) {
  return value === null
    ? "—"
    : value.toLocaleString("ru-RU", {
        minimumFractionDigits: 1,
        maximumFractionDigits: 2,
      });
}

export function StudentWorks({ ws }: { ws: WorkspaceClient }) {
  const [state, setState] = useState("");
  const [offset, setOffset] = useState(0);
  const params = { state, offset, limit: LIMIT };
  const r = useResource(() => ws.studentWorks(params), JSON.stringify(params));
  const { setCounts } = useMenuCounts();
  useEffect(() => {
    if (r.data && !state) setCounts({ works: r.data.total });
  }, [r.data, state, setCounts]);
  return (
    <Main page data-screen="С4">
      <div className="page-head">
        <h1>Мои домашки</h1>
        <Seg
          label="Статус домашних работ"
          value={state}
          onChange={(value) => {
            setState(value);
            setOffset(0);
          }}
          options={[
            { value: "", label: "Все" },
            { value: "in_progress", label: "В работе" },
            { value: "completed", label: "Завершённые" },
          ]}
        />
      </div>
      <Card>
        <Resource value={r}>
          {r.data && (
            <>
              <CardBody flush>
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
                      const href = `#/prepare/${item.publication_id}`;
                      return (
                        <tr
                          key={item.publication_id}
                          className={cx(
                            "is-link",
                            (isClosed(item.status) ||
                              item.status === "published") &&
                              "is-done",
                          )}
                          onClick={(e) => {
                            if ((e.target as HTMLElement).closest("a")) return;
                            go(href.slice(1));
                          }}
                        >
                          <td>
                            <div className="who">
                              <a href={href}>{item.title}</a>
                            </div>
                          </td>
                          <td>{item.course_title}</td>
                          <td className="n">
                            {dayNum(item.submission_deadline)}
                          </td>
                          <td className="n">{item.attempt}</td>
                          <td className="n r">{score(item.score)}</td>
                          <td className="r">
                            <St status={item.status} attempt={item.attempt} />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {r.data.items.length === 0 && (
                  <Empty title="Здесь пока пусто">
                    {state
                      ? "Домашних работ с таким статусом пока нет."
                      : "Домашка появится, когда вы откроете её по ссылке из курса."}
                  </Empty>
                )}
              </CardBody>
              {r.data.total > r.data.limit && (
                <CardFoot>
                  <span>
                    Показаны {r.data.offset + 1}–
                    {r.data.offset + r.data.items.length} из {r.data.total}
                  </span>
                  <BtnRow>
                    <Btn
                      size="s"
                      variant="quiet"
                      disabled={offset === 0}
                      onClick={() =>
                        setOffset(Math.max(0, offset - r.data!.limit))
                      }
                    >
                      Назад
                    </Btn>
                    <Btn
                      size="s"
                      variant="link"
                      disabled={
                        r.data.offset + r.data.items.length >= r.data.total
                      }
                      onClick={() => setOffset(offset + r.data!.limit)}
                    >
                      Показать ещё
                    </Btn>
                  </BtnRow>
                </CardFoot>
              )}
            </>
          )}
        </Resource>
      </Card>
    </Main>
  );
}
