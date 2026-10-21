# Fork changes register

This fork (`Aksin528/Automationn`) is **upstream Tracecat `1.2.1` plus the changes listed here**.

History: the fork started from a bulk copy of upstream commit `bf617e37d` (2026-06-11, version
`1.0.0b48`) and had no common ancestor with upstream in git. On 2026-10-21 the histories were joined
(`git merge-base` now finds `bf617e37d`) and upstream `1.2.1` was merged. From now on updating is a
normal merge:

```
git fetch upstream tag <new-version>
git merge <new-version>
```

Read this file **before** merging. It lists where the fork differs from upstream, why, and what was
deliberately dropped. Update it in the same commit whenever default (upstream) code changes. Per
project rule, generic fixes go to upstream as PRs (separate worktree and branch from `upstream/main`).

How to see the live footprint at any time: `git diff <upstream-tag> --stat -- . ':!frontend' ':!tests'`.

## 1. What the fork adds on top of upstream

### 1.1 Incident report PDF
| Files | Note |
|---|---|
| `tracecat/cases/incident_report_pdf.py`, `tracecat/cases/fonts/*`, `pyproject.toml` (`reportlab`), `uv.lock` | Server-side report PDF |
| `tracecat/cases/report_redirect.py` | `GET /cases/{id}/report/pdf/redirect`: 307 to a short-lived presigned URL (Grafana link) |
| `tracecat/cases/router.py` (+148), `schemas.py`, `config.py` | Upload/download routes; a `try/except ImportError` block named `fork-local` includes `report_redirect` |
| UI: `cases/case-incident-report-*.tsx/ts`, `case-panels.ts` + `case-panel-content.tsx` (panel `report`) | Report dialog, print layout, section |
After a merge: `grep -n fork-local tracecat/cases/router.py` must not be empty.

### 1.2 Approvals of agent tool calls (case-linked, API-key friendly)
Why: AI-agent tool calls need a human approval; bots (Telegram) and workflows decide them with an
API key; approvals are linked to a case.
| Files | Note |
|---|---|
| `packages/tracecat-ee/.../agent/approvals/router.py` (+141) | `GET /approvals` (status, case, tool_call_id filters, `approved_by_email`); `submit_approvals` accepts API keys; 410 Gone for a dead session |
| `.../approvals/service.py` (+146) | `case_id` on approvals, `list_approvals`; helpers `apply_single_decision`, `get_expected_tool_call`, `_build_decision_payload` (currently unused, see 2.2) |
| `tracecat/db/models.py` (+58) | `Approval.case_id`, `ApprovalVote` |
| `alembic/versions/`: `a9c1d4e7f2b3` (orphan columns, harmless), `d1535b42ca96`, `89952e4e5c2d`, `1b7b004b3c68`, merge `261af2786dda` | Keep these as they are: production is already at `1b7b004b3c68`. Never re-parent them |
| `tracecat/dsl/action.py` (+24: `_thread_trigger_case_id`), `dsl/workflow.py` (+15), EE `agent/schemas.py` (+10) | A workflow triggered with a `case_id` links its agent session to that case |
| `agent/executor/activity.py`, `agent/runtime/claude_code/session_lines.py`, `audit/types.py`, `cases/enums.py`, `inbox/types.py`, `workflow/management/management.py`, `service_accounts/constants.py` | Small additions (1-8 lines each) |
| UI: `case-pending-approvals-section.tsx`, `case-manual-approval-actions.tsx`, `lib/hooks.tsx` (`useCaseApprovals`, `useSubmitCaseApproval`, `useDismissCaseApproval`), `ai-elements/tool.tsx`, `case-panels.ts` (panel `approvals`) | Approvals tab in a case |
Used by: Telegram approval workflows and the DB workflow `Auto-reject stale approvals`
(rejects approvals older than N hours through the API; has `dry_run`).

### 1.3 LLM gateway / custom model provider (Ollama)
`tracecat/agent/sandbox/llm_proxy.py` (+51: `enable_thinking` override, `verify=False`),
`tracecat/agent/service.py` (+36: `resolve_catalog_id`), `agent/gateway.py` (+7),
`agent/session/title_generator.py` (+25), `agent/litellm_config.yaml`, `litellm_config.yaml`,
`agent/provider/service.py` (+2: 404 on `/models` means an empty list).
Check after a merge: upstream now has `catalog/service.py::resolve_catalog_id_by_model`; the fork's
`resolve_catalog_id` may be redundant.

### 1.4 Structured output of agents
`tracecat/agent/runtime/claude_code/runtime.py` (+96: stop hook that requires `StructuredOutput`, post-tool
hook that ends the turn after it) and `frontend/.../chat/chat-session-pane.tsx` (+543: readable
`StructuredOutputAnswer` cards, risk badges, CTI source breakdown, hiding redundant trailing text).

### 1.5 Cases
`tracecat/cases/triggers/consumer.py` (+19): tag add/remove trigger filters match the specific tag
(generic fix, upstream PR candidate).

### 1.6 UI
`components/sidebar/app-sidebar.tsx` (Grafana "Dashboard" link from `NEXT_PUBLIC_GRAFANA_URL`),
`components/cases/dropdowns-view.tsx` (Enterprise-only gate removed), `lib/rbac.ts`,
`app/workspaces/[workspaceId]/cases/page.tsx`, `components/editor/expression-input.tsx`, `frontend/Dockerfile`.
The generated client (`frontend/src/client/*.gen.ts`) must be regenerated from the merged backend
(`just gen-client-ci`), never merged by hand.

### 1.7 Infrastructure (fork-owned in practice)
`docker-compose.yml` (Grafana service, exposed ports, healthchecks from upstream), `docker-compose.override.yml`
(local builds, MCP servers, MinIO TLS), `docker-compose.data.yml`, `docker-compose.approval-test.yml`,
`Caddyfile` (upstream's hardened proxy headers plus: `skip_install_trust`, `:443` with own certificate,
HTTPS to MinIO with the public `Host` header), `.github/workflows/build-push-images.yml`, `ca_wrapper*.sh`,
`certs/`, `defaults.py`. Prod runs `git pull origin master` and builds from these.
Upstream added `TRACECAT__TRUSTED_PROXY_CIDRS` (default `127.0.0.1/32`): set it if a proxy sits in front of Caddy.

### 1.8 Fork-only code (no merge conflict possible)
`soc-mcp/` (Cortex, Splunk, PMG, Check Point tools), `docker/minio/`, and everything stored in the Tracecat
DB (workflows, `run_python` scripts, tables, presets), which is not in git.

## 2. Dropped or replaced when moving to 1.2.1 (decision log, 2026-10-21)

### 2.1 Replaced by upstream's own implementation (nothing lost)
- Several tool approvals in one turn: partial approval, `approved_by` per tool, registering approvals from the
  assistant message stream, a single interrupt (`loopback.py`, `claude_code/runtime.py`, `session/service.py`,
  `approvals/service.py`).
- A rejected tool call is not a failed inbox item (new `inbox/providers/agent_runs.py`).
- Dark mode, sidebar long-value fix, `executor/worker.py` beartype passthrough, custom-provider
  discovery through `create_outbound_http_client`, workspace sync buttons (they are back).

### 2.2 Dropped on purpose (re-port later only if needed)
- **Eager execution of approved MCP tools** (execute a tool the moment its own card is approved, without waiting
  for sibling cards) and the per-card database write. Upstream rewrote `durable.py` (cancellation, stream
  rotation, synchronous `set_approvals`), so this feature was removed with it. **Effect: an approved action now
  runs only after every approval in the same batch is decided.** The helpers in `approvals/service.py` are
  kept for a future re-port.
- **Legacy workflow-interactions approval gate** (`ApprovalInteraction`, voting, `separation_of_duties`, case
  timeline events for it, `GET /cases/{id}/pending-approvals`, hooks `usePendingApprovals` and
  `useVoteOnInteraction`, 6 tests). Upstream removed legacy interactions (#3716). Unused in test and prod
  (`action.interaction` is empty). DB table `interaction`, table `approval_vote` and the enum values stay.
- Case title on inbox cards (`CaseSummary`), hardcoded soc-mcp tool suggestions in the agent preset builder
  (check that the picker still lists the soc-mcp tools).

## 3. Checklist after taking anything from upstream
1. `grep -n fork-local tracecat/cases/router.py` is not empty.
2. One alembic head (`alembic heads`); the fork chain above is intact.
3. Regenerate the frontend client (`just gen-client-ci`) and run `pnpm -C frontend check`.
4. `.env`: `TRACECAT__OUTBOUND_ALLOWED_PRIVATE_CIDRS` lists the private addresses Tracecat must reach (soc-mcp,
   Ollama); upstream blocks private targets for MCP and custom LLM requests by default.
5. Rebuild and restart `api`, `executor`, `worker`, `agent-worker`, `agent-executor`, `ui`.
6. Smoke test: an approval-gated agent tool (approve and reject), the case Report tab and the PDF link, the
   `Auto-reject stale approvals` workflow with `dry_run: true`, a Splunk and a Cortex sync run.
