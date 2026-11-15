# CausalForge — Collaborative AI Scientist

CausalForge is a WebSocket-first research workspace where multiple researchers and one shared LangChain research agent investigate questions together. Messages, sources, hypotheses, experiment proposals, progress, and measured results stay synchronized across browsers in a persistent room.

The default local mode is fully functional without an LLM or messaging provider. Photon is an optional adapter and is disabled by default.

## What works

- Authenticated research rooms with working shareable invitation URLs, automatic join, invite codes, and server-verified membership
- Native FastAPI WebSockets at `/ws/rooms/{room_id}`
- Persistent messages and ordered room events in SQLite
- Three or more logical participants per room (limited only by infrastructure)
- Presence, typing, reconnect, missed-event replay, and client-message deduplication
- One serialized AI/experiment run per room to protect shared state
- Explicit `@chatbot` activation so ordinary human conversation stays human
- LangChain paper-search answers with clickable OpenAlex citations and optional Tavily web search
- Separate `@chatbot` intents for answering, suggesting an experiment, and running an experiment
- Live literature, agent-tool, experiment-progress, and completion events
- OpenAlex search plus restricted arXiv ingestion and clickable citations
- A real Braess-paradox traffic assignment simulation for congestion questions
- The existing real PyTorch flow-matching comparison for ML questions
- Optional live LangChain Deep Agents orchestration with literature search, hypothesis, planning, simulation, statistical-analysis, and report tools
- Responsive three-pane React interface for rooms, conversation, and research context

## Run locally

Requirements: Python 3.12+ and Node.js 20+.

```bash
bash scripts/setup.sh
bash scripts/dev.sh
```

Open [http://localhost:5173](http://localhost:5173). The API docs are at [http://localhost:8000/docs](http://localhost:8000/docs).

The first visit creates an opaque 30-day local bearer session. Create a room and click **Invite** to copy a URL containing the room ID and invite code. Opening the link prompts a new visitor to sign in, then joins the room automatically. Open separate browsers or private windows to demonstrate multi-user synchronization.

## Demo conversation

1. Researcher A asks: `@chatbot find the key papers on whether adding a road can increase congestion.`
2. The LangChain agent searches OpenAlex and answers with clickable papers.
3. Researcher A asks: `@chatbot suggest an experiment for this question.`
4. CausalForge publishes a controlled experiment proposal.
5. Researcher B says: `@chatbot run the experiment.` or clicks **Run experiment**.
6. The room receives live progress and a persisted result card. The traffic solver compares equilibrium travel time before and after adding a connector. In the included four-node model, average travel time rises from 65 to 80 minutes—a computed Braess-paradox result, with limitations clearly labeled.

ML questions continue to use the bounded PyTorch two-moons flow-matching experiment retained from the original project.

## Live Deep Agents mode

Default `RESEARCH_MODE=demo` uses deterministic orchestration and real backend tools without pretending an LLM ran. To enable actual LangChain Deep Agents:

```dotenv
RESEARCH_MODE=deepagents
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4o-mini
```

OpenRouter is also supported through `OPENROUTER_API_KEY` and `OPENROUTER_MODEL`. In live mode, the supervisor delegates literature review, experiment planning, and scientific critique. After approval, a Deep Agent must call the trusted simulation tool, statistical-analysis tool, and report tool in sequence. Arbitrary host code execution is never exposed.

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

The suite covers REST compatibility, room permissions, WebSocket sync, payload authorization, deduplication, replay, the real traffic solver, natural-language experiment planning, approval, and execution.

## Project map

```text
backend/app/main.py         REST + WebSocket endpoints and authorization
backend/app/realtime.py     rooms, presence, replay, messaging, agent event bridge
backend/app/storage.py      SQLite jobs, sessions, rooms, messages, ordered events
backend/app/research_store.py indexed research history, backfill, cursor pagination
backend/app/source_graph.py query/source graph and recorded citation relationships
backend/app/deepagent.py    actual LangChain Deep Agents and trusted tools
backend/app/workflow.py     literature → proposal → approval → execution
backend/app/experiment.py   traffic assignment + PyTorch flow-matching simulations
frontend/src/main.jsx       collaborative chat application
frontend/src/style.css      responsive research-room design system
```

## Scientific limits

CausalForge publishes bounded experimental evidence, not universal conclusions. The traffic study is a stylized static network, and the ML study uses one seed and a small synthetic dataset. Source evidence levels and model limitations remain visible in the room.
