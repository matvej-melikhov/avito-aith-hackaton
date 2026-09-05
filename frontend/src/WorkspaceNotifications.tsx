import { WorkspaceClient } from "./api/workspace";
import { Btn, Toast, Toasts } from "./ds";
import { useAction, useResource } from "./ui";

export function WorkspaceNotifications({ ws }: { ws: WorkspaceClient }) {
  const notices = useResource(() => ws.notifications(), "notifications", 30000);
  const action = useAction();
  const notice = notices.data?.items.find((n) => !n.read);
  if (!notice) return null;
  return (
    <Toasts>
      <Toast>
        <span>{notice.text}</span>
        <Btn
          size="s"
          variant="quiet"
          icon
          aria-label="Закрыть уведомление"
          disabled={action.busy}
          onClick={() =>
            void action.run(async () => {
              await ws.command("read_notification", notice.id, 0, {});
              notices.refresh();
            })
          }
        >
          ✕
        </Btn>
      </Toast>
      {!!action.error && (
        <Toast tone="bad">Не удалось закрыть уведомление</Toast>
      )}
    </Toasts>
  );
}
