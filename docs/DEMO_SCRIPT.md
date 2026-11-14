# CausalForge: 2-minute collaborative demo

**0:00–0:20 — A room, not a solo dashboard.** Open the same CausalForge room in three browser profiles. Send a message from each and show instant synchronization, participant presence, and the shared context panel.

**0:20–0:45 — Ask the LangChain bot.** Researcher A asks, “@chatbot find the key papers on whether adding a road can increase congestion.” Show clickable OpenAlex citations. Then ask, “@chatbot suggest an experiment for this question,” and show the proposal appearing for every participant.

**0:45–1:05 — Collaborative approval.** Researcher B says, “@chatbot run the experiment,” or clicks the proposal button. Explain that ordinary messages do not wake the bot, membership is server-verified, and the room serializes agent runs.

**1:05–1:35 — Real experiment.** Show live WebSocket progress and the completed traffic-network result. The actual assignment solver measures 65 minutes before the connector and 80 minutes after it. Point out the network visual, comparison bars, hypotheses, and source panel.

**1:35–2:00 — Persistence and scientific honesty.** Reload one browser to demonstrate missed-event recovery. Show that the result, sources, and discovery remain. Close with the stated limitations: a stylized four-node static network demonstrates a mechanism; it does not forecast a real city.

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
