# Open Job Tracker

A personal job finder and application tracker for US, Canadian and UK software and related engineering roles, June 2027 graduates, and entry-level requirements up to three years. A small Python service, a plain JavaScript table, SQLite, and no paid AI API-key path.

## Run locally

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/uvicorn tracker.app:app --host 127.0.0.1 --port 8127 --no-access-log
```

Open http://127.0.0.1:8127. The first scheduled collection catches up on startup. Set `SCHEDULE_ENABLED=false` to run only manual refreshes. The server must stay running for local schedules. Environment variables must be exported; `.env` is a sample configuration file, not loaded automatically.

## What it does

- Reads public Greenhouse, Ashby, Lever, Recruitee, and SmartRecruiters employer feeds; seven GitHub new-grad lists; and the Remotive, Jobicy, and Himalayas public remote-job feeds.
- Imports thousands of employer board identifiers from two public directories on every run, as well as application URLs in the lists. New directory entries join the same run. Directory membership is a discovery hint; only a successful live feed check counts as working coverage.
- Provides searchable, paginated source health, recent successful checks, unattempted feeds, and failures. See [COVERAGE.md](COVERAGE.md) for exact feed scopes, attribution, and gaps.
- Collects repeatedly through the day with a two-hour target interval, bounded worker pools, and no overlapping runs. Long crawls finish before the next starts. Publication runs independently at **6:30 PM America/New_York**.
- Publishes one fixed daily edition, capped at **100** jobs from the previous cutoff to the current cutoff. Fewer eligible jobs means fewer entries, including zero. No older-job padding or post-publication topups.
- Requires a full employer posting, positive US, Canada or UK location evidence, timezone-aware employer publication in the window, and a successful employer check before cutoff. Missing or community-only dates stay in a separate review queue. First-found time never substitutes for posting time.
- Provides Applied / Skip controls. Opening Apply reveals Yes, applied / No, skip; explicit decisions remove the job and preserve history. Published job IDs and matching company/title/location identities do not appear in subsequent editions. Older editions remain accessible.
- Requires a positive US, Canada or UK location signal in Jobs, Saved, and Applications. Other countries and unknown, ambiguous, or conflicting locations stay out of these views. Dubai/UAE, the country Georgia, and contradictory remote-region titles are covered by regression tests. Existing stored records are migrated before serving the updated feed; statuses and notes are retained. Multi-location jobs can qualify when a US, Canadian or UK option is explicitly listed.
- Includes explicit new-grad/entry-level roles and requirements whose minimum is up to three years. Unspecified experience stays visible. Clearly senior roles, internships, and explicit pre-2027 cohorts go to the audit views. Senior titles and high requirements always take precedence over Engineer I or Associate. The daily edition is stricter: mandatory positive prior experience is held out, while ranges beginning at zero can qualify. Clear entry-level roles with no stated cohort/start date can qualify with uncertainty labeled.
- Provides Jobs, Saved, and Applications views; role, sponsorship, found-date, work arrangement, experience, graduate-cohort, recent employer-check, status, and sort filters. Jobs already applied to are hidden from Jobs by default. Filtered views are bookmarkable. Found-date filtering uses discovery time, not an invented publication date. Work arrangement uses provider metadata or explicit location text, never a guess from benefits/description keywords. Existing records without this metadata may show Not stated until refreshed.
- Prioritizes explicit graduate roles above generic roles with unknown experience. No skill, GPA, university, resume, or fit-score filtering.
- Preserves application status and notes across refreshes and links to the employer application page where available.
- Extracts exact sponsorship evidence. Ambiguous or conflicting evidence stays visible. Sponsorship history, E-Verify participation, and an application question about sponsorship are never treated as a promise.
- Shows stale, unverified, excluded, closed, and failed-source states separately. Community-list visa symbols are unverified hints, not employer evidence.
- Provides a JSON export of jobs, source health, and application history. Credentials are excluded.

## Reliability boundaries

This is a monitored-source collector, not exhaustive internet coverage. ATS feeds operate per employer; employers using other systems are discoverable through lists but remain unverified until an appropriate adapter is available. LinkedIn and the Jobright search website are not scraped: their published policies restrict automated collection. Add a discovered application URL manually, or add a recognized employer board in settings.

Collection retries transient network/HTTP failures with bounded backoff, rejects incomplete/changed schemas, and commits each valid source snapshot in a transaction. Fixed platform worker pools and one SQLite ingestion writer bound concurrent memory and database writes. Lever requests, including pagination and retries, are spaced at least 1.05 seconds apart; shared Greenhouse/Ashby hosts receive at most four requests per second. The broader crawl can take hours, and existing results stay available while it runs.

Failed sources do not remove existing jobs. A job becomes Closed after absence from two successful employer-board snapshots, provided no other employer feed still lists it. An unexpected entirely empty board is flagged for review and does not mass-close prior records. A removed community-list or remote-feed entry alone never proves closure. Aggregator descriptions are unverified and cannot overwrite an employer's evidence. A 200 response from a board is a source check, not a separate verification of every application form. Newly discovered unrelated job families are not persisted; software-related excluded roles and all existing/manual records remain inspectable.

Posting dates are displayed only when supplied, and a source's update timestamp is not relabeled as an original posting date. First found records when this tracker discovered the job. Start dates are often absent; these remain unverified. ISO-formatted explicit start dates before availability are excluded, but other date phrasing needs human review. Rules are conservative heuristics, not proof of eligibility. Geography is intentionally conservative: bare Remote, Worldwide, and ambiguous locations are excluded from the main views unless a US option is explicitly supported. Source location errors can still require correction. Original location text and all records remain available under Settings → Review hidden records; application history is never deleted by filtering.

The collector allows one active run per process. A separate publisher checks the cutoff every 30 seconds and atomically freezes an edition; it never waits for the crawl or Plus review. On first activation it waits for the next cutoff instead of inventing an earlier edition. Published snapshots survive restarts. Missed publication can use only records already collected and checked by that cutoff. Run **one replica and one worker**. Collection is scheduled inside the persistent service, so a separate Railway cron service is unnecessary. It does not provide an uptime SLA. Process health and source-refresh health are separate. Automatic catch-up records the latest due daily run, not one run for each missed day. Partial collection is retried on the next interval or by the Refresh sources button. Coverage may remain incomplete at cutoff and is recorded with each edition.

## Railway

1. Deploy the included Dockerfile to a Railway service.
2. Attach a persistent volume at `/data` before first startup. Never rely on ephemeral deployment storage.
3. Set `APP_PASSWORD` to at least 12 random characters and `APP_SECRET` to at least 32 random characters. The owner can explicitly lower `APP_PASSWORD_MIN_LENGTH` from its default of 12 to a minimum of 8 for a chosen password. Authentication remains required. Keep the same `APP_SECRET` across deployments; it encrypts saved OAuth credentials and signs sessions.
4. Generate a Railway HTTPS domain. The service listens on Railway's `PORT` and exposes `/healthz` for process/database readiness.
5. Keep one replica. The included `railway.toml` configures the Docker build, readiness check, and restart policy.

Other configuration: `DATA_DIR`, `DAILY_HOUR`, `DAILY_TIMEZONE`, `COLLECTION_INTERVAL_HOURS` (default 2), `PUBLICATION_HOUR` (18), `PUBLICATION_MINUTE` (30), `STRICT_2027_COHORT` (false), `START_DATE`, `SCHEDULE_ENABLED`, and `CHATGPT_DAILY_LIMIT` (default 30 attempts per UTC day). Production refuses to start without a password and encryption secret. Sessions are signed, HttpOnly, SameSite=Strict, and Secure in production. JSON mutation endpoints require a same-origin custom request header; credential files never enter localStorage. Source identifiers cannot turn collectors into arbitrary URL fetchers.

Railway Hobby's included usage is not unlimited; this app shares the account's resource allowance. Measure CPU, RAM, and volume usage in Railway before choosing a spending limit.

Use Railway volume backups and the in-app JSON export for recovery. The export is a portable record of jobs and statuses, not an automated restore/import interface. A volume backup or SQLite backup also preserves the encrypted account configuration. Keep `APP_SECRET` separately in a password manager.

## Connect ChatGPT Plus without API credits

This implements OpenAI's documented **Sign in with ChatGPT / ChatGPT plan usage** flow for eligible open-source clients, including the self-hosted VM procedure. It does not read Codex's auth store, scrape ChatGPT, accept an API key, or fall back to paid API credentials.

**Account eligibility and a real completed inference request must be verified by the owner.** Publishing open-source code does not guarantee account access. The app uses the documented public Responses endpoint with OAuth plan credentials, `store: false`, and `stream: true`. That is a protocol endpoint; it does not mean a Platform API key is being billed.

1. In ChatGPT Settings → Usage, disable **Allow other apps to use credits after reaching your usage limit**. The app cannot override this account-level setting. Without this step, a plan-authorized external app may use additional account credits according to your settings. No inference runs until you confirm this step.
2. Open Sources & settings in the tracker and copy the command containing this deployment's host ID. On your computer, from the project directory, run:

   ```sh
   .venv/bin/python -m tracker.connect --host-id HOST_ID_FROM_SETTINGS
   ```

3. The helper starts a loopback callback, opens OpenAI sign-in, requests plan-usage permission, checks state and PKCE, validates the signed identity token and nonce, and writes an owner-only connection file in ignored `data/`.
4. Import that file in the tracker **over HTTPS**. It must contain the deployment's host ID. Server-side identity validation runs again; credentials are encrypted in SQLite. Delete the local transfer file after import. The remote deployment owns subsequent refreshes, avoiding rotating-token races.
5. Load your account's model catalog and choose a model. Run **Review unclear jobs** to verify a successful inference before relying on scheduled review. The UI shows failures and account usage links.

The default cap is 30 inference attempts per UTC day, reserved before each request, including failed/interrupted attempts. Only new/changed unclear employer descriptions are reviewed. An exact quote must match the stored description, and only a terminal `response.completed` event counts as success. Model advice remains advisory and cannot hide jobs. The app pauses on plan errors; it never substitutes a key or another billing method. This cap controls request count, not exact subscription units. ChatGPT plan usage is shared with other apps.

Plus currently enriches collected descriptions; this version does not run autonomous whole-web searches. Public-source discovery comes from refreshed employer directories, GitHub application links, and the editable employer registry. Daily jobs are still collected if Plus is disconnected, rate-limited, or ineligible.

Official references:

- [Registration, scopes, PKCE, and identity validation](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Self-hosted VM connection](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Session refresh, revocation, and shared limits](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board)
- [Ashby Job Posting API](https://developers.ashbyhq.com/docs/public-job-posting-api)
- [Lever Postings API](https://github.com/lever/postings-api)
- [LinkedIn scraping policy](https://www.linkedin.com/help/linkedin/answer/a1341387)
- [Jobright terms](https://jobright.ai/legal/service)

## Checks and dependency updates

```sh
.venv/bin/python -m pytest -q
node --check static/app.js
uv pip compile requirements.in --upgrade -o requirements.txt
```

Tests cover authorization, source parsing, scope/visa ambiguity, deduplication, rollback, retention, closure, revisions, DST scheduling, encrypted credentials, and completed/failed/interrupted Plus streams. Unit tests use mock feeds and do not spend subscription usage or call paid APIs. Regenerate the dependency lock and rerun the suite when updating dependencies.

MIT license applies to application code, not third-party job descriptions or services. No resume, private application history, OAuth file, database, password, or deployment secret belongs in the public repository.

Daily publication rules, timestamp limitations (including republishing), daylight-saving windows, source gaps, and repeat detection limits are documented in [COVERAGE.md](COVERAGE.md).
