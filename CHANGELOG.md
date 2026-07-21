# Change Record

This file is an append-only record of architecture, plan, configuration, and code changes in this workspace.

## 2026-07-20

### Plan

- Defined the demo baseline as a single-user local web client with a local FastAPI control plane, SQLite persistence, a registered Git workspace, isolated worktrees, approval gates, and a remote vLLM-compatible model endpoint when available.
- Selected `testdrivenio/fastapi-jwt` as the initial small Git target for the phone-number validation and unit-test workflow because it is MIT licensed and avoids database or service dependencies.

Verification: reviewed the architecture plans and repository metadata through the GitHub API.

### Code

- Added the Local Agent Workbench: React + TypeScript + Vite frontend, FastAPI + SQLAlchemy + SQLite backend, SSE task events, Git worktree creation, unified-diff validation with `git apply --check`, and a Docker-only test executor.
- Added visible vLLM and Docker readiness diagnostics. The workflow deliberately remains in `awaiting_model` until a real model endpoint is configured.
- Added production build and local launch instructions in `README.md`.

Verification: frontend TypeScript production build passed; FastAPI health endpoint returned `200`; a smoke Git repository verified worktree creation, invalid patch rejection, valid patch application, and Diff display. Docker test execution was not run because Docker is unavailable on this machine.

### Documentation

- Added `AGENTS.md` to require an append-only `CHANGELOG.md` entry for every future plan or code change.

Verification: instruction file is located at the workspace root for automatic Codex discovery.

## 2026-07-20

### Configuration

- Added a mandatory delivery workflow to `AGENTS.md`: requirements analysis, high-level design, detailed design, code review, and unit testing are required for every feature, plan, or source-code change.
- Required every applicable stage outcome, including justified lightweight checks, to be recorded in this change record.

Verification: reviewed the workspace instruction and confirmed the rule applies to future work automatically.

## 2026-07-20

### Plan

- Requirements analysis: add an application-scoped model gateway that can switch the app's model calls between the school vLLM service and an external API, without intercepting system-wide traffic.
- High-level design: represent both targets as selectable Provider profiles and use the OpenAI-compatible API contract as their common boundary.
- Detailed design: persist profile metadata in SQLite; persist API keys only in ignored local data; expose model list, activation, diagnosis, and task requirement-analysis calls through the FastAPI control plane.

Verification: reviewed the current model placeholder and the public `cc-switch` project description to limit the feature to profile switching within this application.

### Code

- Added Provider profile persistence and API endpoints for listing, creating, uniquely activating, and diagnosing vLLM or external OpenAI-compatible endpoints.
- Added a real `run-analysis` task action that calls the active provider's `chat/completions` endpoint and stores the returned requirement analysis as an artifact.
- Added a frontend model-profile panel for selecting vLLM or external API, entering endpoint/model/key details, activating the profile, and running a connection diagnostic.

### Code Review

- Confirmed API keys are excluded from all Provider response payloads and are stored only in the ignored local `data/model-secrets.json` file.
- Confirmed Provider activation deactivates every other profile, while vLLM and external endpoints share the same OpenAI-compatible `/v1` request path.
- Confirmed a model call failure becomes a task event and an HTTP 502 response rather than silently advancing the workflow.

Verification: static review of the provider persistence, activation, diagnosis, and task-call paths.

### Unit Testing

- Added coverage for API-key masking, unique active Provider selection, health reporting of the active Provider, and invalid non-HTTP endpoint rejection.

Verification: `python -m pytest backend/tests -q` passed with 2 tests. `npm --prefix frontend run build` and Python bytecode compilation also passed.

## 2026-07-20

### Plan

- Requirements analysis: change task creation so the code source is chosen before the task starts: either a local Git folder or a GitHub repository URL cloned into a user-selected local folder. This replaces the previous user flow where a task was created first and then a separate task worktree was created afterward.
- High-level design: keep `Workspace` as the persisted local code-source record, but allow `/api/tasks` to create or reuse that record during task creation. The task now stores the bound local code directory immediately in `worktree_path` for compatibility with the existing patch, diff, and test paths.
- Detailed design: add `source_type`, local path, GitHub URL, clone destination, workspace name, and test command fields to task creation; validate GitHub URLs against `github.com`; clone with `git clone`; require clean Git workspaces; update the frontend task modal with existing/local/GitHub source modes; keep the old worktree endpoint as a compatibility binder for legacy tasks.

Verification: reviewed the existing task/workspace API, frontend task modal, patch application guard, and Docker test path before implementation.

### Code

- Added backend task-source preparation for existing workspaces, local Git folders, and GitHub clone targets.
- Changed new tasks to bind directly to the selected local code directory instead of requiring a later isolated worktree creation step.
- Updated patch safety checks so patches can apply to the registered task code directory while retaining compatibility for existing `data/worktrees` task paths.
- Reworked the React task creation UI to choose a code source at task creation time and updated task detail labels from isolated worktree language to local code directory language.
- Added backend tests for local-folder task creation and GitHub-clone task creation.

Verification: `.\\.venv\\Scripts\\python.exe -m py_compile backend\\app\\main.py backend\\tests\\test_task_sources.py` passed.

### Documentation

- Updated `README.md` to describe the new task startup flow: choose an existing source, choose a local Git folder, or provide a GitHub URL and local clone destination.

Verification: reviewed the README wording against the implemented frontend and backend flow.

### Code Review

- Confirmed task creation rejects missing local paths, missing GitHub clone details, non-Git directories, dirty Git workspaces, and non-GitHub repository URLs.
- Confirmed `git clone` is invoked without a shell and the clone destination must be absent or empty.
- Confirmed patch application is limited to the task's registered local code directory or legacy managed worktree paths.

Verification: static review of `backend/app/main.py`, `frontend/src/main.tsx`, and `frontend/src/styles.css`.

### Unit Testing

- Added coverage for local Git folder task creation binding `worktree_path` to the selected folder.
- Added coverage for GitHub-source task creation by monkeypatching clone behavior and verifying the cloned local folder is bound to the task.

Verification: `.\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` passed with 4 tests and 1 existing Starlette deprecation warning. `npm --prefix frontend run build` passed.

## 2026-07-20

### Plan

- Requirements analysis: extend a task from one initial natural-language request into a persistent, iterative conversation without bypassing explicit patch and test controls.
- High-level design: add an ordered task-message store and a task-scoped chat endpoint that sends prior messages plus task context to the active OpenAI-compatible provider.
- Detailed design: seed each task with its initial requirement, persist every user and assistant turn, render the ordered transcript in the task view, and keep the existing artifacts, approval, patch, and test panels available alongside it.

Verification: reviewed the task lifecycle, provider gateway, existing event stream, and frontend task-detail state before implementation.

### Code

- Added persistent `TaskMessage` records, message listing, and a task conversation endpoint that carries prior task messages and current task context into every model call.
- Added a Codex-style task conversation panel with transcript, multiline composer, sending state, and persisted task history reload.
- Seeded new conversations from the task creation requirement and documented the iterative workflow in `README.md`.

Verification: pending unit test and production build execution.

### Code Review

- Confirmed messages are scoped to their task, user input is length-limited and persisted before model invocation, model failures do not create an assistant response, and patch/test execution remains behind existing explicit endpoints.

Verification: static review of backend conversation persistence and frontend send/reload behavior.

### Unit Testing

- Added coverage for initial-message seeding, ordered persistence of a follow-up and assistant response, and inclusion of conversation history in the provider request.

Verification: pending execution.

### Verification Update

- Executed the full backend test suite, Python compilation, frontend production build, and a static search for removed public workspace references.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 6 tests and 1 existing Starlette deprecation warning; `.\.venv\Scripts\python.exe -m py_compile backend\app\main.py backend\tests\test_task_sources.py` passed; `npm --prefix frontend run build` passed. Static search found workspace references only in server-side persistence and patch/test safety paths.

### Verification Update

- Executed the full backend suite, Python compilation, and frontend production build after removing the creation-time requirement.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 5 tests and 1 existing Starlette deprecation warning; `.\.venv\Scripts\python.exe -m py_compile backend\app\main.py backend\tests\test_task_conversation.py` passed; `npm --prefix frontend run build` passed.

## 2026-07-20

### Plan

- Requirements analysis: remove the user-facing code-source registry and its existing-source task mode; task creation must retain only local Git folders and GitHub repository cloning.
- High-level design: preserve the internal workspace record solely as a task-directory safety boundary, while removing its public API, navigation display, registration modal, and task-creation selection.
- Detailed design: limit task input source types to `local` and `github`, omit internal workspace identifiers from task responses, remove frontend workspace loading/state/components, and retain the task-bound directory for patch validation and tests.

Verification: reviewed task creation, patch/test safety checks, sidebar navigation, and current source tests.

### Code

- Removed public workspace routes, existing-source creation logic, code-source sidebar display, registration modal, and task footer source display.
- Limited task creation to local folders and Git repositories, and renamed the GitHub option to `Git 仓库` in the interface.
- Kept the internal task-directory record to preserve existing patch and test safety controls; updated README guidance accordingly.

Verification: pending unit test and production build execution.

### Code Review

- Confirmed no frontend request references `/api/workspaces`, no public task response exposes the internal workspace identifier, and patch/test paths still read the internal binding only on the server.

Verification: static review of `backend/app/main.py` and `frontend/src/main.tsx`.

### Unit Testing

- Updated source tests to inspect the internal binding directly and added coverage that workspace management and the removed `existing` source type are no longer public.

Verification: pending execution.

### Verification Update

- Executed the task conversation unit tests, the complete backend test suite, Python compilation, and the frontend production build.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 5 tests and 1 existing Starlette deprecation warning; `.\.venv\Scripts\python.exe -m py_compile backend\app\main.py backend\tests\test_task_conversation.py` passed; `npm --prefix frontend run build` passed.

## 2026-07-20

### Plan

- Requirements analysis: task creation must identify the task by title without turning a creation-time requirement into the first conversation turn.
- High-level design: retain the task title and code-source binding at creation, while starting the persistent task-message history only when the user sends the first follow-up in the task view.
- Detailed design: make the legacy requirement field optional and empty by default, remove it from the creation form and model context, and adjust conversation coverage to assert no initial message exists.

Verification: reviewed the task input contract, task creation logic, chat request construction, and task modal.

### Code

- Removed initial-requirement message seeding and the required natural-language requirement field from task creation.
- Updated the task conversation system context and task header for title-only tasks, and documented that the first requirement is entered in the conversation.

Verification: pending unit test and production build execution.

### Code Review

- Confirmed a new task now has an empty message history, and only a submitted conversation turn can create the first user message.

Verification: static review of task creation and conversation history paths.

### Unit Testing

- Updated conversation coverage to verify the first submitted message and model reply are the only initial persisted turns.

Verification: pending execution.

## 2026-07-20

### Plan

- Requirements analysis: replace the staged task-detail workflow with a conversation-only workbench; users must not review artifacts, patches, approvals, tests, or execution logs in the interface.
- High-level design: retain task creation and persistent conversation, remove the legacy workflow's public endpoints and event stream, and keep only the compact task navigator plus the active conversation.
- Detailed design: remove task-stage and artifact fields from task responses, remove all related React state/components/styles, delete manual workflow routes, and update the model instruction to advance without asking for stage approval.

Verification: reviewed the task UI, legacy API routes, model prompt, and existing backend tests.

### Code

- Rebuilt task detail as a single conversation panel and removed the timeline, artifact/change panel, manual patch and approval controls, test trigger, and event/execution log.
- Removed public analysis, approval, worktree, patch, test, and event-stream endpoints, along with event persistence and event payloads in task responses.
- Updated the Agent instruction and README for the conversation-only workflow; retained only task creation, task retrieval, and task-message APIs.

Verification: pending unit test and production build execution.

### Code Review

- Confirmed the frontend has no references to legacy workflow controls and the backend has no remaining legacy workflow routes or event-stream implementation.

Verification: static search and review of `frontend/src/main.tsx` and `backend/app/main.py`.

### Unit Testing

- Updated source tests for the smaller task response and added checks that removed workflow paths are unavailable.

Verification: pending execution.

### Verification Update

- Executed the full backend suite, Python compilation, frontend production build, and static search for removed workflow references.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 6 tests and 1 existing Starlette deprecation warning; `.\.venv\Scripts\python.exe -m py_compile backend\app\main.py backend\tests\test_task_sources.py` passed; `npm --prefix frontend run build` passed; static search reported no legacy workflow references.

## 2026-07-20

### Plan

- Requirements analysis: separate model interface management from the model used by a conversation; provide a Codex-like composer where Enter sends and Shift+Enter inserts a newline, with existing model profiles selected from the lower-right corner.
- High-level design: keep model profile creation and diagnostics in the upper-right management dialog, and let the composer select the active saved profile before a message is sent.
- Detailed design: save new profiles inactive by default; list and activate saved profiles from the composer popover; rebuild the conversation surface around a single bottom composer and intercept unmodified Enter in its textarea.

Verification: reviewed the existing provider activation API, task-message gateway, React conversation composition, and the supplied UI reference.

### Code

- Rebuilt the task conversation as a Codex-style chat surface with a fixed bottom composer, icon send action, Enter-to-send, and Shift+Enter-to-newline behavior.
- Moved model profile selection to the composer lower-right menu; selecting a profile explicitly activates it for subsequent model requests.
- Changed the upper-right "配置模型接口" dialog to manage diagnostics and add saved profiles only; creating a profile no longer silently changes the active model.

Verification: frontend production build completed and the local page was opened to verify the management entry, composer, and model-selection control.

### Code Review

- Confirmed that model activation occurs only through the selected profile action, that no profile is auto-activated during creation, and that empty or unselected profiles prevent sending with a clear prompt.
- Confirmed that Enter is prevented only when Shift is not held and IME composition is not in progress, preserving Chinese input composition and multiline entry.

Verification: static review of `frontend/src/main.tsx`, `frontend/src/styles.css`, `backend/app/main.py`, and provider/conversation tests.

### Unit Testing

- Updated provider behavior coverage to assert newly saved profiles remain inactive and updated conversation coverage to explicitly activate the chosen profile before sending.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 6 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

## 2026-07-20

### Plan

- Requirements analysis: a task-bound local Git directory was being sent to the model only as text, so the model had no executable file-access capability and could correctly report that an unmounted path was unavailable.
- High-level design: add Codex-style task permissions with three modes: `read-only`, `workspace-write`, and `full-access`; bind those modes to server-executed model tools instead of relying on prompt text.
- Detailed design: persist the mode per task with an additive SQLite migration; expose read/list tools in every mode, restrict write tools to the task directory in workspace-write mode, and expose arbitrary-path writes plus shell commands only in full-access mode.

Verification: reviewed the task creation, model gateway, SQLite schema initialization, and frontend task modal.

### Code

- Added a task permission selector and task-list/header indicators for read-only, workspace-write, and full-access modes.
- Added persistent `permission_mode` task data, including migration support for existing local SQLite databases.
- Added OpenAI-compatible local tool calling for listing files, reading files, writing files, and running commands. Read-only and workspace-write operations are enforced against the bound task directory; full-access is required for commands and unrestricted paths.

Verification: `npm --prefix frontend run build` passed; `python -m py_compile backend\\app\\main.py backend\\tests\\test_task_sources.py backend\\tests\\test_task_conversation.py` passed.

### Code Review

- Confirmed the backend, rather than the model prompt, enforces path containment for read-only and workspace-write modes; traversal outside the task directory raises an error.
- Confirmed existing task rows receive `read-only` through the additive SQLite migration and every model request receives only the tools allowed by its persisted mode.

Verification: static review of `backend/app/main.py`, `frontend/src/main.tsx`, and the new permission tests; `git diff --check` was not applicable because this workspace is currently entirely untracked.

### Unit Testing

- Added coverage for persisting a selected permission mode, exposing only read tools for the default mode, and rejecting workspace-write attempts outside the registered task directory.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 8 tests and 1 existing Starlette deprecation warning.

## 2026-07-20

### Plan

- Requirements analysis: replace the blocking chat response with a Codex-like operational stream that makes observable agent progress available while avoiding disclosure of hidden model reasoning.
- High-level design: send Server-Sent Events for agent stage, model-network request, tool execution, streamed response tokens, changed files, completion, and errors; retain completed activity as a collapsed transcript section.
- Detailed design: add a task-scoped stream endpoint and permission-checked file viewer; consume the stream through `fetch` in the React composer; render written files as links instead of embedding local source code.

Verification: reviewed the OpenAI-compatible streaming protocol, existing tool-call permission boundary, and task conversation UI.

### Code

- Added `POST /api/tasks/{task_id}/messages/stream`, which streams operational activity and response tokens while persisting the final assistant message.
- Added a permission-checked task file-view endpoint and emitted changed-file events when the Agent calls `write_file`.
- Rebuilt the conversation output to show an expanded live work trace that auto-collapses when complete, followed by the streamed final answer and changed-file hyperlinks.

Verification: `npm --prefix frontend run build` passed; `.\.venv\Scripts\python.exe -m py_compile backend\app\main.py backend\tests\test_task_conversation.py` passed.

### Code Review

- Confirmed the work trace exposes only operational events (stage, network call, tool call, file action, and error), not chain-of-thought or unverified claims.
- Confirmed file links resolve through the same task permission boundary as tool reads, and the model instruction requests summaries instead of pasted changed source.
- Confirmed the legacy non-streaming message endpoint remains available for compatibility while the interface uses the new stream endpoint.

Verification: static review of `backend/app/main.py`, `frontend/src/main.tsx`, and `frontend/src/styles.css`.

### Unit Testing

- Added stream coverage using an OpenAI-compatible chunk sequence to assert activity, token, and completion events are emitted.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 9 tests and 1 existing Starlette deprecation warning.

## 2026-07-20

### Plan

- Requirements analysis: the stream failed after `list_files` because completion persistence assigned to `task` inside the generator, making the earlier tool-call reference a local variable before assignment. Test cleanup also left a user's previously active model profile deactivated.
- High-level design: preserve the stream task snapshot under its original name, use a separate completion-update variable, and make tests restore the original active provider after temporary activation.
- Detailed design: rename the completion variable; simulate a tool-call continuation in stream coverage; snapshot and reactivate the original provider in conversation and gateway test cleanup.

Verification: reviewed the tool-call stream scope, test cleanup paths, and active model profile state.

### Code

- Fixed the stream task-variable shadowing that prevented local tools from running after the model requested them.
- Updated test cleanup to restore the user's previously active model profile after temporary test profiles are removed.

Verification: `.\.venv\Scripts\python.exe -m py_compile backend\app\main.py backend\tests\test_task_conversation.py backend\tests\test_model_gateway.py` passed; `npm --prefix frontend run build` passed.

### Code Review

- Confirmed stream tool execution reads the stable task object while only completion metadata uses the separately loaded database row.
- Confirmed provider test cleanup restores the previous active profile only after temporary provider rows are deleted.

Verification: static review of `backend/app/main.py`, `backend/tests/test_task_conversation.py`, and `backend/tests/test_model_gateway.py`.

### Unit Testing

- Added a two-round streamed `list_files` test covering tool execution, model continuation, and completion; verified test execution preserves the existing active profile.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 9 tests and 1 existing Starlette deprecation warning; health check confirmed the external API profile remains active after tests.

## 2026-07-20

### Plan

- Requirements analysis: final model output was displayed as raw Markdown and sometimes repeated work-planning prose already represented by the operational trace, making the visible answer difficult to scan.
- High-level design: reserve the collapsed work trace for operational events and render the final answer as GitHub-Flavored Markdown; instruct the model to output conclusions only.
- Detailed design: add a Markdown renderer with GFM support, apply it to streamed final answers, map local absolute-path links to the permission-checked task file endpoint, and add restrained typography for Markdown elements.

Verification: reviewed the streamed final-answer surface, local file-link boundary, and the supplied UI screenshots.

### Code

- Added `react-markdown` and `remark-gfm` and rendered streamed final answers as formatted Markdown instead of raw text.
- Added styles for headings, lists, emphasis, inline and block code, tables, blockquotes, and links.
- Updated the model instruction so operational planning, tool narration, and future-intention text remain out of the final response; the application trace remains the single work-process view.
- Converted absolute local Markdown links into task file-view links protected by the current task permission mode.

Verification: `npm --prefix frontend run build` passed; `.\.venv\Scripts\python.exe -m py_compile backend\app\main.py` passed.

### Code Review

- Confirmed final-answer Markdown is rendered only through the renderer component and local absolute paths resolve through `/api/tasks/{task_id}/files`, not through unrestricted browser file URLs.
- Confirmed operational events remain in the auto-collapsing trace, and the final answer no longer uses a raw pre-wrapped text paragraph.

Verification: static review of `frontend/src/main.tsx`, `frontend/src/styles.css`, `frontend/package.json`, and `backend/app/main.py`.

### Unit Testing

- Existing streamed conversation, tool-call continuation, retry, permission, and provider tests remain green; the frontend TypeScript production build validates the Markdown renderer integration.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 9 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

## 2026-07-20

### Plan

- Requirements analysis: after the model called `list_files`, the stream raised `local variable 'task' referenced before assignment` instead of executing the local tool.
- High-level design: keep the task object captured for the entire stream separate from the task row loaded only to update completion metadata.
- Detailed design: rename the completion-only database variable and add a two-round tool-call stream test that executes `list_files` before receiving a final answer.

Verification: reviewed the generator variable scope and the screenshot event sequence.

### Code

- Renamed the completion-time task database variable so it no longer shadows the task object used by stream tool calls.

Verification: `.\.venv\Scripts\python.exe -m py_compile backend\app\main.py backend\tests\test_task_conversation.py` passed; `npm --prefix frontend run build` passed.

### Code Review

- Confirmed the stream's task object remains available through all model and local-tool rounds, while completion timestamp persistence uses a distinct variable.

Verification: static review of `backend/app/main.py`.

### Unit Testing

- Added streamed tool-call coverage for `list_files`, tool-result continuation, and final completion.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 9 tests and 1 existing Starlette deprecation warning.

## 2026-07-20

### Plan

- Requirements analysis: transient model-network failures should reconnect automatically without resubmitting the user's message; the first retry must wait 2 seconds, with five retries and exponential backoff.
- High-level design: retry each outbound model streaming request for transport failures only, surface every retry in the live operational trace, and report a concrete terminal cause after the final attempt.
- Detailed design: use waits of 2, 4, 8, 16, and 32 seconds; include endpoint, exception type, and exception detail after exhaustion; report non-transient HTTP status failures directly with status and response body.

Verification: reviewed the streamed request loop, tool continuation requests, and httpx transport/status exception hierarchy.

### Code

- Added five retry attempts with exponential backoff to every model stream request, including the continuation request after a tool call.
- Added live `网络重连` activity events showing retry count, delay, and transient failure cause.
- Added concrete terminal diagnostics for exhausted transport retries and non-retryable HTTP status responses.

Verification: `.\.venv\Scripts\python.exe -m py_compile backend\app\main.py backend\tests\test_task_conversation.py` passed; `npm --prefix frontend run build` passed.

### Code Review

- Confirmed only `httpx.TransportError` is retried; authentication, validation, and other HTTP status errors fail immediately with server-provided context.
- Confirmed retries are performed inside the model request loop, so a completed local tool call is not repeated when only its follow-up model request loses connectivity.

Verification: static review of `backend/app/main.py`.

### Unit Testing

- Extended stream failure coverage to assert the exact retry delays, five-attempt exhaustion behavior, and concrete transport error payload.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 9 tests and 1 existing Starlette deprecation warning.

## 2026-07-20

### Plan

- Requirements analysis: after `list_files`, an external OpenAI-compatible provider could terminate a continuation stream, causing an uncaught server-side generator exception and a browser-level `TypeError: network error`.
- High-level design: preserve the SSE connection whenever a normal runtime failure occurs by converting it into the existing structured `error` event.
- Detailed design: expand the event-generator boundary to catch ordinary exceptions and extend the streaming fixture with a transport failure after a successful streamed exchange.

Verification: reviewed the screenshot sequence, active provider configuration, and stream generator exception boundary.

### Code

- Changed the stream generator to emit an SSE error event for all ordinary runtime failures instead of allowing the HTTP connection to abort.

Verification: `.\.venv\Scripts\python.exe -m py_compile backend\app\main.py backend\tests\test_task_conversation.py` passed; `npm --prefix frontend run build` passed.

### Code Review

- Confirmed successful stage, network, and tool events are retained before a later stream failure, and the frontend receives a readable error event rather than a failed fetch.

Verification: static review of `backend/app/main.py` and `frontend/src/main.tsx`.

### Unit Testing

- Extended stream coverage to raise a simulated transport failure after a normal stream and assert that the response contains an SSE `error` event.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 9 tests and 1 existing Starlette deprecation warning.

## 2026-07-20

### Plan

- Requirements analysis: the stream UI reported `list index out of range` when an OpenAI-compatible server emitted an SSE chunk with an empty `choices` array before its first content chunk.
- High-level design: treat empty-choice stream chunks as protocol metadata and ignore them, while continuing to process later token or tool-call chunks.
- Detailed design: replace direct indexing with an empty-array guard and add that event to the stream fixture before the first response token.

Verification: reviewed the SSE chunk parser and the reported runtime error.

### Code

- Added an empty-`choices` guard to the streamed model response parser.

Verification: `.\.venv\Scripts\python.exe -m py_compile backend\app\main.py backend\tests\test_task_conversation.py` passed; `npm --prefix frontend run build` passed.

### Code Review

- Confirmed the guard skips only chunks that cannot contain a choice and preserves normal token, tool-call, completion, and error handling.

Verification: static review of `backend/app/main.py`.

### Unit Testing

- Extended streamed-conversation coverage with an empty `choices` SSE event before the response token.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 9 tests and 1 existing Starlette deprecation warning.

## 2026-07-20

### Plan

- Requirements analysis: permission selection belongs to the active task conversation because users need to inspect the code first and change the access level before later messages; it must not be decided in the task creation form.
- High-level design: keep new tasks read-only by default and add a task-scoped permission update action used by a composer control positioned opposite the model selector.
- Detailed design: remove permission input from task creation, add `PATCH /api/tasks/{task_id}/permission`, and persist the selected mode before the next model request is sent.

Verification: reviewed task creation defaults, task response state, the model tool-selection path, and composer layout.

### Code

- Moved the permission selector from the new-task dialog to a popup at the lower-left of the conversation composer.
- Added a task permission update endpoint and connected the popup so changes are saved immediately and update the active conversation state.
- New tasks now always begin in read-only mode; the creation form no longer sends or displays a permission choice.

Verification: `.\.venv\Scripts\python.exe -m py_compile backend\app\main.py backend\tests\test_task_sources.py` passed; `npm --prefix frontend run build` passed.

### Code Review

- Confirmed permission changes are task-scoped, persisted before focus returns to the composer, and therefore used by the next model request's tool list.
- Confirmed the initial permission is assigned on the server rather than trusted from the task-creation client payload.

Verification: static review of `backend/app/main.py` and `frontend/src/main.tsx`.

### Unit Testing

- Updated task source coverage to verify a new task starts read-only and the conversation permission endpoint persists a later workspace-write selection.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 8 tests and 1 existing Starlette deprecation warning.

## 2026-07-20

### Plan

- Requirements analysis: user-sent messages must no longer show the `你` role label while retaining their content and right-aligned bubble styling.
- High-level design: render the role label only for assistant messages so the user label is absent without changing message persistence or layout classes.
- Detailed design: replace the conditional label text with an assistant-only JSX node in the message-list mapping; cover the expected JSX contract with the existing pytest suite.

Verification: reviewed the message-list component and its `.message.user` styling in `frontend/src/main.tsx` and `frontend/src/styles.css`.

### Code

- Removed the `你` label from user-sent message bubbles by rendering the role label exclusively for Agent messages.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 10 tests; `npm --prefix frontend run build` passed.

### Code Review

- Confirmed user messages retain the existing `message user` classes, content paragraph, and bubble styling; only their label node is omitted.

Verification: static review of `frontend/src/main.tsx`.

### Unit Testing

- Added a frontend rendering contract test that asserts the role label is assistant-only and the prior user-label expression is absent.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 10 tests; `npm --prefix frontend run build` passed.

## 2026-07-20

### Plan

- Requirements analysis: the web application routed every message directly to one model conversation; task stages were fixed at creation and there was no complexity decision, Agent role assignment, stage record, or artifact persistence.
- High-level design: add a deterministic Main Agent orchestration layer that classifies requests into read-only analysis, simple development, or full development; represent sub-Agents through stage-specific roles and tool boundaries.
- Detailed design: persist workflow classification and assigned Agent on `Task`; add append-only `StageRun` records and artifact outputs; route both streaming and non-streaming messages through the same stage state machine; require explicit coding confirmation plus a non-read-only permission before the Execution Agent can start.

Verification: reviewed `多Agent MCP客户端网页.md`, `应用开发计划.md`, the existing FastAPI conversation endpoints, SQLite model schema, permissions, and tests.

### Code

- Added Main Agent workflow routing for read-only analysis, simplified development, and full development flows, including the planned requirements, design, coding, review, testing, repair, and acceptance stage transitions.
- Added persistent task workflow metadata, stage-run records, phase outputs, and task-scoped stage/artifact APIs.
- Bound model prompts to the assigned Reading, Execution, Review, or Test Agent role and the current stage; both conversation endpoints now save completed stage outputs and advance the workflow.
- Enforced the coding approval gate in tool selection: a writable task still receives only read tools until the Main Agent enters an execution stage.
- Added workflow orchestration tests covering read-only routing, artifact persistence, full-flow transition, confirmation, and permission gating.

Verification: `python -m py_compile backend\app\main.py backend\tests\test_workflow_orchestration.py` passed; `npm --prefix frontend run build` passed.

### Code Review

- Confirmed the stream and compatibility conversation endpoints both invoke the same `route_message` and `complete_stage` functions.
- Confirmed old SQLite databases receive additive task columns while the new `stage_runs` table is created through SQLAlchemy metadata initialization.
- Identified and fixed the approval-bypass risk where a writable task at `待编码确认` could otherwise receive `write_file`; stage-aware tool selection now restricts that point to read tools.

Verification: static review of `backend/app/main.py` and `backend/tests/test_workflow_orchestration.py`; `git diff --check` completed without whitespace errors.

### Unit Testing

- Added focused unit coverage for read-only Reading Agent routing and stored artifacts, full workflow routing, phase transitions, confirmation handling, and the pre-confirmation read-tool boundary.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 12 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

## 2026-07-20

### Plan

- Requirements analysis: streamed replies were rendered as Markdown, but persisted Agent messages reloaded from the task history were rendered as plain text and exposed Markdown source syntax.
- High-level design: use the existing shared Markdown renderer for every persisted Agent message while preserving plain-text rendering for user messages.
- Detailed design: replace the historical-message content paragraph with an assistant-role conditional that passes content and task ID to `MarkdownContent`; add a frontend source contract test.

Verification: reviewed the message-list and streamed-output rendering paths in `frontend/src/main.tsx`.

### Code

- Rendered persisted Agent conversation messages through `MarkdownContent`, matching the streamed-reply presentation after reload.
- Kept user messages as plain-text bubbles and formatted the existing JSX file for maintainability.

Verification: `npm --prefix frontend run build` passed.

### Code Review

- Confirmed the shared renderer continues to route local absolute-path links through the task-scoped file endpoint and does not affect user message handling.
- Confirmed the change only selects a renderer by message role; conversation persistence and SSE processing remain unchanged.

Verification: static review of `frontend/src/main.tsx`; `git diff --check` completed without whitespace errors.

### Unit Testing

- Added a frontend rendering contract test asserting persisted Agent messages use `MarkdownContent`.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 13 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

## 2026-07-20

### Plan

- Requirements analysis: add a Codex-style context monitor at the lower-right of the conversation composer, show the current-to-total context ratio on hover, and provide an explicit context compression action.
- High-level design: retain visible chat history while excluding compressed messages from future model requests, replacing them with a bounded task-level summary so compression reduces prompt size without deleting the user's transcript.
- Detailed design: expose task-scoped usage and compression APIs, use a deterministic four-characters-per-token local estimate against a 128,000-token limit, persist the compressed-message flag and summary through additive SQLite migrations, and render a gray/black conic progress ring with an accessible hover/focus popover.

Verification: reviewed the composer layout, both streaming and compatibility conversation request paths, SQLite migration pattern, and existing frontend/backend test conventions.

### Code

- Added task context usage and compression endpoints, persistent context summaries, and compressed-message tracking. Compression now preserves the visible transcript while later model calls receive the summary plus only newly added messages.
- Added the lower-right context monitor ring, hover/focus ratio popover, and compression button; the black segment represents estimated used context while the remaining ring stays gray.

Verification: `npm --prefix frontend run build` passed; `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 16 tests and 1 existing Starlette deprecation warning.

### Code Review

- Confirmed compressed history is filtered in both streaming and non-streaming model request paths, while the normal message-list API continues to return the full transcript.
- Confirmed the ring control remains keyboard-accessible through focus-within and disables compression when there are no new messages to compress.

Verification: static review of `backend/app/main.py`, `frontend/src/main.tsx`, and `frontend/src/styles.css`; `git diff --check` passed.

### Unit Testing

- Added coverage for context estimation, compression, persistent compacted flags, and preserved chat-history retrieval, plus frontend source contracts for the ring, hover popover, and compression action.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 16 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

## 2026-07-21

### Plan

- Requirements analysis: the task configuration dialog exposed an initial-requirement field even though task requirements are entered and refined through the conversation. Re-editing it would create a second, misleading task-editing surface.
- High-level design: limit task configuration to stable task metadata: title and Agent permission. Preserve the original persisted requirement for compatibility and history, but do not expose it as an editable control.
- Detailed design: remove the frontend textarea and its payload field, narrow the task-update request model, and assert that a configuration update leaves the existing requirement unchanged.

Verification: reviewed task creation, conversation entry, task update, and configuration-modal flows.

### Code

- Removed the “初始需求” field from task configuration and stopped accepting it from the task configuration update endpoint.
- Kept task title, permission selection, task deletion, and the existing conversation-based requirement workflow unchanged.

Verification: `npm --prefix frontend run build` passed.

### Code Review

- Confirmed configuration updates can no longer overwrite historical requirement data and that all user-facing requirement editing remains in the task conversation.

Verification: static review of `frontend/src/main.tsx` and `backend/app/main.py`.

### Unit Testing

- Updated task-management coverage to assert the persisted requirement remains unchanged and the removed field is absent from the frontend source.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 19 tests and 1 existing Starlette deprecation warning.

### Plan

- Requirements analysis: the Vite development page at port 5173 served its HTML fallback for `/api/tasks`, so the frontend attempted to parse a script-tag response as JSON. The backend already uses port 8787, but the development server had no API forwarding rule.
- High-level design: keep browser requests relative to `/api` and configure the Vite development server as the local reverse proxy to the existing backend port.
- Detailed design: add a `/api` proxy targeting `127.0.0.1:8787`, permit the Vite development origins in backend CORS, and lock the proxy contract with a focused source test.

Verification: reproduced `GET http://127.0.0.1:5173/api/tasks` returning Vite HTML and confirmed no backend process was listening on port 8787.

### Configuration

- Added Vite development-server proxying for `/api` to `http://127.0.0.1:8787` and added the `5173` localhost origins to backend CORS.

Verification: with FastAPI running on port 8787, `Invoke-WebRequest http://127.0.0.1:5173/api/tasks` returned task JSON rather than Vite HTML; `GET /api/health` returned 200.

### Code Review

- Confirmed all frontend API requests already use the `/api` prefix, so proxying applies consistently without changing production API paths or request code.

Verification: static review of `frontend/src/main.tsx`, `frontend/vite.config.ts`, and `backend/app/main.py`.

### Unit Testing

- Added a focused frontend development-proxy configuration contract test.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 19 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

### Plan

- Requirements analysis: the left task list needed task-scoped management without making the primary selection action harder to use. Hovering a task must provide a configuration control, and users need a deliberate way to change configuration or remove obsolete task history.
- High-level design: retain single-click task selection; show a compact overflow control on hover or keyboard focus; place configuration and deletion in a task-level menu. Keep code directories and reusable workspace records intact when a task is deleted.
- Detailed design: add task update and delete APIs; provide a configuration modal for title, initial requirement, and permission; confirm destructive deletion in the browser; remove task conversation and stage data through the existing ORM cascade.

Verification: reviewed the task model relationships, existing task creation and permission APIs, sidebar layout, and frontend component patterns.

### Code

- Added a darker hover/focus state and overflow configuration button to every left-sidebar task row, with menu actions for changing configuration and deleting the task.
- Added a task configuration modal that saves the task title, initial requirement, and Agent permission.
- Added `PATCH /api/tasks/{task_id}` and `DELETE /api/tasks/{task_id}`. Deletion removes only the task and dependent task records; the bound Git workspace remains available for other tasks.

Verification: `npm --prefix frontend run build` passed; `git diff --check` passed.

### Code Review

- Confirmed task selection remains a direct row action while the overflow button has an independent event target; keyboard focus also reveals the control.
- Confirmed deletion is explicitly confirmed and clears the active frontend selection, while ORM task relationships remove dependent messages and stage records without deleting the workspace path.
- Confirmed configuration updates refresh both the sidebar and the active conversation state.

Verification: static review of `backend/app/main.py`, `frontend/src/main.tsx`, and `frontend/src/styles.css`.

### Unit Testing

- Added task API coverage for configuration persistence and deletion, plus a frontend source contract covering the overflow control, configuration action, delete action, and hover styles.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 18 tests and 1 existing Starlette deprecation warning.

### Plan

- Requirements analysis: a completed compression trace must communicate completion explicitly rather than retaining the in-progress title after the operation ends.
- High-level design: use the existing streamed completion/error events to update the activity trace title and append a terminal status row.
- Detailed design: on success, show the resulting token ratio; on either stream or request failure, label the trace as failed while retaining the error detail.

Verification: reviewed the compression SSE event handler and shared activity-trace rendering in `frontend/src/main.tsx`.

### Code

- Added explicit `上下文压缩完成` and `上下文压缩失败` terminal titles to the conversation progress card, including the resulting used/total token count on success.

Verification: `npm --prefix frontend run build` passed.

### Code Review

- Confirmed successful completion updates both the monitor state and the visible trace, while all error paths now use a consistent failed title.

Verification: static review of `frontend/src/main.tsx`; `git diff --check` passed.

### Unit Testing

- Extended the frontend compression contract test to require both terminal status labels.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 16 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

## 2026-07-20

### Plan

- Requirements analysis: replace the immediate local truncation with Codex-like semantic context compression and show its progress in the conversation instead of silently reducing the displayed estimate.
- High-level design: send the existing summary and currently active transcript to the selected model with a preservation-focused summarization prompt; save the returned summary only after the model succeeds.
- Detailed design: expose an SSE compression route with preparation, model-processing, persistence, completion, and failure events; render those events through the existing conversation activity trace and block sends while compression is active.

Verification: reviewed the existing streaming-message protocol, provider request conventions, task summary persistence, and conversation activity rendering.

### Code

- Replaced deterministic text slicing with a model-generated Markdown context summary that retains task goals, requirements, decisions, modified files, test results, workflow state, unresolved work, and exact continuation details.
- Added streamed context-compression progress to the conversation, including preparing context, model summarization, persistence, completed, and error states; the composer prevents concurrent sends and compression operations.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 16 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

### Code Review

- Confirmed that messages receive compacted flags only after a non-empty model summary is returned and persisted; failed model calls leave both the source messages and prior summary untouched.
- Confirmed compressed history remains visible through the normal message API, while both model conversation paths continue to receive the task summary plus only active messages.

Verification: static review of `backend/app/main.py` and `frontend/src/main.tsx`; `git diff --check` passed.

### Unit Testing

- Updated compression coverage to mock a model summary, assert SSE progress and completion events, confirm the model receives the source transcript, verify lower context usage after persistence, and preserve the frontend monitor contract.

Verification: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` passed with 16 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

## 2026-07-21

### Plan

- Requirements analysis: prepare the repository for an initial remote upload while retaining only reproducible source, dependency manifests, tests, documentation, and configuration.
- High-level design: use root-level Git ignore rules to exclude generated dependencies, build output, runtime state, logs, databases, and credentials; make the README the concise onboarding entry point.
- Detailed design: cover Python, Node/Vite, SQLite, environment files, editor metadata, and OS artifacts in `.gitignore`; document prerequisites, startup, tests, and the intended commit scope in `README.md`.

Verification: inspected the repository layout, existing ignore rules, backend requirements, frontend package manifest, and runtime `data/` contents.

### Configuration

- Expanded `.gitignore` to exclude Python and Node generated files, TypeScript build metadata, SQLite sidecars, logs, runtime task data, local environment files, editor settings, OS metadata, and workspace-only agent/planning notes so that secrets, generated state, and internal planning material are not included in uploads.

Verification: `git check-ignore -v` confirmed that `.venv/`, `data/model-secrets.json`, `frontend/node_modules/`, `frontend/dist/`, and Python bytecode are ignored.

### Documentation

- Rewrote `README.md` in Chinese with the project purpose, feature scope, technology stack, prerequisites, local startup commands, test commands, and a clear list of files that must not be committed.

Verification: reviewed all commands against `backend/requirements.txt`, `frontend/package.json`, and the application entry point.

### Code Review

- Confirmed ignore patterns preserve source files, tests, dependency manifests, lock files, project configuration, README, and change records while excluding local runtime data and credentials.
- Confirmed the documented startup and test commands match the checked-in backend and frontend tooling.

Verification: static review of `.gitignore` and `README.md`.

### Unit Testing

- No application code changed; unit-test coverage is not applicable. Configuration verification was performed with Git ignore checks.

Verification: `git check-ignore -v` passed for intended generated and sensitive paths.

## 2026-07-21

### Plan

- Requirements analysis: replace keyword-only task routing with a master-agent decision that explicitly classifies task complexity and the required multi-agent workflow.
- High-level design: use a JSON-only master-agent response as the routing contract, persist the accepted decision on the task, and drive stage transitions from its approved sequence.
- Detailed design: constrain `task_type`, `workflow`, and the exact allowed `required_stages` sequences with Pydantic validation; retain the deterministic classifier only as a failure fallback so malformed model output cannot block a task.

Verification: reviewed the existing model gateway, routing entry points, stage state machine, SQLite additive migration pattern, and orchestration tests.

### Code

- Added a persisted `routing_decision` task field and additive SQLite migration. Task responses now include the master-agent decision containing `task_type`, `complexity_reason`, `workflow`, and `required_stages`.
- Added a JSON-only master-agent routing request for the first task message. The service validates the model result against approved stage sequences before assigning agents and advancing the workflow; invalid JSON, invalid stage sequences, and model failures use a recorded deterministic fallback.
- Updated stage advancement to use the approved `required_stages` sequence, preserving the existing coding-permission confirmation and acceptance gates.

Verification: `git diff --check` passed.

### Code Review

- Confirmed untrusted model output cannot select arbitrary stage names, skip mandatory review/testing stages, or make a read-only task enter a development workflow.
- Confirmed routing decisions are persisted before the stage run and that existing direct routing callers retain the deterministic fallback behavior.

Verification: static review of `backend/app/main.py`; `git diff --check` passed.

### Unit Testing

- Added coverage for a valid master-agent JSON decision controlling and persisting a full workflow, and for rejection of an invalid stage contract with deterministic fallback.

Verification: `.\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` passed with 21 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

## 2026-07-21

### Plan

- Requirements analysis: remove deterministic keyword fallback because task classification must be exclusively owned by the master Agent; failed routing must leave the task unstarted and allow an explicit user retry.
- High-level design: make master-agent routing a required precondition before persisting a user message or opening a stage run; report failures through the existing stream activity surface.
- Detailed design: raise a dedicated routing error for unavailable, malformed, or invalid JSON decisions; return a retryable SSE error for streaming chat and HTTP 502 for the non-stream endpoint; retain the original draft in a retry action.

Verification: reviewed the message persistence order, stage creation path, streamed error renderer, and existing retry-safe composer state.

### Code

- Removed the keyword classifier and all fallback route generation. The master Agent is now the sole source of a task routing decision.
- Added routing-failure handling that prevents user-message persistence and stage creation. Streaming requests emit the exact failure reason with a retryable flag; non-stream requests return HTTP 502.
- Added a visible `重试主 Agent 判定` action to failed conversation runs, using the original task input for a new routing attempt.

Verification: `git diff --check` passed; `npm --prefix frontend run build` passed.

### Code Review

- Confirmed model transport errors, non-JSON output, and invalid stage contracts leave `workflow_type` unclassified and do not create a message or stage run.
- Confirmed retrying does not bypass the master Agent, and only a validated decision can begin the orchestrated workflow.

Verification: static review of `backend/app/main.py` and `frontend/src/main.tsx`; `git diff --check` passed.

### Unit Testing

- Updated model conversation fixtures for the required master-agent routing call and added API coverage proving a failed route persists neither messages nor stage runs.

Verification: `.\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` passed with 22 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

## 2026-07-21

### Plan

- Requirements analysis: add a task-scoped execution mode with a safe default. Plan mode must stop only before coding, automatic mode must pass that gate, and neither mode may alter a task once coding has begun.
- High-level design: persist `execution_mode` on `Task`, expose it in task responses and task configuration, and calculate a server-authoritative lock from the development workflow stage.
- Detailed design: use `confirm_before_coding` and `automatic` as the only accepted values; apply the mode in `next_stage` whenever the following stage is `编码实现`; expose a compact composer control next to the permission selector and reject late changes through both update endpoints.

Verification: reviewed the existing SQLite additive migration, approved workflow sequences, confirmation gate, composer control layout, and task configuration API.

### Code

- Added persistent `execution_mode` storage with an additive SQLite migration; new tasks default to `confirm_before_coding` and task payloads now include the selected mode plus an `execution_mode_locked` flag.
- Added `PATCH /api/tasks/{task_id}/execution-mode` and task-configuration support for changing the mode before coding. Existing task-update clients may omit the new field.
- Updated stage advancement so plan mode enters `待编码确认` before implementation, while automatic mode advances directly to `编码实现`; existing implementation-to-review-to-test-to-acceptance transitions remain uninterrupted.
- Added the two-state execution-mode picker beside the composer permission button and a task-configuration selector. Both controls are disabled after coding begins.

Verification: `git diff --check` passed; `npm --prefix frontend run build` passed.

### Code Review

- Confirmed validation restricts persisted values to the two supported modes and that the server, rather than the disabled frontend control, enforces the post-coding lock.
- Confirmed read-only workflows still finish at `已完成`, simple workflows can bypass design stages, and full workflows retain requirements, overview, and detailed design before the coding decision point.

Verification: static review of `backend/app/main.py`, `frontend/src/main.tsx`, and `frontend/src/styles.css`.

### Unit Testing

- Added coverage for the plan-mode default, automatic full-workflow progression from detailed design through acceptance, and rejection of a mode update after coding starts.

Verification: `.\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` passed with 23 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

## 2026-07-21

### Plan

- Requirements analysis, high-level design, and detailed design: introduce local `stdio` MCP Server connectivity as an application capability while retaining task-scoped authorization and existing workflow permission gates.

Verification: inspected the existing tool dispatch, task configuration, model function-call routes, and Python MCP SDK client interfaces.

### Code

- Added local MCP Server discovery and invocation, persisted server profiles, task-level tool grants, model function-schema adaptation, and server-side read/write/full-access enforcement.

Verification: a test MCP stdio process completed discovery and `tools/call` through the application; `git diff --check` passed.

### Documentation

- Updated README with local MCP setup, task authorization, permission behavior, local-environment secret handling, and the first-version `stdio` transport boundary.

Verification: documentation checked against the implemented API and access-policy paths.

### Code Review

- Confirmed MCP configuration cannot bypass task authorization, workflow stage checks, or task permission; commands and argument arrays are not shell-concatenated.

Verification: static review of backend routing, UI configuration, and MCP-focused tests.

### Unit Testing

- Added real stdio MCP coverage for discovery, call routing, duplicate and unavailable server handling, access classes, and UI configuration contracts.

Verification: `.\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` passed with 26 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.



## 2026-07-21

### Plan

- Requirements analysis: the master Agent was limited to three fixed stage sequences and routed only from the incoming message, so it could repeat planning already completed in the task context.
- High-level design: retain the three workflow labels for task classification, but let the master Agent select the concrete remaining stages from the permitted ordered stage set.
- Detailed design: supply the router with recent conversation, compressed context, prior plan, current stage, and completed artifact summaries; validate that selected stages are unique and ordered while preserving read-only, confirmation, and tool-permission gates.

Verification: inspected routing validation, task state transitions, persisted artifacts, context compaction data, and tool access checks.

### Code

- Reworked master-Agent routing so `simple` and `full` are descriptive collaboration modes rather than fixed sequences. The Agent can now choose only the necessary ordered development stages, including starting directly at implementation, review, or testing when prior context already contains planning.
- Added persisted-context assembly for both streaming and non-streaming routing requests, including recent messages, compressed context, previous plans, current state, and completed artifact summaries.

Verification: `git diff --check` passed; static search confirmed the fixed-sequence validation and prompt wording were removed.

### Code Review

- Confirmed arbitrary stage names, duplicate stages, and out-of-order development plans are rejected; read-only requests remain restricted to analysis, and existing coding-confirmation and write-permission enforcement are unchanged.

Verification: reviewed `backend/app/main.py` routing validation, prompt construction, state transitions, and tool availability checks.

### Unit Testing

- Added coverage proving that a task with persisted design context can select implementation, code review, and unit testing without repeating planning; updated invalid-route coverage to reject an out-of-order stage plan.

Verification: `.\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` passed with 30 tests and 1 existing Starlette deprecation warning.

## 2026-07-22

### Plan

- Requirements analysis: a new repair instruction submitted while a task was waiting at unit testing was treated as input to the Test Agent, so the task produced an audit response instead of replanning the repair.
- High-level design: have the master Agent re-evaluate every new instruction against persisted task context; reserve only explicit coding and acceptance confirmations for their existing guarded transitions.
- Detailed design: replace the stored plan with the validated new plan before selecting its first stage; retain the coding confirmation gate when a replanned workflow starts at implementation.

Verification: traced the reported JWT task state, persisted stage records, and the message-routing path.

### Code

- Updated streaming and non-streaming message routes to invoke master-Agent planning for new instructions on existing tasks, preventing a stale review or test stage from owning unrelated work.
- Added a guarded replan path that assigns the newly selected first stage and Agent while preserving approval-before-coding behavior.

Verification: `git diff --check` passed.

### Code Review

- Confirmed explicit confirmation messages still advance the existing approval states without re-routing, while every other instruction replaces stale workflow plans through validated stage selection.

Verification: reviewed `backend/app/main.py` state transitions and permission gates.

### Unit Testing

- Added coverage for a JWT repair request submitted during an existing unit-test stage; it is replanned to the Execution Agent instead of reusing the Test Agent.

Verification: `.\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` passed with 33 tests and 1 existing Starlette deprecation warning.

## 2026-07-21

### Plan

- Requirements analysis: fix the conversation view so completed Agent work is not rendered a second time after all message history, which separated user instructions from their replies.
- High-level design: treat streamed work output as temporary UI state and use persisted task messages as the sole completed conversation timeline.
- Detailed design: after a stream completes and `refreshTaskWorkflow` loads the persisted user and assistant messages in timestamp order, remove that run by its client-side `runId`; failed runs remain visible with their retry action.

Verification: traced the message-list rendering and confirmed that `messages` and completed `runs` were rendered as two consecutive groups.

### Code

- Removed completed streaming runs after their persisted history refresh, preventing duplicate, out-of-order Agent output in the conversation view.

### Correction

- Reload persisted task messages as part of that history refresh before removing the completed temporary run, so the submitted user message and final Agent reply remain visible without a browser reload.

Verification: full backend suite passed with 31 tests and 1 existing Starlette deprecation warning; frontend production build and `git diff --check` passed.

### Plan

- Requirements analysis: restore the completed execution-process disclosure without reintroducing the prior out-of-order, duplicate run rendering.
- High-level design: use persisted stage-run metadata for completed conversations and retain the existing temporary stream trace only while a response is running.
- Detailed design: associate a completed stage run with its persisted assistant message by exact output content, then render a closed `details` disclosure containing its stage, Agent, status, and input summary.

Verification: reviewed persisted `StageRun` fields and the conversation rendering lifecycle.

### Code

- Added a per-reply expandable `工作过程` panel backed by persisted stage-run data, including compact stage, Agent, status, and request-summary information.

Verification: focused frontend rendering tests and production build passed.

### Code Review

- Confirmed completed traces are nested within their matching Agent reply and do not form a separate conversation group; unmatched or failed streaming runs retain their existing temporary handling.

Verification: static review of stage-run matching and the stream completion path.

### Unit Testing

- Added a frontend contract requiring an expandable completed stage trace matched to the persisted Agent reply.

Verification: full backend suite passed with 32 tests and 1 existing Starlette deprecation warning; frontend production build and `git diff --check` passed.

Verification: reviewed the post-stream lifecycle in `frontend/src/main.tsx`.

### Code Review

- Confirmed only successful completed runs are removed; errors remain available for retry and no server-side task history is changed.

Verification: static review of the stream completion and error paths.

### Unit Testing

- Added a frontend rendering contract requiring completed streaming runs to be removed after history refresh.

Verification: focused frontend pytest passed with 4 tests; full backend suite, frontend production build, and `git diff --check` are pending final execution.

## 2026-07-21

### Plan

- Requirements analysis: fix the conversation view so completed Agent work is not rendered a second time after all message history, which separated user instructions from their replies.
- High-level design: treat streamed work output as temporary UI state and use persisted task messages as the sole completed conversation timeline.
- Detailed design: after a stream completes and `refreshTaskWorkflow` loads the persisted user and assistant messages in timestamp order, remove that run by its client-side `runId`; failed runs remain visible with their retry action.

Verification: traced the message-list rendering and confirmed that `messages` and completed `runs` were rendered as two consecutive groups.

### Code

- Removed completed streaming runs after their persisted history refresh, preventing duplicate, out-of-order Agent output in the conversation view.

Verification: reviewed the post-stream lifecycle in `frontend/src/main.tsx`.

### Code Review

- Confirmed only successful completed runs are removed; errors remain available for retry and no server-side task history is changed.

Verification: static review of the stream completion and error paths.

### Unit Testing

- Added a frontend rendering contract requiring completed streaming runs to be removed after history refresh.

Verification: pending focused and full test execution.

## 2026-07-21

### Plan

- Requirements analysis: clarify that the conversation is chronological, but earlier failed or interrupted requests may have no persisted Agent response, making consecutive user instructions appear together.
- High-level design: retain chronological history while visually separating every Agent response from adjacent replies and the activity trace.
- Detailed design: apply one shared assistant-message container style to both persisted replies and streamed `RunOutput` entries, using a border, restrained surface, and existing spacing without changing message data or ordering.

Verification: compared the reported task's persisted `task_messages` timestamps and roles with the rendered message-list implementation.

### Code

- Added a distinct visual container for Agent messages and streaming work output so successive Agent replies no longer visually merge into one continuous white page.
- Updated the conversation request test to require the built-in read-only tools while allowing globally enabled MCP tools, matching the shared MCP Server configuration model.

Verification: frontend source reviewed for shared `.message.assistant` application to persisted and streamed messages.

### Code Review

- Confirmed the change is presentation-only: it does not reorder messages, hide failed requests, or alter task history and streaming state.

Verification: reviewed `frontend/src/main.tsx` and the assistant-message CSS selector.

### Unit Testing

- Extended the frontend rendering contract to require a dedicated assistant message container and visual boundary.

Verification: focused frontend pytest passed with 3 tests; full backend suite passed with 28 tests and 1 existing Starlette deprecation warning; frontend production build and `git diff --check` passed.

## 2026-07-21

### Plan

- Requirements analysis: change MCP ownership from task-scoped grants to global enabled Server profiles that every task can reuse; provide a one-click bundled coding MCP Server.
- High-level design: discover tools once per global Server profile, expose enabled tools to every task subject to that task's stage and permission checks, and start the bundled Server with the current task workspace supplied only by the host process.
- Detailed design: classify bundled list/read tools as read-only and write tools as workspace-write; reject absolute paths, traversal outside the resolved workspace, and excluded directories inside the bundled Server.

Verification: reviewed existing MCP persistence, model function-schema generation, task-stage gates, and stdio lifecycle before changing the routing model.

### Code

- Added `builtin_coding_mcp.py` and `POST /api/mcp-servers/presets/coding`; the MCP Server management dialog now has an “添加预制编码 MCP Server” action which registers it once and makes its tools globally available.
- Changed MCP tool discovery, model schema exposure, dispatch, and streamed activity lookup to use all enabled Server profiles rather than per-task authorization records. Existing task grants no longer control availability.
- Passed the current task worktree to bundled MCP calls through `LOCAL_AGENT_WORKSPACE`; the bundled Server constrains list, read, and write operations to that workspace and preserves write-stage/task-permission enforcement in the application backend.
- Preserved tool access classifications on Server update and diagnosis. Third-party MCP tools default to read-only when no classification is present.

Verification: `git diff --check` passed; focused tests verified a real bundled stdio Server discovers its tools and writes only after the task enters an allowed coding stage.

### Documentation

- Updated README to document global MCP Server reuse, the prebuilt coding Server, automatic tool use, and its workspace boundary and write restrictions.

Verification: documentation reviewed against the MCP route, adapter environment injection, and bundled Server implementation.

### Code Review

- Confirmed that global Server reuse does not make write tools globally writable: `tools_for_task` filters by the current task before the model sees a function, and `execute_tool` repeats the same check immediately before `tools/call`.
- Confirmed the bundled Server does not accept a model-provided root directory and resolves every relative path against the host-provided task worktree.
- Confirmed re-diagnosing a Server retains access classification and that deleting or disabling a Server removes it from all task tool lists.

Verification: static review of `backend/app/main.py`, `backend/app/builtin_coding_mcp.py`, `frontend/src/main.tsx`, and the focused MCP tests.

### Unit Testing

- Updated MCP tests for global Server visibility and added bundled coding Server coverage for discovery, automatic availability, write-stage gating, and workspace-local writes.
- Updated the frontend contract test to require the prebuilt Server action and absence of per-task MCP authorization UI.

Verification: `.\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` passed with 27 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed; `git diff --check` passed.

## 2026-07-21

### Plan

- Requirements analysis: allow the independent web application to connect to separately implemented local MCP Servers through `stdio`, while preserving the existing task workflow and server-authoritative permission model.
- High-level design: add an MCP client adapter that starts short-lived local server processes for discovery and calls; store server profiles in the application and grant discovered tools to individual tasks only.
- Detailed design: model MCP Server command, argument list, enabled state, and discovered schemas; let each task assign an access class to each selected tool; translate only task-authorized and stage-permitted tools into model function schemas, then resolve tool calls back to MCP `tools/call`.

Verification: inspected the existing tool registry, model function-call loops, task configuration API, workflow-stage permissions, and React configuration modal patterns; installed and inspected the official Python `mcp` SDK `stdio` client API.

### Code

- Added the official `mcp` dependency and a local `stdio` client adapter that initializes an MCP session, discovers tools, invokes tools with bounded timeouts, and closes the child process/session after each operation.
- Added persisted MCP Server profiles, server CRUD and diagnostic APIs, discovered tool schemas, task-specific MCP tool grants, and cleanup of grants when a server or task is deleted.
- Added backend conversion from authorized MCP schemas to unique model function names, routing from model tool calls to MCP `tools/call`, and streamed activity entries naming the called MCP Server and tool.
- Enforced access classes server-side: read-only tools are available when granted, workspace-write tools require the existing writable execution stage and task permission, and full-access tools require full task access.
- Added UI configuration for local `stdio` MCP Servers and task-level tool selection with a per-tool access class; updated the README with the local-only transport, environment-variable secret handling, and permission behavior.

Verification: `git diff --check` passed; a real test MCP stdio process successfully completed initialization, tool discovery, schema conversion, and `tools/call` through the application adapter.

### Code Review

- Confirmed command and arguments are passed to the SDK as separate values rather than through shell concatenation, and MCP Server environment variables are inherited from the host process rather than persisted in the database.
- Confirmed an MCP Server profile or frontend access-class selection does not grant execution by itself: task authorization, current workflow stage, and task permission are all checked immediately before the MCP call.
- Confirmed unknown, disabled, stale, duplicate, and unauthorized MCP tools are rejected before a model tool call can reach a server.

Verification: static review of `backend/app/main.py`, `frontend/src/main.tsx`, `README.md`, and MCP-focused tests; `git diff --check` passed.

### Unit Testing

- Added a real local `stdio` MCP test server to cover discovery, duplicate server rejection, task authorization isolation, model function schema mapping, tool-call response handling, write-stage gating, full-access gating, unavailable commands, and server cleanup.
- Added frontend configuration-contract coverage for MCP server management and task tool authorization controls.

Verification: `.\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` passed with 26 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

## 2026-07-21

### Plan

- Requirements analysis: restore the visible identity of persisted user messages and explain why a task cannot modify documentation or source files when file-write capability is unavailable.
- High-level design: render a role label for both conversation participants and expose a server-authoritative file-write status derived from the task permission and workflow stage.
- Detailed design: make `write_enabled` true only for `编码实现` and `修复` with `工作区读写` or `完全访问`; show that status beside the task title, retain the existing permission selector and coding-confirmation gate, and document that the current tools are local function calls rather than an implemented MCP Server.

Verification: reviewed message rendering, task serialization, workflow-stage transitions, tool selection, and current MCP planning documentation.

### Code

- Restored the `你` role label for every persisted user message while preserving the existing `Agent` label and Markdown rendering for assistant messages.
- Added a server-calculated `write_enabled` task property and a visible task status that distinguishes writable execution stages from read-only or pre-confirmation stages. The local `write_file` tool remains unavailable until both the execution-stage and permission checks pass.

Verification: `git diff --check` passed; `npm --prefix frontend run build` passed.

### Documentation

- Added README documentation describing the exact file-write preconditions and clarifying that the current implementation uses controlled local function tools, not an MCP Server connection.

Verification: reviewed the README statement against `tools_for_task`, `write_enabled`, and the registered local tool definitions in `backend/app/main.py`.

### Code Review

- Confirmed the frontend write-status indicator is informational only; the backend continues to decide whether `write_file` is sent to the model from the current stage and persisted permission.
- Confirmed user-message labels apply equally to newly submitted and reloaded history entries, and that write access is not exposed during analysis, design, confirmation, acceptance, or completed stages.

Verification: static review of `frontend/src/main.tsx`, `backend/app/main.py`, `README.md`, and focused tests; `git diff --check` passed.

### Unit Testing

- Updated the frontend rendering contract for user labels and extended orchestration coverage to assert that `write_file` appears only once a writable task enters the implementation stage.

Verification: `.\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` passed with 23 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.

## 2026-07-21

### Plan

- Requirements analysis, high-level design, and detailed design: introduce local `stdio` MCP Server connectivity as an application capability while retaining task-scoped authorization and existing workflow permission gates.

Verification: inspected the existing tool dispatch, task configuration, model function-call routes, and Python MCP SDK client interfaces.

### Code

- Added local MCP Server discovery and invocation, persisted server profiles, task-level tool grants, model function-schema adaptation, and server-side read/write/full-access enforcement.

Verification: a test MCP stdio process completed discovery and `tools/call` through the application; `git diff --check` passed.

### Documentation

- Updated README with local MCP setup, task authorization, permission behavior, local-environment secret handling, and the first-version `stdio` transport boundary.

Verification: documentation checked against the implemented API and access-policy paths.

### Code Review

- Confirmed MCP configuration cannot bypass task authorization, workflow stage checks, or task permission; commands and argument arrays are not shell-concatenated.

Verification: static review of backend routing, UI configuration, and MCP-focused tests.

### Unit Testing

- Added real stdio MCP coverage for discovery, call routing, duplicate and unavailable server handling, access classes, and UI configuration contracts.

Verification: `.\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` passed with 26 tests and 1 existing Starlette deprecation warning; `npm --prefix frontend run build` passed.
