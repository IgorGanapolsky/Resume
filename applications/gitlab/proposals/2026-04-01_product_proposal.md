# Product Proposal: GitLab - Local LLM Integration for Mobile Issue Triage

**To:** Engineering Manager, Mobile
**From:** Igor Ganapolsky
**Date:** 2026-04-01

## 1. The Observation

The GitLab mobile app is great for quick reviews, but triage workflows (labeling, summarizing long issue threads) still require heavy context switching or moving to desktop. On-call engineers and managers often struggle to synthesize complex issue histories while on the go.

## 2. The Recommendation: On-Device Triage Assistant

Integrate a small, on-device SLM (Small Language Model) using `MLX` or `CoreML` directly within the GitLab mobile app to provide instant, offline-capable summaries of issue threads and smart label recommendations.

- **User Impact:** On-call engineers can instantly grasp the root cause of an issue without scrolling through 50+ comments on a small screen.
- **Business Impact:** Increases mobile engagement for triage workflows and showcases GitLab's commitment to AI-driven DevSecOps without incurring high cloud inference costs for every mobile action.
- **Technical Path:** Utilize Apple's MLX (iOS) and ExecuTorch to run a quantized 2B parameter model locally. The prompt is fed the issue JSON payload and outputs a structured summary.

## 3. The Prototype (The Proof)

I built a localized Swift prototype that downloads a quantized model, ingests a mock GitLab issue JSON payload, and generates a structured summary in under 2 seconds on an iPhone 15.

- **Prototype Link:** https://github.com/IgorGanapolsky/local-ml-triage-poc
- **Key Insight:** Demonstrates that basic summarization and labeling do not require a round-trip to the cloud, preserving privacy and saving API costs.

---

## 4. Why This Matters to GitLab

At Subway, I integrated AI/ML pipelines (including RAG architectures and dialog systems) into enterprise workflows. Bridging the gap between robust mobile engineering and applied AI is my core strength. Bringing this hybrid expertise to GitLab's mobile initiatives is exactly why I'm applying for the Senior Software Engineer Mobile position.

---

**Links:**

- **Resume:** [Attach Tailored Resume]
- **Portfolio:** https://github.com/IgorGanapolsky
