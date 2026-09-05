import { ApiClient, ApiError, type Model } from "./client";
import {
  workspaceRoutes,
  type W,
  type WorkspaceInputs,
  type WorkspaceResults,
} from "./workspace-routes";
export type { W } from "./workspace-routes";
export class WorkspaceClient {
  private pending = new Map<string, string>();
  constructor(public readonly core: ApiClient) {}
  async command<K extends keyof WorkspaceInputs>(
    name: K,
    target: string,
    revision: number,
    payload: WorkspaceInputs[K],
  ): Promise<WorkspaceResults[K]> {
    const key = JSON.stringify([name, target, revision, payload]);
    let body = this.pending.get(key);
    if (!body) {
      body = JSON.stringify({
        request_id: crypto.randomUUID(),
        idempotency_key: crypto.randomUUID(),
        command_name: name,
        target_id: target,
        expected_revision: revision,
        payload,
      });
      this.pending.set(key, body);
    }
    const route = workspaceRoutes[name];
    try {
      const result = await this.core.request<WorkspaceResults[K]>(
        route.path.replace("{identity}", encodeURIComponent(target)),
        { method: route.method, body },
      );
      this.pending.delete(key);
      return result;
    } catch (e) {
      if (
        e instanceof ApiError &&
        e.status >= 400 &&
        e.status < 500 &&
        e.status !== 408 &&
        e.status !== 429
      )
        this.pending.delete(key);
      throw e;
    }
  }
  editorDraft = (id: string, run: string) =>
    this.core.request<W<"EditorDraftView">>(
      `/v2/homeworks/${id}/editor-draft?course_run_id=${run}`,
    );
  policy = (id: string) =>
    this.core.request<W<"PublicationPolicyView"> | null>(
      `/v2/course-run-homeworks/${id}/policy`,
    );
  privateHomework = (id: string) =>
    this.core.request<W<"PrivateHomeworkView">>(
      `/v2/homework-versions/${id}/private-details`,
    );
  notifications = () =>
    this.core.request<W<"NotificationsView">>("/v2/notifications");
  studentWorks = (params: Record<string, string | number> = {}) =>
    this.core.request<W<"StudentHomeworkList">>(
      `/v2/student/homeworks?${new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))}`,
    );
  search = (q: string) =>
    this.core.request<W<"WorkspaceSearchView">>(
      `/v2/search?q=${encodeURIComponent(q)}`,
    );
  courseHomeworks = (id: string) =>
    this.core.request<W<"CoordinatorHomeworkList">>(
      `/v2/courses/${id}/homeworks`,
    );
  catalog = () => this.core.request<W<"CatalogView">>("/v2/catalog");
  directory = () => this.core.request<W<"DirectoryView">>("/v2/directory");
  preferences = () =>
    this.core.request<W<"PreferencesView">>("/v2/reviewer/preferences");
  assignments = (id: string) =>
    this.core.request<W<"AssignmentsView">>(
      `/v2/course-runs/${id}/assignments`,
    );
  works = (params: Record<string, string | number | undefined> = {}) =>
    this.core.request<W<"WorkList">>(
      `/v2/works?${new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined)
          .map(([k, v]) => [k, String(v)]),
      )}`,
    );
  submission = (id: string) =>
    this.core.request<W<"StudentSubmissionView">>(`/v2/submissions/${id}`);
  drafts = () => this.core.request<W<"DraftList">>("/v2/drafts");
  studentContext = (id: string) =>
    this.core.request<W<"StudentContext">>(
      `/v2/course-run-homeworks/${id}/student-context`,
    );
  async reviewDetail(id: string): Promise<Model<"ReviewDetail">> {
    const [detail, draft] = await Promise.all([
      this.core.review(id),
      this.core.request<W<"ReviewDraftView">>(`/v2/reviews/${id}/draft`),
    ]);
    return { ...detail, ...draft };
  }
  reviewAssist = (id: string) =>
    this.core.request<W<"ReviewAssistView"> | null>(`/v2/reviews/${id}/assist`);
  preparation = (id: string) =>
    this.core.request<W<"PreparationView">>(`/v2/preparations/${id}`);
  gradePreview = (id: string) =>
    this.core.request<W<"GradePreview">>(`/v2/reviews/${id}/grade-preview`);
  reviewContext = (id: string) =>
    this.core.request<W<"ReviewContext">>(`/v2/reviews/${id}/context`);
  selfReview = (id: string) =>
    this.core.request<W<"SelfReviewView">>(`/v2/self-reviews/${id}`);
  statistics = (days = 30, courseRun?: string) =>
    this.core.request<W<"StatisticView">>(
      `/v2/statistics?days=${days}${courseRun ? `&course_run_id=${courseRun}` : ""}`,
    );
  export = (id: string) =>
    this.core.request<W<"ExportView">>(`/v2/exports/${id}`);
  download = (id: string) =>
    this.core.request<W<"DownloadView">>(`/v2/artifacts/${id}/download`);
}
export async function uploadFile(
  client: WorkspaceClient,
  file: File,
  user: string,
  privateFile = false,
) {
  if (file.size > 10_000_000) throw new Error("Допустим файл до 10 МБ.");
  const name = file.name.toLowerCase();
  const mediaType = name.endsWith(".md")
    ? "text/markdown"
    : name.endsWith(".pdf")
      ? "application/pdf"
      : name.endsWith(".docx")
        ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        : undefined;
  if (!mediaType) throw new Error("Поддерживаются Markdown, PDF и DOCX.");
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192)
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return client.command("upload_artifact", user, 0, {
    filename: file.name,
    media_type: mediaType,
    content_base64: btoa(binary),
    private: privateFile,
  });
}
