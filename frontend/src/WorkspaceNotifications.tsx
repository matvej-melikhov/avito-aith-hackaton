import { WorkspaceClient } from "./api/workspace";
import { useAction, useResource } from "./ui";

export function WorkspaceNotifications({ ws }: { ws: WorkspaceClient }) {
  const notices = useResource(() => ws.notifications(), "notifications", 30000);
  const catalog = useResource(() => ws.catalog(), "notification-catalog");
  const action = useAction();
  const notice = notices.data?.items.find((n) => !n.read);
  if (!notice) return null;
  const run = catalog.data?.course_runs.find(
    (r) => r.id === notice.course_run_id,
  );
  const course = catalog.data?.courses.find((c) => c.id === run?.course_id);
  return (
    <div className="toast workspace-toast" role="status">
      <div>
        <strong>
          {course?.title ?? "Уведомление по потоку"}
          {run && ` · ${run.title}`}
        </strong>
        <p>{notice.text}</p>
        {!run && <small>Название потока пока недоступно.</small>}
      </div>
      <button
        aria-label="Закрыть уведомление"
        disabled={action.busy}
        onClick={() =>
          void action.run(async () => {
            await ws.command("read_notification", notice.id, 0, {});
            notices.refresh();
          })
        }
      >
        ×
      </button>
      {action.feedback}
    </div>
  );
}
