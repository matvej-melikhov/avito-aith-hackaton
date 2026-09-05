import { WorkspaceClient } from "../api/workspace";
import { Resource, safeUrl, useResource } from "../ui";
export function ArtifactLink({ ws, id }: { ws: WorkspaceClient; id: string }) {
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
          {r.data.filename ?? "Открыть работу"} ↗
        </a>
      )}
    </Resource>
  );
}
