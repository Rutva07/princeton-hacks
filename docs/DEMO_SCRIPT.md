# Labthread: 2-minute collaborative demo

**0:00–0:20 — A room, not a solo dashboard.** Open the same Labthread room in three browser profiles. Send a message from each and show instant synchronization, participant presence, and the shared context panel.

**0:20–0:45 — Ask the LangChain bot.** Researcher A asks, “@chatbot find the key papers on uncertainty estimation.” Show clickable OpenAlex citations and evidence labels.

**0:45–1:05 — Direct autonomous request.** Researcher B says, “@chatbot run an experiment comparing logistic regression and random forest on the breast cancer dataset.” Show the durable queue entry immediately—no preapproved proposal is required.

**1:05–1:35 — Real isolated experiment.** Show the LangGraph stages, container log, result metrics, cross-validation uncertainty, plot, CSV, metadata, and report. Point out that Docker has no network or secrets and that the displayed numbers came from `metrics.json`.

**1:35–2:00 — Persistence and follow-up.** Reload one browser to demonstrate missed-event recovery. Ask, “@chatbot repeat the previous experiment with seed 123.” Show the linked version and F1 delta, then close with the visible limitations and source provenance.

## Sponsor framing

- **Science & Discovery:** source-grounded question → hypothesis → authorized real experiment → bounded evidence.
- **Amazon Best Practical AI Innovation:** a useful multi-user workflow with explicit authorization, persistence, and recoverability.
- **LangChain Deep Agents:** real specialist delegation and trusted simulation/analysis/report tools when live mode is enabled.
- **Photon:** optional future/secondary adapter only. Do not claim Photon eligibility from native WebSockets.

## Reliability checklist

- Run the backend test suite and frontend build before judging.
- Keep a completed traffic experiment in the room as a fallback.
- If showing live Deep Agents, configure a tool-calling model and LangSmith without exposing keys.
- Use `PHOTON_ENABLED=false` for the primary website demo.
