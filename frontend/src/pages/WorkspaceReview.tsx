import { WorkspaceClient } from "../api/workspace";
import { Resource, safeUrl, useResource } from "../ui";
export function ArtifactLink({
  ws,
  id,
  label,
}: {
  ws: WorkspaceClient;
  id: string;
  label?: string;
}) {
  const r = useResource(() => ws.download(id), id);
  return (
    <Resource value={r}>
      {r.data && (
        <a
          className="button"
          href={safeUrl(r.data.url)}
          target="_blank"
          rel="noreferrer"
        >
          {label ?? `${r.data.filename ?? "Открыть работу"} ↗`}
        </a>
      )}
    </Resource>
  );
}
