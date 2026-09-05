import { useState } from "react";
import type { ApiClient, Model, Role } from "../api/client";
import {
  Card,
  Empty,
  Id,
  Resource,
  Status,
  date,
  roleNames,
  useAction,
  useResource,
} from "../ui";
export function PeoplePage({ api }: { api: ApiClient }) {
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
      <h1>Участники и приглашения</h1>
      {action.feedback}
      <Resource value={s}>
        {s.data && (
          <>
            <Card title="Участники организации">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Пользователь</th>
                      <th>Роли</th>
                      <th>Статус</th>
                      <th></th>
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
              </div>
            </Card>
            <div className="two-col">
              <Card title="Пригласить участника">
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
                    }, "Приглашение создано.");
                  }}
                >
                  <label>
                    Email
                    <input
                      type="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                    />
                  </label>
                  <label>
                    Роль
                    <select
                      value={role}
                      onChange={(e) => setRole(e.target.value as typeof role)}
                    >
                      <option value="reviewer">Ревьюер</option>
                      <option value="methodologist">Координатор</option>
                    </select>
                  </label>
                  <label>
                    Действует до
                    <input
                      type="datetime-local"
                      required
                      value={expires}
                      onChange={(e) => setExpires(e.target.value)}
                    />
                  </label>
                  <button className="primary" disabled={action.busy}>
                    Пригласить
                  </button>
                </form>
              </Card>
              <Card title="Приглашения">
                {s.data.invitations.items.map((i) => (
                  <div key={i.id} className="list-item">
                    <strong>{i.normalized_email}</strong>
                    <p>
                      {roleNames[i.role]} · <Status value={i.status} />
                    </p>
                    <small>До {date(i.expires_at)}</small>
                    {i.status === "active" && (
                      <button
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
                        Отозвать приглашение
                      </button>
                    )}
                  </div>
                ))}
                {s.data.invitations.items.length === 0 && (
                  <Empty>Приглашений пока нет.</Empty>
                )}
              </Card>
            </div>
          </>
        )}
      </Resource>
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
      <td>
        <Id value={member.user_id} />
      </td>
      <td>
        {(Object.keys(roleNames) as Role[]).map((r) => (
          <label className="check" key={r}>
            <input
              type="checkbox"
              checked={roles.includes(r)}
              disabled={action.busy}
              onChange={(e) =>
                setRoles(
                  e.target.checked
                    ? [...roles, r]
                    : roles.filter((v) => v !== r),
                )
              }
            />
            {roleNames[r]}
          </label>
        ))}
      </td>
      <td>
        <Status value={member.status} />
      </td>
      <td>
        <button
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
        </button>
        {action.feedback}
      </td>
    </tr>
  );
}
export function PreferencesPage({
  api,
  session,
  onSessionChange,
}: {
  api: ApiClient;
  session: Model<"Session">;
  onSessionChange: () => Promise<void>;
}) {
  const s = useResource(() => api.courses(), "preferences");
  const [selected, setSelected] = useState<string[]>([]);
  const [minutes, setMinutes] = useState(120);
  const [until, setUntil] = useState("");
  const action = useAction();
  return (
    <>
      <h1>Моя доступность</h1>
      {action.feedback}
      <div className="two-col">
        <Card title="Готов проверять">
          <p className="notice">
            Выберите полный список потоков. Сохранение заменит предыдущий выбор.
          </p>
          <Resource value={s}>
            {s.data?.course_runs
              .filter((r) => r.status === "active")
              .map((r) => (
                <label className="check list-item" key={r.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(r.id)}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, r.id]
                          : selected.filter((id) => id !== r.id),
                      )
                    }
                  />
                  {r.title}
                </label>
              ))}
          </Resource>
          <button
            disabled={action.busy || s.loading || !!s.error}
            onClick={() =>
              void action.run(async () => {
                const current = await api.session();
                await api.command(
                  "set_reviewer_course_selection",
                  current.membership_id,
                  current.membership_revision,
                  { course_run_ids: selected },
                );
                await onSessionChange();
              }, "Выбор потоков сохранён.")
            }
          >
            Сохранить потоки
          </button>
        </Card>
        <Card title="Плановое время">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                const current = await api.session();
                await api.command(
                  "set_reviewer_availability",
                  current.membership_id,
                  current.membership_revision,
                  {
                    planned_minutes: minutes,
                    until_at: new Date(until).toISOString(),
                  },
                );
                await onSessionChange();
              }, "Доступность сохранена.");
            }}
          >
            <label>
              Минут на проверку
              <input
                type="number"
                min={0}
                required
                value={minutes}
                onChange={(e) => setMinutes(e.target.valueAsNumber)}
              />
            </label>
            <label>
              До какого времени
              <input
                type="datetime-local"
                required
                value={until}
                onChange={(e) => setUntil(e.target.value)}
              />
            </label>
            <p className="muted">
              Текущие настройки здесь не отображаются. При сохранении вы
              зададите новые значения.{" "}
            </p>
            <button
              disabled={action.busy || !session.roles.includes("reviewer")}
              className="primary"
            >
              Сохранить доступность
            </button>
          </form>
        </Card>
      </div>
    </>
  );
}
