import { WorkspaceClient } from "../api/workspace";
import { Resource, safeUrl, useResource } from "../ui";
import { FileActions, FileIcon, fileKind } from "../FileView";
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
        <span className="artifact-actions">
          <FileIcon kind={fileKind(r.data.filename)} />
          <a
            className="button"
            href={safeUrl(r.data.url)}
            target="_blank"
            rel="noreferrer"
          >
            {label ?? `${r.data.filename ?? "Открыть работу"} ↗`}
          </a>
          <FileActions
            name={r.data.filename ?? "Файл"}
            url={r.data.url}
            previewLabel="Просмотр"
          />
        </span>
      )}
    </Resource>
  );
}
