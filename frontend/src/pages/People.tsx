import { useState } from "react";
import type { ApiClient } from "../api/client";
import type { WorkspaceClient, ReviewerAvailability } from "../api/workspace";

const INVITATION_DAYS = 30;
import {
  Btn,
  Card,
  CardBody,
  CardHead,
  Chk,
  Empty,
  Field,
  Inp,
  Kv,
  Main,
  OpPill,
  Topbar,
  dayLong,
  plural,
  short,
} from "../ds";
import { Resource, roleNames, useAction, useResource } from "../ui";

/** Ревьюеры организации и приглашения. Экрана в паке нет. */
export function PeopleBody({
  api,
  ws,
}: {
  api: ApiClient;
  ws: WorkspaceClient;
}) {
  const s = useResource(async () => {
    const [members, invitations, organization, directory] = await Promise.all([
      api.memberships(),
      api.invitations(),
      api.organization(),
      ws.directory(),
    ]);
    return { members, invitations, organization, directory };
  }, "people");
  const action = useAction();
  const [email, setEmail] = useState("");
  // Приглашение действует месяц: срок на экране не спрашиваем.
  const expiresAt = () => {
    const at = new Date();
    at.setDate(at.getDate() + INVITATION_DAYS);
    return at.toISOString();
  };
  const directory = new Map(
    (s.data?.directory.items ?? []).map((m) => [m.id, m]),
  );
  const reviewers = (s.data?.members.items ?? []).filter((m) =>
    m.roles.includes("reviewer"),
  );
  return (
    <>
      {action.feedback}
      <Resource value={s}>
        {s.data && (
          <div className="stack">
            <Card>
              <CardHead
                title="Ревьюеры"
                sub={`${reviewers.length} ${plural(reviewers.length, "человек", "человека", "человек")}`}
              />
              {reviewers.length === 0 ? (
                <Empty title="Ревьюеров пока нет">
                  Пригласите ревьюера формой ниже.
                </Empty>
              ) : (
                <CardBody flush>
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Ревьюер</th>
                        <th>Доступ</th>
                        <th>Доступность</th>
                      </tr>
                    </thead>
                    <tbody>
                      {reviewers.map((m) => (
                        <tr key={m.id}>
                          <td>
                            <div className="who">
                              {directory.get(m.user_id)?.display_name ??
                                `rev-${short(m.user_id)}`}
                            </div>
                            <div className="sub mono">
                              {short(m.user_id, 8)}
                            </div>
                          </td>
                          <td>
                            {m.status === "active" ? (
                              "Включён"
                            ) : (
                              <OpPill status={m.status} />
                            )}
                          </td>
                          <td>
                            {reviewerAvailability(directory.get(m.user_id))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </CardBody>
              )}
            </Card>
            <div className="row-2">
              <Card>
                <CardHead
                  title="Пригласить внешнего ревьюера"
                  sub={`Ссылка действует ${INVITATION_DAYS} дней`}
                />
                <CardBody>
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      void action.run(async () => {
                        await api.command(
                          "create_invitation",
                          s.data!.organization.id,
                          s.data!.organization.revision,
                          {
                            email,
                            role: "reviewer",
                            expires_at: expiresAt(),
                          },
                        );
                        setEmail("");
                        s.refresh();
                      }, "Приглашение создано. Отправка письма выполняется сервером.");
                    }}
                  >
                    <Field label="Email">
                      <Inp
                        type="email"
                        required
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                      />
                    </Field>
                    <div className="btn-row btn-row--after">
                      <Btn variant="pri" type="submit" disabled={action.busy}>
                        Пригласить
                      </Btn>
                    </div>
                  </form>
                </CardBody>
              </Card>
              <Card>
                <CardHead title="Приглашения" />
                {s.data.invitations.items.length === 0 ? (
                  <Empty title="Приглашений пока нет">
                    Пригласите внешнего ревьюера формой слева.
                  </Empty>
                ) : (
                  <CardBody tight>
                    {s.data.invitations.items.map((i) => (
                      <Kv
                        key={i.id}
                        ink
                        label={
                          <>
                            {i.normalized_email}
                            <span className="caption kv__sub">
                              {" "}
                              {roleNames[i.role]} · до {dayLong(i.expires_at)}
                            </span>
                          </>
                        }
                      >
                        <OpPill status={i.status} />{" "}
                        {i.status === "active" && (
                          <Btn
                            size="s"
                            variant="quiet"
                            disabled={action.busy}
                            onClick={() =>
                              void action.run(async () => {
                                await api.command(
                                  "revoke_invitation",
                                  i.id,
                                  i.revision,
                                  {
                                    reason:
                                      "Отозвано координатором через интерфейс",
                                  },
                                );
                                s.refresh();
                              })
                            }
                          >
                            Отозвать
                          </Btn>
                        )}
                      </Kv>
                    ))}
                  </CardBody>
                )}
              </Card>
            </div>
          </div>
        )}
      </Resource>
    </>
  );
}
export function PeoplePage({
  api,
  ws,
}: {
  api: ApiClient;
  ws: WorkspaceClient;
}) {
  return (
    <>
      <Topbar title="Ревьюеры" />
      <Main>
        <PeopleBody api={api} ws={ws} />
      </Main>
    </>
  );
}

export function reviewerAvailability(
  member?: ReviewerAvailability,
  now = Date.now(),
) {
  if (
    !member ||
    member.absent_from === undefined ||
    member.absent_until === undefined
  )
    return "Нет данных";
  if (
    !member.absent_from ||
    !member.absent_until ||
    Date.parse(member.absent_until) < now
  )
    return "Доступен";
  const date = (value: string) =>
    new Date(value).toLocaleDateString("ru-RU", {
      timeZone: "Europe/Moscow",
      day: "numeric",
      month: "long",
    });
  return Date.parse(member.absent_from) <= now
    ? `В отпуске до ${date(member.absent_until)}`
    : `Отпуск с ${date(member.absent_from)} по ${date(member.absent_until)}`;
}
