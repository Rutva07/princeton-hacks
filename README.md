# ResearchForge / CausalForge — Autonomous Collaborative AI Scientist

ResearchForge is a WebSocket-first research workspace where any collaborator can mention `@chatbot` to ask a question, search the literature, request an experiment, or rerun a previous result. A typed LangGraph researches, plans, generates code, validates it, runs it in an isolated Docker container, evaluates measured outputs, and publishes a reproducible report. Messages, sources, stages, logs, artifacts, and results stay synchronized across browsers in a persistent room.

The default local mode is fully functional without an LLM or messaging provider. Photon is an optional adapter and is disabled by default.

## What works

- Authenticated research rooms with working shareable invitation URLs, automatic join, invite codes, and server-verified membership
- Native FastAPI WebSockets at `/ws/rooms/{room_id}`
- Persistent messages and ordered room events in SQLite
- Three or more logical participants per room (limited only by infrastructure)
- Presence, typing, reconnect, missed-event replay, and client-message deduplication
- A durable SQLite experiment queue with one OS-locked worker and atomic claims
- Explicit `@chatbot` activation so ordinary human conversation stays human
- LangChain paper-search answers with clickable OpenAlex citations and optional Tavily web search
- Direct autonomous execution: `@chatbot run an experiment comparing logistic regression and random forest`
- Typed LangGraph nodes for research, planning, generation, validation, execution, bounded repair, evaluation, and reporting
- Interchangeable OpenAI, OpenRouter, NVIDIA, Groq, and Ollama model providers by agent role
- Fail-closed Docker execution with no network, non-root UID, read-only root, dropped capabilities, CPU/RAM/PID/time/disk limits, and no secret mounts
- Persistent attempts, checkpoints, event logs, source provenance, artifact hashes, lineage, reruns, cancellation, and seed-to-seed comparisons
- A room-authorized experiment evidence API with exact script paths, timestamps, container command/image, exit code, logs, raw metrics, plots, and hash verification
- A semantic completion gate that checks the requested seed/training/simulation counts and required artifacts before an experiment can be marked completed
- Live literature, agent-tool, experiment-progress, and completion events
- OpenAlex search plus restricted arXiv ingestion and clickable citations
- A verified classification benchmark path that works without an LLM and produces JSON, CSV, PNG, metadata, and Markdown/JSON reports
- Model-generated plans and code for other bounded local-CPU studies, with schema and static-policy validation before execution
- The earlier Braess-paradox and PyTorch proposal/approval demos remain available for backward compatibility
- Optional live LangChain Deep Agents orchestration with literature search, hypothesis, planning, simulation, statistical-analysis, and report tools
- Responsive three-pane React interface for rooms, conversation, and research context

## Run locally

Requirements: Python 3.12+, Node.js 20+, and Docker Desktop.

```bash
bash scripts/setup.sh
bash scripts/build-executor.sh
bash scripts/dev.sh
```

Open [http://localhost:5173](http://localhost:5173). The API docs are at [http://localhost:8000/docs](http://localhost:8000/docs).

The first visit creates an opaque 30-day local bearer session. Create a room and click **Invite** to copy a URL containing the room ID and invite code. Opening the link prompts a new visitor to sign in, then joins the room automatically. Open separate browsers or private windows to demonstrate multi-user synchronization.

`scripts/dev.sh` starts three processes: the FastAPI/WebSocket API, the single durable LangGraph worker, and Vite. Docker Desktop must be running before building the executor image or running an autonomous experiment. The system never falls back to host execution.

## Demo conversation

1. Researcher A asks: `@chatbot find the key papers on whether adding a road can increase congestion.`
2. The LangChain agent searches OpenAlex and answers with clickable papers.
3. Researcher A asks: `@chatbot run an experiment comparing logistic regression and random forest on the breast cancer dataset.`
4. ResearchForge immediately creates a durable job and shows `researching → planning → generating → validating → running → evaluating → reporting` to everyone.
5. The isolated container writes measured metrics and artifacts. The room receives the result card, logs, sources, report, plot, CSV, hashes, and limitations.
6. Researcher B asks: `@chatbot repeat the previous experiment with seed 123.` ResearchForge creates a linked version and shows metric deltas against the parent.

ML questions continue to use the bounded PyTorch two-moons flow-matching experiment retained from the original project.

## Model configuration

The verified classification path works without model credentials. Model-generated planning, coding, repair, and broad scientific answers require one configured provider. `auto` selects the first configured provider:

```dotenv
RESEARCH_MODE=deepagents
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4o-mini
PLANNING_MODEL_PROVIDER=openai
CODING_MODEL_PROVIDER=openai
DEBUG_MODEL_PROVIDER=openai
```

OpenRouter, NVIDIA NIM, Groq, and local Ollama are also supported. Each role can use a different provider/model. LangChain handles model/tool integration; LangGraph owns the durable scientific state machine. Generated code is never executed on the host.

The room bot uses the same configured model whenever it is mentioned, even if the experiment workflow remains in demo mode. Academic search uses OpenAlex without a key. To add general-web results, configure `TAVILY_API_KEY`; web content is treated as untrusted evidence and linked for verification. With no model key, `@chatbot` uses a transparent LangChain grounded-search pipeline over real OpenAlex results rather than pretending an LLM ran.

## Photon is optional

Native WebSockets power website chat. The app works with:

```dotenv
PHOTON_ENABLED=false
```

On the machine running the backend, install the optional Photon Beta SDK:

```bash
.venv/bin/python -m pip install -r backend/requirements-photon.txt
```

In the repo-root `.env`, set the credentials from the Photon Beta project
that owns your assigned iMessage line:

```dotenv
PHOTON_ENABLED=true
PHOTON_PROJECT_ID=pho_prj_your_project_id
PHOTON_API_KEY=pho_sk_your_project_key
PHOTON_POLLING_ENABLED=true
PHOTON_PLATFORM=imessage
PHOTON_ALLOW_GROUPS=false
PHOTON_ALLOWED_SENDERS=+15555550100
```

Replace the example sender with your registered iMessage phone number or
Apple Account email. Restart the backend and run one poller for the project.
Text “Can adding a road increase traffic congestion?” to your assigned line.
After the proposal arrives, reply `APPROVE <job-id>` using the ID in the proposal.
The backend runs its bounded traffic simulation and texts the measured result.
These jobs use the same research pipeline as the web app and have their own
iMessage conversation. Polling uses outbound HTTPS and requires no webhook.

## WebSocket contract

Connect with a session token and optional last received sequence:

```text
ws://localhost:8000/ws/rooms/{room_id}?token={opaque_token}&since={sequence}
```

Client events:

- `message.send`
- `typing.started`
- `typing.stopped`

Server events include:

- `room.join`, `room.leave`, `presence.updated`
- `message.created`, `message.history`
- `agent.started`, `agent.thinking`, `agent.tool_started`, `agent.tool_completed`, `agent.completed`
- `experiment.proposed`, `experiment.started`, `experiment.progress`, `experiment.completed`, `experiment.failed`
- `experiment.queued`, `experiment.stage_changed`, `experiment.research_completed`, `experiment.artifact_created`, `experiment.cancelled`
- `experiment.validation_failed`, `experiment.repair_generated`, `experiment.verification_completed`

Every persisted event carries an event ID, room ID, UTC timestamp, monotonic database sequence, and typed payload. The server derives room and participant identity from the authenticated socket; payload-supplied identity fields are ignored.

## Research history pagination

Sources, experiments, hypotheses, and discoveries have no total display cap. Each list
loads 20 records initially and offers **Load more** until its history is exhausted.
Sources are fetched when their tab opens; loaded tabs are cached until research changes.

Authenticated room members can request any list using
`GET /api/rooms/{room_id}/{kind}?limit=20&cursor=...`, where `kind` is `sources`,
`experiments`, `hypotheses`, or `discoveries`. Responses contain `items`, `has_more`,
and `next_cursor`. Omit `cursor` for the first page and pass `next_cursor` unchanged
for the next one. Page sizes may be 1–100; this bounds individual requests, not the
total accessible history. Cursors belong to one room and list.

The room snapshot returns `research_counts`, `research_revision`, `active_experiment`,
and `message_jobs` rather than full research lists. Research completion refreshes
the lists' first pages. Chat and model-context limits are separate and unchanged.

On backend startup, an idempotent migration indexes existing research artifacts,
including source metadata retained in `papers.json` and web results in `review.json`
that former reading-list caps omitted. Subsequent artifact writes update the index
transactionally. Sources are deduplicated by URL within each room, keeping metadata
from the newest job that includes that URL. Search result limits and compute budgets
remain in place.

## Source dependency graph

The **Graph** tab beside Context and Sources connects each research query to its
retrieved sources. Sources are shared nodes, matched by known OpenAlex work ID or
normalized URL. Solid green arrows mean “found for this query”; dashed orange
arrows point from a paper to another room source that it cites. Citation links use
OpenAlex's `referenced_works` metadata; similarity, citation counts, and generated
answer text are never treated as evidence of a citation. Missing reference data is
shown explicitly and does not mean a paper has no citations.

Drag the background to pan, use scroll/pinch or the zoom controls, and use **Fit
graph to view** to reset the viewport. Select nodes to inspect full titles and open
original sources, or focus on a single query. **Expand graph** opens a larger dialog
with a minimap; Escape closes it. The layout adapts to the sidebar and mobile view.

The graph is fetched only when its tab opens. The graph library is loaded separately
from the initial chat bundle. `GET /api/rooms/{room_id}/source-graph?limit=8&cursor=...`
requires room membership and returns `nodes`, `edges`, `query_total`, `has_more`, and
`next_cursor`. **Load older queries** adds pages without duplicating sources or
edges. Only citations between sources already used in the room are included; a
cited source can appear before its originating query's older page has been loaded.

On startup, saved paper metadata is indexed once. New OpenAlex searches retain
reference lists. Opening a graph can fill missing references for older OpenAlex
papers using batched requests, with a timeout and retry cooldown. The provenance
graph remains available if OpenAlex is unavailable. No new API keys are required by
the application, and graph requests do not invoke the language model.

## Tests

```bash
cd backend
../.venv/bin/python -m pytest -q

cd ../frontend
npm test
npm run build
```

The suite covers REST compatibility, room permissions, WebSocket sync, payload authorization, deduplication, replay, direct experiment queueing, atomic worker claims, generated-code policy checks, targeted repair, typed LangGraph completion, procedure-count verification, chatbot evidence retrieval, measured-output evaluation, and report artifacts.

## Project map

```text
backend/app/main.py         REST + WebSocket endpoints and authorization
backend/app/realtime.py     rooms, presence, replay, messaging, agent event bridge
backend/app/storage.py      SQLite jobs, sessions, rooms, messages, ordered events
backend/app/research_store.py indexed research history, backfill, cursor pagination
backend/app/source_graph.py query/source graph and recorded citation relationships
backend/app/experiment_graph.py typed autonomous LangGraph and bounded repair loop
backend/app/executor.py     static validation + fail-closed Docker execution
backend/app/worker.py       OS-locked single-consumer durable queue worker
backend/app/experiment_templates.py verified no-model experiment bundles
backend/app/deepagent.py    actual LangChain Deep Agents and trusted tools
backend/app/workflow.py     literature → proposal → approval → execution
backend/app/experiment.py   traffic assignment + PyTorch flow-matching simulations
frontend/src/main.jsx       collaborative chat application
frontend/src/style.css      responsive research-room design system
```

## Scientific limits

ResearchForge publishes bounded experimental evidence, not universal conclusions. Retrieved metadata is not represented as full-paper reading. A reduced-scale reproduction is labeled as such. Results report the exact dataset, split, seed, runtime contract, measured metrics, artifacts, provenance, and limitations.
