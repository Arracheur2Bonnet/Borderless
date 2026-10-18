# Final compliance, rubric and coherence audit

Audit date: night of 2026-10-18 to 19 (about 40 hours before the deadline). Repository state: `22b144e`, clean tree.
Deadline: **2026-10-20 23:59 Beijing time (UTC+8) = 17:59 CEST = 15:59 UTC**. Registration and submission share it.
Report only: nothing else in the repository was edited. The machine-readable task list is `docs/review/final_plan.json`.

What was run for this audit:

- `scripts/verify_all.py --report-dir <scratch>`: ALL CHECKS PASSED, 5 passed, 0 failed, 0 skipped (pytest 829 passed, 7 xfailed; 20/20 missions; golden path PASS; security report 69/69 and 29/29, matches the committed JSON; evidence identical). The tree stayed clean.
- `scripts/golden_path.py`: the first reply is word for word the one quoted in `README.md` lines 30-35. Final EUR 600.00, USD 196.74, 3 linked receipts.
- 18 live probes of `handle_message` (rules parser, fresh sandbox each), quoted in Section 3 and Section 4 where relevant.
- Per-folder test collection: adversarial 69, benign 29, heldout 73, heldout_v2 105, audit 49, regression 21, scenarios 21, unit 469 (agent 200, ledger 43, policy 183, ui 43): 836 in total, as every document says.
- Every Wilson interval in the documents was recomputed (69/70, 66/70, 26/30, 24/30, 15/15, 58/59, 23/30, 0/5, 54/55, 68/68, 6/6): all correct.
- Every commit hash cited in a judge-visible file was checked with `git cat-file -e`. All exist except `977b936`, `7e5db05` and `167fb64`, which are cited only in the integrator notes of `docs/review/anti_slop.md` and `docs/review/judge_panel.md` as old ids that no longer exist. That is accurate, but those notes tell a reader that the history was rewritten.
- The official templates were downloaded from the site's own bundle (paths below) and read.

---

## 1. Official requirements, item by item

### 1.1 Sources

| Id | Source | What it is |
|---|---|---|
| PDF | https://www.its.ac.id/international/wp-content/uploads/sites/66/2026/10/PBCSF-Appendix-2-Problem-Description-and-Competition-Rules-for-International-Track.pdf | 4-page flyer: theme, topics A-F, scoring, eligibility, submission checklist, review dates, prizes, contact |
| ATT2 | https://fintechathon.g-ican.com/templates/international-topics-rules-en.docx | "Attachment 2 · International Track — Topics & Rules", the text version of the PDF (linked from the site's international participation page) |
| ATT3 | https://fintechathon.g-ican.com/templates/international-registration-form-en.xlsx | "Attachment 3 · Registration Form (English Only)" |
| SITE | https://fintechathon.g-ican.com/#/en (SPA; text read from its bundles `js/chunk-4516c2dd.7f79eeba.js` and `js/chunk-29f967ea.5bd7744c.js`) | International flow, rules, scoring panel, participation instructions, download list |
| TD | https://fintechathon.g-ican.com/templates/international-technical-document-en.docx | Technical Document template (site label: "DOCX · Required") |
| SSA | https://fintechathon.g-ican.com/templates/international-security-self-assessment-en.docx | Security Self-assessment template ("DOCX · Optional") |
| EE | https://fintechathon.g-ican.com/templates/international-execution-evidence-en.docx | Execution Evidence template ("DOCX · Optional") |
| VID | https://fintechathon.g-ican.com/templates/demo-video-guide-en.docx | Demo Video guide ("DOCX · Required") |
| RDM | https://fintechathon.g-ican.com/templates/source-code-readme-template-en.md | Source Code README template ("MD · Optional") |
| PPT | https://fintechathon.g-ican.com/templates/presentation-template.pptx | Presentation template ("PPTX · Required"): 4 slides, 16:9, cover "作品名 / 团队名" (project name / team name), contents, section title, "THANKS". The artwork still says **2025**. |
| NOTES | `docs/notes/competition_notes_fr.md` | Our own notes from the site (French). |

`templates/team-information-form.xlsx` is also listed on the site's international flow as "Registration Form · XLSX · Required", but the file is empty (no field labels). The international participation page itself says: "please complete Attachment 3: Registration Form and return it to this mailbox". Use ATT3.

### 1.2 Where the sources disagree (decide once, then follow the stricter reading)

| Topic | Source A | Source B | Decision |
|---|---|---|---|
| Demo video | PDF / ATT2: "Demo video (optional, no longer than 5 minutes)" | SITE: "Demo video, up to 5 minutes (Required)"; VID: "Required for the International AI Track. The video must be no longer than five minutes." | **Treat as required.** |
| Deadline | PDF / ATT2: "October 20, 2026, 23:59 (Beijing Time, UTC+8)" | SITE: "by 24:00 on 20 October (UTC+8)"; NOTES: "24:00" | **23:59 UTC+8 = 17:59 CEST.** The PDF's "17:00 London (BST)" and ATT2's "16:00 UTC" are inconsistent with 23:59 UTC+8; ignore them. Aim to send by 20 Oct 12:00 CEST. |
| Contact mailbox | SITE and ATT3: "fintechathon@ican-x.com" | ATT2: "info@ican-x.com"; PDF: "wangyr@pbcsf.tsinghua.edu.cn" | Send to **fintechathon@ican-x.com** (the address ATT3 tells you to use). Optionally cc info@ican-x.com. |
| Bonus | PDF / ATT2: "Challenge bonus (on-chain exploration): 0-5 points" | SITE: "A capped 0–5 point challenge/implementation bonus rewards advanced topics or demonstrated real-world deployment." | Topic C cannot get the on-chain part. A sandbox demo is not "real-world deployment"; do not claim it. A public hosted demo is still worth doing (Section 4), but expect 0 bonus. |
| Eligibility wording | PDF: "with 60% non-Chinese nationals, major, or year of study" (garbled) | ATT2 / SITE: "no restrictions on nationality, major, or year of study" plus the 60% non-Chinese rule | Follow ATT2 / SITE. |
| Award name | NOTES: "Best Creative Award" | ATT2 / PDF: "Best Innovation Award RMB 20,000" | ATT2. |
| Scenario list | NOTES section 3 lists "Smart Transfers, Bill Analysis, Wealth, Card, Subscriptions, Cross-scenario" | Those six are the **domestic** "Banking AI Agent" scenarios. The international track has topics A-F; ours is Topic C. | Our missions are labelled with the domestic names (Section 3, C13). Relabel to Topic C. |

### 1.3 Checklist

Status: met / partly / missing / unknown. "Artefact" is the exact path in this repository.

| # | Requirement | Source and quote | Our artefact | Status | What to change |
|---|---|---|---|---|---|
| R1 | Team of 2-5 full-time students at institutions outside China (incl. HK, Macao, Taiwan) | ATT2: "Teams of 2–5 students"; "overseas universities ... established in countries other than China (including Hong Kong, Macao, and Taiwan)" | none in repo; git author is one person (`Arracheur2Bonnet`) | **unknown** | Confirm at least one more eligible member before registering. A one-person entry is ineligible. |
| R2 | At least 60% non-Chinese nationals, rounded up | SITE: "Non-Chinese nationals must account for at least 60% of each team ... a 3-member team must include at least 2 non-Chinese members" | none | **unknown** | Check nationalities before sending ATT3. |
| R3 | One team per person, across both tracks | ATT2: "Each participant may join only one team across both the Domestic and International Tracks" | none | unknown | Each member confirms. |
| R4 | Every member 18 or older (or guardian consent) | ATT3 instruction 8: "Participants must be 18 or older; minors must attach a consent form" | none | unknown | Each member confirms. |
| R5 | Register by email with ATT3, in English | ATT3 instruction 5: "email the completed form to ... fintechathon@ican-x.com, subject: International Track Registration – Team Name – Team Leader Name. A confirmation reply will be sent within 48 hours." | none | **missing** | Send **today**: the 48-hour confirmation must arrive before the deadline. |
| R6 | ATT3 content | ATT3 sheet "I. Team Information": "Team Name *", "Topic (A–F, choose one) *", "Entry Title (provisional)", "Advisor (optional)", "Proposal Summary (optional, 100–200 words)", "Team Email *"; per member: role, full name, gender, age, nationality, passport/ID type and number, student ID, university, major, year, passport bio-page image, proof of enrollment attachment, email, phone; leader signature and date | none | **missing** | Fill it. Topic = **C**. Entry title = "BorderlessGuard". A 150-word summary is in Section 1.5. Attach passport bio-page images and enrollment proofs (instruction 4 and 9). |
| R7 | Topic declaration | SITE: "select one of six topics"; ATT3 "Topic (A–F, choose one) *" | `README.md` line 12, `deliverables/technical_document.md` line 3 say Topic C | partly | Also put "Topic C: Cross-border & Student Finance Assistant" in the header fields of every DOCX and on the deck cover. |
| R8 | English | ATT2: "submissions, presentations, and reviews are all conducted in English"; SITE: "submitted materials must include an English version" | all deliverables are English; `docs/notes/competition_notes_fr.md` and `docs/notes/perplexity-research-prompt.md` are French | partly | Exclude `docs/notes/` from the public repo or the archive (they are not deliverables). |
| R9 | Technical documentation (required) with system architecture, core algorithm description, security design | PDF / ATT2: "Technical documentation (system architecture, core algorithm description, security design)"; SITE: "Technical Document · DOCX · Required" | `deliverables/technical_document.md` (979 lines, Markdown) | **partly** | Content is strong and covers everything, but it is Markdown, has no team/track/date header and does not follow the template's five sections. Produce `TechnicalDocument.docx` (and a PDF) on the TD template, mapped as in Section 2.4. Render the architecture as an image (Mermaid does not render in Word). |
| R10 | TD template header | TD: "Team name / Competition track / Submission date — Please complete" | none | **missing** | Fill: team name; "International AI Track, Topic C"; "2026-10-20". |
| R11 | TD section order | TD: "01 Project Overview", "02 System Architecture", "03 Agent Design", "04 Security Design", "05 Deployment and Testing" | technical_document.md has 14 sections in another order | **partly** | See mapping in 2.4. |
| R12 | Presentation deck, final presentation no longer than 10 minutes (required) | PDF: "final presentation no longer than 10 minutes"; SITE: "Presentation deck, up to 10 minutes (Required)", template "PPTX · Required" | stage deck: `deliverables/pitch/keynote/` (15 slides, HTML); backup `deliverables/pitch/BorderlessGuard_deck.pptx` (14 slides); script `deliverables/pitch/speaker_script.md` (about 8:47) | **partly** | Timing is met. But (a) `deliverables/pitch/keynote/index.html` is **not in git** (`.gitignore` line 11 `*.html`), so the keynote does not exist for anyone who clones the repo and `build_keynote.py` fails on a clean checkout; (b) the PPTX differs from the stage deck (14 vs 15 slides, no Qwen comparison slide); (c) no slide names the team; (d) the official template (cover with project and team name, THANKS slide) is not used. Submit one PPTX that matches the stage deck, plus a PDF. |
| R13 | Demo video, at most 5 minutes (treat as required) | VID: "The video must be no longer than five minutes" | script only: `deliverables/pitch/demo_video_script.md` (target 4:25, 4:45 with optional segment) | **missing** | Record it. Script is ready and its labels all exist in the UI (checked). |
| R14 | Video structure | VID "01 Recommended Structure": "Project and team introduction"; "End-to-end demonstration of the core scenario"; "Safety mechanisms, innovation and results" | script has no team introduction and states no results (line 72: "Do not state evaluation percentages in the video") | partly | Add a 10-15 s opening card with project name, Topic C and team names. Add one closing card with the blind result (69/70 attacks, 0 wrong payments) since the guide asks for "results". Keep under 5:00. |
| R15 | Video quality | VID "02 Video Quality": "Use 16:9 landscape and preferably 1080p"; "Make interface text, narration and English subtitles clearly legible" | script line 10: 1920x1080; line 25: "Burned-in English captions" | met (planned) | Check legibility at 1080p; the UI at 100% zoom may be small, consider 110-125%. |
| R16 | Video final check | VID "03 Final Check": "No copyrighted or sensitive material"; "Suggested filename: Track-TeamName-DemoVideo" | script line 76 (fictional watchlist) | partly | No music unless licence-free. File name e.g. `InternationalAI-<TeamName>-DemoVideo.mp4`. |
| R17 | Source code with README, GitHub link, deployment instructions, test cases (optional) | PDF: "with README, GitHub repository link, deployment instructions, and test cases" | `README.md`, `tests/` (836), `Dockerfile`, `.github/workflows/ci.yml` | **partly** | `https://github.com/Arracheur2Bonnet/Borderless` returns **HTTP 404** to an anonymous request (checked 2026-10-18), so the clone commands (README lines 61 and 72) and the CI badge (line 3) fail for a judge. Make the repository public, or give the organisers access, before submitting. |
| R18 | README template sections | RDM: "1. Project Overview (Track, Team name, Project name, One-line summary)", "2. System Architecture", "3. Environment and Dependencies (OS, runtime, main deps, environment variables)", "4. Installation and Execution", "5. Testing", "6. Repository and Deployment (GitHub, live demo, deployment)", "7. Security Notes" | README has all the substance except team name, a single environment-variable table and a live-demo line | partly | Add a short "Project overview" block (Track, Team, Project, one line), an env-var table (`BG_PARSER`, `BG_LOCAL_MODEL`, `BG_RECEIPT_KEY`, `ANTHROPIC_API_KEY`, `BG_LLM_STATS`) and a "Repository and deployment" block. |
| R19 | Security self-assessment, permission tiers and known-risk list (optional) | PDF: "permission-tier implementation and known-risk list"; SSA sections "01 Permission Model", "02 Security Tests", "03 Data and Privacy", "04 Known Risks" | `deliverables/security_self_assessment.md` | **partly** | Strong on tiers, tests and risks. Missing: a roles table (customer, bank operator who unlocks, LLM parser with no permission, FX provider untrusted), rollback statement (cancel or reject before execution; no reversal after), and the whole **"03 Data and Privacy"** section (data provenance: synthetic seed data and fictional watchlist; consent; minimisation toward the LLM; storage in memory or SQLite; deletion on reset; audit redaction). Produce the DOCX on the SSA template. |
| R20 | Execution evidence: for non-on-chain topics, simulated-account or sandbox operation logs and demo video (optional) | PDF: "for non-on-chain topics: simulated-account/sandbox operation logs and demo video"; EE sections "01 Evidence Summary", "02 Logs or Transactions" ("Timestamp, action, result and relevant screenshot/link"), "03 Reproduction Guide" | `deliverables/execution_evidence.md`, `deliverables/evidence/T01..T20*.jsonl`, `summary.json`, `deliverables/screenshots/` | partly | Content is there. Produce the DOCX on the EE template with a short table per key mission (timestamp, action, result, screenshot) and link the video. |
| R21 | Third-party data, code, components, APIs properly licensed | SITE rule 6: "Data, code, open-source components, APIs and other third-party materials used in an entry must be legally authorised and comply with applicable open-source licences and data-use terms." | `README.md` lines 238-249, `LICENSE` (MIT) | partly | The table lists 4 packages. Add: python-pptx (MIT), Pillow (MIT-CMU), Playwright (Apache-2.0), **PyMuPDF (AGPL-3.0)**, Ollama (MIT), **Qwen2.5-7B-Instruct (Apache-2.0)**, **Qwen2.5-3B-Instruct (Qwen Research License, non-commercial)**, and the Anthropic API terms for the optional Claude path. State that all data is synthetic and the watchlist fictional. |
| R22 | Independent work, no plagiarism | ATT2 section 8: "Entries must be completed independently, free of plagiarism or IP infringement" | every commit carries `Co-Authored-By: Claude Opus 5.5`; `.claude/agents/*.md` define six AI agents; README says separate AI agents wrote the blind set and the audit | partly | The rules do not forbid AI tools. Do not leave the reader to infer it: add one plain "How this was built" paragraph (team directed and reviewed; AI coding assistants wrote much of the code and documents; which AI agents wrote which test sets). Name the human authors. |
| R23 | IP stays with the team; organiser may use for non-commercial promotion | ATT2 section 8 | MIT licence | met | Nothing to do. |
| R24 | Updates allowed until the deadline, latest version counts | PDF: "Submissions may be updated any time before the deadline; the latest version received prevails." | n/a | n/a | Send a complete first version early on 20 Oct, then an update only if needed. |
| R25 | Scoring | SITE: Task Completion 40% "Completion of scripted tasks in a simulated environment"; Security & Compliance 30% "Defence against induced transfers, data extraction, privilege escalation and prompt injection; serious security failures trigger substantial deductions"; Innovation & Interaction 30% "Scenario novelty, real-world value, dialogue efficiency, clarification, correction and fallback capability" | see Section 2 | n/a | n/a |
| R26 | On-chain evidence | PDF: "for the on-chain topic: real on-chain transaction hashes" | none | not applicable | Topic C. Keep "on-chain anchoring" listed as future work only (it is, in all documents). |
| R27 | LLM or API usage | No rule restricts which model or API is used, beyond licensing (R21) | Claude optional, Qwen local optional, rules parser default | met | Keep "Claude path not evaluated live" visible (it is). |
| R28 | File formats and naming | Templates are DOCX and PPTX; VID suggests "Track-TeamName-DemoVideo". No page limit is stated for the international track (the 8-page limit on the site is for the domestic data-analysis track). | Markdown and HTML only | **missing** | Send DOCX + PDF for each document, PPTX + PDF for the deck, MP4 for the video. Use one naming pattern: `InternationalAI-TopicC-<TeamName>-<Item>.<ext>`. |
| R29 | Finals attendance | PDF: "At least one member of each finalist team must attend in person" (Shenzhen, 20-23 Nov) | n/a | unknown | Confirm someone can travel (passport, visa). |

### 1.4 What the submission email should contain

Subject (registration, prescribed by ATT3): `International Track Registration – <Team Name> – <Team Leader Name>`.
Subject (works, not prescribed; keep the same pattern): `International Track Submission – Topic C – <Team Name> – BorderlessGuard`.

Attachments, or one download link (Drive or OneDrive) for anything over about 20 MB:

1. `InternationalAI-TopicC-<Team>-TechnicalDocument.docx` and `.pdf` (required)
2. `InternationalAI-TopicC-<Team>-Deck.pptx` and `.pdf` (required)
3. `InternationalAI-TopicC-<Team>-DemoVideo.mp4`, under 5:00, 1080p, English captions (treat as required)
4. `InternationalAI-TopicC-<Team>-SecuritySelfAssessment.docx`
5. `InternationalAI-TopicC-<Team>-ExecutionEvidence.docx`
6. Source: the public GitHub URL plus a `git archive` zip of the final commit (never a zip of the working folder: it holds untracked research files, the saved site copies and a French PDF).

Body: team name, topic C, entry title, members with universities, the GitHub URL, the one-command check (`python scripts/verify_all.py`), the demo OTP `246810`, and one line saying everything runs offline in a sandbox.

### 1.5 Ready-to-paste proposal summary for ATT3 (151 words)

> BorderlessGuard is an AI money agent for international students. From one sentence ("pay my November tuition and rent, keep 600 euros"), it plans a currency purchase and two payments, and the student approves each one with a tap or a one-time code. The language model only turns the sentence into a structured request: it has no payment tool and never sees an account number, a balance or a document. Plain code checks that every amount and name it proposed appears in what the student wrote, takes account numbers only from saved payees, simulates the plan and applies 13 named rules (limits, reserve, new-payee window, split payments, sanctions). Every action gets a hash-linked receipt, and the student keeps a signed copy of the latest one. In a blind test written by a separate AI agent, 69 of 70 attacks were blocked with zero wrong payments. Everything runs offline in a sandbox.

---

## 2. Rubric: where the evidence is, how a judge would score it now, what moves it

Scores are an estimate of a careful online reviewer with 30-45 minutes who reads the PDF or DOCX, skims the deck and watches the video. The video does not exist yet; the "now" column assumes it is recorded as scripted.

### 2.1 Task Completion (40)

Official text: "Completion of scripted tasks in a simulated environment for non-on-chain topics".

| Evidence | Path |
|---|---|
| 20 scripted missions, 20/20, exact balances, 7 global invariants, one JSONL log each | `deliverables/task_completion.md`, `deliverables/evidence/`, `scenarios/tasks.py` |
| Golden path transcript and receipts | `deliverables/execution_evidence.md` section 3, `scripts/golden_path.py` |
| One-command check, CI on Ubuntu and Windows, Docker | `scripts/verify_all.py`, `.github/workflows/ci.yml`, `Dockerfile` |
| Multi-step planning (FX sized to the reserve, ordering, out-of-order confirmation) | technical_document.md 5.5, 10; missions T01, T07, T11 |

Judge now: **29-32 / 40.** Strong and reproducible, but three things cap it. The organisers' own scripted tasks for Topic C will be about remittances, tuition, credit building and multi-currency management (ATT2 Topic C text): we cover tuition and remittances well, multi-currency only as EUR/USD, credit building not at all, and scheduled or recurring payments not at all (probe: "schedule 100 euros to mom every month" gives "Sorry, I didn't get that"). There is no interface for an external harness (only Python and the Streamlit UI). And the missions are labelled with the domestic track's scenario names ("01 Smart transfers", `scenarios/tasks.py` line 94 and others), which a judge reads as not having read the Topic C brief.

Highest-value changes: record the video (it is the evidence most judges will actually watch); relabel the missions to Topic C areas and add a one-table "Topic C coverage" (remittance, tuition, multi-currency, credit building: covered / partly / not) in the technical document; add a scripted-task interface (feature 5); add one Topic C capability that is visibly missing (feature 7 or 8).

### 2.2 Security & Compliance (30)

Official text: "Defence against induced transfers, data extraction, privilege escalation and prompt injection; serious security failures trigger substantial deductions."

| Evidence | Path |
|---|---|
| Architecture where the LLM has no authority; grounding of every field | technical_document.md 3, 9.3; `bg/agent/grounding.py` |
| R0-R4 policy, 13 payment rules, fail closed | technical_document.md 6; `bg/policy/engine.py`; `bg/contracts/params.py` |
| Blind v2 (rules 69/70; Qwen 7B 66/70), Wilson CIs, frozen commits `44542f2`, `5868d83` | `deliverables/heldout_v2_results.md`, `deliverables/blind_llm/` |
| White-box audit, 12 findings, each with a test | `docs/review/security_audit.md`, `tests/audit/` |
| Development suites by attack family (F1-F5) | `deliverables/security_results.md` |
| Compliance controls: structuring, sanctions with homoglyph folding, IBAN mod-97, payee-name collision, FX price authority | technical_document.md 6; security_self_assessment.md 5 |
| Attack tab, 7 attacks live | `ui/attacks.py`; screenshot `deliverables/screenshots/06_attacks.png` |

Judge now: **25-27 / 30.** This is the entry's strongest dimension, and the honesty (caveats, the corrected v1 claim, the Qwen misses) makes the numbers believable. No serious security failure is known. What can still cost points: the Claude path is untested live (said on four slides and in every document); every post-blind fix is regression-tested only; the self-assessment has no data-and-privacy section; and the fixed OTP and missing authentication are visible (disclosed, sandbox).

Highest-value changes: Data and Privacy section plus a roles table (R19, cheap); a live Claude run on the frozen v2 cases (feature 3); Verification of Payee at add-payee time (feature 4); a fresh blind set after all fixes (feature 6) if time allows.

### 2.3 Innovation & Interaction (30)

Official text: "Scenario novelty, real-world value, dialogue efficiency, clarification, correction and fallback capability."

| Evidence | Path |
|---|---|
| Clarify, correct (new action, never an edit), cancel, affordability simulation, read-only rate quote, plan ordering | technical_document.md 5; screenshots 02, 07, 08 |
| Plain-language replies that explain the tier ("You added Jean Dupont only 20 hours ago ...") | golden path reply |
| Customer-held signed receipt copy, deletion test on a copy | technical_document.md 7; screenshot 05 |
| Guided demo with read-back ticks | `ui/guided.py`; screenshots g0-g6 |
| Local Qwen parser: 5/5 non-English requests | technical_document.md 9.2, 12.4 |

Judge now: **20-23 / 30.** Dialogue mechanics are better than most entries. Weak spots a judge will hit in the first minute of trying it: "hello" and "what can you do?" both get "Sorry, I didn't get that. Try: ..." (probe); "help me build a credit history in France" and "what did I spend last month?" get the same; the default parser is English only, and the persona lives in France ("envoie 50 euros à maman" fails offline). The novelty claim rests on architecture (guardrails outside the LLM), which judges have seen before; the receipts and the grounding check are the distinctive parts and should be named as such.

Highest-value changes: capability and small-talk fallback (feature 1); a hosted demo judges can open (feature 2); one Topic C feature with real-world value (features 7, 8, 9).

### 2.4 Mapping the technical document onto the official template

| Template section (in order) | Template bullet | Our content today | Gap |
|---|---|---|---|
| Header | Team name; Competition track; Submission date | none | Add. |
| 01 Project Overview | Problem, target users and selected topic | technical_document.md 1 (Problem and user); line 3 (Topic C) | Add one Topic C coverage table (remittance, tuition, multi-currency, credit building). |
| 01 Project Overview | Core value and innovation | technical_document.md 2 (Solution overview), scattered | Write 5 bullets: no-authority LLM, grounding of every field, deterministic policy with named rules, customer-held signed receipt copy, blind evaluation with frozen commits. |
| 02 System Architecture | Architecture diagram and component responsibilities | technical_document.md 3 (ASCII and Mermaid) | Render the diagram to PNG for Word. |
| 02 System Architecture | Models, tools, data sources and external services | technical_document.md 9.1, 9.2; params in 10 | Add a table: rules parser / Qwen2.5 7B via Ollama / Claude (optional, untested live); data = synthetic seed in `params.py`; no external service is called by default. |
| 03 Agent Design | Intent understanding, planning, tool use and state management | technical_document.md 4 (typed intents), 5 (dialogue), 9 (parsers) | Reorder only. |
| 03 Agent Design | Core algorithms and evaluation approach | technical_document.md 6 (policy), 7 (receipt chain), 9.3 (grounding), 11-12 (evaluation) | Keep 12 short in the DOCX (one table of layers) and link the full text. |
| 04 Security Design | Tiered permissions and confirmation for sensitive actions | technical_document.md 6, 5.4, 7 | none |
| 04 Security Design | Protection against prompt injection, privilege escalation and privacy leakage | technical_document.md 3 (trust boundary), 8 (audit redaction), 12.5 (audit) | Add a short privacy paragraph (same text as SSA 03). |
| 05 Deployment and Testing | Environment, deployment instructions, test cases and known limitations | technical_document.md 14, 11, 12.6.3, 13; README quick start | Add the commands from README "Quick start" and "Reproduce". |

The document is about 9,000 words. No page limit is stated, but a reviewer with 30 minutes will read two pages. Put a one-page executive summary first (problem, idea, five results, what it is not), then the five template sections, then an appendix with the long evaluation detail.

---

## 3. Coherence check

Each item: location A, location B, which is true (checked how), and the fix. Items C1-C10 are ones a judge can catch; C11-C24 are smaller.

| # | Location A | Location B | True | Fix |
|---|---|---|---|---|
| C1 | `deliverables/technical_document.md:290` "This is fixed in the final commit: a multi-payment message where any part has no exact amount ... now gets one clarifying question and no action" | `deliverables/technical_document.md:907` "several payments in one message where a part has no amount can be mis-split (Section 5.6): ... the reply does not flag the part it did not understand." | A. Live probe: "send 100 to mom and some money to the landlord" and "send 100 euros to mom and money to jean" both return "You asked for more than one payment and I could not read an exact amount for every part. ..." with no action. | Delete the last bullet of 13.4. In 5.6 replace "in the final commit" with "in `bb7981e`". |
| C2 | `deliverables/technical_document.md:798` "It audited the code as of commit `13dded5`" and `deliverables/security_self_assessment.md:3` "That audit read the code as of commit `13dded5`" | `git log`: the audit report and its tests were added in `2ce6035` together with the bg/ fixes, and `13dded5` (later) is the UI fix commit for AUDIT-6. `docs/review/security_audit.md:3` "fixed or mitigated afterwards in commits 2ce6035 and 13dded5". | The audit read the code before `2ce6035`, i.e. `615f260` plus uncommitted work (`security_audit.md:12` says orchestrator.py "is being edited in parallel"). | Say "the code as of `615f260` (with work in progress in `orchestrator.py`)". Also disclose that the audit tests and the fixes were committed together, so the "21 passed, 28 xfailed" state in `security_audit.md:8` is not reproducible from history. |
| C3 | `deliverables/pitch/keynote/README.md:7` "Open: double-click `index.html`" and commit `1498cc9` "offline HTML keynote (15 slides ...)" | `.gitignore:11` `*.html`; `git ls-files` has no `deliverables/pitch/keynote/index.html`; `build_keynote.py:54` reads it | The keynote's slide source is not in the repository. A clean clone cannot open the deck or run `build_keynote.py`. | Add `!deliverables/pitch/keynote/index.html` to `.gitignore` and commit the file. |
| C4 | `README.md:3`, `:12`, `:61`, `:72` link and clone `github.com/Arracheur2Bonnet/Borderless` | anonymous GET of that URL and of `api.github.com/repos/...` returns 404 | The repository is private or not published. | Make it public before submitting (user). |
| C5 | `deliverables/pitch/keynote/README.md:20` "Slide 12 ... has no script section yet: its notes are marked PROPOSED" | `deliverables/pitch/speaker_script.md:209-225` has S12; `notes.js` contains no "PROPOSED" | B. | Delete line 20 of the keynote README. |
| C6 | `deliverables/pitch/speaker_script.md:164`, `:311`, `:345` "Alt+Tab back to PowerPoint", "PowerPoint slide show running from slide 1" | `speaker_script.md:6-9` and `keynote/README.md`: the stage deck is the HTML keynote in a browser | The keynote. | Replace "PowerPoint" with "the keynote (browser)" in those three lines; regenerate `notes.js` (line 521 repeats it). |
| C7 | `speaker_script.md:216` (and `notes.js:823`) "It reads French and German: five of five." | `deliverables/blind_llm/qwen2.5_7b_heldout_v2/report.md`: N01-fr, N02-de, N03-es, N04-pt, N05-mix | Five languages incl. mixed. | "It reads French, German, Spanish and Portuguese: five of five." |
| C8 | `keynote/index.html:372` "One local model measured blind." | technical_document.md 12.4: Qwen2.5 7B **and** 3B were run blind (`deliverables/blind_llm/qwen2.5_3b_heldout_v2/`) | Two models of one family. | "One model family measured blind (Qwen 7B and 3B)." |
| C9 | `keynote/index.html:122` "Every action gets a **signed receipt**." | technical_document.md 7: receipts are hash-chained; only the anchor of the latest receipt is HMAC-signed | Receipts are linked, the head is signed. | "Every action gets a linked receipt; Lina keeps a signed copy of the latest." |
| C10 | `scenarios/tasks.py:94` etc. and `deliverables/task_completion.md` "01 Smart transfers", "02 Bill analysis", "06 Cross-scenario orchestration" | ATT2 Topic C: "remittances, tuition payments, credit building, and multi-currency management"; those six names are the domestic track's | Topic C. | Relabel: "Tuition payment", "Rent payment", "Family remittance", "Multi-currency (FX)", "Account overview", "Security: ...". Re-run `run_scenarios.py`, commit the regenerated evidence. |
| C11 | `deliverables/pitch/qa_prep.md:322` and `docs/review/judge_panel.md:37` "Qiang Yang (... ex-WeBank Chief AI Officer)" | NOTES line 195 (from the official site): "Chief AI Adviser, WeBank" | The site. | Fix the title, or remove the per-judge sections from anything public (see C21). |
| C12 | `deliverables/pitch/build_deck.py:560` slide 14: `python -m pytest tests/heldout_v2 -q  # blind v2 set` | technical_document.md 12.6.1: "v2 is no longer blind for the rules parser" | Regression. | "# held-out v2 (regression since the fixes)". |
| C13 | `bg/contracts/params.py:37` "0.5% of the sell amount" | technical_document.md 10: "fee 11.96 USD included: 0.5% of 2,391.30 USD, the amount before the fee"; 0.5% x 2,403.26 = 12.02 | The document (11.96 is what the code computes). | Fix the comment: "0.5% of the sell amount before the fee". |
| C14 | `deliverables/pitch/speaker_script.md:9` "The older PowerPoint builder `build_deck.py` expects 14 sections" | commit `22b144e` message: "backup pptx maps the 15 sections onto 14 slides" | Both true, but the submitted PPTX then differs from the talk. | Resolve by submitting a PPTX generated from the keynote (plan task P0-deck). |
| C15 | README line 183 and technical_document.md 12.2: v1 written by "a separate author" | README line 207 and 12.3: v2 written "by a separate AI agent (not a human)" | Unknown for v1. | State who wrote v1 (human or AI agent). If it was an AI agent, say so in the same words as v2. |
| C16 | technical_document.md 12.2 "The suite was run once against the unmodified system" | commit `839f146` "Held-out evaluation, deliverables, deck; fix relative amounts, overview, guard patterns": the v1 cases and fixes landed in one commit | The pre-fix run cannot be shown from history (v2 can, via `44542f2`). | Add one sentence: "v1 results and the first fixes were committed together in `839f146`; only v2 has a frozen pre-fix commit." |
| C17 | `README.md:87` "docker run --rm borderlessguard python scripts/verify_all.py   # the same checks inside the image" | `.dockerignore` excludes `.git`; `verify_all.py:116-117` skips the evidence step without git | Inside Docker: 4 passed, 1 skipped. | "(the evidence-diff step is skipped in the image: no git)". |
| C18 | `README.md:238-247` licence table: pydantic, anthropic, streamlit, pytest | `requirements-dev.txt`: python-pptx, pymupdf, playwright; the Qwen weights used in the evaluation | Incomplete. | Add the rows listed in R21. PyMuPDF is AGPL-3.0 and Qwen2.5-3B is under the Qwen Research License (non-commercial); both are fine for this use but must be stated. |
| C19 | `deliverables/pitch/speaker_script.md:3` "about 8:47 on stage (8:17 of speech" | the table at lines 43-59 sums to 1,037 words = 8:18 at 125 wpm | Rounding only. | None needed. |
| C20 | `deliverables/pitch/speaker_script.md:261` "Part 2. The 2-minute version" | same section: "About 280 words (2:15)" | 2:15. | Cut about 30 words or rename "2-minute (2:15) version". |
| C21 | `docs/review/judge_panel.md:16-22` "Tests ... (629 collected) ... Reproduced: 621 passed, 8 xfailed", "Demo video MISSING", "No Dockerfile", "Slide 11 cites commit 44542f2, which does not exist" | README / technical_document: 836 collected, 829 passed; Dockerfile exists; `44542f2` exists | Current state. The panel file is a historical mock review. | Move `docs/review/judge_panel.md`, `anti_slop.md`, `differentiation.md` and `docs/notes/` out of the public branch (or prefix each with "Historical, superseded; numbers are from 2026-10-17"). They also profile the real judges by name, which reads badly if a judge opens them. Update `tests/regression/test_judge_findings.py:1` and technical_document.md 12.6.1, which cite `judge_panel.md`. |
| C22 | `docs/review/anti_slop.md:1`, `judge_panel.md:1` "every commit hash in this file was mechanically replaced ... old ids (977b936, 7e5db05, 167fb64)" | README:223 "The history was re-created from local work on 2026-10-17" | Both true. | Fine if the files stay; moot if C21 is applied. |
| C23 | `deliverables/pitch/demo_video_script.md:42` "A card 'Send 50.00 EUR to Samira Haddad' appears" | live run: the chat line is "Send 50.00 EUR to Samira Haddad. One tap to approve."; the card title is "Pay 50.00 EUR to Samira Haddad (family)" | The card says "Pay". | "A card 'Pay 50.00 EUR to Samira Haddad (family)'". |
| C24 | Speaker S1 / keynote slide 1: "Her rent invoice arrives as a PDF" | `params.py:92-105` the seeded invoice is plain text named `rent_invoice_nov_2026.pdf` | A text document with a .pdf name. | Acceptable as a story; if asked, say the sandbox stores the extracted text. |

Checked and consistent (no action): test counts (829 / 7 / 836, and every per-folder count); 69/69, 29/29; 20/20, 1.60 turns, 2.05 confirmations (`summary.json`); all Wilson intervals; 137 Qwen calls with 0 fallbacks and 2.6 s mean; 97/105 blind and 103/105 regression; 13 / 12 / 5 / 4 / 5 rule counts; thresholds 1,500 / 2,500 / 4,000 / 5,000 / 72 h / 3 codes against `params.py`; FBI IC3 USD 2.77 bn; every UI label quoted in `demo_video_script.md` exists in `ui/app.py`, `ui/guided.py` or `ui/attacks.py`; the golden-path reply; Claude call settings (`claude-opus-5-5`, adaptive thinking, effort low, JSON schema, no tools) match `bg/agent/claude_parser.py:80-87`; local parser timeout 30 s; all cited commit hashes except the three disclosed old ids.

---

## 4. The 10 changes that would most raise the ranking (after the P0 fixes)

The P0 items in `final_plan.json` (registration, public repo, DOCX and PPTX formats, the video, the contradictions above) come first: they decide whether the entry passes screening on 21 Oct, and they add up to about 16 hours in `final_plan.json` (registration, video recording and the three DOCX conversions are the big ones). The features below are ranked by expected points per hour. Each is testable offline except where noted. Realistically, items 1-5 fit; 6-10 are listed so the choice is explicit.

| Rank | Feature | Rubric gain (estimate) | Effort | Risk | Acceptance test |
|---|---|---|---|---|---|
| 1 | **Capability, small-talk and out-of-scope fallback.** New `Help` handling for "hello", "hi", "thanks", "what can you do?", "help": reply with the four things the agent does and one example each, plus quick-reply chips. Explicit, honest replies for Topic C requests that are not implemented ("I can't schedule recurring payments yet; I can pay it now, or remind you ...", "I can't check or build a credit score; here is what I can show ..."). No action is ever created. | Innovation & Interaction +1.5 to 2.5 (the rubric names "fallback capability"; the current "Sorry, I didn't get that" is the first thing a judge sees) | 2 h | Low: parser and reply text only; no policy change | New tests: 12 phrasings, each returns a non-generic reply, `actions == []`, no security event; full suite still 829+ passed; dev suites 69/69, 29/29 unchanged. |
| 2 | **Public hosted demo** (Hugging Face Spaces with the existing Dockerfile, or Streamlit Community Cloud once the repo is public) with `BG_RECEIPT_KEY` set as a host secret; link in README, deck cover and document header. | Innovation & Interaction +1 (judges try it in one click); Task Completion +0.5; bonus probably 0 (it is still a sandbox) | 1.5-2 h | Low to medium: cold starts; each Streamlit session already builds its own backend, so users do not share state | From a fresh browser on another network: `?guided=1` runs 6/6 green ticks; the signing-key notice is absent (key set); the page says "SANDBOX · no real money". |
| 3 | **Live Claude parser run on the frozen v2 cases** (`scripts/eval_parser.py --parser claude --suite tests/heldout_v2 --runxfail`) and on the dev suites; commit the reports unchanged before touching the prompt; report with Wilson CIs. Removes the caveat that appears on four slides. Frame it honestly: first run for this parser, but the rest of the system was already fixed against v2. | Security & Compliance +1 to 1.5; Innovation +0.5 | 1.5-2 h plus API cost (needs `ANTHROPIC_API_KEY`, user) | Medium: results may be worse than Qwen; publish them anyway | `deliverables/blind_llm/claude_heldout_v2/{report.md,junit.xml,llm_calls.jsonl}` committed in one commit before any prompt change; README, technical document, deck and Q&A updated from the report. |
| 4 | **Verification of Payee at add-payee time**, the check EU payment providers must run since 9 Oct 2025 under the Instant Payments Regulation. Simulated name-to-IBAN directory in the sandbox: match / close match ("did you mean Jean Dupont?") / no match / not available. No match keeps the step-up and adds a plain scam warning plus one question ("Did someone ask you to change bank details?"); a "yes" cancels. New rule `PAYEE_VERIFICATION` (R3). | Security & Compliance +1; Innovation (real-world value) +1 | 3-4 h | Medium: touches orchestrator, policy and contracts; docs must count rules again (add-payee goes from 5 to 6 rules) | Unit tests for the four outcomes; mission T21 (close match corrected by the user, then paid); dev suites and v2 unchanged; rule counts updated everywhere (`grep -rn "5 rules"`). |
| 5 | **Scripted-task interface for an external harness.** `python -m bg.cli` reads JSON lines (`{"op":"message","text":...}`, `{"op":"confirm","action_id":...,"otp":...}`, `{"op":"overview"}`) and writes one JSON turn per line; optional stdlib HTTP server with the same four endpoints on localhost. Document it in README section "For evaluators". | Task Completion +1 to 2 if the organisers drive entries with their own scripts; otherwise +0.5 for reproducibility | 3 h | Low: a thin wrapper over the existing `Orchestrator` interface | A JSONL file replays T01 through the CLI and ends at EUR 600.00, USD 196.74, 3 receipts; an HTTP test does the same; both run in `verify_all.py`. |
| 6 | **Fresh blind set v3** (40-60 cases) written by a separate agent with the same file restrictions as v2, aimed at LLM extraction and the new grounding step; frozen in its own commit before any fix; run on rules and Qwen 7B (and Claude if item 3 is done). | Security & Compliance +1 to 2 (the only post-fix generalisation estimate) | 5-6 h | High late in the schedule: it may expose failures with no time to fix. That is acceptable only if reported as found. | Commit "v3 frozen" containing cases plus results, with no change to `bg/` in the same commit; reports in `deliverables/blind_v3/`; numbers with Wilson CIs in every document. |
| 7 | **Remittance cost transparency**: every FX card and the remittance reply show the cost against the bank's reference rate ("you pay 2,403.26 USD; fee 11.96 USD = 0.50%; no hidden FX margin in this sandbox"), and the affordability answer includes it. Optional extension: a MAD corridor for the mother's Moroccan IBAN (`params.py:56`), pay in dirhams with the same price authority. | Innovation (Topic C real-world value) +1; Task Completion +0.5 | 1.5 h for the cost line; +4-5 h for a MAD corridor | Low for the cost line; medium for a new currency (ledger, rates, FX_PRICE_CHECK, docs) | Card and reply tests; golden path text updated in README and keynote via `build_keynote.py`; for MAD, a mission that sends 300 EUR worth of MAD and checks balances to the cent. |
| 8 | **On-time payment statement for credit building**: from the customer's receipt chain, produce a signed statement of executed rent and tuition payments (dates, amounts, payee names, receipt ids, anchor) that a landlord or a bank can verify with the bank's verify function. Honest limits: HMAC means only the bank can verify; no credit bureau integration. | Innovation +1 (covers the "credit building" part of Topic C, which nothing covers today) | 3 h | Low: read-only over existing receipts | Test: statement lists exactly the executed rent and tuition receipts; altering one line fails verification; denied actions are excluded; UI button on the Receipts tab. |
| 9 | **Monthly money view** ("what did I spend this month?", "what bills are coming up?") read-only from the ledger: spending by payee and currency, upcoming bills, EUR needed and the USD that covers it, reserve status. | Innovation +0.5 to 1; Task Completion +0.5 | 2-3 h | Low | Tests: correct totals after the golden path; no action created; the probe "what did I spend last month?" no longer returns "Sorry, I didn't get that". |
| 10 | **French in the offline parser** for the most common requests (payer, envoyer, virer, loyer, frais de scolarité, maman), since the persona lives in France. | Innovation +0.5 | 3-4 h | Medium: regex parser regressions; grounding word lists already include "maman" | v2 N01-fr passes with the rules parser; all English suites unchanged; 10 new French unit tests. |

Not recommended in the time left: recurring or scheduled payments (the frozen demo clock makes an honest test hard, 6-8 h), federated learning, on-chain anchoring, real bank APIs. Keep them as future work, as the documents already do.

### Suggested order for the next 40 hours

1. Today: send the registration (P0-1); make the repo public (P0-2); fix C1-C13 (2 h, docs-writer).
2. Features 1 and 2 (about 4 h), then feature 4 or 3 depending on whether an API key is available.
3. Rebuild screenshots and the keynote once the UI is final; export PPTX and PDF on the template cover (P0-deck).
4. Record and edit the video (3 h with retakes), with the team card at the start.
5. Convert the three documents to the DOCX templates (4 h); export PDFs.
6. 20 Oct, morning CEST: `verify_all.py` on a fresh clone, `git archive`, send. Leave the afternoon for an update if needed.
