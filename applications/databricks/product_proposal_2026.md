---
type: product-proposal
company: Databricks
author: Igor Ganapolsky
date: 2026-04-17
---

# Product Proposal: Databricks

## Why Databricks

Databricks is unifying data and AI through the lakehouse platform, making it easier for enterprises to build AI applications on their data. I'm excited by the intersection of data infrastructure and applied AI—my experience deploying RAG pipelines and agentic systems on GCP, combined with shipping production ML at scale (Play Store ML at Google, FreeStyle Libre analytics at Abbott), positions me to help customers operationalize AI workflows end-to-end.

## What I Bring

- **Forward-Deployed Engineering Mindset**: 8+ years as a field engineer and product-adjacent IC, embedding myself in customer workflows, unblocking production deployment, and gathering product feedback. Proven ability to go from concept to shipped integration in 4-6 weeks.
- **LLM & Agent Infrastructure**: Expert in building LLM routing, tool-use orchestration, and agentic workflows. Direct experience deploying Dialogflow CX agents and multi-step retrieval systems, with deep knowledge of performance optimization, cost control, and reliability patterns.
- **Cloud-Native Architecture & Observability**: Hands-on experience with GCP (Vertex AI, BigQuery), AWS, Kubernetes, and distributed systems. Strong track record designing fault-tolerant, observable, cost-efficient deployments that scale from prototype to production.

## 90-Day Project: Mosaic AI Agent Deployment Toolkit

Develop a production-ready toolkit that accelerates customer deployment of RAG + multi-agent workflows on Databricks Mosaic AI. Today, customers can prototype agents in notebooks but struggle with operationalization (state management, observability, vector retrieval integration, cost optimization).

**Scope**: Build a Python library + Databricks Workflow templates that provide:
- **Agent state persistence** abstraction (Delta Lake backing)
- **Retrieval-augmented generation connectors** (Vector Search, external vector DBs, semantic search on Unity Catalog)
- **Observability framework** (structured logging, token tracking, cost attribution per agent/tool)
- **Deployment scaffolding** (GitHub Actions CI/CD, model serving integration, A/B testing harness)
- **Example agents** (customer support, research assistant, sales analyst) with production-grade error handling and fallbacks

**Expected Impact**: Reduce customer RAG-agent time-to-production from 8-12 weeks to 2-3 weeks. Provide Databricks Solutions Engineering with a repeatable customer playbook. Generate customer case studies demonstrating ROI (e.g., 60% reduction in support tickets, 40% faster analyst workflows).

**Deliverables**: Open-source GitHub repository, comprehensive Databricks blog post, Jupyter notebooks, customer success story, and a demo video.
