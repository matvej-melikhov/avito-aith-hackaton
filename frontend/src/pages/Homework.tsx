import type { ApiClient, Role } from "../api/client";
import {
  Card,
  CardBody,
  CardHead,
  Crumbs,
  Empty,
  Kv,
  Main,
  Pill,
  Topbar,
  dayLong,
  num,
  plural,
  points,
} from "../ds";
import { ErrorBox, Skel, useResource } from "../ui";

/** Задание для ревьюера и студента: условие, критерии, публикации. Экрана в паке нет. */
export function HomeworkPage({
  api,
  id,
  run,
  role,
}: {
  api: ApiClient;
  id: string;
  run: string;
  role: Role;
}) {
  const s = useResource(() => api.homework(id), id);
  const latest = s.data
    ? [...s.data.versions].sort(
        (a, b) => b.version_number - a.version_number,
      )[0]
    : undefined;
  const back = role === "student" ? "#/works" : "#/courses";
  return (
    <>
      <Topbar
        crumbs={
          <Crumbs
            back={run ? `#/courses/${run}` : back}
            items={[
              {
                href: back,
                label: role === "student" ? "Мои домашки" : "Курсы",
              },
            ]}
            current="Задание"
          />
        }
        title={latest ? `Задание, версия ${latest.version_number}` : "Задание"}
        status={
          latest && (
            <Pill>
              {num(latest.max_score)}{" "}
              {plural(latest.max_score, "балл", "балла", "баллов")}
            </Pill>
          )
        }
      />
      <Main>
        {s.loading && <Skel lines={5} />}
        {!!s.error && <ErrorBox error={s.error} retry={s.refresh} />}
        {s.data && (
          <div className="row-side">
            <div className="stack">
              <Card>
                <CardHead title="Условие задания" />
                {latest ? (
                  <CardBody prose>
                    <p className="preserve">{latest.student_text}</p>
                  </CardBody>
                ) : (
                  <Empty title="Версии ещё нет">
                    Координатор пока не сохранил условие задания.
                  </Empty>
                )}
              </Card>
            </div>
            <div className="stack">
              <Card>
                <CardHead title="Что проверяют" />
                <CardBody tight>
                  {latest?.criteria.map((c) => (
                    <Kv key={c.key} label={c.title} ink>
                      <Pill mono>{points(c.max_points)}</Pill>
                    </Kv>
                  ))}
                  {latest && (
                    <Kv label="Всего" total>
                      {num(latest.max_score)}{" "}
                      {plural(latest.max_score, "балл", "балла", "баллов")}
                    </Kv>
                  )}
                </CardBody>
              </Card>
              <Card>
                <CardHead title="Публикации" />
                {s.data.course_run_publications.length === 0 ? (
                  <Empty title="Не опубликовано">
                    Задание ещё не открыто ни в одном потоке.
                  </Empty>
                ) : (
                  <CardBody tight>
                    {s.data.course_run_publications.map((p) => (
                      <Kv
                        key={p.id}
                        ink
                        label={
                          p.is_current
                            ? "Текущая публикация"
                            : "Прошлая публикация"
                        }
                      >
                        сдать до {dayLong(p.submission_deadline)}
                      </Kv>
                    ))}
                  </CardBody>
                )}
              </Card>
            </div>
          </div>
        )}
      </Main>
    </>
  );
}
