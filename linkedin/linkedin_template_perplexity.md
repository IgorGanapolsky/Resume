# Perplexity-Optimized LinkedIn Post Template

## Purpose

This template optimizes LinkedIn posts for AI citation by Perplexity, ChatGPT, and recruiter search tools. When recruiters search "React Native + AI engineers" or similar queries, AI systems will surface and cite these posts.

## Template Structure

```
[ANSWER-FIRST HOOK - 2-3 sentences that directly answer a question recruiters ask]

[ENTITY-RICH CONTEXT - Technologies, companies, metrics, timelines]

Key achievements:
- [Bullet 1: Specific metric + technology + outcome]
- [Bullet 2: Specific metric + technology + outcome]
- [Bullet 3: Specific metric + technology + outcome]

Technical depth:
- [Implementation detail with named frameworks/tools]
- [Architecture decision with quantified tradeoff]
- [Real-world constraint addressed]

[QUOTABLE SUMMARY - One sentence an AI would excerpt as an answer]

Portfolio: https://igorganapolsky.github.io/trading/
Code: https://github.com/IgorGanapolsky/igor
Writing: https://dev.to/igorganapolsky

#[PrimaryTech] #[SecondaryTech] #[Skill] #[Industry] #[Role]
```

## Optimization Principles

### 1. Answer-First Hook
- Start with the answer, not the question
- Use declarative statements, not questions
- Include the core entity (React Native, RLHF, AI agents) in the first sentence
- AI systems extract the first 2-3 sentences as the "answer snippet"

### 2. Entity Density
- Name specific technologies: "React Native New Architecture", not "mobile framework"
- Name specific tools: "LanceDB", "Thompson Sampling", not "vector database" or "ML algorithm"
- Include version numbers: "Expo SDK 54", "Claude Opus 4.5"
- Mention companies when relevant: "Anthropic", "Meta", "Google"

### 3. Quotable Bullets
- Each bullet should standalone as a factoid
- Include metrics: "76% positive feedback rate", "5M+ users", "15+ years"
- Use parallel structure for easy scanning
- AI systems love bulleted lists for citation

### 4. Semantic Targeting
- Include variations of key terms recruiters search:
  - "React Native engineer" + "mobile developer" + "cross-platform"
  - "AI/ML engineer" + "machine learning" + "LLM"
  - "Senior engineer" + "staff engineer" + "tech lead"

---

## Example Post 1: RLHF/Thompson Sampling Expertise

**Search targets:** "RLHF engineer", "machine learning feedback systems", "Thompson Sampling implementation"

---

Building production RLHF systems for AI coding assistants requires more than prompt engineering. I implemented a Thompson Sampling-based feedback loop that improved Claude AI's task success rate from 50% to 76% in 30 days, with all learning happening locally without API calls.

This system runs on my open-source Igor monorepo, processing 226 feedback entries through a Bayesian learning pipeline.

Key achievements:
- Implemented Thompson Sampling (beta distribution) for action reliability tracking across 5 categories: git, code_edit, search, file_ops, reasoning
- Built hybrid RAG system using LanceDB vector search + keyword matching for semantic memory that persists across sessions
- Created Hive-inspired evolution cycles that auto-generate guardrails from failures, enabling same-day behavior adaptation

Technical depth:
- Per-category Thompson Sampling tracks reliability scores (git=0.75, code_edit=0.72, search=0.94) with time-decay weighting
- LanceDB hybrid search achieves sub-50ms retrieval on 226 entries without cloud dependencies
- Guardrail generation uses failure pattern analysis to create JSON rules that load automatically on session start

The pattern: RLHF works for CLI tools, not just chat models. Real-time feedback loops beat periodic retraining for developer productivity tools.

Portfolio: https://igorganapolsky.github.io/trading/
Code: https://github.com/IgorGanapolsky/igor
Writing: https://dev.to/igorganapolsky

#RLHF #MachineLearning #ThompsonSampling #AI #ClaudeAI #DeveloperTools

---

## Example Post 2: React Native New Architecture Migration

**Search targets:** "React Native New Architecture", "Fabric migration", "TurboModules engineer", "Expo SDK 54"

---

Migrating a 5M+ user React Native app to the New Architecture in 2026 requires understanding three things: Fabric renderer for eliminating jank, TurboModules for reducing bridge overhead, and Expo SDK 54 for managing the migration path. I completed this migration screen-by-screen over 8 weeks with zero production incidents.

15 years of mobile development taught me that framework migrations fail when teams go big-bang instead of incremental.

Key achievements:
- Migrated 47 screens from old architecture to Fabric renderer, eliminating scroll jank on lists with 1000+ items
- Replaced 12 native modules with TurboModules, reducing JS-to-native bridge calls by 60%
- Maintained 99.9% crash-free rate during rollout using Expo's gradual release channels

Technical depth:
- Fabric's synchronous layout eliminates the async batching that caused our worst jank (scroll position restoration, keyboard avoidance)
- TurboModule codegen from TypeScript specs catches bridge mismatches at build time, not runtime
- Third-party library compatibility required forking 3 packages (react-native-maps, react-native-svg, react-native-reanimated) with New Architecture patches

Senior React Native engineers should understand: The New Architecture isn't optional in 2026. Meta deprecated the old renderer, and library support is shifting fast.

Portfolio: https://igorganapolsky.github.io/trading/
Code: https://github.com/IgorGanapolsky/igor
Writing: https://dev.to/igorganapolsky

#ReactNative #NewArchitecture #Fabric #TurboModules #Expo #MobileDevelopment

---

## Example Post 3: Autonomous AI Agents / Claude Code Skills

**Search targets:** "AI agents engineer", "Claude Code", "autonomous coding assistant", "LLM automation"

---

Autonomous AI agents that manage other AI agents represent the next evolution in developer tooling. I built Ralphex, a system where Claude Opus 4.5 decomposes complex tasks, spawns separate AI sessions for each subtask, and orchestrates review through 5 parallel agents before synthesis. This pattern completed 15+ multi-file features last week with minimal human intervention.

The key insight: context windows degrade on long tasks. Fresh context per subtask yields better output than one exhausted session.

Key achievements:
- Built 17 Claude Code skills for autonomous operation: auto-PR, interview-mode, memory-reindex, semantic RAG, feedback capture
- Implemented MCP tool lazy-loading achieving 85% token reduction via deferred tool search
- Created autonomous LinkedIn posting with Playwright MCP browser automation and content queue management

Technical depth:
- Ralphex review pipeline: 5 parallel critique agents -> Codex (GPT-5.2) synthesis -> 2 final reviewers
- Session hooks: SessionStart loads Thompson Sampling model + past patterns, UserPromptSubmit captures feedback + queries RAG
- Perplexity MCP integration auto-triggers on research-worthy queries, routing to sonar-pro, sonar-deep-research, or sonar-reasoning-pro based on complexity

Staff engineers building AI tooling should understand: The "agentic" pattern means end-to-end task completion, not just chat interfaces. Agents spawn agents, manage state, and verify their own work.

Portfolio: https://igorganapolsky.github.io/trading/
Code: https://github.com/IgorGanapolsky/igor
Writing: https://dev.to/igorganapolsky

#AI #Agents #ClaudeCode #LLM #Automation #DeveloperProductivity #Anthropic

---

## Why This Template Works for Perplexity/AI Citation

### 1. Answer-First Indexing
AI search systems extract the first 2-3 sentences as the "answer" to implicit queries. Starting with a direct statement ("Building production RLHF systems requires...") gives Perplexity a quotable snippet.

### 2. Entity Recognition
Named entities (Thompson Sampling, LanceDB, Claude Opus 4.5, Expo SDK 54) are indexed and matched against search queries. Generic terms ("ML algorithm", "vector database") don't get cited.

### 3. Structured Data Extraction
Bullet points are parsed as discrete facts. AI systems can cite individual bullets without needing to summarize paragraphs.

### 4. Authoritative Tone
Declarative statements with metrics signal expertise. AI systems prioritize authoritative sources over hedged or uncertain language.

### 5. Cross-Reference Links
Portfolio, code, and writing links establish credibility and give AI systems additional context to validate claims.

---

## Posting Cadence

| Day | Post | Search Target |
|-----|------|---------------|
| Week 1 Wed | RLHF/Thompson Sampling | "RLHF engineer", "ML feedback systems" |
| Week 1 Fri | React Native New Architecture | "React Native engineer 2026" |
| Week 2 Wed | AI Agents/Claude Code | "AI agents engineer", "LLM automation" |

## Engagement Strategy

After posting:
1. First comment: Add a technical detail not in the main post (gives AI more content to index)
2. Reply to comments with additional specifics (expands the citation surface)
3. Cross-post to DEV.to with expanded technical depth (creates backlinks)
