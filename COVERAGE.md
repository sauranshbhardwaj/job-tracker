# Daily search coverage

Collection repeats through the day (a two-hour target interval; long runs finish before the next starts). Every run refreshes the directories and lists below, imports new employer identifiers, and checks all enabled employer feeds. The application reports enabled, never-attempted, recently successful (36 hours), and failed feeds separately. A directory count is not a count of reachable boards, unique employers, or suitable jobs.

## Employer discovery

- [Open ATS Feed](https://github.com/veresk06/open-ats-feed), `actor/data/companies.json`. The four supported platforms in the snapshot inspected October 17, 2026 contain 13,753 board identifiers. Other vendors in the directory are not imported. MIT; copyright 2026 open-ats-feed contributors. No source code or directory copy is redistributed with this app.
- [LastRound AI ATS directory](https://github.com/fyrosofttech/lastroundai-hiring-data/tree/main/ats-directory), `lastroundai-ats-company-directory-2026-08.csv`. 9,935 entries in the inspected snapshot, with substantial overlap. [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Attribution: LastRound AI, [pay transparency study](https://lastroundai.com/), [DOI](https://doi.org/10.6084/m9.figshare.33154145). The app combines factual board identifiers and preserves manually disabled feeds. Directory timestamps describe upstream verification, not this app's live checks.

Directory updates are downloaded on each collection run; upstream publication cadence is outside this app's control. Failed, malformed, or suspiciously small directories leave the existing registry intact. Employer names may fall back to board slugs. An identifier can become obsolete, and different boards can belong to the same employer.

## Employer feeds

| Platform | Collection | Evidence / availability |
|---|---|---|
| Greenhouse | Full public board snapshot | Employer description and two-snapshot closure checks |
| Ashby | Listed public jobs | Employer description and two-snapshot closure checks |
| Lever | All public pages, up to 10,000 postings per board | Employer description and two-snapshot closure checks |
| Recruitee | Published offers, including requirements and multiple locations | Employer description and two-snapshot closure checks |
| SmartRecruiters | All public US pages, with full descriptions for software titles without senior titles | ServiceNow, Western Digital and Bosch verified as reachable October 17; further boards discovered from application links |

Public feed documentation: [Greenhouse](https://docs.greenhouse.io/job-board), [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api), [Lever](https://github.com/lever/postings-api), [Recruitee](https://docs.recruitee.com/reference/offers).

## New-grad lists

1. [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions), README.
2. [vanshb03/New-Grad-2027](https://github.com/vanshb03/New-Grad-2027), README.
3. [SpeedyApply](https://github.com/speedyapply/2027-SWE-College-Jobs/blob/main/NEW_GRAD_USA.md), US new-graduate file; internship files are not imported.
4. [ApplyGuy](https://github.com/ApplyGuy/2027-New-Grad-Jobs), machine-readable new-grad feed. Uses the employer's `listingUrl`, not the one-click-application service.
5. [Ricsign](https://github.com/ricsign/Ricsign-New-Grads-Jobs-2027), machine-readable feed, active `new-grad-swe` track only.

These lists can surface employers using Workday, Microsoft Careers, Amazon Jobs, and other unsupported systems. Such records have application links but remain unverified by an employer feed. Community sponsorship annotations cannot establish eligibility or exclude jobs. Different lists overlap and may apply their own selection rules.

6. [Jobright public SWE list](https://github.com/jobright-ai/2026-Software-Engineer-New-Grad). Despite the repository name, individual entries include 2027 roles; evaluate each posting.
7. [Jobright public engineering list](https://github.com/jobright-ai/2026-Engineering-New-Grad). Software-related titles only.

Jobright lists link back to Jobright. They provide unverified discovery only: the tracker does not scrape Jobright pages or invent timestamps from month/day labels. They cannot enter an edition until independently verified employer metadata is available.

## Remote-job feeds

| Source | Scope | Limitations |
|---|---|---|
| [Remotive](https://github.com/remotive-com/remote-jobs-api) | Entire free public feed, software-related titles retained | Public listings are delayed 24 hours; links stay on Remotive for attribution/application access |
| [Jobicy](https://github.com/Jobicy/remote-jobs-api) | Entire public seven-day feed, all cursor pages, software-related titles retained | Feed expiry does not mean a job closed; links stay on Jobicy |
| [Himalayas](https://himalayas.app/api) | US search, Entry-level and Mid-level labels, all pages until empty | Results can shift during traversal and upstream seniority labels can be wrong; links stay on Himalayas |

The normal US, role, and experience rules apply. Worldwide or unspecified remote locations are excluded from the main views unless a US option is explicitly supported. A US label contradicted by a foreign region in the title is held out for review. Remote listings carry provider descriptions, are not employer-verified, and cannot overwrite authoritative employer evidence. No remote-feed disappearance is treated as a confirmed job closure. Source names and canonical provider links are preserved. This is a private personal tracker, not a public redistribution feed or signup collection service.

## Remaining gaps

- LinkedIn and the Jobright search website are not automatically collected. Jobright public GitHub lists provide partial discovery. Broad employer coverage does not imply complete coverage of either platform.
- No comprehensive direct crawling of Workday, iCIMS, Taleo, Workable, company-specific sites, or every large employer. New-grad lists can expose individual links from these systems, but there is no full daily employer snapshot for them.
- No paid job/search/scraping API, proxy service, or OpenAI API-key billing fallback. Collection itself requires no AI call. The existing Plus review is advisory and capped separately.
- Listings can omit OPT acceptance, future sponsorship, start dates, or minimum experience. Unknown is kept separate from a verified match. The app cannot guarantee every suitable vacancy will be found.

## Daily-edition publication evidence

At 6:30 PM America/New_York, one immutable edition selects at most 100 qualifying jobs from the previous local cutoff to this cutoff. Normal days contain 24 hours; the daylight-saving transition windows contain 23 or 25 hours so the local cutoff never drifts. The interval excludes its start and includes its end. Changes to cutoff settings never create a second edition on the same local date. Publication runs separately from long source crawls. On first activation the tracker waits for the next cutoff; it does not fabricate historical editions. After a restart a missed cutoff can be finalized from records already collected and employer-checked by that cutoff. It cannot reconstruct jobs missed during downtime.

An edition requires a timezone-aware employer publication timestamp in the window, a full employer description, a successful employer check during the window and before cutoff, explicit US, Canada or UK location evidence, an entry-level title or zero-experience evidence, no mandatory positive experience requirement, no incompatible start/cohort/degree evidence, and no explicit or conflicting authorization restriction. A 0–1, 0–2, or 0–3 range can qualify; a mandatory 1+, 2+, or 3+ requirement cannot. Senior titles override Engineer I and Associate. Generic software titles with no experience evidence stay in review. Start dates and future sponsorship may be unstated and are labeled as unconfirmed; this is not a visa eligibility determination. Resume skills and fit scores never decide inclusion.

Greenhouse uses `first_published`; Ashby uses `publishedAt` (documented as last publication, not necessarily original opening); Recruitee uses `published_at`; SmartRecruiters uses `releasedDate`. The earliest observed timestamp is retained to avoid refreshing a known old job through republishing. A never-before-seen repost can still have a recent employer publication timestamp: original vacancy creation cannot be guaranteed. Lever has no publication timestamp in its public feed and remains review-only unless another authoritative source provides one. Aggregator/community dates alone never qualify. Missing dates, date-only labels, naive times, discovery dates, and update timestamps do not prove the 24-hour requirement.

Membership and posting evidence are frozen atomically at publication. Applied/skipped decisions, closure, or newly disqualifying employer evidence can remove entries; nothing backfills those slots. Previously published job IDs and normalized company/title/location identities are suppressed across editions, and decisions suppress matching duplicate identities. This reduces repeats but is not universal fuzzy matching across renamed employers or rewritten titles. Each edition stores its coverage report. The source registry is not exhaustive and a cutoff can occur during a crawl, so suitable jobs may be missed.

Primary references checked October 17, 2026: [Greenhouse public Job Board API](https://docs.greenhouse.io/job-board), [Ashby publication semantics](https://developers.ashbyhq.com/docs/public-job-posting-api), [SmartRecruiters public endpoints](https://developers.smartrecruiters.com/docs/endpoints), [SmartRecruiters posting fields](https://developers.smartrecruiters.com/docs/objects). No paid API key is used for these public feed requests.

The cap is 100 combined across US, Canada and UK. US assumes OPT/STEM OPT with future sponsorship; Canada and UK assume sponsorship is needed from the start. Explicit requirements for existing local work authorization or citizenship are held out. Unknown sponsorship remains visible, never a sponsorship promise. Historical editions retain their original cap and membership. SmartRecruiters and Himalayas now query US, CA and GB separately and deduplicate combined results; other employer feeds already return worldwide postings. Source documentation: https://himalayas.app/api and https://developers.smartrecruiters.com/docs/posting-api. Legacy aggregator source identifiers named `us-engineering` are retained for database continuity, but collection covers all three markets.
