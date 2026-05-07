Subject: AI Deployment Engineer, Public Sector - Federal Civilian — three toughest technical problems

Hello OpenAI team,

In lieu of a standard cover letter, three technical problems I've personally solved:

- **Play-Store-scale malware triage at Google.** Built ML/LLM-assisted detection on Vertex AI with GCP Cloud Build/Functions driving continuous model runs across thousands of apps; weak labels, adversarial authors, asymmetric error costs. Outcome: 100+ malicious apps removed via the detection path I helped build.
- **Customer-facing RAG + conversational-agent stack at CNH** that had to *reduce* human support load, not increase it. Dialogflow CX + Vertex AI intent classification, retrieval over manuals/telemetry, GPT-4o RAG over app/ordering data, prompts tuned for relevance/latency/cost. Outcome: support load –35%, field-support volume –40%.
- **Self-healing autonomous AI trading system** (github.com/IgorGanapolsky/trading). Multi-model LLM gateway on Tetrate Agent Router Service with cost-aware routing and provider fallbacks; LanceDB semantic memory so long-running agent sessions resume with context; CI that fix-PRs its own breakage instead of paging me.

The through-line: production AI systems where reliability, cost, and latency are non-negotiable — built end-to-end, not handed off.

Code and architecture are public:
- GitHub: https://github.com/IgorGanapolsky
- LinkedIn: https://www.linkedin.com/in/igor-ganapolsky-859317343/

Happy to walk any of these in depth.

Igor Ganapolsky
