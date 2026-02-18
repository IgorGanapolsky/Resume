# LinkedIn Technical Posts - February 2026

## Post 1: React Native New Architecture (High Impact)

**Topic:** Migration experience at scale

---

Migrating a 5M+ user React Native app to the New Architecture taught me things the docs don't cover.

The wins:
- Fabric renderer eliminated our worst jank issues
- TurboModules cut bridge overhead significantly
- Expo SDK 54 made the migration path surprisingly smooth

The gotchas:
- Third-party libraries lag behind. Budget time for patches or replacements
- Testing on real devices is non-negotiable. Simulators hide perf issues
- Incremental migration > big bang. We did it screen by screen

If you're planning a New Architecture migration in 2026, happy to share what worked (and what didn't).

I wrote more about React Native architecture on my blog: https://igorganapolsky.github.io/trading/

#ReactNative #MobileDevelopment #Expo #NewArchitecture

---

## Post 2: GitHub Actions Automation (Developer Productivity)

**Topic:** PR automation at scale

---

We reduced manual PR review work by automating the boring parts.

15+ GitHub Actions workflows handling:
- Automated code quality checks before human review
- PR comment cleanup (stale threads resolved automatically)
- Diagram generation for architecture changes
- Bundle size monitoring with automatic warnings

The key insight: Automate the gatekeeping, not the thinking.

Humans review architecture decisions and business logic. Machines check formatting, test coverage, and bundle impacts.

What's your team automating in CI/CD?

More on my automation setup: https://dev.to/igorganapolsky

#GitHubActions #DevOps #DeveloperProductivity #CI/CD

---

## Post 3: Multi-Repo Architecture (Technical Leadership)

**Topic:** Coordinating shared code across platforms

---

Managing shared code across mobile, kiosk, and web taught me the real cost of "DRY."

Our setup:
- `shared-core`: Business logic, no UI
- `mobile`: React Native app
- `kiosk`: Tablet-optimized variant
- `shared-workflows`: GitHub Actions reused across repos

What worked:
- Strict API contracts between repos
- Automated dependency updates via Renovate
- Breaking change detection in CI

What didn't:
- Over-abstracting too early
- Assuming "shared" means "identical"

The best shared code is boring code. If it's exciting, it probably shouldn't be shared.

Deep dive on architecture patterns: https://igorganapolsky.github.io/trading/

#SoftwareArchitecture #Monorepo #TechnicalLeadership

---

## Post 4: On-Device ML (Technical Deep Dive)

**Topic:** TensorFlow Lite implementation

---

Moving ML inference off the main thread changed our app's responsiveness completely.

We use TensorFlow Lite for on-device predictions. The architecture:
- Model runs in a dedicated thread
- Results queue back to JS via TurboModules
- Fallback to server inference if device is underpowered

Why on-device?
- Privacy: User data never leaves the phone
- Speed: No network round-trip
- Offline: Works without connectivity

The tradeoff: Model size vs accuracy. We ship a 4MB model that handles 90% of cases, with server fallback for edge cases.

Anyone else running on-device ML in React Native?

More ML experiments on my trading blog: https://igorganapolsky.github.io/trading/

#MachineLearning #TensorFlow #MobileDevelopment #EdgeAI

---

## Post 5: Claude AI for Developer Tooling (AI/Automation)

**Topic:** Building internal productivity tools

---

I've built 20+ Claude AI skills for developer productivity. Here's what actually saves time:

High ROI skills:
- PR description generation from diffs
- Test case suggestions based on code changes
- Architecture validation before CI runs
- Commit message formatting

Low ROI (surprised me):
- Full code generation (too much review overhead)
- Documentation auto-generation (needs human editing anyway)

The pattern: AI works best as a copilot for tedious tasks, not a replacement for thinking.

What developer tasks have you automated with AI?

I write about AI tooling on DEV.to: https://dev.to/igorganapolsky

#AI #DeveloperTools #Automation #ClaudeAI

---

## Post 6: Job Search Transparency (Personal Brand)

**Topic:** Open about looking

---

I'm exploring new opportunities in mobile development and AI/ML.

Background:
- 15+ years in software engineering
- Recent focus: React Native New Architecture at scale
- Side interest: AI-powered developer tooling

Looking for:
- Senior/Staff Mobile Engineer roles
- Teams doing interesting work with React Native or AI
- Companies that value technical depth

Not looking for:
- "We need someone to manage offshore teams"
- Pure management roles (I still want to code)

If your team is building something interesting, I'd love to hear about it.

DMs open.

Portfolio: https://igorganapolsky.github.io/trading/
Technical writing: https://dev.to/igorganapolsky

#OpenToWork #ReactNative #MobileEngineering #AI

---

## Posting Schedule

| Day | Post | Goal |
|-----|------|------|
| Mon | Post 1 (New Architecture) | Establish technical credibility |
| Wed | Post 2 (GitHub Actions) | Show automation expertise |
| Fri | Post 5 (Claude AI) | AI relevance |
| Next Mon | Post 3 (Multi-Repo) | Architecture thinking |
| Next Wed | Post 4 (On-Device ML) | ML depth |
| Next Fri | Post 6 (Job Search) | Direct ask |

## Engagement Strategy

After posting:
1. Respond to every comment within 2 hours
2. Ask follow-up questions to keep threads alive
3. Share others' relevant posts with your take
4. Comment on RN/Expo official announcements

## Hashtag Strategy

**Always include:** #ReactNative #MobileDevelopment
**Rotate:** #AI #MachineLearning #DevOps #GitHubActions #Expo
**Avoid:** #OpenToWork (badge is enough, hashtag looks desperate)
