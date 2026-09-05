import { WorkspaceClient } from "./api/workspace";
import { useAction, useResource } from "./ui";

export function WorkspaceNotifications({ ws }: { ws: WorkspaceClient }) {
  const notices = useResource(() => ws.notifications(), "notifications", 30000);
  const action = useAction();
  const notice = notices.data?.items.find((n) => !n.read);
  if (!notice) return null;
  return (
    <div className="toast workspace-toast" role="status">
      <span>{notice.text}</span>
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
