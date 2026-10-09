# DoMe cost model

Status: **assumptions, not a forecast.** Every number is either a vendor list price with a dated
source or an explicitly marked assumption. Nothing here has been load-tested at the scales modelled;
the only measured data point is the small loopback load smoke in `tests/test_load_smoke.py`
(40 simulated agents + 40 controllers, 400 commands, numbers only). Prices and conversion rates are
design inputs from `shared/protocol/plans.json` and the master prompt, not validated market research.

How to use: edit the inputs in §2 and §3 and re-run the calculator in §9 (the tables in §5–§7 are
its output). Revenue is **recognised monthly**: an annual payment counts as 1/12 per month.

Source handling: the vendor pages below were looked up on **2026-10-08**. The build environment's
egress proxy blocks direct fetches of vendor sites, so each figure is what the vendor page showed in
search results on that date, cross-checked against at least one third-party summary where the
primary page was not visible. Where sources disagreed, the disagreement is stated. Verify each
figure on the vendor page before relying on it; prices change.

## 1. What drives cost in this architecture

- One FastAPI process serves REST, both WebSocket endpoints and the PWA (ADR-0001 D1). Agents hold
  one outbound WebSocket each and ping every 25 s; the relay caps sockets at
  `DOME_RELAY_MAX_CONNECTIONS` (default 5,000) per process. Controllers (phones) connect only while
  the PWA is open.
- PostgreSQL stores accounts, devices, grants, lifecycle rows and events; no payloads, results or
  titles are stored, so storage grows slowly (see `docs/DATA_RETENTION.md`).
- Inbound traffic to the relay is not billed on the modelled host; outbound (pongs, state frames to
  phones, PWA assets) is.
- Identity is delegated to an OIDC issuer (Auth0 or Keycloak); billing to Stripe. Both scale with
  accounts, not with commands.
- There is no AI cost until the Phase E gate (both plan allowances are 0).
- Manual touchpad/keyboard (protocol 1.1, Free) is the only **streaming** traffic. While a phone
  is actively controlling the cursor, the relay receives and verifies one small signed batch per
  animation-frame flush, and forwards it to the PC. It stores nothing per batch. Its cost is relay
  CPU (one ES256 verification plus a handful of database lookups per batch) and egress, not storage
  (§7A).

## 2. Vendor price inputs (looked up 2026-10-08)

| Item | Figure used | Source as seen on 2026-10-08 | Notes / disagreement |
| --- | --- | --- | --- |
| Fly.io Machine, shared-cpu-1x | $2.02 (256 MB), $3.32 (512 MB), $5.92 (1 GB) per month, billed per second; about $5 per extra GB RAM | [fly.io/docs/about/pricing](https://fly.io/docs/about/pricing/) | The same page also showed an older table ($1.94 / $3.19 / $5.70); the higher figures are used |
| Fly.io outbound data | $0.02 per GB (North America / Europe) | [fly.io/docs/about/pricing](https://fly.io/docs/about/pricing/) via [withorb.com](https://www.withorb.com/blog/flyio-pricing) | One summary claims the first 100 GB/month is free; not relied on |
| Fly.io Managed Postgres | Basic $38/mo (shared-2x, 1 GB), Starter $72/mo (shared-2x, 2 GB), Launch $282/mo, Scale $962/mo; storage $0.30 per provisioned GB-month per node; HA, backups and pooling included | [fly.io/docs/mpg](https://fly.io/docs/mpg) | A third party quoted $0.28/GB; the official $0.30 is used |
| Fly.io volume snapshots (unmanaged Postgres only) | $0.08 per GB-month (reported from Jan 2026) | [sota.io comparison](https://sota.io/blog/railway-vs-render-vs-fly-pricing-2026) | Third-party report; not used in the base case (managed Postgres includes backups) |
| Neon (alternative DB) | Free: 100 CU-hours/project; Launch $0.106 per CU-hour, Scale $0.222 per CU-hour; storage $0.35 per GB-month; no monthly minimum | [neon.com/pricing](https://neon.com/pricing) | Aggregators showed older rates ($0.14 / $0.26, storage $1.50–1.75); official figures used |
| Supabase (alternative DB) | Free (500 MB, pauses after 7 idle days); Pro $25/mo incl. $10 compute credit (Micro, 1 GB RAM); Small ≈ $15, Medium ≈ $60 compute | [supabase.com/pricing](https://supabase.com/pricing) via [jetadmin guide](https://www.jetadmin.io/blog/supabase-pricing-2026-guide-to-plans-limits-and-real-world-costs/) | Pro is per organisation, compute per project |
| Auth0 | Free up to 25,000 MAU; B2C Essentials $35/mo; overage ≈ $0.07/MAU | [auth0.com/pricing](https://auth0.com/pricing) via [costbench](https://costbench.com/software/identity-access-management/auth0/) and [capterra](https://www.capterra.com/p/154900/Auth0/pricing/) | Sources conflict on MAU included with Essentials (500 vs 7,500). **High-sensitivity input, see §8** |
| Keycloak, managed | Skycloak Developer $29/mo (1 cluster, unlimited users, no per-MAU charge); Phase Two prices not published in results | [skycloak.io/pricing](https://skycloak.io/pricing/) | A GetApp listing said $25; vendor page used |
| Keycloak, self-hosted | One Fly Machine 2 GB ≈ $10.92/mo + a database (shares the MPG instance) + operator time | Derived from the Fly.io rows | Operations time is the real cost |
| Stripe | 2.9 % + $0.30 per successful US card charge; Billing pay-as-you-go 0.7 % of billing volume; dispute fee $15; international cards +1.5 %; currency conversion +1 % | [stripe.com/pricing](https://stripe.com/pricing) via [flexprice](https://flexprice.io/blog/stripe-pricing-breakdown-2026) and [superdupr](https://superdupr.com/blog/stripe-fees) | Sources disagree whether the $15 is returned when a dispute is won; the model assumes it is not |
| Sentry | Developer free (1 user, 5k errors/mo); Team $26/mo (annual) with 50k errors; Business $80/mo | [sentry.io/pricing](https://sentry.io/pricing/) via [costbench](https://costbench.com/software/developer-tools/sentry/free-plan/) | One site said Team $29 |
| Grafana Cloud | Free tier, no card, 14-day retention (metrics/logs/traces allowances) | [grafana.com free tier](https://grafana.com/products/cloud/free-tier/) | Exact allowances not confirmed; $0 assumed |
| Better Stack (uptime) | Free: 10 monitors, 1 status page; Responder $29/licence/mo | [betterstack.com/pricing](https://betterstack.com/pricing) | Free tier used |
| Email: Resend | Free 3,000/mo (100/day); Pro $20/mo for 50,000 | [resend.com/pricing](https://resend.com/pricing.md) | — |
| Email: Postmark | Free 100/mo; Basic $15/mo for 10,000 (+$1.80/1k) | [postmarkapp.com](https://postmarkapp.com/blog/new-we-made-pro-and-platform-tier-features-accessible-to-lower-volume-email-plans) via [automationatlas](https://automationatlas.io/answers/postmark-pricing-explained-2026/) | Alternative, not in base case |
| Email: Amazon SES | $0.10 per 1,000 outbound; 3,000/mo free in the first year | [aws.amazon.com/ses/pricing](https://aws.amazon.com/ses/pricing/) | Alternative |
| Windows code signing | Azure Trusted Signing (Artifact Signing) ≈ $9.99/mo (US/CA/EU/UK organisations; individuals US/CA only); OV certificate $129–300/yr; EV $349–400+/yr | [learn.microsoft.com code-signing-options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options) and [codenote.net](https://codenote.net/en/posts/windows-desktop-app-code-signing-distribution-japan-2026/) | Microsoft states EV no longer buys immediate SmartScreen reputation |
| Chrome Web Store | $5 one-time developer registration | [developer.chrome.com register](https://developer.chrome.com/docs/webstore/register) (amount via [spcast.eu](https://www.spcast.eu/en/browser-plugin/registration-in-the-chrome-web-store-developer-dashboard)) | One-time; not in monthly tables |
| Microsoft Edge Add-ons | Free registration | [learn.microsoft.com create-dev-account](https://learn.microsoft.com/en-us/microsoft-edge/extensions/publish/create-dev-account) | — |
| Apple | Not applicable: the phone client is a PWA, no App Store membership | — | A native app later would need the developer programme |
| Domain + DNS | $15/yr ≈ $1.25/mo; DNS at a free tier | assumption | — |

## 3. Product and behaviour assumptions

| Input | Value | Basis |
| --- | --- | --- |
| Monthly active account (MAA) | An account whose agent connected at least once in the month | definition |
| Pro price | $5.99/month, $49.99/year | `plans.json pricing_defaults`, experiment only |
| Interval mix | 70 % monthly, 30 % annual | assumption |
| Recognised revenue per Pro account (ARPA) | 0.7 × 5.99 + 0.3 × 49.99 / 12 = **$5.44/month** | computed |
| Payment fees per Pro account | monthly: 5.99 × (2.9 % + 0.7 %) + 0.30 = $0.516; annual: (49.99 × 3.6 % + 0.30) / 12 = $0.175; blended **$0.41/month** | computed from the Stripe row |
| Refund allowance | 2 % of recognised revenue ($0.11/Pro/month) | assumption |
| Dispute allowance | 0.2 % of Pro accounts per month × $15 = $0.03/Pro/month | assumption |
| PC online hours | 12 h/day per enabled PC; Pro accounts average 1.5 PCs | assumption |
| Phone (PWA) open | 1 h/day | assumption |
| Frames | ping/pong ≈ 0.1 KB each; `state` ≈ 1 KB every 30 s while a phone is subscribed; PWA shell ≈ 1 MB, loaded 10×/month | from `relay_client.py` (25 s pings), `version.json rules.state_cache` (30 s), build output |
| Outbound egress per account | ≈ 19 MB/month → $0.0004 | computed; a $0.01 budget is used per account |
| Database growth | commands and security-event rows ≈ 200 B each; ≈ 300 rows/account/month → < 100 KB/account/month before purge | from the models (no payloads stored) |
| Transactional email | ≈ 1 email per new account (verification via the IdP) + security notices; < 0.5 emails/account/month | assumption |
| Support | 2 % of MAA contact support monthly, 10 minutes each: founder time, not cash (100 MAA → 0.3 h; 1,000 → 3.3 h; 10,000 → 33 h/month) | assumption |
| Staging | one 1 GB machine, kept running | assumption; could be stopped between deploys |
| Monitoring | Grafana Cloud free + Better Stack free at every tier; Sentry Developer free at 100 MAA, Team $26 from 1,000 | assumption |
| AI | $0 (allowances are 0 until Phase E). Sensitivity in §8 | `plans.json` |
| Touchpad/keyboard batch rate cap | 40 batches/s per phone, burst 80; identical on Free and Pro | `plans.json input_rate_limit`, `version.json limits.input_batches_per_second` |
| Typical batch rate during active use | 10–20 batches/s while a finger is moving or text is being typed; about 1/s (empty keepalive) while the touchpad screen is open and idle; 0 when the screen is closed | assumption: the PWA flushes at most once per animation frame (up to 60 Hz on iPhone) and sends a keepalive about every second (`mobile-app/src/lib/input.ts`); active-use rate **not measured on a device** |
| Input frame size | ≈ 0.65 KB keepalive, ≈ 0.7 KB one motion event, ≈ 0.77 KB motion + click, ≈ 3.6 KB for a full 64-event batch; relay → agent adds ≈ 0.1 KB of routing metadata | computed on 2026-10-09 by building and signing frames with `shared/python` (`build_input_batch_payload`, `sign_payload`); compact JSON, ES256 envelope |
| Acks to the phone | `input_ack` ≤ 4/s per live session, ≈ 0.3 KB each; `input_session` frames only on start/suspend/end | `rules.input_sessions` (ack rate), size estimated from the schema |
| Active touchpad/keyboard use | 20 % of MAA use it; 60 minutes of active control per user per month; peak concurrent active sessions ≈ 1 % of MAA | assumption, no usage data exists |
| Verification per batch | Relay: one ES256 signature verification plus ≈ 5 database lookups (controller, account/plan, plan-enabled controllers, PC, grant). Agent: one more ES256 verification on the customer's PC (not a service cost) | `relay/router.py::route_input_batch`, `cloud-api/KNOWN_ISSUES.md` #7 |
| ES256 verification time | ≈ 80 µs per signature with Python `cryptography` on the 4-CPU build machine (one micro-benchmark of 2,000 verifications, single thread) | measured once on 2026-10-09; **not load-tested** and not measured on the production host |
| Input storage | none per batch: no `commands` row, no result, no log line; two lifecycle rows per session (`input.session_start`/`stop`) and at most one `input_rejected` security event per controller per minute | `rules.input_sessions`, cloud-api `README.md` "Manual input" |

## 4. Cost structure

### Fixed per month (does not change with accounts within a tier)

| Line | 100 MAA | 1,000 MAA | 10,000 MAA | Notes |
| --- | --- | --- | --- | --- |
| Relay/API machine(s), Fly.io | $10.92 (1 × shared-cpu-1x, 2 GB) | $10.92 | $41.84 (2 × shared-cpu-1x, 4 GB: one serving, one warm standby) | Single relay process by design; the standby is for deploys/recovery, not horizontal scale |
| Staging machine | $5.92 | $5.92 | $5.92 | 1 GB |
| Managed Postgres | $41 (Basic $38 + 10 GB × $0.30) | $41 | $78 (Starter $72 + 20 GB) | HA/backups included |
| Identity provider | $35 (Auth0 Essentials) | $35 | $35 (**or up to $700, see §8**) | Free tier would be $0 but lacks a custom domain |
| Error monitoring | $0 | $26 | $26 | Sentry |
| Email | $0 (Resend free) | $0 | $20 (Resend Pro) | |
| Code signing | $9.99 | $9.99 | $9.99 | Azure Trusted Signing; OV certificate alternative ≈ $10.75/mo |
| Domain/DNS | $1.25 | $1.25 | $1.25 | |
| **Fixed total** | **$104.08** | **$130.08** | **$218.00** | |

One-time: Chrome Web Store $5; Edge $0; business registration, legal review and any EV certificate
are founder decisions outside this table.

### Variable per active Free account

Egress $0.0004 + database < $0.001 + email ≈ $0 within free tiers → **$0.01/month budgeted**
(the budget is ten times the metered estimate to cover capacity headroom). The practical cost of
Free accounts is the step from one hosting tier to the next (relay memory, socket cap, Postgres
plan, IdP MAU), which is why the fixed tiers above differ.

### Variable per paying (Pro) account

| Component | $/month |
| --- | --- |
| Payment fees (blended) | 0.41 |
| Refund allowance | 0.11 |
| Dispute allowance | 0.03 |
| Egress/database budget (up to 5 PCs) | 0.01 |
| **Total** | **0.56** |

Contribution per Pro account = 5.44 − 0.56 = **$4.88/month** before fixed costs.

## 5. Scenarios

Recognised revenue, costs and contribution margin per month. "Payment fees + refunds" is the part
of variable cost that is purely financial; "Variable costs" includes it plus the per-account budgets.

| MAA | Conversion | Pro accounts | Free accounts | Recognised revenue | Payment fees + refunds | Fixed costs | Variable costs | Total costs | Contribution margin | Net |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | 2% | 2 | 98 | $11 | $1 | $104 | $2 | $106 | $9 (81%) | $-95 |
| 100 | 5% | 5 | 95 | $27 | $3 | $104 | $4 | $108 | $23 (86%) | $-81 |
| 100 | 10% | 10 | 90 | $54 | $6 | $104 | $7 | $111 | $48 (88%) | $-56 |
| 1,000 | 2% | 20 | 980 | $109 | $11 | $130 | $21 | $151 | $88 (81%) | $-42 |
| 1,000 | 5% | 50 | 950 | $272 | $28 | $130 | $38 | $168 | $235 (86%) | $104 |
| 1,000 | 10% | 100 | 900 | $544 | $55 | $130 | $65 | $195 | $479 (88%) | $349 |
| 10,000 | 2% | 200 | 9,800 | $1,089 | $110 | $218 | $210 | $428 | $878 (81%) | $660 |
| 10,000 | 5% | 500 | 9,500 | $2,721 | $276 | $218 | $376 | $594 | $2,345 (86%) | $2,127 |
| 10,000 | 10% | 1000 | 9,000 | $5,443 | $552 | $218 | $652 | $870 | $4,790 (88%) | $4,572 |

Net excludes founder time (support, operations, development), taxes, legal and any marketing. It is
a cash view of the modelled vendors only.

## 6. Approximate break-even

Break-even Pro count = (fixed + Free accounts × $0.01) ÷ ($5.44 − $0.56 + $0.01):

| MAA | Pro accounts needed | Conversion needed |
| --- | --- | --- |
| 100 | ≈ 22 | ≈ 22 % |
| 1,000 | ≈ 29 | ≈ 2.9 % |
| 10,000 | ≈ 65 | ≈ 0.7 % |

Reading: at 100 accounts the service runs at a loss of roughly $60–95/month whatever the conversion;
the fixed floor (about $104/month, dominated by managed Postgres and the identity provider) is what
matters. Around 1,000 accounts a 3 % conversion covers the cash costs. These are design assumptions;
the only way to validate conversion is the pricing experiment itself.

## 7. Capacity notes (what the money buys, and what has not been measured)

| Item | Modelled assumption | Evidence |
| --- | --- | --- |
| Concurrent sockets | Peak ≈ 50 % of PCs online + 10 % of phones. At 10,000 MAA with 1.1 PCs/account that is ≈ 5,500 agent sockets + 1,000 phones, **above the 5,000 default cap of one process** | not yet verified: memory per socket and the real cap of a 4 GB machine have not been load-tested; the horizontal-scaling seam (Redis pub/sub, ADR-0001 D1) is not built. Treat 10,000 MAA as requiring either a measured cap increase or the Redis work |
| Heartbeats | 25 s pings per agent: 5,500 sockets → 220 frames/s inbound, trivial CPU | load-tested only at 40 + 40 sockets on loopback (numbers printed by `test_load_smoke.py`) |
| Dispatch rate | Plan limits allow 120 manual + 360 coalescable commands/min per controller; realistic use is a few commands per session | not measured beyond the load smoke |
| Manual-input stream | ≤ 40 batches/s per phone (typical 10–20 while active), verified and forwarded with no storage; see §7A | integration-tested for correctness and rate limiting (cloud-api `tests/test_input_routing.py`, `tests/test_input_hardening.py`); throughput and latency **not load-tested**; the input-age budget (1 s at dispatch) has not been measured on a real network |
| Database size | < 1 GB for the first 10,000 accounts given the retention table; MPG Basic's 1 GB RAM is the limit, not disk | estimate |
| Backups | Included with Managed Postgres; a restore has **not** been rehearsed (required by the spec before calling a release production-ready) | not yet verified |
| Idle agent footprint | Target "modest"; not measured on Windows | not yet verified (not Windows-device-tested) |
| Latency targets | Median < 500 ms, p95 < 1.5 s on a healthy network are targets to measure, not claims | not yet verified on real devices; loopback smoke numbers only |

## 7A. Active touchpad/keyboard sessions (assumptions)

Everything in this section is an assumption or a computation from the inputs in §3. No touchpad
session has run on a device or against a deployed relay, so the active-use rate and the
concurrency are guesses until real usage exists.

Per active session (one phone controlling one PC):

| Quantity | Typical (15 batches/s) | Cap (40 batches/s) | Idle screen (1 keepalive/s) |
| --- | --- | --- | --- |
| Phone → relay (inbound, not billed on the modelled host) | ≈ 11 KB/s | ≈ 31 KB/s with small batches; at most ≈ 144 KB/s with full 64-event motion batches | ≈ 0.65 KB/s |
| Relay → PC (outbound, billed) | ≈ 12 KB/s | ≈ 32–148 KB/s | ≈ 0.75 KB/s |
| Relay → phone acks (outbound, billed) | ≈ 1.2 KB/s (4 acks/s) | ≈ 1.2 KB/s | ≈ 0 (acks follow accepted events) |
| Relay signature verifications | 15/s ≈ 1.2 ms CPU/s | 40/s ≈ 3.2 ms CPU/s | 1/s |
| Relay database lookups (≈ 5 per batch) | ≈ 75/s | ≈ 200/s | ≈ 5/s |
| Storage | none per batch | none | none |

Monthly egress for one touchpad user at the §3 assumption (60 active minutes at the typical rate):
(12 + 1.2) KB/s × 3,600 s ≈ 48 MB ≈ **$0.001** at $0.02/GB. This is negligible next to the $0.01
per-account budget in §4, so the per-account variable costs in §4–§6 are unchanged.

Concurrency is what matters. At the assumed 1 % of MAA controlling at the same moment:

| MAA | Concurrent active sessions | Batches/s at the relay (typical) | ES256 CPU (≈ 80 µs each) | Database lookups/s (≈ 5 per batch) |
| --- | --- | --- | --- | --- |
| 100 | 1 | 15 | ≈ 0.1 % of one core | ≈ 75 |
| 1,000 | 10 | 150 | ≈ 1.2 % of one core | ≈ 750 |
| 10,000 | 100 | 1,500 | ≈ 12 % of one core (32 % at the cap) | ≈ 7,500 |

Reading:
- Signature verification is cheap at these scales.
- The per-batch database work is not. About 7,500 lookups/s at 10,000 MAA is well beyond what
  this model can assume of the Basic or Starter Managed Postgres plans in §4. No query rate has
  been load-tested.
- Before that scale, the relay needs the short per-socket authorisation cache proposed in
  `cloud-api/KNOWN_ISSUES.md` #7 (invalidated by `grant_update`, revocation and PC status changes).
  With it, each batch needs no database work, and the fixed tiers in §4 should stand.
- Without the cache, budget a larger Postgres plan at 10,000 MAA (for example Launch, $282/month
  instead of Starter, +$210/month). That would lower the 10,000-MAA rows of §5 by that amount.

Free and Pro carry the same input cost by design (identical `input_rate_limit`). Input
responsiveness is never a paid lever, so touchpad traffic is a Free-account cost.

## 8. Sensitivities

- **Identity provider MAU.** If Auth0 Essentials includes only 500 MAU with $0.07/MAU overage (one of
  the two readings found), the identity line becomes ≈ $70/month at 1,000 MAA and ≈ $700/month at
  10,000 MAA, which would turn the 10,000/2 % scenario from +$660 to about −$5. Mitigations: stay on
  the Auth0 Free tier (25,000 MAU, no custom domain) until the number is confirmed, or run Keycloak
  (Skycloak $29/month, or self-hosted ≈ $11/month plus operations). Verify on the vendor page before
  launch.
- **Interval mix.** 100 % monthly raises ARPA to $5.99 and fees to $0.52; 100 % annual lowers ARPA to
  $4.17 and fees to $0.18. Contribution per Pro account stays between $3.9 and $5.4.
- **Refunds and disputes.** Doubling both allowances costs $0.14/Pro/month; at 1,000 Pro accounts
  that is $140/month.
- **AI (Phase E, if released).** Provider prices are not modelled here because no provider is chosen.
  A fully used allowance of 250 interpretations + 30 transcription minutes is likely to cost well
  under $1 per Pro account per month at current list prices of mainstream providers, but that is an
  unsourced assumption and must be priced with the chosen provider before any allowance is promised.
  The operator spending ceiling in `docs/BILLING.md` §10 bounds the downside.
- **Free-tier dependence.** The base case uses free tiers for monitoring and email at small scale.
  Removing every free tier (Sentry Team $26, Resend Pro $20, Better Stack Responder $29) adds $75/month
  at 100 MAA. Free tiers are convenient, not a plan.
- **Touchpad concurrency.** If 5 % of MAA control at the same moment instead of 1 %, relay batch
  volume and database lookups grow five-fold (≈ 37,500 lookups/s at 10,000 MAA without the cache in
  §7A). Egress stays immaterial (≈ $0.005/user/month even at 5 hours of active control). Measure the
  real rate (batches per active minute, session length, concurrency) on staging before sizing the
  database. The relay rate limits bound the worst case per phone, not the number of phones.
- **Hosting alternative.** Neon Launch at an average 0.25 CU (≈ $19/month + $0.35/GB) or Supabase Pro
  ($25/month, Micro compute) would replace the $41 Postgres line at small scale; Fly Managed Postgres is
  kept in the base case because it stays inside the relay's private network and includes HA.

## 9. Calculator

The tables in §4–§6 come from this script (inputs at the top); re-run it after changing an assumption.

```python
MONTHLY=5.99; ANNUAL=49.99; MIX_MONTHLY=0.70
CARD_PCT=0.029; CARD_FIXED=0.30; BILLING_PCT=0.007
arpa = MIX_MONTHLY*MONTHLY + (1-MIX_MONTHLY)*ANNUAL/12
fee_m = MONTHLY*(CARD_PCT+BILLING_PCT)+CARD_FIXED
fee_a = (ANNUAL*(CARD_PCT+BILLING_PCT)+CARD_FIXED)/12
fees = MIX_MONTHLY*fee_m + (1-MIX_MONTHLY)*fee_a
pro_var = fees + 0.02*arpa + 0.002*15 + 0.01     # fees + refunds + disputes + egress budget
free_var = 0.01
fixed = {100: 104.08, 1000: 130.08, 10000: 218.00}  # from the table in section 4
for maa, fx in fixed.items():
    for conv in (0.02, 0.05, 0.10):
        pro = round(maa*conv); free = maa - pro
        rev = pro*arpa; var = pro*pro_var + free*free_var
        print(maa, conv, pro, round(rev), round(fx), round(var), round(rev-var), round(rev-var-fx))
    print("break-even Pro accounts:", round((fx + maa*free_var)/(arpa-pro_var+free_var), 1))
```
