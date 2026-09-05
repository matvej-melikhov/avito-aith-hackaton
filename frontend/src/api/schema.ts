export interface paths {
    "/v1/auth/stepik/callback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["completeStepikAuthentication"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/auth/stepik/start": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["startStepikAuthentication"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/session": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["getCurrentSession"];
        put?: never;
        post?: never;
        delete: operations["revokeCurrentSession"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/auth/reviewer/magic-link": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["consumeReviewerMagicLink"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/organization": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["getOrganization"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/courses": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listCourses"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/course-runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listCourseRuns"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/organization/memberships": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listOrganizationMemberships"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/invitations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listInvitations"];
        put?: never;
        post: operations["createInvitation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/invitations/{invitationId}/revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["revokeInvitation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/courses/imports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["startCourseImport"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/course-runs/{courseRunId}/homeworks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listPublishedHomeworks"];
        put?: never;
        post: operations["createHomework"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/course-runs/{courseRunId}/memberships": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listCourseRunMemberships"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/course-runs/{courseRunId}/archive": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["archiveCourseRun"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/course-runs/{courseRunId}/restore": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["restoreCourseRun"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/courses/{courseId}/archive": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["archiveCourse"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/courses/{courseId}/restore": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["restoreCourse"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/memberships/{membershipId}/roles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put: operations["changeMembershipRoles"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/homeworks/{homeworkId}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["createHomeworkVersion"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/homeworks/{homeworkId}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["getHomeworkHistory"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/homework-versions/{homeworkVersionId}/publish": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["publishHomeworkVersion"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/reviewer/course-selections": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["setReviewerCourseSelection"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/reviewer/availability": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put: operations["setReviewerAvailability"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/course-run-homeworks/{courseRunHomeworkId}/submissions/preflight": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["preflightSubmission"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/submissions/{submissionId}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["submitWork"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/submissions/{submissionId}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["getSubmissionHistory"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/review-cases/{reviewCaseId}/iterations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["openReviewIteration"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/review-queue/next": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["recommendNextReview"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/review-iterations/{reviewIterationId}/revisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["saveReviewRevision"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/review-iterations/{reviewIterationId}/requirements-migrations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["migrateReviewRequirements"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/review-iterations/{reviewIterationId}/corrections": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["createReviewCorrection"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/review-iterations/{reviewIterationId}/responsibility-events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["recordReviewResponsibility"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/review-iterations/{reviewIterationId}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["getReviewIteration"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/review-iterations/{reviewIterationId}/publish": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["publishReview"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/review-iterations/{reviewIterationId}/publication-requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["requestReviewPublication"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/review-iterations/{reviewIterationId}/ai-review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["startAIReview"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/memberships/{membershipId}/agent-authorizations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["grantAgentAuthorization"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/agent-authorizations/{agentAuthorizationId}/revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["revokeAgentAuthorization"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/internal/ai-review/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["acceptAIReviewEvent"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/operations/{operationId}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["getOperation"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/deliveries/{deliveryId}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: operations["retryDelivery"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/deliveries": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["listDeliveries"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/internal/artifacts/{artifactVersionId}/content": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["downloadArtifactVersion"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        Organization: {
            /** Format: uuid */
            id: string;
            name: string;
            /** @enum {unknown} */
            status: "active" | "archived";
            revision: number;
        };
        Session: {
            /** Format: uuid */
            user_id: string;
            /** Format: uuid */
            organization_id: string;
            /** Format: uuid */
            membership_id: string;
            roles: ("methodologist" | "reviewer" | "student")[];
            membership_revision: number;
            auth_epoch: number;
            /** @enum {unknown} */
            actor_type: "user" | "agent";
            /** Format: uuid */
            agent_id?: string | null;
        };
        Course: {
            /** Format: uuid */
            id: string;
            title: string;
            /** @enum {unknown} */
            status: "active" | "archived";
            revision: number;
        };
        CourseList: {
            items: components["schemas"]["Course"][];
            course_runs: components["schemas"]["CourseRun"][];
        };
        CourseRun: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            course_id: string;
            title: string;
            timezone: string;
            /** @enum {unknown} */
            status: "draft" | "active" | "archived";
            revision: number;
        };
        CourseRunList: {
            items: components["schemas"]["CourseRun"][];
        };
        OrganizationMembershipList: {
            items: {
                /** Format: uuid */
                id: string;
                /** Format: uuid */
                user_id: string;
                roles: ("methodologist" | "reviewer" | "student")[];
                /** @enum {unknown} */
                status: "active" | "archived";
                revision: number;
                auth_epoch: number;
            }[];
        };
        InvitationList: {
            items: {
                /** Format: uuid */
                id: string;
                /** Format: email */
                normalized_email: string;
                /** @enum {unknown} */
                role: "methodologist" | "reviewer";
                /** @enum {unknown} */
                status: "active" | "consumed" | "revoked" | "expired";
                /** Format: date-time */
                expires_at: string;
                revision: number;
            }[];
        };
        CreatedResource: {
            /** Format: uuid */
            id: string;
            revision: number;
        };
        UpdatedResource: {
            /** Format: uuid */
            id: string;
            revision: number;
        };
        CourseMembershipList: {
            items: {
                /** Format: uuid */
                user_id: string;
                /** @enum {unknown} */
                kind: "student" | "reviewer";
                /** @enum {unknown} */
                status: "active" | "removed" | "archived";
                /** @enum {unknown} */
                source: "imported" | "invitation" | "self_selected";
            }[];
        };
        HomeworkSummary: {
            /** Format: uuid */
            course_run_homework_id: string;
            revision: number;
            /** Format: uuid */
            homework_id: string;
            /** Format: uuid */
            current_version_id: string;
            title: string;
            max_score: number;
            artifact_kinds: ("github" | "google_docs")[];
            /** Format: date-time */
            submission_deadline: string;
            /** Format: date-time */
            review_deadline: string;
        };
        HomeworkList: {
            items: components["schemas"]["HomeworkSummary"][];
        };
        HomeworkHistory: {
            /** Format: uuid */
            homework_id: string;
            homework_revision: number;
            versions: components["schemas"]["HomeworkVersionSummary"][];
            /** @description Append-only publication history; is_current is evaluated independently inside each Course Run. */
            course_run_publications: components["schemas"]["CourseRunHomeworkPublicationSummary"][];
        };
        HomeworkVersionSummary: {
            /** Format: uuid */
            id: string;
            revision: number;
            version_number: number;
            /** Format: uuid */
            criterion_set_id: string;
            student_text: string;
            max_score: number;
            artifact_kinds: ("github" | "google_docs")[];
            estimated_review_minutes: number;
            criteria: components["schemas"]["Criterion"][];
        };
        CourseRunHomeworkPublicationSummary: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            course_run_homework_id: string;
            /** Format: uuid */
            course_run_id: string;
            /** Format: uuid */
            homework_version_id: string;
            publication_sequence: number;
            /** Format: date-time */
            submission_deadline: string;
            /** Format: date-time */
            review_deadline: string;
            /** Format: date-time */
            published_at: string;
            is_current: boolean;
        };
        SubmissionHistory: {
            /** Format: uuid */
            submission_id: string;
            /** Format: uuid */
            course_run_id: string;
            /** Format: uuid */
            homework_id: string;
            /** Format: uuid */
            current_submission_version_id: string | null;
            /** Format: uuid */
            current_publication_id: string | null;
            versions: components["schemas"]["SubmissionVersionSummary"][];
            artifact_versions: components["schemas"]["ArtifactVersionSummary"][];
            review_iterations: components["schemas"]["ReviewIterationSummary"][];
            review_revisions: components["schemas"]["ReviewRevisionSummary"][];
            publications: components["schemas"]["ReviewPublicationSummary"][];
        };
        Criterion: {
            /** Format: uuid */
            id: string;
            key: string;
            title: string;
            description: string;
            max_points: number;
            position: number;
        };
        SubmissionVersionSummary: {
            /** Format: uuid */
            id: string;
            sequence: number;
            /** Format: uuid */
            homework_version_id: string;
            /** Format: uuid */
            artifact_reference_id: string;
            /** Format: date-time */
            submitted_at: string;
            /** Format: date-time */
            effective_deadline: string;
            /** @enum {unknown} */
            phase: "before_deadline" | "revision";
            /** @enum {unknown} */
            status: "validating" | "ready" | "access_error" | "pending_review" | "superseded";
            /** Format: uuid */
            artifact_version_id: string | null;
            /** Format: uuid */
            capture_operation_id: string | null;
        };
        ArtifactVersionSummary: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            artifact_reference_id: string;
            /** @enum {unknown} */
            provider: "github" | "google_docs";
            provider_version: string;
            content_digest: string;
            media_type: string;
            byte_size: number;
            /** Format: date-time */
            captured_at: string;
        };
        ReviewIterationSummary: {
            /** Format: uuid */
            id: string;
            iteration_number: number;
            /** Format: uuid */
            submission_version_id: string;
            /** Format: uuid */
            homework_version_id: string;
            /** Format: uuid */
            criterion_set_id: string;
            /** @enum {unknown} */
            origin: "initial" | "resubmission" | "correction" | "requirements_migration";
            /** @enum {unknown} */
            status: "queued" | "in_review" | "ready_to_publish" | "published" | "canceled";
            revision: number;
        };
        ReviewRevisionSummary: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            review_iteration_id: string;
            revision_number: number;
            /** Format: uuid */
            author_user_id: string;
            feedback: string;
            total_score: number;
            /** Format: date-time */
            created_at: string;
        };
        ReviewPublicationSummary: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            review_iteration_id: string;
            /** Format: uuid */
            review_revision_id: string;
            /** Format: uuid */
            published_by: string;
            /** Format: date-time */
            published_at: string;
            total_score: number;
        };
        ReviewCriterionDecision: {
            /** Format: uuid */
            criterion_id: string;
            /** @description MUST NOT exceed max_points of the matching criterion in the ReviewIteration snapshot. */
            points: number;
            /** @enum {unknown} */
            decision: "accepted" | "changed" | "manual";
            reason: string;
            evidence_ids?: string[];
        };
        ReviewNote: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            criterion_id?: string | null;
            text: string;
            /** Format: uuid */
            author_user_id: string;
            position: number;
        };
        AISuggestion: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            criterion_id: string;
            /** @enum {unknown} */
            status: "suggested" | "needs_human" | "not_checked";
            /** @description When numeric, MUST NOT exceed max_points of the matching criterion in the AI request. */
            proposed_points: number | null;
            reason: string;
            evidence: components["schemas"]["AIEvidence"][];
            /** @enum {unknown} */
            confidence: "low" | "medium" | "high";
            reviewer_note: string | null;
            student_feedback: string | null;
            flags: string[];
        };
        AIEvidence: {
            locator: string;
            quote: string;
            verified: boolean;
        };
        AISignal: {
            /** @enum {unknown} */
            level: "none" | "low" | "medium" | "high" | "insufficient_data";
            evidence: components["schemas"]["AIEvidence"][];
            limitations: string[];
            questions: string[];
        };
        AIReviewSummary: {
            /** Format: uuid */
            run_id: string;
            input_fingerprint: string;
            /** @constant */
            contract_version: "1.1.0";
            /** @enum {unknown} */
            state: "pending" | "running" | "partial" | "succeeded" | "retryable_failed" | "action_required" | "stale";
            attempts: components["schemas"]["OperationAttempt"][];
            suggestions: components["schemas"]["AISuggestion"][];
            signal: components["schemas"]["AISignal"] | null;
            error: components["schemas"]["Error"];
        };
        ResponsibilityEvent: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            reviewer_id: string;
            /** Format: uuid */
            actor_id: string;
            /** @enum {unknown} */
            action: "started" | "joined" | "released" | "completed";
            /** Format: date-time */
            occurred_at: string;
        };
        DeliverySummary: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            operation_id: string;
            /** Format: uuid */
            destination_binding_id: string;
            /** @enum {unknown} */
            destination_kind: "stepik" | "github";
            /** @enum {unknown} */
            state: "pending" | "processing" | "retryable_failed" | "unknown_outcome" | "reconciling" | "succeeded" | "action_required" | "superseded";
            attempts: components["schemas"]["OperationAttempt"][];
            provenance: components["schemas"]["DeliveryProvenance"];
            error: components["schemas"]["Error"];
        };
        DeliveryProvenance: {
            /** Format: uuid */
            course_run_id: string;
            /** Format: uuid */
            homework_version_id: string;
            /** Format: uuid */
            criterion_set_id: string;
            /** Format: uuid */
            submission_version_id: string;
            /** Format: uuid */
            artifact_version_id: string;
            artifact_content_digest: string;
            /** Format: uuid */
            review_iteration_id: string;
            /** Format: uuid */
            review_revision_id: string;
            contract_version: string;
        };
        ReviewImmutableInputs: {
            /** Format: uuid */
            course_run_id: string;
            /** Format: uuid */
            submission_version_id: string;
            /** Format: date-time */
            effective_deadline: string;
            /** Format: uuid */
            artifact_version_id: string;
            artifact_content_digest: string;
            /** Format: uri */
            artifact_download_url: string;
            /** Format: date-time */
            artifact_download_expires_at: string;
            /** Format: uuid */
            homework_version_id: string;
            /** Format: uuid */
            criterion_set_id: string;
            contract_version: string;
        };
        ReviewDetail: {
            /** Format: uuid */
            review_iteration_id: string;
            immutable_inputs: components["schemas"]["ReviewImmutableInputs"];
            /** Format: uuid */
            current_review_revision_id: string | null;
            current_review_revision: components["schemas"]["ReviewRevisionSummary"] | null;
            revision: number;
            /** @enum {unknown} */
            status: "queued" | "in_review" | "ready_to_publish" | "published" | "canceled";
            criterion_decisions: components["schemas"]["ReviewCriterionDecision"][];
            review_notes: components["schemas"]["ReviewNote"][];
            ai_review: components["schemas"]["AIReviewSummary"] | null;
            responsibility_events: components["schemas"]["ResponsibilityEvent"][];
            publication_request: components["schemas"]["PublicationRequest"] | null;
            deliveries: components["schemas"]["DeliverySummary"][];
        };
        PublicationRequest: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            review_revision_id: string;
            /** @enum {unknown} */
            status: "pending" | "confirmed" | "rejected" | "expired" | "superseded";
            /** Format: date-time */
            expires_at: string;
            revision: number;
        };
        AgentAuthorizationCreated: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            agent_id: string;
            scopes: string[];
            /** Format: date-time */
            expires_at: string;
            revision: number;
            readonly access_token: string;
        };
        SubmissionVersionCreated: {
            /** Format: uuid */
            submission_id: string;
            submission_revision: number;
            /** Format: uuid */
            submission_version_id: string;
            submission_version_revision: number;
            /** Format: uuid */
            capture_operation_id: string;
        };
        ReviewIterationCreated: {
            /** Format: uuid */
            review_iteration_id: string;
            /** Format: uuid */
            review_case_id: string;
            revision: number;
        };
        ReviewRevisionSaved: {
            /** Format: uuid */
            review_revision_id: string;
            /** Format: uuid */
            review_iteration_id: string;
            review_iteration_revision: number;
        };
        DeliveryList: {
            items: components["schemas"]["DeliverySummary"][];
        };
        ArtifactCapability: {
            /** @enum {unknown} */
            provider: "github" | "google_docs";
            /** @enum {unknown} */
            read_capability: "available" | "requires_action" | "unavailable";
            /** @enum {unknown} */
            feedback_capability: "available" | "not_supported" | "requires_action";
            /** Format: uuid */
            artifact_reference_id: string | null;
            error: components["schemas"]["Error"];
            /** Format: uuid */
            submission_id: string;
            submission_revision: number;
        } & unknown;
        ReviewRecommendation: {
            /** Format: uuid */
            review_case_id: string;
            review_case_revision: number;
            /** Format: uuid */
            submission_version_id: string;
            reason: string[];
        };
        OperationAttempt: {
            attempt_number: number;
            /** @enum {unknown} */
            state: "processing" | "succeeded" | "retryable_failed" | "unknown_outcome" | "action_required";
            /** Format: date-time */
            started_at: string;
            /** Format: date-time */
            finished_at: string | null;
            error: components["schemas"]["Error"];
        };
        Operation: {
            /** Format: uuid */
            id: string;
            /** @enum {unknown} */
            kind: "course_import" | "artifact_capture" | "ai_review" | "external_delivery" | "email_delivery";
            input_version: string;
            /** @enum {unknown} */
            state: "pending" | "processing" | "partial" | "succeeded" | "retryable_failed" | "unknown_outcome" | "reconciling" | "action_required" | "stale";
            attempts: components["schemas"]["OperationAttempt"][];
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: date-time */
            finished_at: string | null;
            error: components["schemas"]["Error"];
        };
        Publication: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            review_revision_id: string;
            /** Format: uuid */
            published_by: string;
            /** Format: uuid */
            publication_request_id: string | null;
            /** @enum {unknown} */
            status: "published";
            deliveries: components["schemas"]["DeliverySummary"][];
        };
        Error: {
            code: string;
            message: string;
            action: string | null;
        } | null;
        ErrorObject: {
            code: string;
            message: string;
            action: string | null;
        };
        /** Format: uuid */
        uuid: string;
        command_core: {
            request_id: components["schemas"]["uuid"];
            idempotency_key: string;
            /** @enum {unknown} */
            command_name: "activate_bootstrap" | "create_invitation" | "revoke_invitation" | "start_course_import" | "archive_course" | "restore_course" | "archive_course_run" | "restore_course_run" | "change_membership_roles" | "create_homework" | "create_homework_version" | "publish_homework_version" | "set_reviewer_course_selection" | "set_reviewer_availability" | "preflight_submission" | "submit_work" | "open_review_iteration" | "migrate_review_requirements" | "create_review_correction" | "record_review_responsibility" | "save_review_revision" | "request_review_publication" | "publish_review" | "start_ai_review" | "retry_delivery" | "grant_agent_authorization" | "revoke_agent_authorization" | "recover_methodologist";
            /** @enum {unknown} */
            revision_target: "organization" | "membership" | "invitation" | "course" | "course_run" | "course_run_homework" | "homework" | "homework_version" | "submission" | "review_case" | "review_iteration" | "publication_request" | "external_delivery" | "agent_authorization";
            target_id: components["schemas"]["uuid"];
            expected_revision: number;
            payload: Record<string, never>;
        };
        external_identity: {
            provider: string;
            issuer: string;
            subject: string;
        };
        activate_bootstrap_payload: {
            external_identity: components["schemas"]["external_identity"];
        };
        create_invitation_payload: {
            /** Format: email */
            email: string;
            /** @enum {unknown} */
            role: "methodologist" | "reviewer";
            /** Format: date-time */
            expires_at: string;
        };
        reason_payload: {
            reason: string;
        };
        start_course_import_payload: {
            provider: string;
            /** Format: uri */
            external_url: string;
        };
        empty_payload: Record<string, never>;
        change_membership_roles_payload: {
            roles: ("methodologist" | "reviewer" | "student")[];
        };
        create_homework_payload: {
            title: string;
        };
        criterion_input: {
            key: string;
            title: string;
            description: string;
            max_points: number;
        };
        create_homework_version_payload: {
            student_text: string;
            max_score: number;
            artifact_kinds: ("github" | "google_docs")[];
            estimated_review_minutes: number;
            criteria: components["schemas"]["criterion_input"][];
        };
        publish_homework_version_payload: {
            course_run_id: components["schemas"]["uuid"];
            /** Format: date-time */
            submission_deadline: string;
            /** Format: date-time */
            review_deadline: string;
        };
        set_reviewer_course_selection_payload: {
            course_run_ids: components["schemas"]["uuid"][];
        };
        set_reviewer_availability_payload: {
            planned_minutes: number;
            /** Format: date-time */
            until_at: string;
        };
        preflight_submission_payload: {
            /** Format: uri */
            artifact_url: string;
        };
        submit_work_payload: {
            artifact_reference_id: components["schemas"]["uuid"];
        };
        open_review_iteration_payload: {
            submission_version_id: components["schemas"]["uuid"];
        };
        migrate_review_requirements_payload: {
            homework_version_id: components["schemas"]["uuid"];
            criterion_set_id: components["schemas"]["uuid"];
        };
        create_review_correction_payload: {
            published_review_revision_id: components["schemas"]["uuid"];
            reason: string;
        };
        record_review_responsibility_payload: {
            /** @enum {unknown} */
            action: "started" | "joined" | "released" | "completed";
        };
        review_decision: {
            criterion_id: components["schemas"]["uuid"];
            /** @description MUST NOT exceed max_points of the matching criterion in the ReviewIteration snapshot. */
            points: number;
            /** @enum {unknown} */
            decision: "accepted" | "changed" | "manual";
            reason: string;
            evidence_ids?: components["schemas"]["uuid"][];
        };
        review_note: {
            criterion_id?: components["schemas"]["uuid"] | null;
            text: string;
        };
        save_review_revision_payload: {
            feedback: string;
            criterion_decisions: components["schemas"]["review_decision"][];
            review_notes: components["schemas"]["review_note"][];
        };
        request_review_publication_payload: {
            review_revision_id: components["schemas"]["uuid"];
            /** Format: date-time */
            expires_at: string;
        };
        publish_review_payload: {
            review_revision_id: components["schemas"]["uuid"];
            publication_request_id?: components["schemas"]["uuid"] | null;
        };
        retry_delivery_payload: {
            /** @constant */
            reconcile_first: true;
        };
        /** @enum {unknown} */
        agent_scope: "courses:read" | "review_preferences:write" | "review_queue:read" | "reviews:read" | "reviews:write" | "ai_reviews:start" | "operations:read" | "publication_requests:write";
        grant_agent_authorization_payload: {
            agent_id: components["schemas"]["uuid"];
            scopes: components["schemas"]["agent_scope"][];
            /** Format: date-time */
            expires_at: string;
        };
        recover_methodologist_payload: {
            organization_id: components["schemas"]["uuid"];
            provider: string;
            issuer: string;
            subject: string;
            reason: string;
        };
        command_variant: {
            /** @constant */
            command_name?: "activate_bootstrap";
            /** @constant */
            revision_target?: "organization";
            payload?: components["schemas"]["activate_bootstrap_payload"];
        } | {
            /** @constant */
            command_name?: "create_invitation";
            /** @constant */
            revision_target?: "organization";
            payload?: components["schemas"]["create_invitation_payload"];
        } | {
            /** @constant */
            command_name?: "revoke_invitation";
            /** @constant */
            revision_target?: "invitation";
            payload?: components["schemas"]["reason_payload"];
        } | {
            /** @constant */
            command_name?: "start_course_import";
            /** @constant */
            revision_target?: "organization";
            payload?: components["schemas"]["start_course_import_payload"];
        } | {
            /** @constant */
            command_name?: "archive_course";
            /** @constant */
            revision_target?: "course";
            payload?: components["schemas"]["reason_payload"];
        } | {
            /** @constant */
            command_name?: "restore_course";
            /** @constant */
            revision_target?: "course";
            payload?: components["schemas"]["empty_payload"];
        } | {
            /** @constant */
            command_name?: "archive_course_run";
            /** @constant */
            revision_target?: "course_run";
            payload?: components["schemas"]["reason_payload"];
        } | {
            /** @constant */
            command_name?: "restore_course_run";
            /** @constant */
            revision_target?: "course_run";
            payload?: components["schemas"]["empty_payload"];
        } | {
            /** @constant */
            command_name?: "change_membership_roles";
            /** @constant */
            revision_target?: "membership";
            payload?: components["schemas"]["change_membership_roles_payload"];
        } | {
            /** @constant */
            command_name?: "create_homework";
            /** @constant */
            revision_target?: "course_run";
            payload?: components["schemas"]["create_homework_payload"];
        } | {
            /** @constant */
            command_name?: "create_homework_version";
            /** @constant */
            revision_target?: "homework";
            payload?: components["schemas"]["create_homework_version_payload"];
        } | {
            /** @constant */
            command_name?: "publish_homework_version";
            /** @constant */
            revision_target?: "homework_version";
            payload?: components["schemas"]["publish_homework_version_payload"];
        } | {
            /** @constant */
            command_name?: "set_reviewer_course_selection";
            /** @constant */
            revision_target?: "membership";
            payload?: components["schemas"]["set_reviewer_course_selection_payload"];
        } | {
            /** @constant */
            command_name?: "set_reviewer_availability";
            /** @constant */
            revision_target?: "membership";
            payload?: components["schemas"]["set_reviewer_availability_payload"];
        } | {
            /** @constant */
            command_name?: "preflight_submission";
            /** @constant */
            revision_target?: "course_run_homework";
            payload?: components["schemas"]["preflight_submission_payload"];
        } | {
            /** @constant */
            command_name?: "submit_work";
            /** @constant */
            revision_target?: "submission";
            payload?: components["schemas"]["submit_work_payload"];
        } | {
            /** @constant */
            command_name?: "open_review_iteration";
            /** @constant */
            revision_target?: "review_case";
            payload?: components["schemas"]["open_review_iteration_payload"];
        } | {
            /** @constant */
            command_name?: "migrate_review_requirements";
            /** @constant */
            revision_target?: "review_iteration";
            payload?: components["schemas"]["migrate_review_requirements_payload"];
        } | {
            /** @constant */
            command_name?: "create_review_correction";
            /** @constant */
            revision_target?: "review_iteration";
            payload?: components["schemas"]["create_review_correction_payload"];
        } | {
            /** @constant */
            command_name?: "record_review_responsibility";
            /** @constant */
            revision_target?: "review_iteration";
            payload?: components["schemas"]["record_review_responsibility_payload"];
        } | {
            /** @constant */
            command_name?: "save_review_revision";
            /** @constant */
            revision_target?: "review_iteration";
            payload?: components["schemas"]["save_review_revision_payload"];
        } | {
            /** @constant */
            command_name?: "request_review_publication";
            /** @constant */
            revision_target?: "review_iteration";
            payload?: components["schemas"]["request_review_publication_payload"];
        } | {
            /** @constant */
            command_name?: "publish_review";
            /** @constant */
            revision_target?: "review_iteration";
            payload?: components["schemas"]["publish_review_payload"];
        } | {
            /** @constant */
            command_name?: "start_ai_review";
            /** @constant */
            revision_target?: "review_iteration";
            payload?: components["schemas"]["empty_payload"];
        } | {
            /** @constant */
            command_name?: "retry_delivery";
            /** @constant */
            revision_target?: "external_delivery";
            payload?: components["schemas"]["retry_delivery_payload"];
        } | {
            /** @constant */
            command_name?: "grant_agent_authorization";
            /** @constant */
            revision_target?: "membership";
            payload?: components["schemas"]["grant_agent_authorization_payload"];
        } | {
            /** @constant */
            command_name?: "revoke_agent_authorization";
            /** @constant */
            revision_target?: "agent_authorization";
            payload?: components["schemas"]["reason_payload"];
        } | {
            /** @constant */
            command_name?: "recover_methodologist";
            /** @constant */
            revision_target?: "organization";
            payload?: components["schemas"]["recover_methodologist_payload"];
        };
        wire: components["schemas"]["command_core"] & components["schemas"]["command_variant"];
        create_invitation: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "create_invitation";
        };
        revoke_invitation: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "revoke_invitation";
        };
        start_course_import: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "start_course_import";
        };
        create_homework: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "create_homework";
        };
        archive_course_run: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "archive_course_run";
        };
        restore_course_run: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "restore_course_run";
        };
        archive_course: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "archive_course";
        };
        restore_course: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "restore_course";
        };
        change_membership_roles: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "change_membership_roles";
        };
        create_homework_version: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "create_homework_version";
        };
        publish_homework_version: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "publish_homework_version";
        };
        set_reviewer_course_selection: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "set_reviewer_course_selection";
        };
        set_reviewer_availability: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "set_reviewer_availability";
        };
        preflight_submission: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "preflight_submission";
        };
        submit_work: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "submit_work";
        };
        open_review_iteration: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "open_review_iteration";
        };
        save_review_revision: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "save_review_revision";
        };
        migrate_review_requirements: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "migrate_review_requirements";
        };
        create_review_correction: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "create_review_correction";
        };
        record_review_responsibility: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "record_review_responsibility";
        };
        publish_review: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "publish_review";
        };
        request_review_publication: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "request_review_publication";
        };
        start_ai_review: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "start_ai_review";
        };
        grant_agent_authorization: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "grant_agent_authorization";
        };
        revoke_agent_authorization: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "revoke_agent_authorization";
        };
        evidence: {
            locator: string;
            quote: string;
            verified: boolean;
        };
        aiSignal: {
            /** @enum {unknown} */
            level: "none" | "low" | "medium" | "high" | "insufficient_data";
            evidence: components["schemas"]["evidence"][];
            limitations: string[];
            questions: string[];
        };
        error: {
            code: string;
            message: string;
            retryable: boolean;
        };
        suggestion: {
            /** Format: uuid */
            criterion_id: string;
            /** @enum {unknown} */
            status: "suggested" | "needs_human" | "not_checked";
            /** @description When numeric, MUST NOT exceed max_points of the matching criterion from this request. */
            proposed_points: number | null;
            reason: string;
            evidence: components["schemas"]["evidence"][];
            /** @enum {unknown} */
            confidence: "low" | "medium" | "high";
            reviewer_note: string | null;
            student_feedback: string | null;
            flags: string[];
        } & unknown;
        /** @description Backend validates both ID arrays against the request and verifies one suggestion per reported criterion ID. */
        criterionCoverage: {
            expected_criterion_ids: string[];
            reported_criterion_ids: string[];
            complete: boolean;
        };
        event: {
            /** @constant */
            contract_version: "1.1.0";
            /** Format: uuid */
            run_id: string;
            /** Format: uuid */
            attempt_id: string;
            attempt_number: number;
            /** Format: uuid */
            event_id: string;
            sequence: number;
            input_fingerprint: string;
            /** @enum {unknown} */
            status: "running" | "partial" | "succeeded" | "retryable_failed" | "action_required";
            is_final: boolean;
            suggestions: components["schemas"]["suggestion"][];
            criterion_coverage: components["schemas"]["criterionCoverage"];
            ai_signal: components["schemas"]["aiSignal"] | null;
            error: components["schemas"]["error"] | null;
        } & (unknown & unknown & unknown);
        retry_delivery: components["schemas"]["wire"] & {
            /** @constant */
            command_name?: "retry_delivery";
        };
    };
    responses: {
        /** @description Expected revision or current-state conflict */
        Conflict: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/json": components["schemas"]["ErrorObject"];
            };
        };
    };
    parameters: {
        CourseRunId: string;
        HomeworkId: string;
        ReviewIterationId: string;
        SubmissionId: string;
        CourseRunHomeworkId: string;
    };
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    completeStepikAuthentication: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Server session established */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Invalid OAuth response */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorObject"];
                };
            };
        };
    };
    startStepikAuthentication: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Redirect to Stepik authorization */
            302: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    getCurrentSession: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Current user, organization, roles, auth epoch and agent context */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Session"];
                };
            };
        };
    };
    revokeCurrentSession: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Session revoked */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    consumeReviewerMagicLink: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    token: string;
                    /** Format: uuid */
                    state_id: string;
                };
            };
        };
        responses: {
            /** @description Invitation consumed and session established */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Invalid, expired, used, or mismatched invitation */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorObject"];
                };
            };
        };
    };
    getOrganization: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Current organization */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Organization"];
                };
            };
        };
    };
    listCourses: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Courses visible to the current actor */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CourseList"];
                };
            };
        };
    };
    listCourseRuns: {
        parameters: {
            query?: {
                course_id?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Course runs visible to the current actor */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CourseRunList"];
                };
            };
        };
    };
    listOrganizationMemberships: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Organization memberships with identifiers required for role changes */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OrganizationMembershipList"];
                };
            };
        };
    };
    listInvitations: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Invitations visible to the current methodologist */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvitationList"];
                };
            };
        };
    };
    createInvitation: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["create_invitation"];
            };
        };
        responses: {
            /** @description Invitation created */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CreatedResource"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    revokeInvitation: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                invitationId: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["revoke_invitation"];
            };
        };
        responses: {
            /** @description Invitation revoked */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            409: components["responses"]["Conflict"];
        };
    };
    startCourseImport: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["start_course_import"];
            };
        };
        responses: {
            /** @description Import accepted */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Operation"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    listPublishedHomeworks: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                courseRunId: components["parameters"]["CourseRunId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Published homework summaries visible to the current actor */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HomeworkList"];
                };
            };
        };
    };
    createHomework: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                courseRunId: components["parameters"]["CourseRunId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["create_homework"];
            };
        };
        responses: {
            /** @description Homework created */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CreatedResource"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    listCourseRunMemberships: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                courseRunId: components["parameters"]["CourseRunId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Current and historically relevant course-run memberships */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CourseMembershipList"];
                };
            };
        };
    };
    archiveCourseRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                courseRunId: components["parameters"]["CourseRunId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["archive_course_run"];
            };
        };
        responses: {
            /** @description Course run archived with history preserved */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            409: components["responses"]["Conflict"];
        };
    };
    restoreCourseRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                courseRunId: components["parameters"]["CourseRunId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["restore_course_run"];
            };
        };
        responses: {
            /** @description Course run restored */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            409: components["responses"]["Conflict"];
        };
    };
    archiveCourse: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                courseId: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["archive_course"];
            };
        };
        responses: {
            /** @description Course archived */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            409: components["responses"]["Conflict"];
        };
    };
    restoreCourse: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                courseId: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["restore_course"];
            };
        };
        responses: {
            /** @description Course restored */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            409: components["responses"]["Conflict"];
        };
    };
    changeMembershipRoles: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                membershipId: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["change_membership_roles"];
            };
        };
        responses: {
            /** @description Roles changed and auth epoch advanced */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UpdatedResource"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    createHomeworkVersion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                homeworkId: components["parameters"]["HomeworkId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["create_homework_version"];
            };
        };
        responses: {
            /** @description Version created */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CreatedResource"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    getHomeworkHistory: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                homeworkId: components["parameters"]["HomeworkId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Current published homework and immutable version history */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HomeworkHistory"];
                };
            };
        };
    };
    publishHomeworkVersion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                homeworkVersionId: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["publish_homework_version"];
            };
        };
        responses: {
            /** @description Version published to Course Run */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CreatedResource"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    setReviewerCourseSelection: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["set_reviewer_course_selection"];
            };
        };
        responses: {
            /** @description Selection updated */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            409: components["responses"]["Conflict"];
        };
    };
    setReviewerAvailability: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["set_reviewer_availability"];
            };
        };
        responses: {
            /** @description Availability plan updated */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UpdatedResource"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    preflightSubmission: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                courseRunHomeworkId: components["parameters"]["CourseRunHomeworkId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["preflight_submission"];
            };
        };
        responses: {
            /** @description Artifact capability result */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ArtifactCapability"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    submitWork: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                submissionId: components["parameters"]["SubmissionId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["submit_work"];
            };
        };
        responses: {
            /** @description Submission version created */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SubmissionVersionCreated"];
                };
            };
            409: components["responses"]["Conflict"];
            /** @description Artifact inaccessible or unsupported */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorObject"];
                };
            };
        };
    };
    getSubmissionHistory: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                submissionId: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Submission versions, review iterations, publications, and effective requirements */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SubmissionHistory"];
                };
            };
        };
    };
    openReviewIteration: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                reviewCaseId: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["open_review_iteration"];
            };
        };
        responses: {
            /** @description Review iteration opened */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewIterationCreated"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    recommendNextReview: {
        parameters: {
            query: {
                course_run_id: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Recommendation or empty queue */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewRecommendation"] | null;
                };
            };
        };
    };
    saveReviewRevision: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                reviewIterationId: components["parameters"]["ReviewIterationId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["save_review_revision"];
            };
        };
        responses: {
            /** @description Revision saved */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewRevisionSaved"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    migrateReviewRequirements: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                reviewIterationId: components["parameters"]["ReviewIterationId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["migrate_review_requirements"];
            };
        };
        responses: {
            /** @description Successor review iteration created for new requirements */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewIterationCreated"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    createReviewCorrection: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                reviewIterationId: components["parameters"]["ReviewIterationId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["create_review_correction"];
            };
        };
        responses: {
            /** @description Correction review iteration created without mutating the publication */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewIterationCreated"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    recordReviewResponsibility: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                reviewIterationId: components["parameters"]["ReviewIterationId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["record_review_responsibility"];
            };
        };
        responses: {
            /** @description Non-exclusive responsibility event recorded */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CreatedResource"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    getReviewIteration: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                reviewIterationId: components["parameters"]["ReviewIterationId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Review iteration with immutable inputs, human decisions, notes, AI suggestions, responsibility, publication request, and deliveries */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewDetail"];
                };
            };
        };
    };
    publishReview: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                reviewIterationId: components["parameters"]["ReviewIterationId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["publish_review"];
            };
        };
        responses: {
            /** @description Local publication committed and deliveries queued */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Publication"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    requestReviewPublication: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                reviewIterationId: components["parameters"]["ReviewIterationId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["request_review_publication"];
            };
        };
        responses: {
            /** @description Publication request created without publishing or creating deliveries */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PublicationRequest"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    startAIReview: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                reviewIterationId: components["parameters"]["ReviewIterationId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["start_ai_review"];
            };
        };
        responses: {
            /** @description Existing or new AI review run */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Operation"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    grantAgentAuthorization: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                membershipId: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["grant_agent_authorization"];
            };
        };
        responses: {
            /** @description Scoped expiring agent authorization granted */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AgentAuthorizationCreated"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    revokeAgentAuthorization: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                agentAuthorizationId: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["revoke_agent_authorization"];
            };
        };
        responses: {
            /** @description Authorization and pending agent commands revoked */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            409: components["responses"]["Conflict"];
        };
    };
    acceptAIReviewEvent: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["event"];
            };
        };
        responses: {
            /** @description Event accepted or deduplicated */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            409: components["responses"]["Conflict"];
        };
    };
    getOperation: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operationId: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Operation state */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Operation"];
                };
            };
        };
    };
    retryDelivery: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                deliveryId: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["retry_delivery"];
            };
        };
        responses: {
            /** @description Retry or reconciliation accepted */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Operation"];
                };
            };
            409: components["responses"]["Conflict"];
        };
    };
    listDeliveries: {
        parameters: {
            query?: {
                state?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Delivery exceptions and current states */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeliveryList"];
                };
            };
        };
    };
    downloadArtifactVersion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                artifactVersionId: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Immutable artifact bytes with digest header */
            200: {
                headers: {
                    Digest?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/octet-stream": string;
                };
            };
            /** @description Component is not authorized for this organization or run */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorObject"];
                };
            };
        };
    };
}
