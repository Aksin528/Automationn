# Fork changes register

This fork (`Aksin528/Automationn`) is based on `TracecatHQ/tracecat` but does **not** share
git history with it (`git merge-base` finds no common ancestor: the history starts with a small
commit and the whole upstream tree arrives in the bulk commit `ec1b99aa2`), so
`git merge upstream/main` cannot be used blindly. Upstream code is taken **selectively**
(cherry-pick / copy a file).

This file lists every place where the fork changed default (upstream) code, why, and what to
check when taking a newer upstream version of the same file. Read it **before** merging or
copying anything from upstream.

How it was built: from the fork's git history (44 commits after the import). Two commits are bulk
imports and are not itemised: `ec1b99aa2` (2026-07-10, whole tree sync) and `0faffc8c4` (CA cert).
`1c93f04a8` (2026-09-24) is one large mixed commit (MinIO TLS, report PDF, approvals, threat
intel), so the per-file "why" below is grouped by feature and is approximate.
Update this file in the same commit whenever default code is changed again.

## 1. Approval system (EE package, agents, inbox)

Why: tool calls of AI agents need a human approval; approvals pile up, and bots (Telegram) and
workflows must decide them with an API key instead of a human session.

| File | What differs from upstream | When taking upstream |
|---|---|---|
| `packages/tracecat-ee/tracecat_ee/agent/approvals/router.py` | `GET /approvals` list (status, case, tool_call_id filters, `approved_by_email`); `submit_approvals` accepts API keys (`WorkspaceActorRouteRole`); 410 Gone for a dead session | Keep all three. Upstream added a 409 handler (`TracecatConflictError`) that we lack: take that too |
| `.../approvals/service.py` | `case_id` on approvals, `list_approvals`, partial approval of multi-tool turns, per-card decision write (`apply_single_decision`) | Workflow-state code: merge by hand, never overwrite |
| `.../agent/workflows/durable.py` | Execute approved MCP tools directly (not through registry lock), multi-approval handling | Same: merge by hand, replay-safety matters |
| `tracecat/executor/activities.py`, `tracecat/executor/worker.py` | Activity for approved MCP tool execution; `beartype` passed through the sandbox | Keep |
| `tracecat/agent/session/service.py`, `agent/runtime/claude_code/runtime.py`, `session_lines.py`, `executor/loopback.py`, `executor/activity.py` | Surface every tool in a multi-tool approval turn | Keep |
| `packages/tracecat-ee/.../inbox/providers/approvals.py`, `tracecat/inbox/schemas.py`, `types.py` | Do not mark a session FAILED because a tool was rejected; show case short id and summary | Keep |
| `.../interactions/service.py`, `decorators.py`, `tracecat/interactions/schemas.py`, `dsl/action.py`, `dsl/workflow.py`, `validation/service.py`, `workflow/executions/router.py`, `workflow/management/management.py` | Approval-gated actions (commit `1c93f04a8`; details not reviewed line by line) | Re-test an approval-gated action after any merge |
| `tracecat/db/models.py`, `cases/enums.py`, `audit/types.py` | `approval.case_id`, approval vote table, approval case event types | Must match the 3 migrations below |
| `alembic/versions/1b7b004b3c68_add_case_id_to_approval.py`, `89952e4e5c2d_add_approval_case_event_types.py`, `d1535b42ca96_add_approval_vote_table.py` | Our 3 migrations | After a merge run `alembic heads`: there must be **one** head; re-parent ours if upstream added migrations (`/reparent` skill) |
| UI: `components/ai-elements/tool.tsx`, `chat/chat-session-pane.tsx`, `cases/case-pending-approvals-section.tsx`, `case-manual-approval-actions.tsx`, `hooks/use-chat.ts`, `lib/hooks.tsx`, `lib/agents.ts`, `hooks/use-inbox.ts` | Pending/rejected tool calls shown as such, approvals tab in a case | Keep |

Used by: Telegram approval workflows and the DB workflow `Auto-reject stale approvals`
(rejects approvals older than N hours through the API; has `dry_run`).

## 2. Incident report PDF

| File | What / why | When taking upstream |
|---|---|---|
| `tracecat/cases/incident_report_pdf.py` (new), `cases/fonts/*` (new) | Server-side report PDF | Ours only |
| `tracecat/cases/report_redirect.py` (new) | `GET /cases/{id}/report/pdf/redirect`: 307 to a short-lived presigned URL, used by the Grafana link | Ours only |
| `tracecat/cases/router.py` | PDF upload/download routes and a `try/except ImportError` block named `fork-local` that includes `report_redirect` | After a merge: `grep -n fork-local tracecat/cases/router.py` must not be empty |
| `tracecat/cases/schemas.py`, `service.py`, `config.py` | Report fields, MinIO presign settings | Keep |
| UI: `cases/case-incident-report-dialog.tsx`, `-print.ts`, `-section.tsx`, `case-panel-view.tsx` | Report dialog, print layout, save uploads the PDF | Keep |

## 3. Fixes that are generic (candidates for upstream PRs)

| Change | File | Status |
|---|---|---|
| Schedule overlap policy configurable (`TRACECAT__SCHEDULE_OVERLAP_POLICY`) | `tracecat/workflow/schedules/bridge.py` | **Upstream PR #3878 open** (2026-10-20). This tree still has the flat `SKIP` change, uncommitted |
| Tag add/remove trigger filters match the specific tag | `tracecat/cases/triggers/consumer.py` | Not sent (check upstream first) |
| Approval-related fixes of section 1 | see above | Not sent; would be several small PRs |

## 4. LLM / agent gateway

| File | What | Note |
|---|---|---|
| `tracecat/agent/internal_router.py` (new, 267 lines) | Resolve `catalog_id` for the custom model provider in preset agents | Ours; check whether upstream has an equivalent |
| `tracecat/agent/gateway.py` (new, 734 lines) | Translate the thinking parameter for a custom model provider (Ollama) | Ours |
| `tracecat/agent/sandbox/llm_proxy.py`, `title_generator.py` | Small changes in `1c93f04a8` | Check on merge |

## 5. UI (not approval related)

| Change | Files | Note |
|---|---|---|
| Dark mode (cherry-picked from upstream #2963, #2982, #3127) | About 60 files under `frontend/src/components`, `app/layout.tsx`, `providers/theme.tsx`, `styles/globals.scss` | If upstream main is merged, take **upstream's** version and re-check the toggle |
| Sidebar long values (equivalent of upstream #3238) | `components/ui/sidebar.tsx` | Upstream has it: take theirs |
| Grafana link configurable by env | `components/sidebar/app-sidebar.tsx`, `controls-header.tsx` | Keep |
| Removed unsupported workspace-sync / duplicate theme code | `workspace-sync/*`, `nav/controls-header.tsx` | Keep |
| Generated API client | `frontend/src/client/*.gen.ts` | Never merge by hand: regenerate (`just gen-client-ci`) after the backend is merged |

## 6. Infrastructure files (fork-owned in practice)

`docker-compose.yml` (Grafana env), `docker-compose.override.yml` (local builds, MCP servers,
MinIO TLS), `docker-compose.data.yml`, `docker-compose.approval-test.yml`, `Caddyfile` (TLS port,
`/s3/*` to MinIO), `.github/workflows/build-push-images.yml`, `ca_wrapper*.sh`, `certs/`.
Prod runs `git pull origin master` and builds from these, so keep them in sync with prod.

## 7. Fork-only code (no upstream counterpart, no merge conflict)

`soc-mcp/` (Cortex, Splunk, PMG, Check Point tools), `docker/minio/`, and everything stored in
the Tracecat DB (workflows, `run_python` scripts, tables, presets) which is not in git.

## 8. Checklist after taking anything from upstream

1. `grep -n fork-local tracecat/cases/router.py` is not empty.
2. `alembic heads` shows one head; our 3 approval migrations are in the chain.
3. Regenerate the frontend client (`just gen-client-ci`).
4. Rebuild and restart `api`, `executor`, `worker`, `agent-worker`, `agent-executor`, `ui`.
5. Smoke test: an approval-gated agent tool (approve and reject), a case report PDF download
   link, and the `Auto-reject stale approvals` workflow with `dry_run: true`.
