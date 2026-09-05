import type { components } from "./schema";
import {
  revisionTargets,
  type CommandName,
  type CommandPayloads,
  type WireCommand,
} from "./commands";
import { commandRoutes, type CommandResults } from "./routes";
export type Model<K extends keyof components["schemas"]> =
  components["schemas"][K];
export type Role = Model<"Session">["roles"][number];
export type Transport = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public action: string | null = null,
  ) {
    super(message);
  }
}
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Сессия завершена. Войдите снова.";
    if (error.status === 403)
      return "У вашей роли нет доступа к этому действию.";
    if (error.status === 409)
      return "Данные изменились. Обновите экран и проверьте изменения перед повторным сохранением.";
    return [error.message, error.action].filter(Boolean).join(" ");
  }
  return error instanceof Error
    ? error.message
    : "Не удалось выполнить запрос.";
}
export class ApiClient {
  onUnauthorized?: () => void;
  // An uncertain POST keeps its exact receipt identity until a definitive response.
  private pending = new Map<string, { body: string; requestId: string }>();
  constructor(
    private transport: Transport = (input, init) => fetch(input, init),
  ) {}
  clearPending() {
    this.pending.clear();
  }
  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    let response: Response;
    try {
      response = await this.transport(`/api${path}`, {
        ...init,
        credentials: "same-origin",
        signal: init.signal ?? AbortSignal.timeout(20000),
        headers: {
          Accept: "application/json",
          ...(init.body ? { "Content-Type": "application/json" } : {}),
          ...init.headers,
        },
      });
    } catch (e) {
      if (init.signal?.aborted) throw e;
      throw new ApiError(
        0,
        "network_error",
        "Нет ответа от сервера. Проверьте подключение. Повтор того же действия использует прежний ключ запроса.",
      );
    }
    if (!response.ok) {
      if (
        response.status === 401 &&
        path !== "/v1/session" &&
        !path.startsWith("/v1/auth/")
      ) {
        this.clearPending();
        this.onUnauthorized?.();
      }
      let data: Partial<Model<"ErrorObject">> = {};
      try {
        data = await response.json();
      } catch {
        /* A reverse proxy can return HTML. */
      }
      throw new ApiError(
        response.status,
        data.code ?? "http_error",
        data.message ?? `Сервер вернул ошибку ${response.status}.`,
        data.action ?? null,
      );
    }
    if (response.status === 204) return undefined as T;
    try {
      return (await response.json()) as T;
    } catch {
      throw new ApiError(
        0,
        "invalid_response",
        "Сервер вернул нечитаемый ответ. Обновите данные перед следующим действием.",
      );
    }
  }
  async command<K extends CommandName & keyof typeof commandRoutes>(
    name: K,
    targetId: string,
    revision: number,
    payload: CommandPayloads[K],
  ): Promise<CommandResults[K]> {
    const route = commandRoutes[name];
    const key = JSON.stringify([name, targetId, revision, payload]);
    let receipt = this.pending.get(key);
    if (!receipt) {
      const requestId = crypto.randomUUID();
      const wire: WireCommand<K> = {
        request_id: requestId,
        idempotency_key: crypto.randomUUID(),
        command_name: name,
        revision_target: revisionTargets[name],
        target_id: targetId,
        expected_revision: revision,
        payload,
      };
      receipt = { body: JSON.stringify(wire), requestId };
      this.pending.set(key, receipt);
    }
    try {
      const result = await this.request<CommandResults[K]>(
        route.path.replace(/\{[^}]+\}/g, encodeURIComponent(targetId)),
        { method: route.method, body: receipt.body },
      );
      this.pending.delete(key);
      return result;
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.status >= 400 &&
        error.status < 500 &&
        error.status !== 408 &&
        error.status !== 429
      )
        this.pending.delete(key);
      throw error;
    }
  }
  session = () => this.request<Model<"Session">>("/v1/session");
  organization = () => this.request<Model<"Organization">>("/v1/organization");
  courses = () => this.request<Model<"CourseList">>("/v1/courses");
  homeworks = (id: string) =>
    this.request<Model<"HomeworkList">>(
      `/v1/course-runs/${encodeURIComponent(id)}/homeworks`,
    );
  homework = (id: string) =>
    this.request<Model<"HomeworkHistory">>(
      `/v1/homeworks/${encodeURIComponent(id)}`,
    );
  submission = (id: string) =>
    this.request<Model<"SubmissionHistory">>(
      `/v1/submissions/${encodeURIComponent(id)}`,
    );
  review = (id: string) =>
    this.request<Model<"ReviewDetail">>(
      `/v1/review-iterations/${encodeURIComponent(id)}`,
    );
  next = (id: string) =>
    this.request<Model<"ReviewRecommendation"> | null>(
      `/v1/review-queue/next?course_run_id=${encodeURIComponent(id)}`,
    );
  operation = (id: string) =>
    this.request<Model<"Operation">>(
      `/v1/operations/${encodeURIComponent(id)}`,
    );
  deliveries = () => this.request<Model<"DeliveryList">>("/v1/deliveries");
  memberships = () =>
    this.request<Model<"OrganizationMembershipList">>(
      "/v1/organization/memberships",
    );
  invitations = () => this.request<Model<"InvitationList">>("/v1/invitations");
  courseMembers = (id: string) =>
    this.request<Model<"CourseMembershipList">>(
      `/v1/course-runs/${encodeURIComponent(id)}/memberships`,
    );
}
