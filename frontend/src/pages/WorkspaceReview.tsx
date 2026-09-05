import { WorkspaceClient } from "../api/workspace";
import { Btn } from "../ds";
import { Resource, safeUrl, useResource } from "../ui";
export function ArtifactLink({ ws, id }: { ws: WorkspaceClient; id: string }) {
  const r = useResource(() => ws.download(id), id);
  return (
    <Resource value={r}>
      {r.data && (
        <Btn
          size="s"
          variant="quiet"
          href={safeUrl(r.data.url) ?? "#"}
          target="_blank"
          rel="noreferrer"
        >
          {r.data.filename ?? "Открыть работу"} ↗
        </Btn>
      )}
    </Resource>
  );
}
