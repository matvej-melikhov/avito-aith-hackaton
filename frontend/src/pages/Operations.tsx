import type { ApiClient, Model } from "../api/client";
import {
  Btn,
  Callout,
  Card,
  CardBody,
  CardHead,
  Empty,
  Kv,
  Main,
  OpPill,
  Topbar,
  dayLong,
} from "../ds";
import { Resource, useResource } from "../ui";

/** Карточка фоновой операции: состояние, попытки, ошибка. Экрана в паке нет. */
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
    <Card>
      <CardHead title="Операция" />
      <Resource value={state}>
        {state.data && (
          <>
            <CardBody tight>
              <Kv label="Состояние">
                <OpPill status={state.data.state} />
              </Kv>
              <Kv label="Идентификатор">
                <span className="mono">{state.data.id}</span>
              </Kv>
              {state.data.attempts.map((a) => (
                <Kv
                  key={a.attempt_number}
                  label={`Попытка ${a.attempt_number}`}
                >
                  <OpPill status={a.state} />{" "}
                  <span className="caption">{dayLong(a.started_at)}</span>
                </Kv>
              ))}
            </CardBody>
            {state.data.error && (
              <CardBody>
                <Callout tone="warn">
                  <p>
                    {state.data.error.message} {state.data.error.action}
                  </p>
                </Callout>
              </CardBody>
            )}
          </>
        )}
      </Resource>
    </Card>
  );
}

export function OperationPage({ api, id }: { api: ApiClient; id: string }) {
  return (
    <>
      <Topbar title="Операция" />
      <Main>
        <OperationPanel api={api} id={id} />
      </Main>
    </>
  );
}

export function DeliveryCards({
  items,
}: {
  items: Model<"DeliverySummary">[];
}) {
  return (
    <Card>
      <CardHead
        title="Внешние доставки"
        sub="Публикация ревью и отправка во внешнюю систему имеют независимые статусы"
      />
      {items.length === 0 ? (
        <Empty title="Доставок пока нет">
          Когда ревью уйдёт во внешнюю систему, здесь появится его статус.
        </Empty>
      ) : (
        <CardBody tight>
          {items.map((d) => (
            <Kv
              key={d.id}
              label={
                <>
                  {d.destination_kind === "github" ? "GitHub" : "Stepik"}
                  {d.error && (
                    <span className="caption kv__err">
                      {" "}
                      {d.error.message} {d.error.action}
                    </span>
                  )}
                </>
              }
              ink
            >
              <OpPill status={d.state} />{" "}
              <span className="caption">попыток: {d.attempts.length}</span>{" "}
              <Btn
                size="s"
                variant="quiet"
                href={`#/operations/${d.operation_id}`}
              >
                Операция
              </Btn>
            </Kv>
          ))}
        </CardBody>
      )}
    </Card>
  );
}

export function DeliveriesBody({ api }: { api: ApiClient }) {
  const state = useResource(() => api.deliveries(), "deliveries", 5000);
  return (
    <div className="stack">
      <Resource value={state}>
        {state.data && <DeliveryCards items={state.data.items} />}
      </Resource>
      <Callout>
        <p>Ручной повтор доставки пока недоступен.</p>
      </Callout>
    </div>
  );
}

export function DeliveriesPage({ api }: { api: ApiClient }) {
  return (
    <>
      <Topbar title="Доставки" />
      <Main>
        <DeliveriesBody api={api} />
      </Main>
    </>
  );
}
