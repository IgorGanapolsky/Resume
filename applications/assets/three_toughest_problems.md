# Three Toughest Technical Problems I've Solved

Dense, outcome-led summary for signal-driven hiring processes (Musk-cos, frontier AI labs, principal-engineer tracks) where boilerplate cover letters are noise. Reuse as the opener on those applications.

---

**1. Play-Store-scale malware triage without a labeled ground truth.**
At Google, the hard part was not the classifier — it was the operating environment: millions of binaries per day, adversarial authors, weak labels, and a bar where a false positive is a dev-livelihood event and a false negative is a consumer-safety event. Built the ML/LLM-assisted triage pipeline on Vertex AI with GCP Cloud Build/Functions driving continuous model runs, logging, and performance monitoring across thousands of apps. Outcome: measurably faster detection and safer Play Store review at scale, with 100+ malicious apps removed from the store via the detection path I helped build.

**2. A customer-facing RAG + conversational-agent stack that had to reduce human support load, not increase it.**
At CNH, the tempting failure mode was shipping a chatbot that punts everything back to a human. Architected Dialogflow CX agents integrated with Vertex AI for intent classification, stitched to retrieval over manuals/telemetry plus GPT-4o-backed RAG over app/ordering data for personalized menu recommendations. Tuned prompts for relevance, latency, and token cost; wrote the triage workflow so the system escalates only what it can't answer. Outcome: customer-service load down 35%, field-support volume down 40%, and the system is still the primary front door for support questions.

**3. A self-healing autonomous AI trading system with cost-aware model routing.**
My own project (github.com/IgorGanapolsky/trading). The interesting problems: (a) picking the cheapest capable model per step without blowing tail latency, (b) degrading gracefully when a provider blackholes, (c) keeping a long-running agent grounded in its own prior state across restarts, and (d) making the CI recover from its own breakage instead of paging me. Built a multi-model LLM gateway on Tetrate Agent Router Service (TARS) with provider fallbacks, cost-aware model selection, and prompt/cost controls; bolted on a LanceDB-backed semantic memory so sessions resume with context; wired self-healing CI so a broken build gets an automated fix-PR rather than a dead loop.

---

*Code and architecture are public: github.com/IgorGanapolsky. Happy to walk any of these in depth.*
