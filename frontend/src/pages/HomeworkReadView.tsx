import type { ApiClient } from "../api/client";
import { Card, Empty, Resource, date, useResource } from "../ui";
import { num } from "../ds";
import { ScreenTitle } from "../workspace-ui";

/** Чтение версии, опубликованной именно в открытом потоке. */
export function HomeworkReadView({
  api,
  id,
  run,
}: {
  api: ApiClient;
  id: string;
  run: string;
}) {
  const state = useResource(async () => {
    if (!run) return null;
    const [history, list] = await Promise.all([
      api.homework(id),
      api.homeworks(run),
    ]);
    const summary = list.items.find((item) => item.homework_id === id);
    const version = history.versions.find(
      (item) => item.id === summary?.current_version_id,
    );
    return {
      summary,
      version,
      publications: history.course_run_publications.filter(
        (item) => item.course_run_id === run,
      ),
    };
  }, `${id}:${run}`);
  const data = state.data;
  return (
    <>
      <ScreenTitle
        code="С2"
        title={data?.summary?.title ?? "Условие задания"}
        breadcrumbs={
          <a href={run ? `#/courses/${run}` : "#/courses"}>
            ← Вернуться к потоку
          </a>
        }
      />
      <Resource value={state}>
        {data?.summary && data.version ? (
          <>
            <p className="small dim">Версия {data.version.version_number}</p>
            <div className="two-col">
              <div className="stack">
                <Card title="Условие задания">
                  <div className="preserve homework-reading-text">
                    {data.version.student_text}
                  </div>
                </Card>
                <Card title="Критерии проверки">
                  {data.version.criteria.map((criterion) => (
                    <section
                      className="homework-reading-criterion"
                      key={criterion.key}
                    >
                      <h3>{criterion.title}</h3>
                      <p className="preserve">{criterion.description}</p>
                      <p className="small">
                        Максимум баллов: {num(criterion.max_points, 20)}
                      </p>
                    </section>
                  ))}
                  {!data.version.criteria.length && (
                    <Empty>Критерии не указаны.</Empty>
                  )}
                </Card>
              </div>
              <div className="stack">
                <Card title="Сроки и оценка">
                  <p>
                    Максимальный балл:{" "}
                    <strong>{num(data.version.max_score, 20)}</strong>
                  </p>
                  <p>Сдать до {date(data.summary.submission_deadline)}</p>
                  <p>Проверить до {date(data.summary.review_deadline)}</p>
                </Card>
                <Card title="История публикаций">
                  {data.publications.map((publication) => (
                    <div className="homework-publication" key={publication.id}>
                      <p>
                        {publication.is_current
                          ? "Текущая публикация"
                          : "Прошлая публикация"}{" "}
                        · {date(publication.published_at)}
                      </p>
                      <small>
                        Срок сдачи: {date(publication.submission_deadline)}
                      </small>
                    </div>
                  ))}
                  {!data.publications.length && (
                    <Empty>Публикаций в этом потоке нет.</Empty>
                  )}
                </Card>
              </div>
            </div>
          </>
        ) : (
          <Empty>
            {run
              ? "Опубликованная версия задания в этом потоке недоступна."
              : "Откройте задание из списка выбранного потока."}
          </Empty>
        )}
      </Resource>
    </>
  );
}
