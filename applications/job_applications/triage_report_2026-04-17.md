---
title: Triage Report
date: 2026-04-17
author: Igor Ganapolsky (automated)
---

# Triage Report — 2026-04-17

## Task 1: Quarantine Triage

**26 Quarantined rows reviewed. 17 status changes made.**

### Promoted to ReadyToSubmit (12 rows)
| Company | Role |
|---------|------|
| Anthropic | Forward Deployed Engineer, Applied AI |
| Anthropic | Applied AI Engineer (Digital Natives Business) |
| Anthropic | Senior / Staff Software Engineer, Continuous Integration |
| Databricks | AI Engineer - FDE (Forward Deployed Engineer) |
| Databricks | Senior Solutions Engineer |
| Databricks | Sr. Solutions Engineer |
| Databricks | Sr. Solutions Engineer - Communications, Media, Entertainment and Games |
| Databricks | Sr. Solutions Engineer - Digital Native Business, Strategic |
| Vercel | Forward-Deployed Engineer |
| Vercel | Senior Partner Solutions Engineer |
| Runway | Engineering Manager, API |
| Runway | Member of Technical Staff, Backend Engineer, API |

### Kept Quarantined with Triage Notes (9 rows)
| Company | Role | Note |
|---------|------|------|
| Anthropic | AI Safety Fellow | role mismatch - not core engineering track |
| Anthropic | AI Security Fellow | role mismatch - not core engineering track |
| Anthropic | Application Security Engineer | role mismatch - not core engineering track |
| Databricks | Head of AI FDE APJ | APJ region mismatch |
| Databricks | Sr. Manager AI FDE | management track, not IC |
| Runway | MTS Inference | reCAPTCHA Enterprise block - needs manual submit |
| Baseten | Senior Software Engineer - Infrastructure | spam flag - needs manual browser submit |
| Inferact | MTS Exceptional Generalist | spam flag - needs manual browser submit |
| Oracle | Sr Principal AI Software Engineer | oracle ATS - needs manual verification |

### Demoted to Draft (5 rows)
| Company | Role | Note |
|---------|------|------|
| Anthropic | Data Center Engineer, Resource Efficiency | role mismatch |
| Anthropic | Data Center Mechanical Engineer | role mismatch |
| Anthropic | Engineering Manager, Networking | role mismatch |
| Anthropic | Engineering Manager, People Products | role mismatch |
| Vercel | Senior Customer Support Engineer | support role, below target level |

---

## Task 2: Follow-Up Email Drafts

**9 follow-up emails created for overdue Applied rows:**

| Company | Role | Days Since Applied | Path |
|---------|------|--------------------|------|
| Owner.com | Software Engineer Mobile | 74 | `applications/owner-com/cover_letters/2026-04-17_owner-com_followup.md` |
| ElevenLabs | Forward Deployed Engineer | 58 | `applications/elevenlabs/cover_letters/2026-04-17_elevenlabs_followup.md` |
| OpenEvidence | SRE Software Engineer | 58 | `applications/openevidence/cover_letters/2026-04-17_openevidence_followup.md` |
| Mercor | Software Engineer (Trajectory + III) | 58 | `applications/mercor/cover_letters/2026-04-17_mercor_followup.md` |
| Simile | General Inquiry | 58 | `applications/simile/cover_letters/2026-04-17_simile_followup.md` |
| SKYCATCHFIRE | Senior Python Backend Developer | 46 | `applications/skycatchfire/cover_letters/2026-04-17_skycatchfire_followup.md` |
| nooro | iOS Developer | 46 | `applications/nooro/cover_letters/2026-04-17_nooro_followup.md` |
| AMI | AMI Engineer | 31 | `applications/ami/cover_letters/2026-04-17_ami_followup.md` |
| Perplexity | AI Software Engineer - Agents | 37 | `applications/perplexity/cover_letters/2026-04-17_perplexity_followup.md` |

---

## Task 3: RemoteOK/Remotive URL Fixes

**20 Draft rows with aggregator URLs reviewed.**

### Direct URLs Updated (3 entries)
- TELUS Digital → `https://www.telusinternational.com/careers`
- A.Team (2 entries) → `https://jobs.ashbyhq.com/a-team`

### Marked for Manual URL Update (17 entries)
Companies where direct ATS URL could not be confidently determined:
BNSF Railway (3 positions), Welo Data, Clipboard Health, Marketerx, Exaware, shopware AG, Fluence International, ELECTE S.R.L., Anuttacon, Lemon.io, Sanctuary Computer, XXIX, Nebulab, AutoHDR (2 positions)

---

## Task 4: Product Proposals

**4 product proposals created:**

| Company | Path |
|---------|------|
| Anthropic | `applications/anthropic/product_proposal_2026.md` |
| Databricks | `applications/databricks/product_proposal_2026.md` |
| Vercel | `applications/vercel/product_proposal_2026.md` |
| OpenAI | `applications/openai/product_proposal_2026.md` |

**23 tracker rows updated** with Product Proposal Path for matching Tier 1 companies (ReadyToSubmit + Applied).

---

## Task 5: RAG Build & Tests

- **RAG Build**: Built 289 records (JSONL only; lancedb unavailable)
- **Tests**: 281 passed, 1 failed in 5.00s
  - **FAILED**: `test_ralph_loop_workflow.py::test_live_submit_requires_profile_and_answers_but_not_auth`
    - Pre-existing test failure: assertion checks for a string that was removed from the workflow YAML. Not related to this triage session.

---

## Blockers / Manual Action Required

1. **Baseten** — spam flag on Ashby, needs manual browser submit
2. **Inferact** — spam flag on Ashby, needs manual browser submit
3. **Runway MTS Inference** — reCAPTCHA Enterprise block, needs manual submit
4. **Oracle** — Oracle ATS, needs manual verification of submission
5. **17 Draft rows** with remotive.com URLs need manual ATS URL lookup
6. **1 pre-existing test failure** in `test_ralph_loop_workflow.py` (not caused by this session)
7. **LanceDB unavailable** — RAG index built as JSONL fallback only

---

## Summary Statistics

| Metric | Count |
|--------|-------|
| Quarantined rows reviewed | 26 |
| Promoted to ReadyToSubmit | 12 |
| Kept Quarantined (noted) | 9 |
| Demoted to Draft | 5 |
| Follow-up emails created | 9 |
| Product proposals created | 4 |
| Tracker proposal paths updated | 23 |
| URLs updated to direct ATS | 3 |
| URLs flagged for manual update | 17 |
| RAG records built | 289 |
| Tests passed | 281 |
| Tests failed | 1 (pre-existing) |
