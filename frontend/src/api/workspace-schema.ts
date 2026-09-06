export interface paths {
    "/v2/artifacts/{identity}/download": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Artifact Download */
        get: operations["artifact_download_api_v2_artifacts__identity__download_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/catalog": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Catalog */
        get: operations["catalog_api_v2_catalog_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/course-run-homeworks/{identity}/draft": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Save Draft */
        post: operations["save_draft_api_v2_course_run_homeworks__identity__draft_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/course-run-homeworks/{identity}/policy": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Policy */
        get: operations["get_policy_api_v2_course_run_homeworks__identity__policy_get"];
        put?: never;
        /** Set Policy */
        post: operations["set_policy_api_v2_course_run_homeworks__identity__policy_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/course-run-homeworks/{identity}/sources": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Submission Sources */
        get: operations["get_submission_sources_api_v2_course_run_homeworks__identity__sources_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/course-run-homeworks/{identity}/student-context": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Student Context */
        get: operations["student_context_api_v2_course_run_homeworks__identity__student_context_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/course-runs/{identity}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Update Course Run */
        post: operations["update_course_run_api_v2_course_runs__identity__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/course-runs/{identity}/assignments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Assignments */
        get: operations["assignments_api_v2_course_runs__identity__assignments_get"];
        put?: never;
        /** Assign */
        post: operations["assign_api_v2_course_runs__identity__assignments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/course-runs/{identity}/insights": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Insights */
        get: operations["insights_api_v2_course_runs__identity__insights_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/course-runs/{identity}/memberships": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Membership */
        post: operations["membership_api_v2_course_runs__identity__memberships_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/course-runs/{identity}/priority": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Set Run Priority */
        post: operations["set_run_priority_api_v2_course_runs__identity__priority_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/course-runs/{identity}/reminders": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Remind */
        post: operations["remind_api_v2_course_runs__identity__reminders_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/courses": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Course */
        post: operations["create_course_api_v2_courses_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/courses/{identity}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Update Course */
        post: operations["update_course_api_v2_courses__identity__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/courses/{identity}/course-runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Run */
        post: operations["create_run_api_v2_courses__identity__course_runs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/courses/{identity}/homeworks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Homeworks */
        get: operations["homeworks_api_v2_courses__identity__homeworks_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/directory": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Directory */
        get: operations["directory_api_v2_directory_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/drafts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Drafts */
        get: operations["drafts_api_v2_drafts_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/exports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Export */
        post: operations["create_export_api_v2_exports_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/exports/{identity}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Export */
        get: operations["get_export_api_v2_exports__identity__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/homework-versions/{identity}/private-details": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Private */
        get: operations["get_private_api_v2_homework_versions__identity__private_details_get"];
        put?: never;
        /** Save Private */
        post: operations["save_private_api_v2_homework_versions__identity__private_details_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/homework-versions/{identity}/publish": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Publish Workspace */
        post: operations["publish_workspace_api_v2_homework_versions__identity__publish_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/homeworks/{identity}/editor-draft": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Editor Draft */
        get: operations["editor_draft_api_v2_homeworks__identity__editor_draft_get"];
        put?: never;
        /** Save Editor Draft */
        post: operations["save_editor_draft_api_v2_homeworks__identity__editor_draft_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/homeworks/{identity}/title": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Update Workspace Homework */
        post: operations["update_workspace_homework_api_v2_homeworks__identity__title_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/internal/self-review/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Self Review Event */
        post: operations["self_review_event_api_v2_internal_self_review_events_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/notifications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Notifications */
        get: operations["notifications_api_v2_notifications_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/notifications/{identity}/read": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Read Notification */
        post: operations["read_notification_api_v2_notifications__identity__read_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/preparations/{identity}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Preparation */
        get: operations["preparation_api_v2_preparations__identity__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/review-assists/{identity}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Assist */
        get: operations["get_assist_api_v2_review_assists__identity__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/review-assists/{identity}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry Assist */
        post: operations["retry_assist_api_v2_review_assists__identity__retry_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/reviewer/preferences": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Preferences */
        get: operations["preferences_api_v2_reviewer_preferences_get"];
        put?: never;
        /** Save Preferences */
        post: operations["save_preferences_api_v2_reviewer_preferences_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/reviews/{identity}/assist": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Latest Assist */
        get: operations["latest_assist_api_v2_reviews__identity__assist_get"];
        put?: never;
        /** Start Assist */
        post: operations["start_assist_api_v2_reviews__identity__assist_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/reviews/{identity}/context": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Review Context */
        get: operations["review_context_api_v2_reviews__identity__context_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/reviews/{identity}/draft": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Review Draft */
        get: operations["get_review_draft_api_v2_reviews__identity__draft_get"];
        put?: never;
        /** Save Assisted Draft */
        post: operations["save_assisted_draft_api_v2_reviews__identity__draft_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/reviews/{identity}/grade-preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Preview Grade */
        get: operations["preview_grade_api_v2_reviews__identity__grade_preview_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/reviews/{identity}/outcome": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Save Outcome */
        post: operations["save_outcome_api_v2_reviews__identity__outcome_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/reviews/{identity}/publish": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Publish Workspace Review */
        post: operations["publish_workspace_review_api_v2_reviews__identity__publish_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/reviews/{identity}/requirements": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Add Review Requirement */
        post: operations["add_review_requirement_api_v2_reviews__identity__requirements_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/search": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Search Workspace */
        get: operations["search_workspace_api_v2_search_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/self-reviews/{identity}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Self Review */
        get: operations["self_review_api_v2_self_reviews__identity__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/self-reviews/{identity}/release": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Release Self Review */
        post: operations["release_self_review_api_v2_self_reviews__identity__release_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/statistics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Statistics */
        get: operations["statistics_api_v2_statistics_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/student/homeworks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Student Homeworks */
        get: operations["list_student_homeworks_api_v2_student_homeworks_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/submissions/{identity}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Student Submission */
        get: operations["get_student_submission_api_v2_submissions__identity__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/submissions/{identity}/open-review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Open Work */
        post: operations["open_work_api_v2_submissions__identity__open_review_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/uploads": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Upload Artifact */
        post: operations["upload_artifact_api_v2_uploads_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/work-drafts/{identity}/prepare": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Prepare Work Draft */
        post: operations["prepare_work_draft_api_v2_work_drafts__identity__prepare_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/work-drafts/{identity}/self-reviews": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Start Self Review */
        post: operations["start_self_review_api_v2_work_drafts__identity__self_reviews_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/work-drafts/{identity}/submit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Submit Work Draft */
        post: operations["submit_work_draft_api_v2_work_drafts__identity__submit_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/work-drafts/{identity}/submit-upload": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Submit Upload */
        post: operations["submit_upload_api_v2_work_drafts__identity__submit_upload_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/works": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Works */
        get: operations["works_api_v2_works_get"];
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
        /** AssignmentInput */
        AssignmentInput: {
            /** Reason */
            reason: string;
            /** Reviewer Id */
            reviewer_id: string | null;
            /**
             * Student Id
             * Format: uuid
             */
            student_id: string;
        };
        /** AssignmentView */
        AssignmentView: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Reviewer Id */
            reviewer_id: string | null;
            /** Reviewer Name */
            reviewer_name: string | null;
            /** Revision */
            revision: number;
            /**
             * Student Id
             * Format: uuid
             */
            student_id: string;
            /** Student Name */
            student_name: string;
        };
        /** AssignmentsView */
        AssignmentsView: {
            /** Items */
            items: components["schemas"]["AssignmentView"][];
        };
        /** AssistEvidence */
        AssistEvidence: {
            /** Line End */
            line_end?: number | null;
            /** Line Start */
            line_start?: number | null;
            /** Locator */
            locator?: string | null;
            /** Path */
            path?: string | null;
            /**
             * Polarity
             * @description supports подтверждает выполнение, contradicts объясняет снижение балла
             */
            polarity?: ("supports" | "contradicts") | null;
            /** Quote */
            quote: string;
            /**
             * Verified
             * @default false
             * @constant
             */
            verified: false;
        };
        /** AuthorshipSignal */
        AuthorshipSignal: {
            /** Evidence */
            evidence?: components["schemas"]["AssistEvidence"][];
            /** Explanation */
            explanation: string;
            /** Id */
            id: string;
            /** Probability */
            probability?: number | null;
        };
        /** CatalogView */
        CatalogView: {
            /** Course Runs */
            course_runs: components["schemas"]["CourseRunView"][];
            /** Courses */
            courses: components["schemas"]["CourseView"][];
        };
        /** CoordinatorHomeworkItem */
        CoordinatorHomeworkItem: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Latest Version Number */
            latest_version_number: number | null;
            /** Published Run Ids */
            published_run_ids: string[];
            /** Revision */
            revision: number;
            /** Title */
            title: string;
        };
        /** CoordinatorHomeworkList */
        CoordinatorHomeworkList: {
            /**
             * Course Id
             * Format: uuid
             */
            course_id: string;
            /** Items */
            items: components["schemas"]["CoordinatorHomeworkItem"][];
        };
        /** CourseInput */
        CourseInput: {
            /**
             * Description
             * @default
             */
            description: string;
            /** Owner Id */
            owner_id?: string | null;
            /** Stepik Url */
            stepik_url?: string | null;
            /** Title */
            title: string;
        };
        /** CourseRunInput */
        CourseRunInput: {
            /**
             * Ends At
             * Format: date-time
             */
            ends_at: string;
            /**
             * Priority
             * @default assigned
             * @enum {string}
             */
            priority: "assigned" | "deadline";
            /**
             * Starts At
             * Format: date-time
             */
            starts_at: string;
            /**
             * Timezone
             * @default Europe/Moscow
             */
            timezone: string;
            /** Title */
            title: string;
        };
        /** CourseRunView */
        CourseRunView: {
            /**
             * Course Id
             * Format: uuid
             */
            course_id: string;
            /** Ends At */
            ends_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Priority
             * @enum {string}
             */
            priority: "assigned" | "deadline";
            /** Priority Revision */
            priority_revision: number;
            /**
             * Reviewer Count
             * @default 0
             */
            reviewer_count: number;
            /** Revision */
            revision: number;
            /** Starts At */
            starts_at: string | null;
            /** Status */
            status: string;
            /** Timezone */
            timezone: string;
            /** Title */
            title: string;
        };
        /** CourseView */
        CourseView: {
            /** Description */
            description: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Owner Id */
            owner_id: string | null;
            /** Revision */
            revision: number;
            /** Status */
            status: string;
            /** Stepik Url */
            stepik_url?: string | null;
            /** Title */
            title: string;
        };
        /** CriterionChangeStatistic */
        CriterionChangeStatistic: {
            /** Change Percent */
            change_percent: number;
            /** Changed Works */
            changed_works: number;
            /** Compared Works */
            compared_works: number;
            /**
             * Criterion Id
             * Format: uuid
             */
            criterion_id: string;
            /** Title */
            title: string;
        };
        /** CriterionSettings */
        CriterionSettings: {
            /**
             * Evaluate Quality
             * @default false
             */
            evaluate_quality: boolean;
            /**
             * Score Step
             * @default 0.5
             */
            score_step: number;
        };
        /** DirectoryMember */
        DirectoryMember: {
            /**
             * Absent From
             * @default null
             */
            absent_from: string | null;
            /**
             * Absent Until
             * @default null
             */
            absent_until: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Display Name */
            display_name: string;
            /** Roles */
            roles: string[];
        };
        /** DirectoryView */
        DirectoryView: {
            /** Items */
            items: components["schemas"]["DirectoryMember"][];
        };
        /** DownloadView */
        DownloadView: {
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /** Filename */
            filename?: string | null;
            /** Url */
            url: string;
        };
        /** DraftInput */
        DraftInput: {
            /**
             * Artifact Url
             * @default
             */
            artifact_url: string;
            /**
             * Comment
             * @default
             */
            comment: string;
            /** Upload Id */
            upload_id?: string | null;
        };
        /** DraftList */
        DraftList: {
            /** Items */
            items: components["schemas"]["DraftView"][];
        };
        /** DraftView */
        DraftView: {
            /** Artifact Url */
            artifact_url: string;
            /** Comment */
            comment: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Publication Id
             * Format: uuid
             */
            publication_id: string;
            /** Revision */
            revision: number;
            /** Upload Id */
            upload_id: string | null;
        };
        /** EditorCriterion */
        EditorCriterion: {
            /**
             * Check Class
             * @default content
             * @enum {string}
             */
            check_class: "formal" | "content" | "judgement";
            /**
             * Description
             * @default
             */
            description: string;
            /**
             * Evaluate Quality
             * @default false
             */
            evaluate_quality: boolean;
            /** Key */
            key: string;
            /**
             * Max Points
             * @default 0
             */
            max_points: number;
            /**
             * Score Step
             * @default 0.5
             */
            score_step: number;
            /**
             * Title
             * @default
             */
            title: string;
        };
        /** EditorDraftInput */
        EditorDraftInput: {
            /** Allowed Sources */
            allowed_sources?: ("upload" | "github" | "google_docs")[];
            /** Artifact Kinds */
            artifact_kinds?: ("github" | "google_docs")[];
            /**
             * Course Run Id
             * Format: uuid
             */
            course_run_id: string;
            /** Criteria */
            criteria?: components["schemas"]["EditorCriterion"][];
            /**
             * Estimated Review Minutes
             * @default 30
             */
            estimated_review_minutes: number;
            /** Material Upload Ids */
            material_upload_ids?: string[];
            /**
             * Max Score
             * @default 0
             */
            max_score: number;
            policy?: components["schemas"]["PublicationPolicyInput"] | null;
            /** Reference Upload Id */
            reference_upload_id?: string | null;
            /** Review Deadline */
            review_deadline?: string | null;
            /**
             * Reviewer Guidance
             * @default
             */
            reviewer_guidance: string;
            /**
             * Student Text
             * @default
             */
            student_text: string;
            /** Submission Deadline */
            submission_deadline?: string | null;
        };
        /** EditorDraftView */
        EditorDraftView: {
            /**
             * Homework Revision
             * @default 0
             */
            homework_revision: number;
            /**
             * Homework Title
             * @default
             */
            homework_title: string;
            /** Revision */
            revision: number;
            value: components["schemas"]["EditorDraftInput"] | null;
        };
        /** EmptyInput */
        EmptyInput: Record<string, never>;
        /** ExportInput */
        ExportInput: {
            /**
             * Audience
             * @enum {string}
             */
            audience: "team" | "students";
            /** Columns */
            columns: ("student_id" | "score" | "status" | "attempt" | "feedback" | "reviewer_id" | "criterion_points" | "artifact_url")[];
            /**
             * Course Run Id
             * Format: uuid
             */
            course_run_id: string;
            /**
             * Format
             * @enum {string}
             */
            format: "csv" | "xlsx";
            /** Homework Id */
            homework_id?: string | null;
            /**
             * Include Unpublished
             * @default false
             */
            include_unpublished: boolean;
        };
        /** ExportView */
        ExportView: {
            download: components["schemas"]["DownloadView"] | null;
            /** Error */
            error: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Rows */
            rows: number;
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "processing" | "succeeded" | "failed";
        };
        /** ExtraRequirementInput */
        ExtraRequirementInput: {
            /**
             * Description
             * @default
             */
            description: string;
            /** Max Points */
            max_points: number;
            /** Title */
            title: string;
        };
        /** GradePreview */
        GradePreview: {
            /** Final Score */
            final_score: number;
            /** Pass Score */
            pass_score: number | null;
            /** Penalty */
            penalty: number;
            /** Penalty Days */
            penalty_days: number;
            /** Penalty Rate */
            penalty_rate: number;
            /** Policy Revision */
            policy_revision: number | null;
            /** Raw Score */
            raw_score: number;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** HomeworkTitleInput */
        HomeworkTitleInput: {
            /** Title */
            title: string;
        };
        /** LocalIdentities */
        LocalIdentities: {
            /** Enabled */
            enabled: boolean;
            /** Items */
            items: components["schemas"]["LocalIdentity"][];
        };
        /** LocalIdentity */
        LocalIdentity: {
            /** Key */
            key: string;
            /** Label */
            label: string;
            /** Roles */
            roles: string[];
        };
        /** LocalLoginInput */
        LocalLoginInput: {
            /** Identity */
            identity: string;
        };
        /** MembershipInput */
        MembershipInput: {
            /**
             * Active
             * @default true
             */
            active: boolean;
            /**
             * Kind
             * @enum {string}
             */
            kind: "student" | "reviewer";
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /** NotificationInput */
        NotificationInput: {
            /** Reviewer Ids */
            reviewer_ids: string[];
            /** Text */
            text: string;
        };
        /** NotificationPreferences */
        NotificationPreferences: {
            /**
             * Deadline
             * @default true
             */
            deadline: boolean;
            /**
             * Pool
             * @default false
             */
            pool: boolean;
            /**
             * Revision
             * @default true
             */
            revision: boolean;
        };
        /** NotificationView */
        NotificationView: {
            /**
             * Course Run Id
             * Format: uuid
             */
            course_run_id: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Read */
            read: boolean;
            /** Text */
            text: string;
        };
        /** NotificationsView */
        NotificationsView: {
            /** Items */
            items: components["schemas"]["NotificationView"][];
        };
        /** OpenWorkInput */
        OpenWorkInput: {
            /**
             * Submission Version Id
             * Format: uuid
             */
            submission_version_id: string;
        };
        /** OutcomeInput */
        OutcomeInput: {
            /**
             * Decision
             * @enum {string}
             */
            decision: "needs_changes" | "passed" | "failed";
            /** Reason */
            reason: string;
            /** Revision Deadline */
            revision_deadline?: string | null;
        };
        /** PeerComparisonStatistic */
        PeerComparisonStatistic: {
            /**
             * Compared Criteria
             * @default 0
             */
            compared_criteria: number;
            /** Course Divergence Percent */
            course_divergence_percent?: number | null;
            /** Divergence Percent */
            divergence_percent?: number | null;
            /**
             * Fully Agreed Criteria
             * @default 0
             */
            fully_agreed_criteria: number;
            /**
             * Sample Count
             * @default 0
             */
            sample_count: number;
            /** Softer Criteria */
            softer_criteria?: string[];
            /** Stricter Criteria */
            stricter_criteria?: string[];
        };
        /** PreferencesInput */
        PreferencesInput: {
            /** Absent From */
            absent_from?: string | null;
            /** Absent Until */
            absent_until?: string | null;
            /** Course Run Ids */
            course_run_ids: string[];
            notifications?: components["schemas"]["NotificationPreferences"];
            /** Planned Minutes */
            planned_minutes: number;
            /**
             * Show Pool
             * @default true
             */
            show_pool: boolean;
            /**
             * Until At
             * Format: date-time
             */
            until_at: string;
        };
        /** PreferencesView */
        PreferencesView: {
            /** Revision */
            revision: number;
            value: components["schemas"]["PreferencesInput"] | null;
        };
        /** PreparationView */
        PreparationView: {
            /** Artifact Id */
            artifact_id: string | null;
            /** Draft Revision */
            draft_revision: number;
            /** Error Code */
            error_code: string | null;
            /** Filename */
            filename: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Status
             * @enum {string}
             */
            status: "pending" | "processing" | "succeeded" | "failed";
        };
        /** PriorityInput */
        PriorityInput: {
            /**
             * Priority
             * @enum {string}
             */
            priority: "assigned" | "deadline";
        };
        /** PrivateHomeworkInput */
        PrivateHomeworkInput: {
            /** Allowed Sources */
            allowed_sources?: ("upload" | "github" | "google_docs")[];
            /** Criterion Classes */
            criterion_classes?: {
                [key: string]: "formal" | "content" | "judgement";
            };
            /** Criterion Settings */
            criterion_settings?: {
                [key: string]: components["schemas"]["CriterionSettings"];
            };
            /** Material Upload Ids */
            material_upload_ids?: string[];
            /** Reference Upload Id */
            reference_upload_id?: string | null;
            /**
             * Reviewer Guidance
             * @default
             */
            reviewer_guidance: string;
        };
        /** PrivateHomeworkView */
        PrivateHomeworkView: {
            /** Allowed Sources */
            allowed_sources?: ("upload" | "github" | "google_docs")[];
            /** Criterion Classes */
            criterion_classes?: {
                [key: string]: "formal" | "content" | "judgement";
            };
            /** Criterion Settings */
            criterion_settings?: {
                [key: string]: components["schemas"]["CriterionSettings"];
            };
            /** Material Upload Ids */
            material_upload_ids?: string[];
            /** Reference Upload Id */
            reference_upload_id?: string | null;
            /**
             * Reviewer Guidance
             * @default
             */
            reviewer_guidance: string;
            /** Revision */
            revision: number;
        };
        /** ProfileView */
        ProfileView: {
            /** Display Name */
            display_name: string;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /** PublicCriterion */
        PublicCriterion: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Key */
            key: string;
            /**
             * Max Points
             * @default 0
             */
            max_points: number;
            /** Title */
            title: string;
        };
        /** PublicationPolicyInput */
        PublicationPolicyInput: {
            /**
             * Max Resubmissions
             * @default 3
             */
            max_resubmissions: number;
            /**
             * Pass Score
             * @default 0
             */
            pass_score: number;
            /**
             * Penalty Per Day
             * @default 0
             */
            penalty_per_day: number;
            /**
             * Revision Days
             * @default 7
             */
            revision_days: number;
            /** Self Review Limit */
            self_review_limit: number;
        };
        /** PublicationPolicyView */
        PublicationPolicyView: {
            /**
             * Max Resubmissions
             * @default 3
             */
            max_resubmissions: number;
            /**
             * Pass Score
             * @default 0
             */
            pass_score: number;
            /**
             * Penalty Per Day
             * @default 0
             */
            penalty_per_day: number;
            /** Revision */
            revision: number;
            /**
             * Revision Days
             * @default 7
             */
            revision_days: number;
            /** Self Review Limit */
            self_review_limit: number;
        };
        /** PublishWithPolicyInput */
        PublishWithPolicyInput: {
            /**
             * Course Run Id
             * Format: uuid
             */
            course_run_id: string;
            /** Expected Policy Revision */
            expected_policy_revision: number;
            policy: components["schemas"]["PublicationPolicyInput"];
            /**
             * Review Deadline
             * Format: date-time
             */
            review_deadline: string;
            /**
             * Submission Deadline
             * Format: date-time
             */
            submission_deadline: string;
        };
        /** PublishWorkspaceReviewInput */
        PublishWorkspaceReviewInput: {
            /** Apply Penalty */
            apply_penalty: boolean;
            /**
             * Review Revision Id
             * Format: uuid
             */
            review_revision_id: string;
        };
        /** PublishedCriterionView */
        PublishedCriterionView: {
            /** Description */
            description: string;
            /** Max Points */
            max_points: number;
            /** Points */
            points: number;
            /** Reason */
            reason: string;
            /** Title */
            title: string;
        };
        /** PublishedGradeView */
        PublishedGradeView: {
            grade: components["schemas"]["GradePreview"];
            /**
             * Id
             * Format: uuid
             */
            id: string;
        };
        /** PublishedWorkspaceHomework */
        PublishedWorkspaceHomework: {
            /**
             * History Publication Id
             * Format: uuid
             */
            history_publication_id: string;
            /** Policy Revision */
            policy_revision: number;
            /**
             * Publication Id
             * Format: uuid
             */
            publication_id: string;
            /** Revision */
            revision: number;
        };
        /** QuotaView */
        QuotaView: {
            /** Active Run Id */
            active_run_id?: string | null;
            /** Limit */
            limit: number;
            /** Policy Revision */
            policy_revision: number;
            /** Remaining */
            remaining: number;
            /** Reserved */
            reserved: number;
            /** Used */
            used: number;
        };
        /** ReleaseInput */
        ReleaseInput: {
            /** Reason */
            reason: string;
        };
        /** ResourceResult */
        ResourceResult: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Revision */
            revision: number;
        };
        /** ReviewAssistResult */
        ReviewAssistResult: {
            authorship_signal?: components["schemas"]["AuthorshipSignal"] | null;
            /** Feedback Draft */
            feedback_draft?: string | null;
            /** Suggestions */
            suggestions: components["schemas"]["ReviewerSuggestion"][];
        };
        /** ReviewAssistView */
        ReviewAssistView: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Error Code */
            error_code: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            result: components["schemas"]["ReviewAssistResult"] | null;
            /** Revision */
            revision: number;
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "running" | "unknown_outcome" | "succeeded" | "failed" | "stale";
        };
        /** ReviewContext */
        ReviewContext: {
            /** Ai Run Id */
            ai_run_id?: string | null;
            /**
             * Artifact Label
             * @default Снимок работы
             */
            artifact_label: string;
            /**
             * Attempt
             * @default 0
             */
            attempt: number;
            /** Criteria */
            criteria: components["schemas"]["ReviewCriterionView"][];
            /**
             * Criterion Set Id
             * Format: uuid
             */
            criterion_set_id: string;
            /** Decision History */
            decision_history?: components["schemas"]["ReviewDecisionEvent"][];
            /**
             * Homework Id
             * Format: uuid
             */
            homework_id: string;
            /**
             * Homework Version Id
             * Format: uuid
             */
            homework_version_id: string;
            /** Latest Review Iteration Id */
            latest_review_iteration_id?: string | null;
            /** Max Score */
            max_score: number;
            outcome: components["schemas"]["OutcomeInput"] | null;
            /** Outcome Revision */
            outcome_revision: number;
            private_details: components["schemas"]["PrivateHomeworkView"] | null;
            /** Self Reviews */
            self_reviews: components["schemas"]["SelfReviewView"][];
            /** Signal Decisions */
            signal_decisions?: {
                [key: string]: "confirm" | "reject";
            };
            /**
             * Student Name
             * @default
             */
            student_name: string;
            /** Student Text */
            student_text: string;
            /** Submission Id */
            submission_id?: string | null;
            /** Submitted At */
            submitted_at?: string | null;
            /**
             * Title
             * @default
             */
            title: string;
        };
        /** ReviewCriterionView */
        ReviewCriterionView: {
            /** Description */
            description: string;
            /**
             * Evaluate Quality
             * @default false
             */
            evaluate_quality: boolean;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Key */
            key: string;
            /** Max Points */
            max_points: number;
            /** Position */
            position: number;
            /**
             * Score Step
             * @default 0.5
             */
            score_step: number;
            /** Title */
            title: string;
        };
        /** ReviewDecision */
        ReviewDecision: {
            /**
             * Criterion Id
             * Format: uuid
             */
            criterion_id: string;
            /**
             * Decision
             * @enum {string}
             */
            decision: "accepted" | "changed" | "manual";
            /** Evidence Ids */
            evidence_ids?: string[];
            /** Points */
            points: number;
            /** Reason */
            reason: string;
        };
        /** ReviewDecisionEvent */
        ReviewDecisionEvent: {
            /** Actor */
            actor?: string | null;
            /** Text */
            text: string;
            /**
             * Timestamp
             * Format: date-time
             */
            timestamp: string;
        };
        /** ReviewDraftView */
        ReviewDraftView: {
            /** Criterion Decisions */
            criterion_decisions: components["schemas"]["ReviewDecision"][];
            current_review_revision: components["schemas"]["WorkspaceRevisionSummary"] | null;
            /** Current Review Revision Id */
            current_review_revision_id: string | null;
            /** Review Notes */
            review_notes: components["schemas"]["WorkspaceNoteView"][];
            /** Revision */
            revision: number;
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "in_review" | "ready_to_publish" | "published" | "canceled";
        };
        /** ReviewNote */
        ReviewNote: {
            /** Criterion Id */
            criterion_id?: string | null;
            /** Text */
            text: string;
        };
        /** ReviewerSuggestion */
        ReviewerSuggestion: {
            /**
             * Confidence
             * @default medium
             * @enum {string}
             */
            confidence: "low" | "medium" | "high";
            /**
             * Criterion Id
             * Format: uuid
             */
            criterion_id: string;
            /** Evidence */
            evidence?: string[];
            /** Proposed Points */
            proposed_points: number | null;
            /** Reason */
            reason: string;
            /** Requirement Met */
            requirement_met?: boolean | null;
            /** Reviewer Note */
            reviewer_note?: string | null;
            /** Sources */
            sources?: components["schemas"]["AssistEvidence"][];
            /**
             * Status
             * @enum {string}
             */
            status: "suggested" | "needs_human" | "not_checked";
            /** Student Feedback */
            student_feedback?: string | null;
        };
        /** SaveReviewRevisionPayload */
        SaveReviewRevisionPayload: {
            /** Criterion Decisions */
            criterion_decisions: components["schemas"]["ReviewDecision"][];
            /** Feedback */
            feedback: string;
            /** Review Notes */
            review_notes: components["schemas"]["ReviewNote"][];
        };
        /** SearchHomeworkView */
        SearchHomeworkView: {
            /** Course Run Id */
            course_run_id: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Title */
            title: string;
        };
        /** SelfReviewEvent */
        SelfReviewEvent: {
            /** Attempt */
            attempt: number;
            /**
             * Contract Version
             * @constant
             */
            contract_version: "2.0.0";
            /** Error Code */
            error_code?: ("unavailable" | "invalid_artifact" | "unsupported_format" | "invalid_result") | null;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /** Input Fingerprint */
            input_fingerprint: string;
            result?: components["schemas"]["SelfReviewResult"] | null;
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /** Sequence */
            sequence: number;
            /**
             * Status
             * @enum {string}
             */
            status: "running" | "succeeded" | "failed";
        };
        /** SelfReviewFinding */
        SelfReviewFinding: {
            /**
             * Criterion Id
             * Format: uuid
             */
            criterion_id: string;
            /**
             * Evidence
             * @default
             */
            evidence: string;
            /** Feedback */
            feedback: string;
            /**
             * Status
             * @enum {string}
             */
            status: "met" | "needs_attention" | "not_checked";
        };
        /** SelfReviewResult */
        SelfReviewResult: {
            /** Findings */
            findings: components["schemas"]["SelfReviewFinding"][];
        };
        /** SelfReviewView */
        SelfReviewView: {
            /** Artifact Id */
            artifact_id: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Disposition
             * @enum {string}
             */
            disposition: "reserved" | "consumed" | "released";
            /** Draft Revision */
            draft_revision: number;
            /** Error Code */
            error_code: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            quota: components["schemas"]["QuotaView"];
            result: components["schemas"]["SelfReviewResult"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "capturing" | "pending" | "running" | "unknown_outcome" | "succeeded" | "failed";
        };
        /** SourcePolicyInput */
        SourcePolicyInput: {
            /** Allowed Sources */
            allowed_sources?: ("upload" | "github" | "google_docs")[];
        };
        /** StatisticView */
        StatisticView: {
            /** Ai Acceptance Percent */
            ai_acceptance_percent?: number | null;
            /** Average Elapsed Minutes */
            average_elapsed_minutes: number | null;
            /** Average Wait Minutes */
            average_wait_minutes?: number | null;
            /** Changed Decisions */
            changed_decisions: number;
            /** Compared Decisions */
            compared_decisions: number;
            /** Course Ai Acceptance Percent */
            course_ai_acceptance_percent?: number | null;
            /** Course Average Elapsed Minutes */
            course_average_elapsed_minutes?: number | null;
            /** Course Average Overdue Publications */
            course_average_overdue_publications?: number | null;
            /** Course Average Wait Minutes */
            course_average_wait_minutes?: number | null;
            /** Criterion Changes */
            criterion_changes?: components["schemas"]["CriterionChangeStatistic"][];
            /**
             * Elapsed Sample Count
             * @default 0
             */
            elapsed_sample_count: number;
            /**
             * From Date
             * Format: date-time
             */
            from_date: string;
            /**
             * Overdue Publications
             * @default 0
             */
            overdue_publications: number;
            /**
             * Overdue Sample Count
             * @default 0
             */
            overdue_sample_count: number;
            peer_comparison?: components["schemas"]["PeerComparisonStatistic"];
            /** Publications */
            publications: number;
            /**
             * Repeated Publications
             * @default 0
             */
            repeated_publications: number;
            /**
             * Until Date
             * Format: date-time
             */
            until_date: string;
            /**
             * Wait Sample Count
             * @default 0
             */
            wait_sample_count: number;
        };
        /** StudentContext */
        StudentContext: {
            /** Allowed Sources */
            allowed_sources?: ("upload" | "github" | "google_docs")[];
            /**
             * Course Run Id
             * Format: uuid
             */
            course_run_id: string;
            /**
             * Course Title
             * @default
             */
            course_title: string;
            /** Criteria */
            criteria: components["schemas"]["PublicCriterion"][];
            draft: components["schemas"]["DraftView"] | null;
            /**
             * Homework Id
             * Format: uuid
             */
            homework_id: string;
            /**
             * Homework Version Id
             * Format: uuid
             */
            homework_version_id: string;
            /** Material Upload Ids */
            material_upload_ids?: string[];
            /**
             * Max Score
             * @default 0
             */
            max_score: number;
            policy: components["schemas"]["PublicationPolicyInput"] | null;
            /**
             * Publication Id
             * Format: uuid
             */
            publication_id: string;
            quota: components["schemas"]["QuotaView"] | null;
            /**
             * Run Title
             * @default
             */
            run_title: string;
            /** Self Reviews */
            self_reviews: components["schemas"]["SelfReviewView"][];
            /** Student Text */
            student_text: string;
            /**
             * Submission Deadline
             * Format: date-time
             */
            submission_deadline: string;
            /** Submission Id */
            submission_id?: string | null;
            /** Title */
            title: string;
        };
        /** StudentHomeworkItem */
        StudentHomeworkItem: {
            /** Attempt */
            attempt: number;
            /** Course Run Title */
            course_run_title: string;
            /** Course Title */
            course_title: string;
            /** Draft Id */
            draft_id: string | null;
            /**
             * Publication Id
             * Format: uuid
             */
            publication_id: string;
            /** Revision Deadline */
            revision_deadline?: string | null;
            /** Score */
            score: number | null;
            /** Status */
            status: string;
            /**
             * Submission Deadline
             * Format: date-time
             */
            submission_deadline: string;
            /** Submission Id */
            submission_id: string | null;
            /** Title */
            title: string;
        };
        /** StudentHomeworkList */
        StudentHomeworkList: {
            /** Items */
            items: components["schemas"]["StudentHomeworkItem"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /** StudentReviewView */
        StudentReviewView: {
            /** Criteria */
            criteria: components["schemas"]["PublishedCriterionView"][];
            /** Decision */
            decision: string | null;
            /** Decision Reason */
            decision_reason?: string | null;
            /** Feedback */
            feedback: string;
            grade?: components["schemas"]["GradePreview"] | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Iteration Id
             * Format: uuid
             */
            iteration_id: string;
            /**
             * Published At
             * Format: date-time
             */
            published_at: string;
            /** Revision Deadline */
            revision_deadline: string | null;
            /** Score */
            score: number;
            /**
             * Submission Version Id
             * Format: uuid
             */
            submission_version_id: string;
        };
        /** StudentSubmissionView */
        StudentSubmissionView: {
            /** Attempts */
            attempts: components["schemas"]["SubmissionAttemptView"][];
            /**
             * Course Run Id
             * Format: uuid
             */
            course_run_id: string;
            /** Current Publication Id */
            current_publication_id: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Publication Id
             * Format: uuid
             */
            publication_id: string;
            /** Reviews */
            reviews: components["schemas"]["StudentReviewView"][];
            /** Title */
            title: string;
        };
        /** SubmissionAttemptView */
        SubmissionAttemptView: {
            /** Artifact Id */
            artifact_id: string | null;
            /** Capture Operation Id */
            capture_operation_id: string | null;
            /** Comment */
            comment: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Sequence */
            sequence: number;
            /** Status */
            status: string;
            /**
             * Submitted At
             * Format: date-time
             */
            submitted_at: string;
        };
        /** TypicalCriterionFailure */
        TypicalCriterionFailure: {
            /**
             * Criterion Id
             * Format: uuid
             */
            criterion_id: string;
            /** Failed */
            failed: number;
            /** Ratio */
            ratio: number;
            /** Reviewed */
            reviewed: number;
            /** Title */
            title: string;
        };
        /** UploadInput */
        UploadInput: {
            /** Content Base64 */
            content_base64: string;
            /** Filename */
            filename: string;
            /**
             * Media Type
             * @enum {string}
             */
            media_type: "text/markdown" | "application/pdf" | "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
            /**
             * Private
             * @default false
             */
            private: boolean;
        };
        /** UploadView */
        UploadView: {
            /** Byte Size */
            byte_size: number;
            /** Digest */
            digest: string;
            /** Filename */
            filename: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Media Type */
            media_type: string;
        };
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** WorkItem */
        WorkItem: {
            /** Attempt */
            attempt: number;
            /**
             * Course Run Id
             * Format: uuid
             */
            course_run_id: string;
            /** Course Run Title */
            course_run_title: string;
            /**
             * Course Title
             * @default
             */
            course_title: string;
            /** Draft Id */
            draft_id?: string | null;
            /** Feedback */
            feedback: string | null;
            /**
             * Homework Id
             * Format: uuid
             */
            homework_id: string;
            /** Participant Ids */
            participant_ids?: string[];
            /** Primary Reviewer Id */
            primary_reviewer_id: string | null;
            /**
             * Publication Id
             * Format: uuid
             */
            publication_id: string;
            /** Published By */
            published_by: string | null;
            /** Responsible Reviewer Id */
            responsible_reviewer_id: string | null;
            /** Review Case Id */
            review_case_id: string | null;
            /** Review Case Revision */
            review_case_revision: number;
            /** Review Deadline */
            review_deadline: string | null;
            /** Review Iteration Id */
            review_iteration_id: string | null;
            /** Review Revision */
            review_revision: number;
            /** Review Submission Version Id */
            review_submission_version_id: string | null;
            /** Reviewer Name */
            reviewer_name?: string | null;
            /** Score */
            score: number | null;
            /** Status */
            status: string;
            /**
             * Student Id
             * Format: uuid
             */
            student_id: string;
            /** Student Name */
            student_name: string;
            /** Submission Deadline */
            submission_deadline?: string | null;
            /** Submission Id */
            submission_id: string | null;
            /** Submission Revision */
            submission_revision: number;
            /** Submission Version Id */
            submission_version_id: string | null;
            /** Submitted At */
            submitted_at: string | null;
            /** Taken At */
            taken_at?: string | null;
            /** Title */
            title: string;
            /** Updated At */
            updated_at?: string | null;
        };
        /** WorkList */
        WorkList: {
            /** Items */
            items: components["schemas"]["WorkItem"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /** WorkspaceCommand[AssignmentInput] */
        WorkspaceCommand_AssignmentInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["AssignmentInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[CourseInput] */
        WorkspaceCommand_CourseInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["CourseInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[CourseRunInput] */
        WorkspaceCommand_CourseRunInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["CourseRunInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[DraftInput] */
        WorkspaceCommand_DraftInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["DraftInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[EditorDraftInput] */
        WorkspaceCommand_EditorDraftInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["EditorDraftInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[EmptyInput] */
        WorkspaceCommand_EmptyInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["EmptyInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[ExportInput] */
        WorkspaceCommand_ExportInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["ExportInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[ExtraRequirementInput] */
        WorkspaceCommand_ExtraRequirementInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["ExtraRequirementInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[HomeworkTitleInput] */
        WorkspaceCommand_HomeworkTitleInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["HomeworkTitleInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[MembershipInput] */
        WorkspaceCommand_MembershipInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["MembershipInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[NotificationInput] */
        WorkspaceCommand_NotificationInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["NotificationInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[OpenWorkInput] */
        WorkspaceCommand_OpenWorkInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["OpenWorkInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[OutcomeInput] */
        WorkspaceCommand_OutcomeInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["OutcomeInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[PreferencesInput] */
        WorkspaceCommand_PreferencesInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["PreferencesInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[PriorityInput] */
        WorkspaceCommand_PriorityInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["PriorityInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[PrivateHomeworkInput] */
        WorkspaceCommand_PrivateHomeworkInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["PrivateHomeworkInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[PublicationPolicyInput] */
        WorkspaceCommand_PublicationPolicyInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["PublicationPolicyInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[PublishWithPolicyInput] */
        WorkspaceCommand_PublishWithPolicyInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["PublishWithPolicyInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[PublishWorkspaceReviewInput] */
        WorkspaceCommand_PublishWorkspaceReviewInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["PublishWorkspaceReviewInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[ReleaseInput] */
        WorkspaceCommand_ReleaseInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["ReleaseInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[UploadInput] */
        WorkspaceCommand_UploadInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["UploadInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceCommand[WorkspaceReviewSaveInput] */
        WorkspaceCommand_WorkspaceReviewSaveInput_: {
            /** Command Name */
            command_name: string;
            /** Expected Revision */
            expected_revision: number;
            /** Idempotency Key */
            idempotency_key: string;
            payload: components["schemas"]["WorkspaceReviewSaveInput"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** WorkspaceInsightsView */
        WorkspaceInsightsView: {
            pool_metrics: components["schemas"]["WorkspacePoolMetrics"];
            status_counts: components["schemas"]["WorkspaceStatusCounts"];
            /** Typical Failures */
            typical_failures: components["schemas"]["TypicalCriterionFailure"][];
        };
        /** WorkspaceNoteView */
        WorkspaceNoteView: {
            /**
             * Author User Id
             * Format: uuid
             */
            author_user_id: string;
            /** Criterion Id */
            criterion_id: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Position */
            position: number;
            /** Text */
            text: string;
        };
        /** WorkspacePoolMetrics */
        WorkspacePoolMetrics: {
            /** Active Reviewers */
            active_reviewers: number;
            /** Average Wait Minutes */
            average_wait_minutes: number | null;
            /** Stuck */
            stuck: number;
            /** Submitted */
            submitted: number;
            /** Total Reviewers */
            total_reviewers: number;
            /** Waiting */
            waiting: number;
        };
        /** WorkspaceReviewSaveInput */
        WorkspaceReviewSaveInput: {
            /** Ai Run Id */
            ai_run_id?: string | null;
            draft: components["schemas"]["SaveReviewRevisionPayload"];
            /** Signal Decisions */
            signal_decisions?: {
                [key: string]: "confirm" | "reject";
            };
        };
        /** WorkspaceRevisionSummary */
        WorkspaceRevisionSummary: {
            /**
             * Author User Id
             * Format: uuid
             */
            author_user_id: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Feedback */
            feedback: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Review Iteration Id
             * Format: uuid
             */
            review_iteration_id: string;
            /** Revision Number */
            revision_number: number;
            /** Total Score */
            total_score: number;
        };
        /** WorkspaceSearchView */
        WorkspaceSearchView: {
            /** Homeworks */
            homeworks: components["schemas"]["SearchHomeworkView"][];
            /** Students */
            students: components["schemas"]["WorkItem"][];
        };
        /** WorkspaceStatusCounts */
        WorkspaceStatusCounts: {
            /**
             * All
             * @default 0
             */
            all: number;
            /**
             * Draft
             * @default 0
             */
            draft: number;
            /**
             * Failed
             * @default 0
             */
            failed: number;
            /**
             * In Review
             * @default 0
             */
            in_review: number;
            /**
             * Needs Changes
             * @default 0
             */
            needs_changes: number;
            /**
             * Passed
             * @default 0
             */
            passed: number;
            /**
             * Pending Review
             * @default 0
             */
            pending_review: number;
            /**
             * Repeat Review
             * @default 0
             */
            repeat_review: number;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    artifact_download_api_v2_artifacts__identity__download_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DownloadView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    catalog_api_v2_catalog_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CatalogView"];
                };
            };
        };
    };
    save_draft_api_v2_course_run_homeworks__identity__draft_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_DraftInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DraftView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_policy_api_v2_course_run_homeworks__identity__policy_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PublicationPolicyView"] | null;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_policy_api_v2_course_run_homeworks__identity__policy_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_PublicationPolicyInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_submission_sources_api_v2_course_run_homeworks__identity__sources_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourcePolicyInput"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    student_context_api_v2_course_run_homeworks__identity__student_context_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StudentContext"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_course_run_api_v2_course_runs__identity__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_CourseRunInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    assignments_api_v2_course_runs__identity__assignments_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AssignmentsView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    assign_api_v2_course_runs__identity__assignments_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_AssignmentInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    insights_api_v2_course_runs__identity__insights_get: {
        parameters: {
            query?: {
                homework_id?: string | null;
            };
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WorkspaceInsightsView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    membership_api_v2_course_runs__identity__memberships_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_MembershipInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_run_priority_api_v2_course_runs__identity__priority_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_PriorityInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    remind_api_v2_course_runs__identity__reminders_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_NotificationInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_course_api_v2_courses_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_CourseInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_course_api_v2_courses__identity__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_CourseInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_run_api_v2_courses__identity__course_runs_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_CourseRunInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    homeworks_api_v2_courses__identity__homeworks_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CoordinatorHomeworkList"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    directory_api_v2_directory_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DirectoryView"];
                };
            };
        };
    };
    drafts_api_v2_drafts_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DraftList"];
                };
            };
        };
    };
    create_export_api_v2_exports_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_ExportInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExportView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_export_api_v2_exports__identity__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExportView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_private_api_v2_homework_versions__identity__private_details_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PrivateHomeworkView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    save_private_api_v2_homework_versions__identity__private_details_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_PrivateHomeworkInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PrivateHomeworkView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    publish_workspace_api_v2_homework_versions__identity__publish_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_PublishWithPolicyInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PublishedWorkspaceHomework"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    editor_draft_api_v2_homeworks__identity__editor_draft_get: {
        parameters: {
            query: {
                course_run_id: string;
            };
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EditorDraftView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    save_editor_draft_api_v2_homeworks__identity__editor_draft_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_EditorDraftInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EditorDraftView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_workspace_homework_api_v2_homeworks__identity__title_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_HomeworkTitleInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    self_review_event_api_v2_internal_self_review_events_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SelfReviewEvent"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SelfReviewView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    notifications_api_v2_notifications_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["NotificationsView"];
                };
            };
        };
    };
    read_notification_api_v2_notifications__identity__read_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_EmptyInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preparation_api_v2_preparations__identity__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PreparationView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_assist_api_v2_review_assists__identity__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewAssistView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    retry_assist_api_v2_review_assists__identity__retry_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_EmptyInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewAssistView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preferences_api_v2_reviewer_preferences_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PreferencesView"];
                };
            };
        };
    };
    save_preferences_api_v2_reviewer_preferences_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_PreferencesInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PreferencesView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    latest_assist_api_v2_reviews__identity__assist_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewAssistView"] | null;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    start_assist_api_v2_reviews__identity__assist_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_EmptyInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewAssistView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    review_context_api_v2_reviews__identity__context_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewContext"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_review_draft_api_v2_reviews__identity__draft_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewDraftView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    save_assisted_draft_api_v2_reviews__identity__draft_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_WorkspaceReviewSaveInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_grade_api_v2_reviews__identity__grade_preview_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GradePreview"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    save_outcome_api_v2_reviews__identity__outcome_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_OutcomeInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    publish_workspace_review_api_v2_reviews__identity__publish_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_PublishWorkspaceReviewInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PublishedGradeView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_review_requirement_api_v2_reviews__identity__requirements_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_ExtraRequirementInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    search_workspace_api_v2_search_get: {
        parameters: {
            query?: {
                q?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WorkspaceSearchView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    self_review_api_v2_self_reviews__identity__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SelfReviewView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    release_self_review_api_v2_self_reviews__identity__release_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_ReleaseInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    statistics_api_v2_statistics_get: {
        parameters: {
            query?: {
                days?: number;
                course_run_id?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StatisticView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_student_homeworks_api_v2_student_homeworks_get: {
        parameters: {
            query?: {
                state?: "" | "in_progress" | "completed";
                offset?: number;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StudentHomeworkList"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_student_submission_api_v2_submissions__identity__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StudentSubmissionView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    open_work_api_v2_submissions__identity__open_review_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_OpenWorkInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    upload_artifact_api_v2_uploads_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_UploadInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UploadView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    prepare_work_draft_api_v2_work_drafts__identity__prepare_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_EmptyInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PreparationView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    start_self_review_api_v2_work_drafts__identity__self_reviews_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_EmptyInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SelfReviewView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    submit_work_draft_api_v2_work_drafts__identity__submit_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_EmptyInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    submit_upload_api_v2_work_drafts__identity__submit_upload_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                identity: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkspaceCommand_EmptyInput_"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResourceResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    works_api_v2_works_get: {
        parameters: {
            query?: {
                course_run_id?: string | null;
                homework_id?: string | null;
                q?: string;
                state?: string;
                view?: string;
                stuck?: boolean;
                priority?: ("assigned" | "deadline") | null;
                offset?: number;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WorkList"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}
