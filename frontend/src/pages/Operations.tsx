import type { ApiClient, Model } from "../api/client";
import { Card, Empty, Id, Resource, Status, date, useResource } from "../ui";
export function OperationPanel({ api, id }: { api: ApiClient; id: string }) {
  const state = useResource(
    () => api.operation(id),
    id,
    4000,
    (op) =>
      [
        "pending",
        "processing",
        "partial",
        "unknown_outcome",
        "reconciling",
      ].includes(op.state),
  );
  return (
    <Card title="Операция">
      <Resource value={state}>
        {state.data && (
          <>
            <Status value={state.data.state} />
            <p>
              <Id value={state.data.id} />
            </p>
            {state.data.error && (
              <div className="notice warn">
                {state.data.error.message} {state.data.error.action}
              </div>
            )}
            {state.data.attempts.map((a) => (
              <p key={a.attempt_number}>
                Попытка {a.attempt_number} · <Status value={a.state} />
                <small>{date(a.started_at)}</small>
              </p>
            ))}
          </>
        )}
      </Resource>
    </Card>
  );
}
export function DeliveryCards({
  items,
}: {
  items: Model<"DeliverySummary">[];
}) {
  return (
    <Card title="Внешние доставки">
      {items.length === 0 ? (
        <Empty>Доставок пока нет.</Empty>
      ) : (
        items.map((d) => (
          <article key={d.id} className="list-item">
            <div className="row">
              <strong>
                {d.destination_kind === "github" ? "GitHub" : "Stepik"}
              </strong>
              <Status value={d.state} />
            </div>
            {d.error && (
              <p className="notice warn">
                {d.error.message} {d.error.action}
              </p>
            )}
            <p>Попыток: {d.attempts.length}</p>
            <a href={`#/operations/${d.operation_id}`}>Открыть операцию →</a>
          </article>
        ))
      )}
    </Card>
  );
}
export function DeliveriesPage({ api }: { api: ApiClient }) {
  const state = useResource(() => api.deliveries(), "deliveries", 5000);
  return (
    <>
      <h1>Доставки</h1>
      <p className="muted">
        Публикация ревью и отправка во внешнюю систему имеют независимые
        статусы.
      </p>
      <Resource value={state}>
        {state.data && <DeliveryCards items={state.data.items} />}
      </Resource>
      <p className="notice">Ручной повтор доставки пока недоступен.</p>
    </>
  );
}
