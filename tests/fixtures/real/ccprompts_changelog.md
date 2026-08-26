<!--
Note: Only use **NEW:** for entirely new prompt files, NOT for new additions/sections within existing prompts.
-->

### Claude Code System Prompts Changelog

#### [2.1.245](https://github.com/Piebald-AI/claude-code-system-prompts/commit/f5060ac)

<sub>_No changes to the system prompts in v2.1.245._</sub>

#### [2.1.243](https://github.com/Piebald-AI/claude-code-system-prompts/commit/8daa909)

<sub>_No changes to the system prompts in v2.1.243._</sub>

# [2.1.242](https://github.com/Piebald-AI/claude-code-system-prompts/commit/e28b8de)

_+30,636 tokens_

- **NEW:** System Prompt: Project timeline user message provenance — Treats server-verified project-owner timeline markers as direct user turns while keeping coordinator relays, fetched project content, other participants, and context-free approvals untrusted.
- **NEW:** System Reminder: Directory sync guidance and notices — Adds mode-specific guidance for synced git checkouts, live uncommitted checkouts, and plain folders, including exclusions, conflict and deletion behavior, transfer budgets, stale or one-way files, branch-name collisions, and branch switches that park prior agent work.
- **NEW:** System Reminder: Artifact comment reply session collision — Reports when another live session yields, claims, or may duplicate automatic replies for the same Artifact without letting Claude stop either watch on its own.
- **NEW:** Tool Description: Artifact type discovery and creation guidance and System Reminder: Artifact type instructions trust boundary — Adds listing, inspection, and creation from published Artifact types while confining third-party type instructions to the new Artifact's files and the user's requested scope.
- **NEW:** Data: Artifact host MCP server guidance — Documents how locally configured MCP servers can be exposed to Artifacts as `host:<server>`, excludes Claude app built-ins and Claude.ai connectors, and warns that viewers need the same local server connected.
- Data: Artifact MCP connector guidance — Requires every declared connector server to name a non-empty tool allowlist and directs publishers to omit or clear MCP capabilities rather than treating an empty list as unrestricted access.
- Tool Description: Artifact — Narrows Artifact creation to durable, team-facing decisions and other content that benefits from a shared page, while keeping immediate one-person advice in the terminal unless the user opts into publishing it.
- Tool Description: Artifact database guidance — Adds multi-document batch writes with a single approval and atomic application where supported, preferring them over several individual writes.
- Tool Description: Artifact identical resubmission refusal, Artifact supporting files guidance, Artifact publishing and update guidance, and Tool Parameter: Artifact force overwrite guidance — Require a fresh Artifact fetch before retrying a stale publish, define additive, replacement, and explicit-null file removal semantics and per-publish limits, and clarify that forcing discards the newer page while preserving unmentioned supporting files.
- Tool Description: Artifact live room guidance and Artifact publishing and update guidance — Make room approval reusable for the same Artifact during a conversation, allow automatic rejoining until the user stops the room, and clarify client-specific watch restoration after resume or continue.
- Tool Description: Computer request_access and Skill: Computer Use MCP — Add macOS and Windows framing, require Finder access for desktop, Dock, and Finder interactions, explain Windows UIPI restrictions on elevated processes, and distinguish application grants from automatically requested screen-takeover consent.
- Tool Description: Computer computer_batch — Reworks coordinate guidance so every click and zoom in a batch uses the pre-call full-screen screenshot, permits screenshot and zoom actions with interleaved image results, and makes the latest returned full screenshot the reference for the next call.
- **REMOVED:** Tool Description: device_bash and Tool Description: device_bash (opening) — Remove the standalone descriptions for sandboxed shell execution on the user's local device.
- Agent Prompt: Agent Hook — Explains that remotely served hook calls for another machine's session have no local conversation transcript to read.
- Agent Prompt: Security monitor for autonomous agent actions — Adds authoritative Claude-in-Chrome navigation provenance, evaluates actions against the landed URL and ordered browsing path, and treats sensitive actions on unexpected origins as suspect.
- Skill: Dynamic pacing loop execution, Skill: `/loop` self-pacing mode, autonomous loop tick prompts, and Tool Description: ScheduleWakeup delay and reason guidance — Require each continuing tick to report `noop: true` when nothing changed or `false` when work advanced, collapse consecutive no-op ticks in the terminal, and make ScheduleWakeup's no-op reporting unconditional alongside its cache-TTL-aware delay guidance.
- System Prompt: Coordinator mode orchestration — Makes delegated workers inherit the session model, allowing an explicit model only when the user requested one and forbidding autonomous downshifts for substantive work.
- System Reminder: Browser read-only access guidance — Updates the deferred Claude-in-Chrome tool prefix from `mcp__Claude_in_Chrome__*` to `mcp__claude-in-chrome__*`.
- **NEW:** Data: Query result pending command count, Rate limit unified windows, and Upload device hook template request — Document queued user sends awaiting turns, per-window subscription utilization and reset snapshots, and vetted hook-template uploads that precede device-hook registration.
- Agent Prompt: Status line setup — Clarifies that subscription rate-limit windows appear only while the API reports them and before their reset timestamps pass.
- Data: Interrupt cancel queued parameter, Interrupt receipt still queued field, SDK cloud session init snapshot field, and SDK protocol capabilities field — Expand cancellation behavior for client-held and already delivered sends, document reattach frame ordering and unapplied host options, and replace the legacy CCR label with cloud-session terminology.
- Data: Plugin eval and skill-doctor quick reference and reference — Add layered MCP server mocks, canned, fixture, guarded, error, and agent-driven responses, plus `mock_calls` grading, output, and mock-result diagnostics.
- Data: Claude API reference — Python, Skill: Building LLM-powered applications with Claude, and Skill: Model migration guide — Update the current Sonnet example pricing from $3/$15 to $2/$10 per million input/output tokens.
- Data: Platform availability, Skill: Building LLM-powered applications with Claude, and Skill: Model migration guide — Add beta server-side fallback availability on Claude Platform on AWS, distinguish the default and array-form beta headers, and remove first-party-API-only fallback guidance.
- Skill: Building LLM-powered applications with Claude and Skill: Model migration guide — Replace separate tool-preamble and thinking-tag advice with a combined generic instruction, recommend starting effort at `high` and measuring before using `xhigh` or `max`, and add an immediate-visible-answer instruction for latency-sensitive chat and voice routes.
- Skill: Design — Adds live-canvas editability refusal guidance, records interactive-artboard and editor text-style metadata, raises the default canvas height, and updates fixed-versus-flow PDF export and pagination behavior.
- Data, skills, agent and system prompts, reminders, and tool descriptions — Broadly normalize rendered Unicode typography and notation to ASCII or textual equivalents, including dashes, arrows, ellipses, comparison signs, status and beta symbols, keyboard glyphs, and box-drawing diagrams.

# [2.1.241](https://github.com/Piebald-AI/claude-code-system-prompts/commit/0260612)

_+182 tokens_

- **NEW:** Data: SDK set max thinking tokens request schema — Documents the `set_max_thinking_tokens` control request, including resetting an omitted or null token budget to the session default and optionally setting or clearing the session-scoped thinking display mode.

# [2.1.240](https://github.com/Piebald-AI/claude-code-system-prompts/commit/18f32d7)

_-1,911 tokens_

- **NEW:** Tool Description: Agent (simple usage notes) — Adds concise Agent-tool guidance covering when to delegate, fork behavior, resuming agents, worktree isolation, background execution, parallel launches, and context restrictions.
- Agent Prompt: Security monitor for autonomous agent actions (second part) — Expands Self-Modification protection to an explicit set of agent configuration paths, while treating `.claude/worktrees/<name>/` and unlisted project-specific `.claude/` directories as ordinary project files unless they contain protected configuration.
- Agent Prompt: Worker fork — Clarifies that “default to forking” guidance belongs to the parent agent and that the fork must execute its directive directly without spawning another agent.
- Tool Description: Snooze (delay and reason guidance) — Prohibits short-interval polling for harness-tracked background work, recommends a 1200-second-or-longer fallback heartbeat, and reserves short cache-preserving delays for external state such as CI runs, deployments, and remote queues.
- Tool Description: Write (read existing file first) — Clarifies that Write is for creating files or fully replacing previously read files, while partial modifications should use Edit.

# [2.1.239](https://github.com/Piebald-AI/claude-code-system-prompts/commit/50c563b)

_+960 tokens_

- **NEW:** Data: SDK MCP server errors field — Documents skipped `--mcp-config` entries on SDK init frames, including stable error categories, omitted affected servers, Remote Control bridge failures, and CI handling.
- **NEW:** Data: SendMessage ambiguous recipient display — Restores user-facing “not sent” explanations for inexact or duplicate agent names, unavailable or truncated session searches, and recipients that require exact-name or pinned-identity confirmation.
- **NEW:** Tool Description: Artifact content host network block guidance — Explains the Artifact content-host allowlist entry for personal and shared cloud environments and forbids republishing until Claude can read the live version.
- **NEW:** Tool Description: Artifact files guidance — Adds `list_files` and `read_file` actions for multi-file Artifacts, defaulting reads to the scratchpad while reserving `out_dir` for user-requested destinations.
- **NEW:** Tool Description: Artifact identical resubmission refusal, Tool Parameter: Artifact URL guidance, and Tool Parameter: Artifact force overwrite guidance — Require existing owned Artifact URLs for in-place updates, reject unchanged retries after stale-version conflicts, and reserve forced overwrites for explicit approval to discard a specific newer version.
- **REMOVED:** Skill: Artifact slides and Skill: Artifact spreadsheet — Remove the standalone editable slide-deck and spreadsheet Artifact skills and their template-preservation workflows.
- Agent Prompt: Security monitor for autonomous agent actions — Adds an unverifiable-deletion-scope soft block for runtime-computed destructive writes against shared or remote state, requiring a transcript-visible resolved target list or literal verified names within the user-approved scope.
- Skill: `/insights` report output — Reworks the restored `/insights` flow into an exact unwrapped shareable-report handoff, replaces the additional-context injection with report-header context, and forbids adding, omitting, or rewording any line.
- Tool Description: Artifact and Skill: Artifact design — Make HTML the default Artifact format, allowing Markdown only when a loaded skill explicitly requires it and converting shared Markdown documents into designed HTML pages rather than one-to-one transcriptions.
- Tool Description: Artifact publishing and update guidance; Skill: Artifact PR review, Skill: Design, and Skill: Whiteboard — Move Artifact reads from WebFetch to `action: "read"`, returning owned pages as raw HTML and shared pages as isolated summaries—except same-session Slack-channel pages, which return full untrusted content—while updating dependent workflows to consume saved large-page results safely.
- Tool Description: Artifact publishing and update guidance, Tool Description: Artifact runtime capabilities guidance, and Tool Parameter: Artifact watch actions guidance — Distinguish unavailable, durable-wake, and live watch modes; expand capability examples to live or connected data, shared state, viewer identity, Claude questions, added files, and self-saving pages; and require re-read, merge, and republish handling when local source falls behind a page-authored version.
- Tool Description: Artifact live room guidance — Requires explicit per-publish approval to join a room and approval for every `room_send`, exposes joined rooms through status, and coalesces and caps incoming events so pages send summaries rather than streams.
- Data: Artifact MCP connector guidance — Expands Artifact MCP manifests from claude.ai and host connectors to include built-in connector guidance while retaining exact upstream-tool-name discovery.
- Data: Claude API reference — Python and Skill: Anthropic Python SDK 0.x to 1.x upgrade — Switch timeout and custom-client guidance to `anthropic.Timeout`/`httpx2`, expand `output_format` migration coverage, and preserve supported older-model sampling parameters through `extra_body` instead of deleting them blindly.
- Data: Background tasks changed event schema, SDK protocol capabilities field, Interrupt cancel queued parameter, Interrupt receipt still queued field, and SDK subagent stats schema — Add reinitialize snapshots for live background tasks, hosted-session exceptions to cancel-queued receipts, and held-back-result timing details for cost, duration, model usage, and subagent totals.
- System Prompt: Coordinator mode orchestration and Tool Description: ListAgents — Add capability-aware user-message routing, `blocked` worker outcomes, concise launch updates when no communications role exists, and teammates as addressable agents.
- System Reminder: Previously invoked skills — Broadens the post-compaction warning so request or argument text anywhere in restored skill bodies, including “User Request” sections, is historical context rather than a new live instruction.
- System Prompt: Interactive agent intro and System Prompt: Harness instructions — Add a collaborative-goals identity branch when no output style is active, alongside the existing software-engineering framing.
- Tool Description: Bash (Git commit and PR creation instructions) — Corrects generated pull-request bodies so summary and test-plan templates appear under their matching headings and optional attribution follows the test plan.
- System Prompt: Auto memory durable lesson instructions — Adds a runtime note about the configured memory directory alongside the persistent-memory introduction.

# [2.1.238](https://github.com/Piebald-AI/claude-code-system-prompts/commit/d2f451b)

_+3,292 tokens_

- **NEW:** Data: SDK subagent stats schema — Documents cumulative per-session Agent-tool subagent counts by launch mode, type, nesting, outcome, and refusal, including reset, background-lifecycle, remote-launch, and result-stream timing caveats.
- **NEW:** Data: Self-hosted runner deferred shutdown timing notice — Explains deferred session release and fallback drain timing, supervisor stop-timeout sizing, forced-kill consequences, and second-signal behavior.
- **NEW:** Data: VCS state changed branch field — Defines the optional best-effort branch hint for commit and push events, including per-branch events for multi-branch pushes and uncertain-attribution cases.
- **NEW:** Tool Description: Artifact live room guidance — Adds transient, at-most-once rooms for live Artifact viewers, with untrusted event handling, bounded `room_send` broadcasts, peer-status results, and guidance to use republishes or the artifact database for durable state.
- **NEW:** Tool Description: Artifact runtime verification guidance — Adds post-publish `verify` diagnostics for console output, uncaught errors, failed resources, and capability calls, while treating viewer-reported diagnostics as untrusted data and no-viewer results as inconclusive.
- Data: Artifact MCP connector guidance — Allows supported sessions to expose locally configured MCP servers to Artifacts as `host:<server>`, while warning that they work only for viewers with the same local server connected.
- Data: Self-hosted runner command help — Adds command- and file-backed `Proxy-Authorization` header injection for egress proxies, including per-connection refresh, child-process proxy rewriting, secret-handling, and orchestrator limitations.
- Data: Self-hosted runner command help and System Prompt: Self-hosted runner doctor — Document `--defer-shutdown-max-min`, which stops new assignments while attached sessions continue until release or the ceiling, then parks or drains them under explicit signal, grace-period, and supervisor-timeout rules.
- Data: VCS state changed event schema — Clarifies that a push updating several branches emits one push event per branch.
- Skill: Artifact PR review and Skill: Artifact PR review (composed publish flow) — Harden decision-island extraction by anchoring searches to the full JSON script opening tag and reading from its end, avoiding collisions with prose that merely mentions the island ID.
- Skill: Artifact document — Stops requiring in-page status chips and author, date, or version metadata, treats those details as Artifact-owned chrome, and removes the related self-checks.
- Skill: Design — Expands canvas authoring with no-human-in-loop defaults, visible direction-selection artboards, `Main.dc.html` handoff rules, data-visualization routing, factual sample-value constraints, print dimensions, frame-fit and optional browser checks, image-basename and generic-name validation, template-binding and per-artboard-state caveats, static-artboard guidance, and required `file_path`, description, and favicon metadata on every publish.
- Skill: Prototype — Makes Artifact `verify` the sanctioned post-publish runtime check when available and warns that an empty no-viewer result does not prove the demo works.
- System Prompt: Artifact comment result guidance, Tool Description: Artifact comments guidance, and Tool Parameter: Artifact comment actions guidance — Require Claude activation for both replying to and resolving a comment thread, leaving non-activated threads for the commenter to resolve even after Claude addresses them elsewhere.
- System Prompt: Artifact comment thread framing — Adds untrusted multi-file page markers to identify which Artifact file a thread anchors to, and explicitly avoids assuming the main page when attribution is degraded.
- System Prompt: Coordinator mode orchestration — Adds capability-aware Skill-tool guidance alongside the existing workflow-tool notes.
- System Reminder: Artifact auto-replies resumed, Tool Parameter: Artifact watch actions guidance, and Tool Description: Artifact publishing and update guidance — Distinguish interruption pauses that keep the watch from killed or unwatched stops, answer comments sent during a pause after resuming, and recognize an “already registered” result as evidence of a remote watch.
- System Reminder: Output style active — Escapes the displayed active-style value as untrusted text and reads the turn reminder from the active style configuration.
- Tool Description: Artifact publishing and update guidance and Tool Description: Artifact runtime capabilities guidance — Document per-artifact browser storage for lightweight per-viewer conveniences, require failure-safe access, and prefer shared runtime capabilities for state that must be durable, cross-viewer, or readable by Claude.

# [2.1.237](https://github.com/Piebald-AI/claude-code-system-prompts/commit/9c96204)

_+1,249 tokens_

- **NEW:** System Prompt: Auto mode Slack message provenance — Treats bound-thread Slack relays that open with the server-verified human marker as direct user intent capable of clearing soft blocks, while bot-attributed or nested cross-session relays remain untrusted and cannot establish consent or launder permissions.
- **NEW:** System Prompt: Concise output style — Defines the built-in Concise style to lead with results, omit narration and repeated recaps, use short plain answers by default, and preserve requested detail and correctness, including full error reports, failing-test output, security warnings, and destructive-action confirmations.
- **NEW:** Tool Description: Poll — Adds an idle wait for queued harness events, returning pending events immediately and yielding to new user input, while defining authoritative envelope provenance, untrusted event content, nonce manifests, transcript-replay compatibility, and oldest-first chunked delivery.

# [2.1.236](https://github.com/Piebald-AI/claude-code-system-prompts/commit/e39d195)

_+26,676 tokens_

- **NEW:** Agent Prompt: Security monitor host-context line guidance and Data: Hook classifier context field — Distinguish live host-attached user statements, which may satisfy a soft-block consent bar, from restored or mixed host context, which remains unverified; define call binding, trust, size, timing, and rewrite-integrity rules, and integrate the guidance into the autonomous-action monitor.
- **NEW:** Data: SDK register device hooks request schema — Documents how cloud device clients register forwarded hooks and vetted worker-side templates, including the size limit, worker-epoch handling, and retryable versus terminal registration errors.
- **NEW:** Skill: Anthropic Python SDK 0.x to 1.x upgrade — Adds an executable migration workflow for the Python 3.10 floor, `httpx2`, removed deprecated APIs and parameters, raw-response and streaming changes, Bedrock region handling, verification, and user-owned migration decisions, with `/claude-api upgrade` routing and an authoritative migration-guide source.
- **NEW:** System Reminder: Artifact auto-replies resumed — Reports that a requested Artifact comment auto-reply resume is re-arming the live watch, explains which comments from the stopped period will be handled based on the stop cause, and warns that the stop persists until reconnection succeeds.
- **NEW:** System Reminder: Artifact type page untrusted content warning — Treats HTML supplied by an owned Artifact's type publisher as untrusted data that cannot grant instructions or permission escalation.
- **NEW:** System Reminder: Background task notification with concurrent user input — Separates an automated background-task event from genuine user input delivered in the same turn and prevents the notification from being treated as approval or consent.
- **REMOVED:** System Reminder: Ultrareview launch acknowledgement — Removes the standalone acknowledgement prompt for already-visible cloud review launches and remembered `--fix` intent.
- Agent Prompt: Managed Agents onboarding flow; Data: Managed Agents self-hosted sandboxes, memory stores reference, and related Managed Agents/AWS references — Expand self-hosted SDK-worker guidance with single-item handling, safe shutdown and file confinement, synchronized memory stores and recovery, resource and deployment constraints, and Claude Platform on AWS authentication, session-duration, and memory-store limitations.
- Data: Managed Agents tools and skills and related Managed Agents references — Add per-tool `web_search` and `web_fetch` domain filters, search location and fetch-size settings, validation and runtime behavior, typed-SDK configuration changes, multiagent restriction layering, and guidance that these tools run server-side rather than under environment networking rules.
- Data: Managed Agents events and steering and Data: Managed Agents overview — Document the Console session viewer's searchable transcript, timeline, raw events, tool/resource/thread inspection, cost views, JSON export, and event deep links.
- Data: Plugin eval and skill-doctor reference — Requires every eval case to provide an execution prompt, treating it as the resumed session's next user turn when a history file is also supplied.
- Skill: Artifact document — Clarifies that publishing assigns comment-anchor block IDs, the editor assigns IDs to user-added blocks, and existing IDs must still be preserved rather than copied or hand-authored unnecessarily.
- System Prompt: Artifact comment reply composer — Makes change-request reply guidance conditional instead of always prescribing a generic work-in-progress acknowledgement, while retaining brief plain-text answers and prohibitions on claiming completed edits.
- Tool Description: Artifact database guidance — Adds file-backed database reads via `out_dir` and writes via a local JSON `file_path` so large or numerous documents need not be returned or retyped inline.
- Tool Description: Artifact publishing and update guidance and Tool Parameter: Artifact watch actions guidance — Add terminal and web paths for reopening artifacts, distinguish watch arming from an established connection, limit comment wakes to armed auto-replies, restore at most one watch after resume or continue, and tighten status-based claims about active watches.
- Tool Description: Edit, Tool Description: Edit single replacement, and Tool Description: Write — Make read-before-edit and read-before-overwrite guidance path-sensitive, explicitly retaining the prior-Read requirement for files outside the working directory.
- Tool Description: SendMessage cross-session guidance — Adds one-shot `notify_when_idle` subscriptions for local sessions, including pure subscriptions, approval-held notices, expiry behavior, and guidance to use them instead of polling or status-chasing messages.

# [2.1.235](https://github.com/Piebald-AI/claude-code-system-prompts/commit/e9b6b49)

_+6,990 tokens_

- **NEW:** Data: SendMessage ambiguous recipient display — Adds user-facing “not sent” explanations for inexact or duplicate agent names, incomplete session searches, and recipients that need exact-name or pinned-identity confirmation.
- **NEW:** System Prompt: Artifact comment fast acknowledgement selection — Selects one canned pre-reply acknowledgement from editability, trigger history, and whether the newest comment asks for a specific edit, broader inspection, or a thread-only answer, with a safe ambiguity and off-topic fallback.
- **NEW:** System Prompt: Non-fork subagent delegation examples — Consolidates synchronous and background delegation examples into capability-aware guidance that suppresses default-agent examples when general-purpose agents are unavailable.
- **NEW:** System Reminder: Ultrareview launch acknowledgement — Briefly acknowledges an already-visible cloud review without repeating its details, preserves `--fix` intent, and relates later findings to review notes the cloud pass cannot see.
- **REMOVED:** System Prompt: Background subagent delegation examples and System Prompt: Foreground subagent delegation examples — Remove the separate execution-mode variants superseded by the capability-aware non-fork delegation prompt.
- Data: Plugin eval and skill-doctor quick reference, Data: Plugin eval and skill-doctor reference, and Skill: Plugin eval authoring interview — Promote configurable `--eval-dir` suites from upcoming to current behavior, document containment-checked plugin discovery and result placement, teach the interview to honor custom suite paths and treat plugin paths as data, and distinguish tool-free negative checks from file-content graders that require an existing file.
- Skill: Artifact document — Adds block-ID rules that keep viewer comments anchored, preserve editor-generated IDs across edits, strip IDs from duplicated blocks, and reserve hand-written IDs for short in-page link targets.
- System Prompt: Forked agent guidance and Tool Description: Agent (when to launch subagents) — Make omitted `subagent_type` behavior conditional on general-purpose-agent availability, add plan-specific subagent restrictions, and provide fallback instructions when the general-purpose type is unavailable.
- Tool Description: Artifact database guidance — Updates current-viewer identity lookup from `claude.user.id()` to `id()` on the page’s `user` capability.

# [2.1.234](https://github.com/Piebald-AI/claude-code-system-prompts/commit/373b98c)

_+11,405 tokens_

- **NEW:** Data: SDK cloud session init snapshot field, Data: SDK control cancel request schema, Data: SDK request user dialog kind field, and Data: SDK result message schema — Document cloud-session attachment and directory-sync state, cancellation of in-flight control requests, capability-negotiated user-dialog kinds, and the turn-complete result contract.
- **NEW:** Data: syncClaudeAiSkills setting — Documents how `false` disables account-synced skills across user, managed, workspace, and invocation scopes, including hiding, trash cleanup, re-enablement, and unsupported project-setting behavior.
- **NEW:** Data: VCS state changed event schema — Restores the best-effort repository cache-invalidation event with working-directory hints, stricter push-branch attribution, and caveats for silenced or backgrounded mutations.
- **NEW:** Skill: Claude guide unavailable reference fallback — Adds an embedded live-source index and WebFetch fallback when the Claude guide's on-disk reference files cannot be written.
- **NEW:** Tool Description: Artifact assets guidance — Adds upload, list, read, reference, and permanent-delete guidance for files in artifacts that declare the `assets` capability.
- **NEW:** Tool Parameter: Artifact watch actions guidance — Adds action-level guidance for watch, unwatch, status, durable remote wakes, comment wakes, and explicitly authorized `resume_replies` behavior.
- **REMOVED:** Skill: Build with Claude API (reference guide) — Removes the redundant standalone quick-task navigation prompt; the primary Claude API skill retains its integrated reading guide.
- **REMOVED:** System Reminder: File modification detected (budget exceeded) and System Reminder: File modified by user or linter — Remove the standalone reminders for externally modified files and omitted modification snippets.
- **REMOVED:** System Reminder: MCP resource no displayable content — Consolidates the no-displayable-content case into the generalized MCP resource status reminder.
- Agent Prompt: Coding session title generator and Agent Prompt: Session title and branch generation — Shift titles to specific, two-to-five-word noun phrases that omit generic task verbs and abstract action labels, retain recognizable identifiers, and follow the user's language while keeping branch names in English.
- Agent Prompt: Status line setup — Adds GitLab merge-request metadata and labeling alongside GitHub pull-request status-line support.
- Data: Artifact decision component script, Data: Workshop artifact HTML template, and Skill: Artifact components — Harden verifier-pinned decision scripts against ambiguous script tags, quoted-attribute boundaries, escaped script states, and non-ASCII whitespace while refreshing the blessed digest.
- Data: Plugin eval and skill-doctor quick reference and Data: Plugin eval and skill-doctor reference — Promote image judging to current behavior, document binary-grading remedies, strengthen sandbox isolation from host repositories and project settings, and add plugin-load advisories, authentication-partial results, richer quiet-JSON diagnostics, and SIGTERM handling.
- Skill: Plugin eval authoring interview — Makes tools and execution budgets follow each grader's actual side effects, rejects pilots whose plugin failed to load or whose graders cannot pass with granted tools, and adds image and binary-artifact grading guidance.
- Skill: Building LLM-powered applications with Claude — Clarifies that cited language and shared reference paths are external skill files that must be read on demand before relying on them.
- Skill: Artifact design, Skill: Design, Skill: Prototype, and Tool Description: Artifact publishing and update guidance — Allow Google Fonts stylesheets and font files as the sole external-host exception, require fallback stacks, and note export fallbacks where applicable.
- Skill: Artifact document, Skill: Artifact slides, and Skill: Artifact spreadsheet — Switch collaborative editing to explicit whole-artifact saves under the `artifact` capability, distinguish write-access and view-only behavior, preserve reader changes through conflict-safe rereads, remove obsolete server-owned `data-id` restrictions, support structural slide edits, and make spreadsheet scratch cells persist when saved.
- Skill: Artifact PR review and Skill: Artifact PR review (composed publish flow) — Make decision controls the default unless a display-only page was requested, and clarify that connector-backed live PR data—not artifact saving alone—is what restricts external sharing.
- Skill: Design — Pins every canvas publish to runtime contract `0.1.31`, asks whether app concepts should be static or clickable, leaves publish approval to the tool and handles refusals without unsafe retries, preserves existing capability declarations on updates, clarifies that export—not saving—limits sharing to the organization, and favors a concise handoff followed by a content-focused second pass.
- System Prompt: Artifact comment list framing and System Prompt: Artifact comment thread framing — Add trusted attribution for comments posted through an artifact's own interface, treating sent comments as the account holder's request while asking when they conflict with directly typed feedback.
- System Prompt: Coordinator mode orchestration — Adapts peer-tool and post-launch instructions to available capabilities and identifies worker results inside harness-generated system reminders without reproducing their wrapper or XML.
- System Reminder: Compact file reference, System Reminder: File opened in IDE, System Reminder: File truncated, System Reminder: Large PDF read guidance, and System Reminder: Lines selected in IDE — Escape untrusted filenames before interpolation and soften the instruction about mentioning file truncation.
- System Reminder: MCP resource no content — Escapes server and URI attributes and generalizes the rendered resource status to cover both no-content and no-displayable-content cases.
- Tool Description: Artifact comments guidance and Tool Description: Artifact publishing and update guidance — Detect when remote artifact watching is unavailable, avoid claiming an active watch, and direct users to `claude --watch-artifact <url>` on their own machine.

# [2.1.233](https://github.com/Piebald-AI/claude-code-system-prompts/commit/2f5e820)

_+27,728 tokens_

- **NEW:** Data: Plugin eval and skill-doctor quick reference and Data: Plugin eval and skill-doctor reference — Add condensed and comprehensive offline guidance for early-access `claude plugin eval`, `eval init`, and `/skill-doctor`, covering enablement, suite authoring, graders, run options, result and report formats, sandboxing, CI, and troubleshooting.
- **NEW:** System Prompt: Plugin eval enabled-session status — Announces when plugin eval is enabled and gives the exact `CLAUDE_CODE_WALNUT_SPIRE=1` fallback for clients and CI that cannot receive the organization rollout, with supported shell, user-settings, and managed-settings locations and a warning not to rely on project settings.
- Agent Prompt: Claude Code guide, Agent Prompt: Claude guide agent, Data: Claude Code live documentation sources, Data: Claude Code recent changes reference, and Skill: Claude Code configuration guide — Route plugin-evaluation and skill-diagnostics questions through current-build checks and the new offline references, distinguish `/skill-doctor` from linting, and warn against stale-memory answers, guessed documentation URLs, or invented enablement variables.
- Data: Artifact decision component script, Data: Workshop artifact HTML template, and Skill: Artifact components — Update decision controls to acquire artifact publishing through the asynchronous viewer 0.2 `claude.use('artifact')` API, retain viewer 0.1 `artifact`/`self` compatibility, arm only after capability availability, and refresh the verifier-pinned digest.
- Data: Claude Code gateway protocol — Relays Anthropic 400/413 error messages needed for client recovery such as auto-compaction while continuing to sanitize other upstream messages and preserve error types.
- Skill: Artifact document — No longer presents document artifacts as supporting selection-based comments or requires hidden comment-store machinery, while preserving live editing and reframing review language around feedback.
- Skill: /doctor slash command and Skill: /doctor slash command description — Add diagnosis of malformed skill YAML frontmatter, explaining that parse failures drop every field and trigger fallback naming and descriptions while silently disabling tool, model, and invocation settings.
- Skill: Plugin eval authoring interview — Keeps every calibration pilot and re-pilot private with `--no-publish` and tells users how to keep final full-suite reports local.
- System Reminder: Team Coordination and Tool Description: SendMessage — Make task-list resources and coordination instructions conditional on available task tooling and allow legacy status updates in plain prose when those tools are absent.
- Tool Description: WebFetch, Tool Description: WebFetch (concise), and Tool Description: WebFetch private URL warning — Derive cache-expiry text at render time instead of hard-coding 15 minutes.

# [2.1.232](https://github.com/Piebald-AI/claude-code-system-prompts/commit/a21a614)

_+48,736 tokens_

- **NEW:** Agent Prompt: Web fetch agent usage guidance and Agent Prompt: Web reading specialist — Add a dedicated WebFetch delegation flow that returns focused, source-grounded reports from untrusted pages, supports follow-up questions about already-read content, and confines binary-file handling to harness-reported tool-results paths.
- **NEW:** Skill: Artifact components and Data: Artifact decision component assets — Add reusable, verifier-pinned decision blocks for non-workshop HTML artifacts, including canonical design tokens, styles, markup, and scripts for persisted selections and readback, plus composition and injection-safety constraints.
- **NEW:** System Prompt: Artifact comment fast acknowledgement — Adds a no-tools, single-sentence acknowledgement under 160 characters before the full comment response, distinguishing change requests from questions while preserving plain-text and internal-handling restrictions.
- **NEW:** System Reminder: Bound conversation activity authority warning — Treats bound-conversation edits and reactions as awareness-only, never as fresh instructions, approval, consent, or a way around a denial, while still allowing relevant activity to inform work in progress.
- **NEW:** Tool Description: Background monitor push notification guidance — Directs background monitors to push only events that materially change what the user should do next, such as a new error or a status transition they were awaiting.
- **REMOVED:** Agent Prompt: /code-review workflow routing and System Prompt: Code review artifact publishing instructions — Remove the standalone prompts for routing `/code-review` through a background workflow and publishing its findings as a shareable Artifact.
- **REMOVED:** Agent Prompt: WebFetch summarizer — Removes the inline page-content summarizer superseded by the dedicated web-reading agent flow.
- **REMOVED:** Data: VCS state changed event schema — Removes the standalone schema for best-effort repository-state cache-invalidation events emitted after detected foreground VCS mutations.
- Agent Prompt: Security monitor for autonomous agent actions (second part) — Extends real-browser protections to Chrome tools reached through the remote-device bridge and hard-blocks attacks on recognizable third-party systems outside the task's trust boundary unless an exercise or authorized engagement designates the target.
- Agent Prompt: Worker fork — Updates the fork agent's availability description from the “fork experiment” to the “fork gate.”
- Data: Managed Agents multiagent sessions — Removes the temporary exclusion of Fable advisors so valid pairings mirror the Messages advisor-tool pairing table.
- Data: Workshop artifact HTML template; Skill: Artifact PR review, Skill: Artifact PR review (composed publish flow), Skill: Design, and Skill: Whiteboard — Migrate self-update guidance and clients to the `artifact` capability spelling while retaining legacy `self` compatibility, and clarify that design canvases open ready to edit but cannot retain changes when artifact publishing is unavailable.
- Skill: Artifact design and Tool Description: Artifact — Require design calibration before writing both HTML and Markdown artifacts, treating format as a deliberate choice and forbidding Markdown as a speed shortcut while preserving the workshop-specific exceptions.
- Skill: Prototype — Adds logic-first prototypes with full-state walkthroughs for behavior questions, requires every prototype to state one design question, verifies source-derived shell bytes against trusted registry digests before reuse, and keeps structurally distinct exploratory variants in one artifact until a direction is chosen.
- System Prompt: Artifact comment edit composer — Aligns edit replies with the shared plain-text formatting and internal-handling nondisclosure restrictions used by comment replies and fast acknowledgements.
- System Reminder: Artifact comment reply activation failure and Tool Parameter: Artifact comment actions guidance — Clarify that comment-thread activation survives artifact republishes and renames, while deactivation or thread deletion can clear it.
- Tool Description: ListAgents and Tool Description: SendMessage cross-session guidance — Clarify that exact live names deliver across local, remote, and cloud sessions, that references are only for ambiguity or lookup failures, and that cloud sessions can receive messages but cannot yet reply to another session.
- Tool Description: SendFeedback drafting guidance — Tightens feedback privacy by replacing personal identifiers with roles, excluding customer channel IDs and excerpts, constraining file-path evidence, and describing suspected vulnerabilities without working exploits or extraction steps.

#### [2.1.231](https://github.com/Piebald-AI/claude-code-system-prompts/commit/fd3c642)

<sub>_No changes to the system prompts in v2.1.231._</sub>

# [2.1.229](https://github.com/Piebald-AI/claude-code-system-prompts/commit/37fb9dc)

_+24,422 tokens_

- **NEW:** Agent Prompt: Pull request creation and Agent Prompt: Quick git commit — Add focused workflows for opening one GitHub pull request from existing commits and creating one local commit, with preloaded repository context, platform-correct multiline formatting, attribution hooks, pre-commit checks, and explicit git-safety boundaries.
- **NEW:** Data: Command plugin source command field — Defines command-backed plugin sources as platform-shell commands that emit exactly one absolute plugin-directory path, finish populating it before exit, and are re-resolved for installs, updates, and once-per-session background checks before being copied into cache.
- **NEW:** Data: Sandbox network domain spelling warning — Explains canonical domain and bracketed-IPv6 spellings and the conservative allow/deny behavior applied to malformed sandbox network entries until they are corrected.
- **NEW:** Skill: Artifact slides — Adds live, editable presentation-deck artifacts with a slide rail, direct editing, speaker notes, comments, presentation and print modes, projection-oriented composition rules, and template-preservation requirements.
- **NEW:** Skill: Design and Skill: Design description — Add Claude Design canvas artifacts for editable multi-artboard UI, marketing, social, and print layouts, including source-grounded design-system matching, reusable components, canvas organization, explicit save/export capability handling, and safe updates to existing canvases.
- **NEW:** Skill: Prototype description — Adds a dedicated trigger for working proof-of-concept artifacts, including explicitly requested demonstrations of a new feature in place on an existing app.
- **NEW:** System Reminder: Queued notifications delivery and Tool Description: ReadNotifications — Add authoritative, oldest-first draining of queued GitHub activity, scheduled triggers, and cross-session messages; require prompt handling when notified, pagination until the queue is empty, sender-based trust decisions, and verification of surprising relayed content.
- **NEW:** Tool Description: Artifact unsupported supporting file error — Explains why an unsupported supporting-file media type prevents publication, distinguishes page-served assets from viewer downloads, and points file handoff to an available runtime capability instead of inert download links.
- **NEW:** Tool Description: PowerShell (git guidance) — Adds reusable PowerShell git guidance to prefer new commits, seek safer alternatives before destructive operations, and never bypass hooks or signing without an explicit user request.
- **REMOVED:** Skill: Artifact PR review description — Removes the standalone PR-review trigger description; the full Artifact PR review skills remain.
- **REMOVED:** Skill: Code walkthrough, Skill: PR explainer, and Skill: PR explainer artifact-template mode — Remove the dedicated interactive code-walkthrough and pull-request walkthrough artifact workflows.
- Agent Prompt: Quick PR creation — Requires `gh pr edit` to omit a pull-request number or URL so `gh` resolves and updates the current branch's pull request.
- Data: Claude Code gateway protocol — Requires gateways to emit their own `event: ping` during silent streaming gaps because SDK iterators drop upstream pings and Bedrock sends none, preventing long thinking pauses from tripping client or proxy idle timeouts.
- Data: Code change published event schema — Broadens the event framing from a session-associated pull or merge request to any code change sent for review, including other providers in internal builds, while retaining its repeatable, best-effort, verify-before-trust semantics.
- Data: SDK protocol capabilities field — Adds the `queued_notifications` capability so backends can detect whether the CLI accepts queued-notification stream messages and drains them through `ReadNotifications`.
- Data: Self-hosted runner command help — Marks `--base-dir` as required on Windows, where the runner has no default checkout directory.
- Skill: Artifact PR review, Skill: Artifact PR review (composed publish flow), and Skill: Artifact PR review description (composed publish flow) — Remove routing to the retired `pr-explainer` workflow while preserving the distinction between structured review briefings and narrative walkthroughs.
- Skill: Prototype and Skill: Prototype runtime capabilities guidance — Introduce sketch, clickable, and wired fidelity levels with a clickable default; support privacy-checked screenshot overlays or source-matched shells for explicitly requested in-app concepts; allow per-region promotion; classify live data, actions, and file saving as wired capabilities; and require an approved must-have/nice-to-have/cut brief before turning an accepted prototype into production code.
- System Prompt: Artifact comment list framing — Adds optional file/page anchor guidance alongside selected-text and anchor-path context while preserving the untrusted-viewer-data boundary.
- Tool Description: Artifact database guidance — Adds private per-viewer storage under `data/users/`, with `me` resolving to the current viewer's user ID and requiring the published artifact to declare both `user` and `db` capabilities.
- Tool Description: Artifact publishing and update guidance and Tool Description: Artifact runtime capabilities guidance — Make remote-session watches durable wake subscriptions for republishes and, where granted, comments; warn that viewer sandboxes block page-initiated downloads; and route files intended for viewers to save through an available runtime capability.
- Tool Description: Bash (Git commit and PR creation instructions) — Applies the full commit and pull-request safety workflow consistently instead of switching to abbreviated git guidance when the commit command is loaded, retaining explicit commit and push consent, targeted staging, new-commit recovery after hook failures, and limits on unrelated exploration.
- Tool Description: PowerShell — Replaces generic command notes with PowerShell-edition-specific syntax and detected developer-tool context, adds background-execution and sleep-avoidance guidance, and separates reusable git safety guidance from the main tool prompt.
- Tool Description: Workflow — Clarifies that concurrent agent capacity is calculated from available CPUs rather than raw CPU-core count.

# [2.1.228](https://github.com/Piebald-AI/claude-code-system-prompts/commit/b718060)

_+7,141 tokens_

- **NEW:** Data: Claude Code gateway customer-routed inference protocol — Defines offline validation of short-lived, audience-bound CRI JWTs; operator-credential upstream forwarding without credential relay; response-header and error-body hygiene; stable capability-rejection recovery tokens; policy-block responses; discovery metadata; and fixed Messages API endpoint behavior.
- **NEW:** Skill: Artifact document — Adds creation guidance for live, editable word-processor-style document artifacts with status and ownership metadata, block-level edits, inline comments, stable updates, and preservation of the template's editor machinery.
- **NEW:** Skill: Artifact spreadsheet — Adds creation guidance for live, editable spreadsheet artifacts with persistable rows, cell editing, formulas, sorting, comments, status metadata, and preservation of the template's editor machinery.
- **REMOVED:** Agent Prompt: Bash command prefix detection — Removes the standalone policy prompt that extracted allowlistable command prefixes and flagged suspected command injection.
- Data: Self-hosted runner command help — Documents a non-disableable grace hold that keeps a just-finished background task in flight until the follow-up turn reading its result starts, bounded by `SELF_HOSTED_RUNNER_BG_RESULT_GRACE_MS` and falling back to the default when zero or unusable.
- Skill: Artifact design, Skill: Prototype, Skill: Whiteboard, and Tool Description: Artifact — Tighten artifact naming around short, distinctive, product-style titles, keep explainers in descriptions rather than dash- or colon-appended title suffixes, and rename topical boards as `<topic> whiteboard` instead of `Whiteboard — <topic>`.
- System Prompt: Artifact comment list framing and System Prompt: Artifact comment thread framing — Add optional anchor-path guidance to comment lists and keep thread instructions synchronized with the runtime anchor-path marker while continuing to treat viewer-influenced paths and element snippets as untrusted data.
- Tool Description: ListAgents — Clarifies that Remote Control-connected account listings cover both sessions on other machines and cloud sessions, with each row labeled by kind.

# [2.1.227](https://github.com/Piebald-AI/claude-code-system-prompts/commit/1314a83)

_+6,757 tokens_

- **NEW:** Agent Prompt: Artifact comment thread analyst and System Prompt: Artifact comment thread triage — Add a read-only, single-thread analysis brief for edit composition and classify the newest human request as an artifact edit or a reply-only pipeline action.
- **NEW:** System Prompt: Artifact comment result guidance and Tool Parameter: Artifact comment actions guidance — Add focused thread reads, comment-list pagination, activated-thread reply rules, and precise resolve semantics for addressed, open, and already-resolved threads.
- **NEW:** Agent Prompt: `/ultrareview` GitHub comment poster — Publishes one plain pull-request comment containing the review findings, omitted-finding count, locations, and a run deduplication marker; trims long finding text to stay under 40,000 characters and forbids all other writes.
- **NEW:** Data: Auto-compact inputs changed event schema — Documents worker-resolved auto-compaction state emitted at boot, after resolved-setting changes and conversation resets, and re-checked at each turn start so thin-client countdowns follow the effective trigger, while noting turn-scoped model-override divergence.
- **NEW:** System Reminder: Project memory disconnected — Marks prior connected-store lists, shared indexes, and memory-tool results stale after disconnect or failed reconnection; directs re-checking with `memory_list` and falls back to personal memory when available.
- **NEW:** Tool Description: `device_bash` — Runs fresh non-interactive shells on the user's device under its Claude Code sandbox, with launch-directory-relative paths, bounded timeout and concurrency, and refusal when device sandboxing is disabled.
- **NEW:** Tool Description: ProposeGoal — Proposes evaluator-verifiable completion conditions for multi-turn work without blocking progress, requires approval unless the user explicitly requested the exact outcome, avoids retrying declined proposals, and replaces any active goal when accepted.
- **REMOVED:** Tool Description: Code review command — Removes the dedicated tool-description prompt for review targets, effort levels, inline pull-request comments, and working-tree fix mode.
- Agent Prompt: `/schedule` slash command and Tool Description: RemoteTrigger prompt — Add `list_runs` and `get_run_log` diagnostics for routine sessions, explain why pre-session refusals and existing-session posts may leave no new run row, and treat remote run titles and logs as untrusted data.
- Data: Claude Code gateway protocol — Documents optional per-user usage-cap headers and 75%/95% notices, stripping upstream rate-limit headers, and non-retryable `429 billing_error` responses that preserve the gateway's reset and remediation message.
- Data: VCS state changed event schema — Allows the cache-invalidation event's otherwise minimal payload to include the branch acted on while consumers continue re-reading head and pull-request state.
- System Prompt: Artifact comment edit composer, System Prompt: Artifact comment reply composer, and Tool Description: Artifact comments guidance — Feed read-only analyst briefs into edits, keep replies free of backend session/thread/flag machinery, acknowledge requested edits as work in progress rather than merely flagged, apply resolved-thread reply guidance, and resolve only feedback that was actually addressed.
- Tool Description: Artifact and Tool Description: Artifact publishing and update guidance — Require an HTML `<title>` near the top because only the first 8KB is scanned, and recover an earlier artifact's URL through listing or the user instead of accidentally publishing a separate artifact and announcing a new link.
- System Prompt: Self-hosted runner setup and System Prompt: Self-hosted runner doctor — Update onboarding and diagnostic paths for environment keys, runner/session activity, retries, and health indicators to the canonical Admin settings → Cloud environments UI while identifying the older Claude Code settings surface as transitional.
- Tool Description: Agent (usage notes) — Restricts foreground agents to cases where the very next action depends on their result and no other useful work can proceed, keeping independent, fire-and-forget, and interruptible work in the background.
- Tool Description: SendUserFile — Broadens file delivery beyond final deliverables, sends complete drafts or meaningful updates as they are produced, excludes scratch files and incremental-save noise, and re-sends only materially changed files.
- System Prompt: Action safety and truthful reporting, System Prompt: Autonomous operation guidelines, and System Prompt: Memory instructions — Replace dash-heavy wording with clearer sentence, parenthetical-example, and frontmatter-description punctuation while preserving the underlying safety, evidence, and memory instructions.
- System Prompt: Outcome-first communication style — Cleans up list and calibration punctuation and generalizes the warning against reviewer-directed comments from noise after a pull request merges to noise after any change merges.

#### [2.1.226](https://github.com/Piebald-AI/claude-code-system-prompts/commit/daeea64)

<sub>_No changes to the system prompts in v2.1.226._</sub>

# [2.1.225](https://github.com/Piebald-AI/claude-code-system-prompts/commit/4b82ebc)

_+1,314 tokens_

- **NEW:** Tool Description: Bash (pre-commit skill checks) — Requires a visible `RAN`/`NOT RUN` status for each applicable verification, simplification, and code-review skill immediately before nontrivial commits, runs checks that are not still valid for the current diff, and limits skips to explicit user instructions or enumerated trivial-only changes.
- Data: Workshop artifact HTML template — Shows the waiting painter when the opening version has no decisions and, after three minutes without a newer version, warns that Claude may no longer be watching and suggests reloading.
- System Prompt: Artifact comment reply composer — Answers questions and feedback directly, flags requested artifact changes for the owning session without discussing its own limitations, and avoids claiming or promising that edits will happen.
- Tool Description: Artifact — Treats finished audience-facing deliverables such as team reports, shared plans, and reference documents as incomplete until they are published as private artifacts and handed off with a link.
- Tool Description: ListAgents — Reframes Remote Control connectivity as listing the user's Remote Control sessions on other machines when connected here, replacing the previous reply-only remote-bridge guidance.
- Tool Description: RemoteTrigger prompt — Adds webhook-trigger creation for wiring a scoped, filtered event source to an existing routine and returns that routine's Claude.ai link without a scheduled run time.

# [2.1.224](https://github.com/Piebald-AI/claude-code-system-prompts/commit/1079f62)

_+32,958 tokens_

- **NEW:** Data: Cross-session inbound and dialog expiry settings — Document `accept`/`hold`/`refuse` handling for peer-session messages, permission-mode parity defaults, and a shared trusted-source timeout for remote dialogs and held messages that resolves to safe cancel/drop behavior.
- **NEW:** System Prompt: Coordinator cross-session peer guidance and Tool Description: SendMessage cross-session guidance — Add peer discovery and `name [ref]` addressing, reply routing from `<cross-session-message>` wrappers, remote-bridge reply-only constraints, and explicit protection against treating peers as workers, authority, or a way around permission decisions.
- **NEW:** Data: Sandbox credential environment no-match and file mask-claims settings — Add fail-open warning, fail-closed deny, and setup-error behavior when an environment extraction matches nothing, plus selective masking of named claims inside decoded file credentials while preserving non-secret claims.
- **NEW:** Data: Se