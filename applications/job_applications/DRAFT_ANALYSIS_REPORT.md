# APPLICATION DRAFT ANALYSIS REPORT

**Generated:** 2026-03-02  
**Analyzed File:** `/Users/ganapolsky_i/workspace/git/igor/Resume/applications/job_applications/application_tracker.csv`

---

## EXECUTIVE SUMMARY

- **Total Drafts:** 91 (70% of 130 total applications)
- **Ready to Submit:** 0 (requires submission execution)
- **Closest to Ready:** 20 applications at 60-65% field completion
- **Primary Blocker:** 100% of drafts missing resume path + submission evidence (post-submission fields)
- **Secondary Blocker:** 87/91 (95.6%) missing Date Applied field
- **Automation Available:** YES - `ci_submit_pipeline.py` exists with Playwright-based ATS adapters (Ashby, Greenhouse, Lever)

---

## DRAFT BREAKDOWN BY COMPLETION

### Overall Completion Metrics

| Metric | Value |
|--------|-------|
| Total Field Count | 23 |
| Fields 100% Filled | 11 |
| Fields 0% Filled | 3 |
| Average Fill Rate | 52% |

### Field Completion Rate (All 91 Drafts)

**Always Filled (100%):**
- Company
- Role
- Status
- Interview Stage
- Tags
- Notes
- Career Page URL
- Remote Policy
- Remote Likelihood Score
- Remote Evidence
- Submission Lane
- Cover Letter Used (97.8%)

**Rarely Filled (<10%):**
- Submitted Resume Path: 0/91 (0%)
- Submission Evidence Path: 0/91 (0%)
- Date Applied: 4/91 (4.4%)
- Response Type: 0/91 (0%)
- Days To Response: 0/91 (0%)
- What Worked: 0/91 (0%)

**Partially Filled (20-80%):**
- Location: 71/91 (78%)
- Salary Range: 18/91 (19.8%)
- Response: 6/91 (6.6%)

---

## CATEGORIZATION OF DRAFTS (BY READINESS)

### Category 1: Most Ready (Top 3)
**Count:** 3 applications at 65.2% complete
- OpenEvidence - Software Engineer - Backend and Infrastructure
- Runway - Member of Technical Staff - Inference
- Decagon - Senior Software Engineer, Infrastructure

**Status:** Have all metadata + URL + date applied  
**Missing:** Only post-submission fields (resume path, evidence path)  
**Action:** Can submit immediately once execution/path fields are populated

### Category 2: Nearly Ready (Rank 4-20)
**Count:** 17 applications at 60.9% complete
- Inferact, PaleBlueDot AI, Flapping Airplanes, Coalition Technologies, Mitre Media (2x), nooro, SKYCATCHFIRE, TELUS Digital, Welo Data, BNSF Railway (3x), Marketerx, Exaware, Fluence International, A.Team

**Status:** Have URL + most metadata  
**Missing:** Date Applied + post-submission fields  
**Action:** Add application dates (can use today or target date), then submit

### Category 3: Incomplete (<60% complete)
**Count:** 71 applications
**Typical Gaps:**
- Date Applied (missing in 87 drafts total)
- Salary Range (missing in 73 drafts)
- Location specifics (missing in 20 drafts)

**Status:** Preliminary research phase  
**Action:** Not yet submission-ready; requires additional research/enrichment

---

## SUBMISSION BLOCKERS ANALYSIS

### What's Preventing Submission?

**Post-Submission Fields (0/91 filled):**
- `Submitted Resume Path`: Path to tailored resume used—filled AFTER successful submission
- `Submission Evidence Path`: Screenshot/confirmation proof—filled AFTER successful submission
- `Submission Verified At`: Timestamp of verification—filled AFTER successful submission

**Pre-Submission Fields (partially filled):**
- `Date Applied`: 4/91 filled (4.4%) → **87 drafts need a date**
  - Top 3 have dates; ranks 4-20 missing (16/20)
  - Can use today or a target submission date

**Quality Gaps:**
- `Salary Range`: 18/91 (19.8%) filled → Most drafts don't capture compensation expectations
- `Response`: 6/91 (6.6%) filled → No follow-up responses yet (early-stage applications)

---

## TOP 20 COMPANIES PRIORITIZED FOR SUBMISSION

### Tier 1: Highest Completion (65.2%)
| Rank | Company | Role | Filled | Missing |
|------|---------|------|--------|---------|
| 1 | OpenEvidence | Software Engineer - Backend/Infrastructure | 15/23 | Resume Path, Evidence Path |
| 2 | Runway | Member of Technical Staff - Inference | 15/23 | Resume Path, Evidence Path |
| 3 | Decagon | Senior Software Engineer, Infrastructure | 15/23 | Resume Path, Evidence Path |

**Quick Win:** These 3 can submit with ~20 minutes of work per application (just need to execute submission and capture evidence).

### Tier 2: High Completion (60.9%)
| Rank | Company | Role | Missing |
|------|---------|------|---------|
| 4-6 | Inferact, PaleBlueDot AI, Flapping Airplanes | Various | Add Date, then submit |
| 7-10 | Coalition, Mitre Media (2x), nooro | Various | Add Date, then submit |
| 11-20 | SKYCATCHFIRE, TELUS, Welo, BNSF (3x), Marketerx, Exaware, Fluence, A.Team | Various | Add Date, then submit |

**Effort:** 15 minutes per application (set date + execute submission).

---

## AUTOMATION INFRASTRUCTURE FOUND

### Available Submission Scripts

1. **`ci_submit_pipeline.py`** (255 KB)
   - Primary automation for form submission
   - Supports: Ashby, Greenhouse, Lever (via Playwright)
   - Features:
     - Reads tracker rows with `Status=ReadyToSubmit`
     - Pulls answers from `application_answers.md`
     - Fills forms automatically
     - Captures submission screenshot as evidence
     - Updates tracker with success status
   - Status: Ready to use

2. **`prepare_ci_ready_artifacts.py`** (13 KB)
   - Pre-submission artifact generation
   - Creates tailored resumes/cover letters
   - Identifies which applications are ATS-compatible
   - Status: Ready to use

3. **`ralph_loop_ci.py`** (72 KB)
   - GitHub Actions integration
   - Runs entire submission pipeline in CI
   - Auto-creates PRs with tracker updates
   - Status: CI-ready

### Supporting Templates

**Resume Variants Available:**
- `Igor_Ganapolsky_Sr_AI_Software_Engineer_v3` (PDF, DOCX, HTML)
- `Igor_Ganapolsky_AI_Systems_Engineer_2026-02-17` (PDF, DOCX, HTML)
- `Igor_Ganapolsky_Senior_ML_Engineer_Enhanced` (DOCX)
- `Igor_Ganapolsky_Discovery_Resume` (DOCX)

**Pre-Written Application Answers:**
- File: `application_answers.md` (3+ KB)
- Contains: Personal info, visa status, company-specific answers (Owner.com, Automattic, etc.)
- Coverage: Standard answers for common screeners + 2+ company-specific response sets
- Status: Can be extended with more companies

**Cover Letters:**
- `Cover_Letter_DWave.txt/pdf/docx`
- `Cover_Letter_Anyscale.txt`
- Status: Company-specific variants exist; can be reused/adapted

---

## RECOMMENDATIONS

### Immediate Actions (Next 24 hours)

1. **Add Date Applied to Top 20**
   - Use today's date (2026-03-02) or target submission dates
   - Quick CSV edit: 5 minutes

2. **Trigger Submission for Top 3**
   - Verify `application_answers.md` has Runway, OpenEvidence, Decagon entries
   - Run: `python3 Resume/scripts/ci_submit_pipeline.py --source=tracker --limit=3`
   - Expected: 3 applications submitted + tracker updated + evidence captured
   - Effort: 20 minutes (includes setup)

3. **Run Artifact Preparation**
   - Check which of top 20 have supported ATS types
   - Run: `python3 Resume/scripts/prepare_ci_ready_artifacts.py`
   - Expected: Tailored resumes generated, tracker marked for submission
   - Effort: 10 minutes

### Short-term Actions (This week)

1. **Batch Submit Top 20**
   - Set all to `ReadyToSubmit` status after artifact prep
   - Run pipeline: `python3 Resume/scripts/ci_submit_pipeline.py`
   - Expected: 17 applications submitted in parallel
   - Effort: 30 minutes execution + monitoring

2. **Extend Application Answers**
   - Add entries to `application_answers.md` for companies in ranks 4-20
   - Copy existing answers; tailor company-specific section
   - Effort: 2 hours (5 min per company)

3. **Research Remaining 71 Drafts**
   - Prioritize by remote likelihood + salary range interest
   - Backfill salary range + location for highest-priority targets
   - Effort: 3-4 hours (depends on priority)

### Automation Setup (Optional but Recommended)

Enable GitHub Actions in repo → run `ralph_loop_ci.py` to:
- Submit all `ReadyToSubmit` applications in CI
- Auto-update tracker on success
- Create PR with submission evidence
- No manual submission required

---

## FIELD INTERPRETATION

**Why "Submitted Resume Path" / "Submission Evidence Path" are 0%:**
- These are **post-submission fields**
- Set AFTER successful application
- `Submitted Resume Path`: Path to resume version uploaded (e.g., `resumes/Igor_...v3.pdf`)
- `Submission Evidence Path`: Screenshot of confirmation page (e.g., `applications/Company/submissions/screenshot.png`)
- **Not a blocker**—they're filled by automation or manually after submission

**What Actually Blocks Submission:**
1. Missing `Date Applied` (87 drafts) → Prevents tracker from showing submission intent
2. No pre-written answers in `application_answers.md` → Prevents form automation
3. No tailored resume generated → May require manual upload

---

## SUMMARY TABLE

| Metric | Value | Status |
|--------|-------|--------|
| Total Drafts | 91 | On track |
| Ready to Submit (0% effort) | 3 | Complete |
| Tier 2 (5-30 min each) | 17 | High priority |
| Needs Research | 71 | Backlog |
| Automation Available | Yes | Ready to use |
| Blocker: Date Applied | 87/91 | Easy fix (5 min) |
| Blocker: Post-Submission | 91/91 | Expected (auto-filled) |

---

## FILES REFERENCED

- Tracker: `/Users/ganapolsky_i/workspace/git/igor/Resume/applications/job_applications/application_tracker.csv`
- Submission Script: `/Users/ganapolsky_i/workspace/git/igor/Resume/scripts/ci_submit_pipeline.py`
- Artifact Prep: `/Users/ganapolsky_i/workspace/git/igor/Resume/scripts/prepare_ci_ready_artifacts.py`
- CI Pipeline: `/Users/ganapolsky_i/workspace/git/igor/Resume/scripts/ralph_loop_ci.py`
- Answers Template: `/Users/ganapolsky_i/workspace/git/igor/Resume/applications/job_applications/application_answers.md`
- Resumes: `/Users/ganapolsky_i/workspace/git/igor/Resume/resumes/` (4 variants)
- Cover Letters: `/Users/ganapolsky_i/workspace/git/igor/Resume/cover_letters/` (2+ variants)

