import { useState } from "react";
import type { ApiClient, Model, Role } from "../api/client";
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
  Sel,
  Topbar,
  dayLong,
  short,
} from "../ds";
import { Resource, roleNames, useAction, useResource } from "../ui";

/** Участники организации и приглашения. Экрана в паке нет. */
export function PeopleBody({ api }: { api: ApiClient }) {
  const s = useResource(async () => {
    const [members, invitations, organization] = await Promise.all([
      api.memberships(),
      api.invitations(),
      api.organization(),
    ]);
    return { members, invitations, organization };
  }, "people");
  const action = useAction();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"reviewer" | "methodologist">("reviewer");
  const [expires, setExpires] = useState("");
  return (
    <>
      {action.feedback}
      <Resource value={s}>
        {s.data && (
          <div className="stack">
            <Card>
              <CardHead title="Участники организации" />
              <CardBody flush>
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Пользователь</th>
                      <th>Роли</th>
                      <th>Статус</th>
                      <th className="r"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.data.members.items.map((m) => (
                      <MemberRow
                        key={`${m.id}:${m.revision}`}
                        api={api}
                        member={m}
                        refresh={s.refresh}
                      />
                    ))}
                  </tbody>
                </table>
              </CardBody>
            </Card>
            <div className="row-2">
              <Card>
                <CardHead title="Пригласить участника" />
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
                            role,
                            expires_at: new Date(expires).toISOString(),
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
                    <Field label="Роль">
                      <Sel
                        value={role}
                        onChange={(e) => setRole(e.target.value as typeof role)}
                      >
                        <option value="reviewer">Ревьюер</option>
                        <option value="methodologist">Координатор</option>
                      </Sel>
                    </Field>
                    <Field label="Действует до">
                      <Inp
                        type="datetime-local"
                        required
                        value={expires}
                        onChange={(e) => setExpires(e.target.value)}
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
                    Пригласите ревьюера или координатора формой слева.
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
export function PeoplePage({ api }: { api: ApiClient }) {
  return (
    <>
      <Topbar title="Участники" />
      <Main>
        <PeopleBody api={api} />
      </Main>
    </>
  );
}

function MemberRow({
  api,
  member,
  refresh,
}: {
  api: ApiClient;
  member: Model<"OrganizationMembershipList">["items"][number];
  refresh: () => void;
}) {
  const [roles, setRoles] = useState<Role[]>(member.roles);
  const action = useAction();
  return (
    <tr>
      <td className="mono" title={member.user_id}>
        {short(member.user_id, 8)}
      </td>
      <td>
        <div className="btn-row">
          {(Object.keys(roleNames) as Role[]).map((r) => (
            <Chk
              key={r}
              checked={roles.includes(r)}
              disabled={action.busy}
              onChange={(e) =>
                setRoles(
                  e.target.checked
                    ? [...roles, r]
                    : roles.filter((v) => v !== r),
                )
              }
            >
              {roleNames[r]}
            </Chk>
          ))}
        </div>
      </td>
      <td>
        <OpPill status={member.status} />
      </td>
      <td className="r">
        <Btn
          size="s"
          disabled={action.busy}
          onClick={() =>
            void action.run(async () => {
              await api.command(
                "change_membership_roles",
                member.id,
                member.revision,
                { roles },
              );
              refresh();
            })
          }
        >
          Сохранить роли
        </Btn>
        {action.feedback}
      </td>
    </tr>
  );
}
