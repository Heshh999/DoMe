# Putting DoMe online: the founder's setup guide

This guide takes you from "accounts not created" to "DoMe running at https://domeremote.com". You
never type a command. You create accounts, paste values into settings pages, and press one button on
GitHub called **Deploy**.

> **Honest status (2026-10-14).** The Deploy button and everything behind it are written and tested
> against stand-ins, but **have never run against the real Fly.io, Neon, Auth0 or Cloudflare**. Your
> first staging run is the real test. Each run ends with a plain-English summary that says what happened
> and what to click next. The vendor screens below come from each vendor's documentation, read on
> 2026-10-13 (Neon's from its documentation as shown in search results on 2026-10-14). They are marked
> *(per vendor docs, not yet exercised)* because nobody has clicked through them for DoMe yet, and
> dashboards change. If a button has moved, look for the same words nearby.

Order: **staging first, then production.** Staging is a full copy at https://staging.domeremote.com,
where you can try things safely. Production only accepts the exact version that already runs well on
staging.

> **Words you will see**
>
> - **Commit**: one saved version of DoMe's code, shown as a short code like `1a2b3c4d5e6f`.
> - **Default branch**: the line of saved versions the Deploy button uses; today it is called
>   `claude/stoic-heisenberg-txq6ja`.
> - **Tenant** (Auth0): a separate Auth0 space with its own users. You need one for staging and one
>   for production.
> - **DNSSEC**: a free Cloudflare setting that stops others from faking your domain's addresses.
> - **Neon**: the company that hosts staging's free database (section 6).
> - **Direct connection string**: the database address Neon shows with **Connection pooling** turned
>   off. Staging needs this one; its host name has no `-pooler`.
> - **Pooled URL**: the database address Fly labels "pooled". You only need it if a production summary
>   asks.
> - **Test kit**: the engineer's testing setup. You do not need it.
> - **Engineer**: whoever maintains DoMe's code for you, for example a Claude Code session working on
>   this repository (section 14 says what to give them).

---

## 0. Before you start

**Time:** about 1.5 hours for staging (mostly waiting), about 1 hour for production.

**Monthly cost: about $50 for both environments** (your budget). Fly.io prices read 2026-10-13 (check
fly.io/docs/about/pricing); Neon's free plan as of October 2026 (check neon.com/pricing).

| Item | Staging | Production |
| --- | --- | --- |
| The database | Neon, free plan: $0 | Fly Managed Postgres, Basic plan: $38 |
| Database storage | included (free plan: 1 GB per project; Neon raised it from 0.5 GB on 2026-10-02) | 10 GB at $0.28/GB: about $3 |
| One app machine (shared-cpu-1x, 512 MB) | at most $3.69; it sleeps when nobody uses it | $3.69, always on |
| Internet addresses (shared IPv4 + IPv6) and the first 10 certificates | free | free |
| Data sent to users in North America | $0.02 per GB | $0.02 per GB |
| **Total** | **about $4** | **about $45** |
| Auth0 (sign-in), Free plan up to 25,000 monthly active users | $0 | $0 |
| Cloudflare DNS and redirect rules | $0 | $0 |
| GitHub Actions (the Deploy button) on a public repository | $0 | $0 |

**Staging sleeps; production never does.** When nobody has used staging for a few minutes, its app
machine stops, and its Neon database goes to sleep too. The first visit after a nap takes a few seconds
longer while both wake up; that is expected. A PC linked to staging keeps staging awake for as long as it
is connected. Production stays on all the time, because every linked PC keeps a connection open to it.

Fly.io has **no spending cap and no billing alerts**. **Deleting an app does not delete its database**:
production's database keeps costing about $41 a month (with its storage) until you delete it separately
(section 13).

**Keep the GitHub repository public.** The Deploy button relies on GitHub's "Required reviewers"
approval (section 3). On a private repository, only GitHub Enterprise keeps that approval; on the
Free, Pro and Team plans it disappears (and on Free, the environment secrets go too). So on those
plans, keep the repository public. Ask an engineer before changing this.

## 1. Three rules about passwords and tokens

1. Paste tokens, passwords and secrets **only** into the exact field named in this guide, on
   github.com, fly.io, console.neon.tech or auth0.com.
2. **Never** paste one into a chat, an email, a GitHub issue or a document, and never give one to
   anyone, Claude included. Claude will never ask you for one. If something asks, it is wrong.
3. If one leaks anyway, **revoke it where it was made first** (the Fly Tokens page, Auth0 >
   Credentials > Rotate, or Neon's **Reset password**, section 6). Then paste the new one into GitHub
   and press Deploy.

Names like `FLY_API_TOKEN` are labels, not secrets, so it is fine to write them anywhere.

## 2. Fly.io: account and card *(per vendor docs, not yet exercised)*

1. Go to https://fly.io, sign up, and confirm your email.
2. Open https://fly.io/dashboard/personal/billing and add a credit card **before the first deploy**.
   Without a card, Fly's free trial stops every machine after 5 minutes, so DoMe would look broken.
   Adding a card ends the trial. Fly may place a temporary hold of under $10.
3. Use your **Personal** organization. Do not create apps or databases by hand: the Deploy button
   does that. If you deliberately use another Fly organization, add its short name (the "slug") as an
   **Environment variable** named `FLY_ORG` in both the staging and production environments (GitHub >
   Settings > Environments > each environment > **Environment variables** > **Add environment
   variable**; section 3 creates the environments).

## 3. GitHub: two environments *(per vendor docs, not yet exercised)*

**Do not press Deploy until both environments are set up as below.** The Deploy button checks these
settings before every run and refuses to start while one is missing or wrong.

Open https://github.com/Heshh999/DoMe/settings/environments (repository **Settings** >
**Environments**). Do the following twice: once with the name `staging`, then once with `production`
(lower case, exactly).

1. Click **New environment**, type the name, and click **Configure environment**.
2. Tick **Required reviewers**, type `Heshh999` (you), and **leave "Prevent self-review" unticked**.
   You are the only reviewer, and ticking it would lock you out.
3. **Untick "Allow administrators to bypass configured protection rules"** (GitHub ticks it by
   default). Ticked, it lets the run skip your approval. Click **Save protection rules**.
4. Under **Deployment branches and tags**, choose **Selected branches and tags**. Then **Add deployment
   branch or tag rule**, keep **Ref type: Branch**, type the default branch name exactly as it is today:
   `claude/stoic-heisenberg-txq6ja`, and click **Add rule**. Add no other rule.

Why both environments need your approval: the Fly token you store in each (next section) can manage
**every** app and database in your Fly organization, production included. With you as the required
reviewer, no run receives a token without an approval from your GitHub account. That approval lets
the run use whatever code is on the default branch at that moment.

**Approve only a run you started yourself in the last few minutes.** If GitHub asks you to approve any
other run, even one that shows your own name, click **Reject** and tell an engineer. A request from a
run someone else (or an AI assistant) started looks exactly the same as your own.

If the default branch is ever renamed (for example to `main`), these two rules must be changed to the
new name, or every deploy is refused.

## 4. Fly tokens into GitHub *(per vendor docs, not yet exercised)*

1. Open https://fly.io/dashboard/personal/tokens (on the Fly dashboard: your organization in the
   organization drop-down, then **Tokens**).
2. Create an **organization token** named `github-staging`. If an expiry is offered, choose 1 year
   and put a reminder in your calendar.
3. Copy the **whole** value, including `FlyV1 ` at the start and the space after it. Right away, open
   GitHub > Settings > Environments > **staging** > **Environment secrets** > **Add environment
   secret**. Name: `FLY_API_TOKEN`. Value: paste. Click **Add secret**.
4. Repeat with a second token named `github-production`, pasted into the **production** environment as
   `FLY_API_TOKEN`.

It must be an **Environment secret** of that environment, never a **repository secret** (the list
under Settings > **Secrets and variables** > **Actions** > **Repository secrets**). GitHub gives a
repository secret to every workflow run without asking you, so your approval would not protect it. A
repository secret is **not** reported as missing: instead, the Deploy button refuses to run at all
while a repository secret named `FLY_API_TOKEN`, `DOME_OIDC_CLIENT_SECRET`, `DATABASE_URL`,
`DOME_STRIPE_SECRET_KEY` or `DOME_STRIPE_WEBHOOK_SECRET` exists, and tells you to delete it and make a
new one.

## 5. Auth0 for staging *(per vendor docs, not yet exercised)*

1. Sign up at https://auth0.com. A **tenant** is a separate Auth0 space; you need one for staging and
   one for production. In the Auth0 dashboard (https://manage.auth0.com), click the tenant name at the
   top left > **Create tenant**. Tenant Domain `domeremote-staging`, Region **US**, Environment Tag
   **Development** > **Create**. The tenant name cannot be changed later.
   If sign-up already made a tenant with a random name (like `dev-abc123`), you may use it for staging
   instead: set its Environment Tag to **Development** under **Settings > General**. Only the Domain
   you paste in step 5 is different.
2. **Applications > Applications > Create Application**. Name it `DoMe staging`, choose **Regular Web
   Application**, and click **Create**.
3. On the **Settings** tab:
   - **Allowed Callback URLs**: exactly `https://staging.domeremote.com/v1/auth/callback`
   - Leave **Allowed Logout URLs** and **Allowed Web Origins** empty (DoMe does not use them).
   - Click **Save**.
4. On the **Credentials** tab, set **Authentication Method** to **Client Secret (Post)**, and save.
   DoMe sends its secret that way.
5. Paste into GitHub > Settings > Environments > **staging**:
   - Under **Environment variables** > **Add environment variable**:
     - `DOME_OIDC_ISSUER` = the **Domain** shown on the Auth0 Settings tab (for example
       `domeremote-staging.us.auth0.com`). Paste it as shown; the button accepts it with or without
       `https://` and a trailing `/`.
     - `DOME_OIDC_CLIENT_ID` = the **Client ID**.
     - Optional: `VITE_DOME_SUPPORT_URL` = your support address, for example
       `mailto:support@domeremote.com` (it is shown publicly on the Support page).
   - Under **Environment secrets** > **Add environment secret**:
     - `DOME_OIDC_CLIENT_SECRET` = the **Client Secret** (use Auth0's copy button).

Variables appear in the public run log. **Only the Client Secret is a secret.** Never put it under
Variables. If you ever do, rotate it in Auth0 (Credentials > Rotate) and paste the new one under
Secrets. The Deploy button refuses a Client ID that looks like a secret.

## 6. Neon: staging's free database *(per vendor docs, not yet exercised)*

Staging keeps its data in a free database at Neon, not at Fly. Production gets its own Fly database
later (section 11). Neon's free plan, as of October 2026 (check https://neon.com/pricing):

- 1 GB of storage per project (Neon raised it from 0.5 GB on 2026-10-02; some of Neon's pages may still
  say 0.5 GB). Either is plenty for testing.
- 100 compute-hours a month. Neon counts size times time; at the smallest size (0.25) that is about
  400 hours awake, a little over half a month.
- The database sleeps after about 5 minutes without use and wakes on the next visit.
- A 6-hour restore window: Neon can rewind the database to any moment in the last 6 hours, no further.
- If the month's compute-hours run out, Neon pauses the database until the next month. Staging then
  stops working. Production keeps running, but you cannot deploy a new version to production until
  staging works again, because production only accepts a version that runs healthily on staging. You can
  then wait for the next month, or ask an engineer about switching Neon to a paid plan for the rest of the
  month (Neon bills by use, with no monthly minimum). So **do not leave a test PC linked to staging around
  the clock**: while it is connected, staging and its database stay awake, and that uses up the month's
  hours in about 17 days.

1. Go to https://console.neon.tech and sign up, either with **GitHub** (your `Heshh999` account) or
   with your email. Stay on the **Free** plan; do not upgrade.
2. Create a project (Neon may offer this right after sign-up; otherwise click **New Project**):
   - **Project name**: `domeremote-staging`
   - **Postgres version**: **16**
   - Cloud provider **AWS**, region **US East 1 (N. Virginia)** (`aws-us-east-1`). It is the Neon
     region closest to Fly's Ashburn, Virginia, where the DoMe app runs.
   - Click **Create Project**.
3. On the project's dashboard, click **Connect**. In the window that opens:
   - Turn **Connection pooling** **off**. DoMe needs the direct address, and the Deploy button refuses
     the pooled one (its host name contains `-pooler`).
   - The branch menu shows `production`. That is only Neon's name for its main branch; it is still
     staging's database. Keep it, and do not choose `development`.
   - Leave the database and role as Neon shows them. If Neon offers several formats, choose the plain
     connection string.
   - Click the **copy** button. What you copied starts with `postgresql://`, contains the database
     password, has **no** `-pooler` in it, and ends with `?sslmode=require&channel_binding=require`.
     The Deploy button refuses it without `channel_binding=require`.
4. Right away, open GitHub > Settings > Environments > **staging** > **Environment secrets** > **Add
   environment secret**. Name: `DATABASE_URL`. Value: paste. Click **Add secret**. Only staging needs
   it; production does not. Never add this Neon address to the production environment; the Deploy
   button refuses it there.

That address contains the database password: treat it like a token (section 1). If it ever leaks, or
you just want a new password: in Neon open the project > **Branches** > `production` (marked
**Default**) > **Roles & Databases**, open the menu next to the role (normally `neondb_owner`) and choose
**Reset password**. Copy the connection string again (step 3), paste it over the staging secret
`DATABASE_URL` (in GitHub, click the pencil next to it, paste, **Update secret**), and run Deploy for
staging. Every staging deploy stores the address from GitHub in Fly again, so nothing else is needed.

## 7. First staging deploy

1. Open https://github.com/Heshh999/DoMe/actions/workflows/deploy.yml and click **Run workflow** on
   the right.
2. Leave the branch as it is (the default branch). Choose **environment: staging** and **action:
   deploy**. Leave **create the database** unticked: that box is for production only, and staging
   uses the Neon database from section 6 (a tick is ignored for staging). Click the green **Run
   workflow**.
3. The first job, "Checks before approval", takes about a minute. It also checks your settings from
   sections 3 and 4 and stops with a plain message if one is missing. Then the run waits for you:
   click **Review deployments**, tick **staging**, and click **Approve and deploy**.
   **Approve only a run you started yourself in the last few minutes.** If GitHub asks you to approve
   any other run, click **Reject** and tell an engineer.
4. Wait 10-20 minutes. If `DATABASE_URL` is missing or is not the direct Neon address, the run stops
   early with a "needs you" summary that says what to copy where (section 6), and changes nothing.
5. Open the run and read its **Summary**. It lists what was created, what was already there, and
   **What to do next**. Red is fine on a first try: the summary says what to fix, and you can press
   Run workflow again at any time. A second run never creates anything twice and never changes the
   generated passwords. The run also checks that DoMe's sign-in page sends visitors to Auth0 with your
   settings and that PCs can reach DoMe through Fly; it cannot sign in for you.

## 8. Cloudflare record for staging *(per vendor docs, not yet exercised)*

Do exactly what the summary's **What to do next** says. Normally that is:

1. Open https://dash.cloudflare.com > **domeremote.com** > **DNS** > **Records**. If a record named
   `staging` already exists (type A, AAAA or CNAME), delete it first.
2. **Add record**: Type **CNAME**, Name `staging`, Target exactly as the summary shows (normally
   `domeremote-staging.fly.dev`).
3. Click the orange cloud so it turns **grey ("DNS only")**. This matters: with the orange cloud, Fly
   cannot issue the security certificate. Click **Save**.

## 9. Check staging and sign in

1. About 10 minutes later, run **Deploy** again with **environment: staging** and **action:
   status**. Status needs no approval and changes nothing. A status run is red only when something is
   deployed and wrong; while DNS or the certificate is still on its way, it stays green and says so.
2. When the summary says **"Staging is live at https://staging.domeremote.com"**, open that address,
   create an account, and sign in. The Deploy button cannot test sign-in; only you can.
3. If you sign out and sign in again, Auth0 may let you straight back in without asking. That is
   Auth0 remembering you, and it is expected.
4. A normal DoMe installer on a Windows PC always connects to domeremote.com (production). Linking a
   PC to **staging** needs the engineer's test kit; you can skip it. A PC linked to staging keeps
   staging awake while it is connected (section 0), so unlink or close it when you finish testing.
5. Staging sleeps when nobody uses it. If a page takes a few seconds to appear after a quiet spell,
   that is the machine and the database waking up.

## 10. Auth0 for production *(per vendor docs, not yet exercised)*

Same as section 5, with these differences:

- A **separate tenant**: in https://manage.auth0.com, click the tenant name at the top left > **Create
  tenant**. Tenant Domain `domeremote`, Region **US**, Environment Tag **Production** > **Create**.
- Application name `DoMe`. **Allowed Callback URLs**: exactly `https://domeremote.com/v1/auth/callback`.
- **Credentials > Authentication Method: Client Secret (Post)**.
- In the application's **Connections** tab, turn **off** `google-oauth2` and keep
  **Username-Password-Authentication** on. Auth0's shared Google keys are not meant for production.
  Turn Google back on only after an engineer has set up your own Google keys.
- Before real customers: set up an email provider in Auth0 for sign-up and password-reset emails
  (Auth0's built-in email is for testing).
- Paste `DOME_OIDC_ISSUER` and `DOME_OIDC_CLIENT_ID` (Variables) and `DOME_OIDC_CLIENT_SECRET` (Secret)
  into the GitHub **production** environment.

## 11. Production deploy

1. Open https://github.com/Heshh999/DoMe/actions/workflows/deploy.yml > **Run workflow**:
   **environment: production**, **action: deploy**, tick **create the database** (about $41 a month
   for the database and its storage; about $45 a month for the whole of production). Production does
   not use Neon and needs no `DATABASE_URL` secret. If you ever added `DATABASE_URL` to the production
   environment, delete it first (GitHub > Settings > Environments > **production** > **Environment
   secrets** > the bin icon next to it): the Deploy button refuses a Neon address there.
2. The first job checks that staging already runs this exact version and is healthy. If not, it
   stops before asking you for anything and tells you to deploy staging first.
3. When asked: **Review deployments** > tick **production** > **Approve and deploy**.
   **Approve only a run you started yourself in the last few minutes.** If GitHub asks you to approve
   any other run, click **Reject** and tell an engineer.
4. When it finishes, add the two records the summary lists in https://dash.cloudflare.com >
   **domeremote.com** > **DNS** > **Records**. First delete any existing A, AAAA or CNAME record whose
   Name shows as `domeremote.com` (that is what Cloudflare calls `@`). Then:
   - Type **A**, Name `@`, IPv4 address from the summary, **grey cloud (DNS only)**
   - Type **AAAA**, Name `@`, IPv6 address from the summary, **grey cloud (DNS only)**
5. Run **status** for production until it says **"Production is live at https://domeremote.com"**.
   Then sign up and sign in there yourself.

## 11A. Stripe (payments) *(per vendor docs, not yet exercised)*

Stripe is the company that takes card payments for DoMe. Card details are typed on Stripe's own pages and
never reach DoMe. Until the values below are in place, DoMe sells nothing: the Billing page says
"Upgrading is not available yet" and everyone stays on Free. That is the state of https://domeremote.com
today.

> **Honest status (2026-10-20).** DoMe's payment code is written and tested against a stand-in for Stripe,
> **never against Stripe itself**. The Deploy button now carries the Stripe values to Fly (tested against a
> stand-in for Fly, never against the real one). Your first test-mode run on staging (steps 6 and 7) is the
> real test of both. Steps 1 and 2 are already done (you set up the Stripe sandbox and the five prices on
> 2026-10-20).

**Cost.** Stripe has no monthly fee. For each successful charge on a US card it keeps **2.9% + $0.30**,
plus **0.7%** of subscription payments for Stripe Billing (the subscription feature DoMe uses); cards from
outside the US cost **1.5%** more, and a customer's dispute (chargeback) costs **$15**. Test mode is free:
nothing is charged and no fee is taken. Example: of a $2.99 AI top-up, Stripe keeps about $0.39 and about
**$2.60** reaches you; of a $9.99 Pro month, about $0.66 goes to Stripe. The figures and their sources are
in `docs/COST_MODEL.md` §3.

**Test mode first.** Stripe has a **Test mode** (also called a sandbox) where no real money moves and
test card numbers work. Staging only ever uses Test mode: the Deploy button refuses a live key there.
Production accepts both, so you can try production in Test mode first (step 8); every Deploy summary
says which one runs: **"Stripe: TEST MODE — no real charges"** or **"Stripe: LIVE — real charges"**.

**All seven values or none.** Payments need exactly seven values in a GitHub Environment: two
**secrets** and five **variables**. With none, payments stay off. With only some, or with a value that
cannot be right (a `pk_` key, a product id instead of a price id, a live key on staging), the Deploy
button stops before changing anything and says which name to fix.

1. **Account.** Sign in at https://dashboard.stripe.com and switch the dashboard to **Test mode**
   (a toggle or a "Sandbox" label at the top). *Done 2026-10-20.*
2. **Prices.** **Product catalog** > **Add product**, three products, with prices exactly as on the
   pricing page:
   - "DoMe Pro": recurring **$9.99 monthly** and recurring **$99.99 yearly** (two prices on one product)
   - "DoMe Pro Plus": recurring **$24.99 monthly** and recurring **$249.99 yearly**
   - "DoMe AI top-up" (100 requests): **one-time** **$2.99**
   Your prices include tax ("inclusive"); keep it that way, and leave **Stripe Tax** off (the Deploy
   button never switches it on). Open each price and copy its id: it starts with `price_` (not the
   product's id, which starts with `prod_`). You need five ids. *Done 2026-10-20 (the founder holds the
   five ids).*
3. **Secret key.** **Developers** > **API keys** > **Secret key** > **Reveal**. In test mode it starts
   with `sk_test_`. Treat it like a password (section 1). The **Publishable key** (`pk_test_...`) on the
   same page is **not** the one: DoMe does not use it, and the Deploy button refuses it.
4. **Webhook** (how Stripe tells DoMe that someone paid). Staging must already be live at
   https://staging.domeremote.com (section 9). In Stripe (Test mode): **Developers** > **Webhooks** >
   **Add destination** (older dashboards: **Add endpoint**):
   - **Events from**: **Your account**. Leave the API version as Stripe suggests.
   - **Events**: search for and tick each of these nine:
     `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
     `checkout.session.expired`, `customer.subscription.created`, `customer.subscription.updated`,
     `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`,
     `invoice.payment_action_required`. Also tick `charge.refunded`, `charge.dispute.created`,
     `charge.dispute.updated` and `charge.dispute.closed`: DoMe changes nothing for these four, but
     records them so an engineer hears about refunds and disputes. Tick nothing else (other events are
     ignored).
   - **Destination type**: **Webhook endpoint**. **Endpoint URL**: exactly
     `https://staging.domeremote.com/v1/billing/webhook`. Save (**Create destination**).
   - On the new endpoint's page, **Signing secret** > **Reveal**, and keep it for step 6. It starts
     with `whsec_` and is a password too.
   Staging sleeps when nobody uses it; Stripe's first delivery wakes it, and Stripe tries again by itself
   if that first one is slow.
5. **Customer portal.** **Settings** > **Billing** > **Customer portal**: allow customers to update
   their payment method, cancel **at the end of the billing period**, and switch between the four
   subscription prices (DoMe Pro and DoMe Pro Plus, monthly and yearly). Do not allow pausing.
6. **Paste the seven values into GitHub (staging).** Open
   https://github.com/Heshh999/DoMe/settings/environments (repository **Settings** > **Environments**)
   and click **staging**.
   - Under **Environment secrets**, click **Add environment secret** twice (names exactly as written,
     capital letters and underscores):

     | Name | Value |
     | --- | --- |
     | `DOME_STRIPE_SECRET_KEY` | the secret key from step 3 (`sk_test_...`) |
     | `DOME_STRIPE_WEBHOOK_SECRET` | the signing secret from step 4 (`whsec_...`) |

     After each one, click **Add secret**. They must be **Environment secrets** of **staging**: never
     repository secrets (the Deploy button refuses to run while one exists) and never **variables**
     (variables are not hidden; the Deploy button refuses that too, and the key must then be rolled).
   - Under **Environment variables**, click **Add environment variable** five times, then **Add
     variable** after each:

     | Name | Value: the `price_...` id of |
     | --- | --- |
     | `DOME_STRIPE_PRICE_PRO_MONTHLY` | DoMe Pro, $9.99 monthly |
     | `DOME_STRIPE_PRICE_PRO_ANNUAL` | DoMe Pro, $99.99 yearly |
     | `DOME_STRIPE_PRICE_PRO_PLUS_MONTHLY` | DoMe Pro Plus, $24.99 monthly |
     | `DOME_STRIPE_PRICE_PRO_PLUS_ANNUAL` | DoMe Pro Plus, $249.99 yearly |
     | `DOME_STRIPE_PRICE_TOPUP` | DoMe AI top-up, $2.99 one-time |

     ("ANNUAL" is the yearly price.) Price ids are not secret; the five must all be different.
7. **Deploy staging and try it.**
   1. https://github.com/Heshh999/DoMe/actions/workflows/deploy.yml > **Run workflow**: **environment:
      staging**, **action: deploy** > **Run workflow**, and approve it as in section 7.
   2. In the run's **Summary**, the row **Billing (Stripe)** must say **"Stripe: TEST MODE — no real
      charges"**, and the row **billing (/v1/plans)** "reports test, as configured". **What to do next**
      repeats the webhook address and the events of step 4: compare them with Stripe. If the run stopped
      instead, the sentence under the headline says which value to fix (section 14).
   3. Sign in at https://staging.domeremote.com and open **Billing**: it says **"Test mode: no real
      charges"**. Choose DoMe Pro and pay on Stripe's page with the test card **`4242 4242 4242 4242`**,
      any future expiry date, any 3-digit CVC, any name and postcode. Back in DoMe the page says
      "Pending activation" and turns to **Pro** within a minute.
   4. Choose **Manage subscription**, cancel, and check the Billing page shows the end date.
   5. In Stripe (Test mode) > **Developers** > **Webhooks** > your endpoint, the deliveries should show as
      succeeded (200). Tell the engineer what you saw; that run is what lets DoMe call payments "tested
      with Stripe".
   Pro Plus and the AI top-up are not offered while the assistant is off; that is expected.
8. **Production.** Same steps, in the GitHub **production** environment, with the webhook endpoint
   **`https://domeremote.com/v1/billing/webhook`** (production must already be live, section 11). You can
   do it in two rounds:
   - **First in Test mode (optional).** Paste the Test mode values (the same `sk_test_...` key and the five
     test `price_...` ids; a **second** Test mode webhook endpoint for `https://domeremote.com/v1/billing/webhook`
     with its own `whsec_...`) into **production**, run Deploy for production, and check the summary says
     **"Stripe: TEST MODE — no real charges"**. While it does, **anyone** who visits domeremote.com can get
     Pro with the test card, for free, so keep this short.
   - **Then Live mode.** First, in Stripe (Test mode) > **Customers** or **Subscriptions**, cancel every
     test subscription made on production **immediately**, and check those accounts' Billing page says
     Free. (After the switch DoMe uses the live key, which cannot see test subscriptions: one left over
     would keep its plan, and only an engineer can fix that.) Then switch the Stripe dashboard to **Live
     mode** and repeat steps 2 to 5 there: new live prices (same amounts, tax-inclusive), the live secret
     key (`sk_live_...`), a live webhook endpoint `https://domeremote.com/v1/billing/webhook` with its own
     `whsec_...`, and the portal settings. In GitHub > **production**, click the pencil next to each of
     the seven values and replace it with the live one (all seven: test and live prices, keys and signing
     secrets never mix). Run Deploy for production: the summary must say **"Stripe: LIVE — real
     charges"**. Real cards are charged from then on.
9. **Switching payments off again, or with no values** (today). Delete all seven values from that GitHub
   environment and run Deploy: the Deploy button also removes the two Stripe secrets from Fly, the summary
   says **"Stripe: off"** and notes that billing was switched off. Nothing can be bought, the Billing page
   says upgrading is not available yet, and the website keeps "Coming at paid launch". Subscriptions that
   already exist keep running and charging at Stripe, but DoMe no longer hears about them: cancel them in
   Stripe first. Deleting only some of the seven stops the next Deploy instead ("Payments are half set
   up").

## 11B. Screen view (beta) *(per vendor docs, not yet exercised)*

Screen view shows your PC's screen on the phone. It is built (protocol 1.4) but **off**, and it stays
off until you turn it on. Read this whole section before you do.

**The switch is for everyone at once.** `DOME_SCREEN_VIEW_ENABLED` is one switch for the whole
service, not per account: turned on, every signed-in account sees the "Screen view (beta)" tile, while
the website's FAQ and pricing still say "Coming" until the launch gate in `docs/ACCEPTANCE.md`
Scenario 26 is met. Turn it on in **staging** first; leave production off until the device round and
the launch gate are done.

**A PC needs a Windows build that includes Screen view.** Every DoMe build made so far does not
(the PC answers "This version of DoMe on your PC doesn't include Screen view yet"). The first one
needs the LGPL video library built by GitHub > Actions > **ffmpeg-lgpl** > **Run workflow** (never
run yet): an engineer then pins it and makes a new Windows build.

Steps, per environment (staging first):

1. **Relay server key (optional but needed on mobile data).** In the Cloudflare account you use for
   the domain: Dashboard > **Realtime** > **TURN Server** > **Create**, named `domeremote-staging`
   (later `domeremote-production`). Copy the **key id** and the **API token** with Cloudflare's copy
   buttons. Also create one read-only API token for analytics so DoMe can read its own TURN usage every
   hour (an engineer names the exact permission). Check whether Cloudflare asks for a payment method
   and turn on its usage notifications (`docs/design/screen-view.md` §20; free up to 1,000 GB a month,
   then $0.05/GB, to verify on the vendor page). Without a key Screen view still works where a direct
   connection exists (most home Wi-Fi), with an honest message elsewhere.
2. In GitHub > Settings > **Environments** > **staging**: add `DOME_TURN_KEY_ID` under **Environment
   variables**, and `DOME_TURN_API_TOKEN` and `DOME_TURN_ANALYTICS_TOKEN` under **Environment secrets**
   (never as variables, never as repository secrets).
3. Add the **Environment variable** `DOME_SCREEN_VIEW_ENABLED` = `true` (absent or `false` keeps it off).
4. Run **Deploy** for staging as usual. The summary says "Screen view: ON (beta)" and whether the relay server
   is on; `https://staging.domeremote.com/healthz` shows `screen_view: on`, `turn: ok` and
   `turn_month_gb`.
5. Re-pair your iPhone with the PC once (on the PC: tray > Paired phones > remove the phone, then pair
   again), so the phone pins the PC's key; without it the phone says "Pair again".
6. On the PC: tray > Paired phones > your phone > **Allow Screen view…** (only there, never from the
   phone).

Once a month: compare Cloudflare's TURN usage with `/healthz` `turn_month_gb` (`turn_month_basis`
says whether it is Cloudflare's own figure or DoMe's estimate); if they differ by more than 10 % or
usage nears 1,000 GB, tell an engineer.

To turn Screen view off again: set `DOME_SCREEN_VIEW_ENABLED` to `false` (or delete it) and run Deploy.
Sessions that are running continue until they stop; nothing new starts.

## 12. Redirects and DNSSEC *(per vendor docs, not yet exercised)*

Only `https://domeremote.com` serves DoMe. The other names send visitors there. All of this is in
https://dash.cloudflare.com.

**In the domeremote.com zone:**

1. **DNS > Records > Add record**: Type **AAAA**, Name `www`, IPv6 address `100::`, **orange cloud
   (Proxied) ON**. (This record only exists so Cloudflare can answer and redirect.)
2. **Rules > Overview > Create rule > Redirect Rule**. **Rule name**: anything, for example `www to
   domeremote.com`. Then **Wildcard pattern**: Request URL `http*://www.domeremote.com/*`, Target URL
   `https://domeremote.com/${2}`, Status code **301**, tick **Preserve query string**, then **Deploy**.

**In the domeremote.app zone:**

1. Add two records, both **orange cloud ON**: Type **AAAA**, Name `@`, `100::`; and Type **AAAA**, Name
   `www`, `100::`.
2. Two redirect rules as above (each with any Rule name), with Request URL `http*://domeremote.app/*`
   and `http*://www.domeremote.app/*`, both with Target URL `https://domeremote.com/${2}`, **301**, and
   Preserve query string.

**DNSSEC, in each of the two zones** (it stops others from faking your domain's addresses): **DNS >
Settings > Enable DNSSEC**. Cloudflare finishes it by itself within 1-2 days.

## 13. Stopping or undoing *(per vendor docs, not yet exercised)*

To stop paying for **production**, do **both** of the following on fly.io:

1. Delete the app `domeremote-production`: open the app > **Settings** > delete.
2. Delete its database **separately**: **Managed Postgres** > `domeremote-production-db` > delete.
   Deleting the app does not do this, and the database data is gone for good.

To stop **staging**: delete the app `domeremote-staging` on fly.io the same way. Its Neon database is
free; to remove it and its data too, open the project in Neon > **Settings** > **Delete project**.

Then revoke the Fly tokens (Tokens page), delete the GitHub Environment (Settings > Environments), the
Auth0 application or tenant, and the Cloudflare records.

## 14. If a run stops: what the summary means

Each run ends with a headline: **finished**, **Stopped: needs you**, **Failed: needs an engineer**, or
just **Failed**.

**An engineer** means whoever maintains DoMe's code for you, for example a Claude Code session working
on this repository. Give them only the link to the run page; the log is built to show no secrets.
Never give them a token or a password, and never approve a run they started (section 3).

Look for the sentence under the headline in this table first: it can be something you can fix
yourself even when the headline says "needs an engineer".

| The summary says | What to do |
| --- | --- |
| "Some values this deploy needs are missing or not usable" | Add exactly the names listed, on the tab it names. Check they are under Settings > **Environments** > the right environment, on the right tab (**secrets** vs **variables**). |
| "... exist as repository secrets" | Delete them under Settings > **Secrets and variables** > **Actions** > **Repository secrets**. For `FLY_API_TOKEN`: revoke that token on the Fly Tokens page, create a new one, and paste it only as an **Environment secret** (section 4). For `DOME_OIDC_CLIENT_SECRET`: rotate it in Auth0 and paste the new one as an Environment secret. For `DATABASE_URL`: reset the password in Neon (section 6) and paste the new address only as the **staging** Environment secret. For `DOME_STRIPE_SECRET_KEY`: in Stripe > **Developers** > **API keys**, **Roll key**, and paste the new key only as an Environment secret. For `DOME_STRIPE_WEBHOOK_SECRET`: in Stripe > **Developers** > **Webhooks** > the endpoint, roll the **Signing secret**, and paste the new one only as an Environment secret (section 11A, step 6). Run Deploy again. |
| "The GitHub Environment ... does not exist yet" or "... is not protected yet" | Set that environment up exactly as in section 3; the summary lists which setting is wrong. Run Deploy again. |
| "Staging keeps its data in a free Neon database, and its address ... is missing" | Do section 6, steps 3 and 4, then run Deploy for staging again. |
| "... is Neon's pooled address", "... is not a database connection string", "... does not include `sslmode=require`", "... does not include `channel_binding=require`", "... has stars where the password should be", "... contains a space, a quote or #", "... has an unreadable port number" or "... is missing its user name, password, host or database name" | In Neon > **Connect**, turn **Connection pooling** off, copy with the **copy** button, and paste it over the staging secret `DATABASE_URL` (section 6, steps 3 and 4). Run Deploy again. |
| "The production Environment secret `DATABASE_URL` is a Neon address" | Delete `DATABASE_URL` from GitHub > Settings > Environments > **production** > **Environment secrets** (the bin icon next to it). Production keeps its data in Fly's database (section 11); the Neon address belongs only to staging. Run Deploy again. |
| "Payments are half set up in the ... environment: ... missing" | Payments need all seven Stripe values or none. Either add the names listed (the two `DOME_STRIPE_SECRET_KEY` / `DOME_STRIPE_WEBHOOK_SECRET` under **Environment secrets**, the `DOME_STRIPE_PRICE_...` ones under **Environment variables**; section 11A, step 6), or delete every `DOME_STRIPE_` value from that environment to keep payments off. Run Deploy again. Nothing was changed. |
| "`DOME_STRIPE_SECRET_KEY` in the staging environment is a **live** key" | Staging only ever uses Stripe's Test mode. Switch the Stripe dashboard to **Test mode**, copy the secret key there (`sk_test_...`, section 11A step 3) and paste it over the staging secret. The live key belongs only in **production**. |
| "... is Stripe's **publishable** key", "... holds the webhook's signing secret", "... is not a Stripe secret key" or "... is too short to be a whole Stripe key" | `DOME_STRIPE_SECRET_KEY` must be the **Secret key** from Stripe > **Developers** > **API keys** > **Reveal** (`sk_test_...` or `sk_live_...`), copied with the copy button; the `pk_...` key and the `whsec_...` secret go elsewhere (section 11A, steps 3 and 4). |
| "`DOME_STRIPE_WEBHOOK_SECRET` must be the webhook endpoint's **Signing secret**" or "... holds a Stripe API key, not the webhook's signing secret" | Copy the **Signing secret** (`whsec_...`) of the right endpoint: Stripe > **Developers** > **Webhooks** > the endpoint the summary names > **Signing secret** > **Reveal** (section 11A, step 4). |
| "`DOME_STRIPE_...` contains a space, a quote or #" | Copy the value again with Stripe's copy button and paste it over the old one. |
| "... holds a product id (`prod_...`), not a price id", "... is not a Stripe price id" or "... hold the same price id" | In Stripe > **Product catalog**, open the product, click the price named in the summary and copy its id (`price_...`). Each of the five variables needs a different price (section 11A, step 6). |
| "The variable `DOME_STRIPE_PRICE_...` holds a Stripe secret instead of a price id" | Variables are not hidden, so that key or signing secret must be replaced: follow the summary (Stripe **Roll key**, or roll the webhook's **Signing secret**), paste the new one under **Environment secrets**, and put the `price_...` id in the variable. |
| "`DOME_STRIPE_SECRET_KEY` (or `DOME_STRIPE_WEBHOOK_SECRET`) is saved as an Environment **variable**" | Roll it in Stripe as the summary says, add the new value under **Environment secrets** with the same name, and delete the **variable** (bin icon). Run Deploy again. |
| "Screen view's relay server is half set up in the ... environment: ... is missing" | Both come from Cloudflare > **Realtime** > **TURN Server** > the key for that environment: the key id is the **variable** `DOME_TURN_KEY_ID`, the token is the **secret** `DOME_TURN_API_TOKEN` (section 11B). Add the missing one, or delete every `DOME_TURN_` value from that environment to keep the relay server off. Run Deploy again. Nothing was changed. |
| "`DOME_TURN_API_TOKEN` (or `DOME_TURN_ANALYTICS_TOKEN`) is saved as an Environment **variable**" | Variables are not hidden, so treat that token as seen by others: delete it in Cloudflare and create a new one as the summary says, paste the new one under **Environment secrets** with the same name, and delete the **variable** (bin icon). Run Deploy again. |
| "`DOME_TURN_ANALYTICS_TOKEN` is set, but the relay server key is not" | Add `DOME_TURN_KEY_ID` and `DOME_TURN_API_TOKEN` too (section 11B), or delete the analytics token. |
| "The variable `DOME_SCREEN_VIEW_ENABLED` must be `true` or `false`" | Fix the variable's value (exactly `true` or `false`), or delete it to keep Screen view off. |
| "The variable `DOME_TURN_KEY_ID` is not a TURN key id" or "... contains a space, a quote or #, or is too short to be a whole token" | Copy the value again from Cloudflare > **Realtime** > **TURN Server** with the copy button and paste it over the old one. |
| "Fly did not remove the secrets (DOME_STRIPE_...)" | Fly did not do that step; nothing was deployed. Run Deploy again; if it repeats, share the run link with an engineer. |
| "/v1/plans reports billing ..., but this deploy configured ..." or "/v1/plans did not answer with a billing mode" | Share the run link with an engineer. |
| Note: "Billing was switched **off** by this deploy" | Expected only if you deleted all seven Stripe values on purpose (section 11A, step 9). Existing subscriptions keep charging at Stripe: cancel them there, or put the seven values back and run Deploy. |
| Note: "**Production is in Stripe Test mode**" | Expected while you try production in Test mode (section 11A, step 8). Keep it short; before Live mode, cancel every test subscription. |
| "Staging and production would both use the Fly app ..." | GitHub > Settings > **Secrets and variables** > **Actions** > **Variables** tab: correct `FLY_STAGING_APP` or `FLY_PRODUCTION_APP` so that the two names differ (without these variables, staging is `domeremote-staging` and production is `domeremote-production`). Run Deploy again. Nothing was changed. |
| "Production has no database yet ... about $41 a month" | If you agree, run Deploy for production again with **create the database** ticked. If the summary also says the production secret `DATABASE_URL` was not used, you can delete that secret. If it lists other Fly databases by name, do not tick: ask an engineer first. |
| "The **create the database** tick is for production only" | Nothing to do: staging uses its Neon database and nothing was created. |
| "Staging is deployed but not healthy (HTTP 503)", "Staging's database at Neon is not answering", or a staging deploy says `/healthz` is HTTP 503 | Open https://console.neon.tech first. If the project says this month's compute-hours are used up, staging is down until the next month, and no new version can reach production until then (production itself keeps running). Either wait for the next month, or ask an engineer about switching Neon to a paid plan for the rest of the month (Neon bills by use with no monthly minimum; `docs/COST_MODEL.md` section 2). If you reset the Neon password, paste the new address into GitHub and run Deploy. Otherwise share the run link with an engineer. |
| "Fly did not finish the deploy" (staging) | If the log says the database could not be reached, check Neon as in the row above: staging's migrations cannot run while its Neon database is paused. Otherwise share the run link with an engineer. |
| "Could not read the apps of organization ..." or "Fly refused this token" | Copy the token again from Fly (whole value, with `FlyV1 `) into `FLY_API_TOKEN`. If you use another Fly organization, check the variable `FLY_ORG`. |
| "personal sign-in token (it starts with fo1_)" | Create an **organization** token on the Fly Tokens page (section 4) and paste it over the old one. |
| "The Fly app name ... belongs to someone else" | GitHub > Settings > **Secrets and variables** > **Actions** > **Variables** tab > **New repository variable**: `FLY_STAGING_APP` (or `FLY_PRODUCTION_APP`) = another name, for example `domeremote-staging-2`. Run Deploy again. |
| "Fly is still preparing the database" | Nothing is broken. Run Deploy again in about 10 minutes. |
| "Fly would not let this token create or list Managed Postgres databases" | Follow the dashboard steps in the summary: create the database on fly.io (name, region, plan, storage 10 GB and PostgreSQL 16 are listed; about $41 a month), then run Deploy again. |
| "Production only receives a commit that staging already runs" | If the staging check in that summary shows `HTTP 503` or `database unavailable`, open https://console.neon.tech first (the HTTP 503 row above): a staging deploy cannot finish while Neon's compute-hours are used up. Otherwise run Deploy for **staging** again, wait for it to finish green, then run production right away. If anyone changed the code in between, staging must be deployed again first. |
| "The sign-in provider ... could not be reached" or "names a different issuer" | Check `DOME_OIDC_ISSUER` is the Auth0 **Domain** of the right tenant. |
| "looks like the Client Secret, not the Client ID" | Rotate the secret in Auth0 (Credentials), put the new secret under **Secrets** and the Client ID under **Variables**. |
| "... sign-in request carries a different client id" or "... sends visitors to ... instead of the sign-in provider" | Check that `DOME_OIDC_ISSUER` and `DOME_OIDC_CLIENT_ID` both come from the same Auth0 application of the right tenant. If they do, share the run link with an engineer. |
| "a WebSocket upgrade to /ws/agent ..." or "resolves, but unhealthy" | Share the run link with an engineer. |
| "no DNS record yet" | Add the Cloudflare record(s) listed (sections 8 and 11). |
| "DNS record is proxied by Cloudflare" | Click the record's orange cloud so it turns grey ("DNS only"). |
| "DNS points somewhere else" | Replace the record(s) with the ones listed. |
| "certificate not issued yet" | Wait 10 minutes, then run **status**. |
| **Failed** (without "needs ...") | Fly or GitHub did not do one step. Run Deploy once more; if the same step fails again, share the run link with an engineer. |
| "Failed: needs an engineer" (and nothing above fits) | Share the run link with an engineer. Do not retry production repeatedly. |
| GitHub shows "branch not allowed" | Check section 3, step 4: the deployment branch rule must name the default branch exactly. |
| A deploy started without asking you to approve | Open Settings > Environments > that environment now, tick **Required reviewers** with `Heshh999`, untick the administrator bypass and save (section 3, steps 2 and 3); check the branch rule (step 4). If the environment was missing, GitHub created it without protection when the run started, so set it up exactly as in section 3. Tell an engineer. |
| There is no **Run workflow** button | The repository must stay public, and the Deploy workflow must be on the default branch. Ask an engineer. |

## 15. Where every value lives

| Name | Secret? | Where you paste it | Where it comes from |
| --- | --- | --- | --- |
| `FLY_API_TOKEN` | **yes** | GitHub > Environments > staging / production > Environment **secrets** (never a repository secret) | Fly > organization > Tokens (one per environment) |
| `DOME_OIDC_CLIENT_SECRET` | **yes** | GitHub > Environments > staging / production > Environment **secrets** (never a repository secret) | Auth0 > Application > Settings > Client Secret |
| `DATABASE_URL` (staging) | **yes** | GitHub > Environments > **staging** > Environment **secrets** (required; never a repository secret) | Neon > project `domeremote-staging` > **Connect**, Connection pooling off (the direct connection string, section 6) |
| `DATABASE_URL` (production) | **yes** | Only if a production summary asks: GitHub > Environments > **production** > Environment **secrets** | Fly > Managed Postgres > cluster > Connect (the pooled URL); never the Neon address (the Deploy button refuses it) |
| `DOME_OIDC_ISSUER` | no | GitHub > Environments > ... > Environment **variables** | Auth0 > Application > Settings > Domain |
| `DOME_OIDC_CLIENT_ID` | no | GitHub > Environments > ... > Environment **variables** | Auth0 > Application > Settings > Client ID |
| `VITE_DOME_SUPPORT_URL` (optional) | no (public) | GitHub > Environments > ... > Environment **variables** | your support mailbox |
| `DOME_SUPPORT_RESPONSE_EXPECTATION` (optional) | no | GitHub > Environments > ... > Environment **variables** | your promise, for example "within 2 business days" |
| `DOME_STRIPE_SECRET_KEY` (optional: payments) | **yes** | GitHub > Environments > staging / production > Environment **secrets** (never a repository secret, never a variable) | Stripe > **Developers** > **API keys** > Secret key: `sk_test_...` (Test mode; the only kind staging accepts) or `sk_live_...` (Live mode, production only) |
| `DOME_STRIPE_WEBHOOK_SECRET` (optional: payments) | **yes** | GitHub > Environments > staging / production > Environment **secrets** (never a repository secret, never a variable) | Stripe > **Developers** > **Webhooks** > the endpoint `https://staging.domeremote.com/v1/billing/webhook` (staging) or `https://domeremote.com/v1/billing/webhook` (production) > Signing secret (`whsec_...`) |
| `DOME_STRIPE_PRICE_PRO_MONTHLY`, `DOME_STRIPE_PRICE_PRO_ANNUAL`, `DOME_STRIPE_PRICE_PRO_PLUS_MONTHLY`, `DOME_STRIPE_PRICE_PRO_PLUS_ANNUAL`, `DOME_STRIPE_PRICE_TOPUP` (optional: payments) | no | GitHub > Environments > staging / production > Environment **variables** | Stripe > **Product catalog** > the product > the price: its `price_...` id (Pro monthly, Pro yearly, Pro Plus monthly, Pro Plus yearly, AI top-up), from the same mode (Test or Live) as the secret key. All seven Stripe values or none (section 11A) |
| `DOME_SCREEN_VIEW_ENABLED` (optional: Screen view) | no | GitHub > Environments > staging / production > Environment **variables** | `true` turns Screen view on for **every** account of that environment; absent or `false` = off (section 11B) |
| `DOME_TURN_KEY_ID` (optional: Screen view relay server) | no | GitHub > Environments > staging / production > Environment **variables** | Cloudflare > **Realtime** > **TURN Server** > the key: its id (one key per environment) |
| `DOME_TURN_API_TOKEN` (optional, with the key id) | **yes** | GitHub > Environments > staging / production > Environment **secrets** (never a variable, never a repository secret) | Cloudflare > **Realtime** > **TURN Server** > the key: its API token |
| `DOME_TURN_ANALYTICS_TOKEN` (optional, with both above) | **yes** | GitHub > Environments > staging / production > Environment **secrets** | Cloudflare > API tokens: a read-only analytics token (the engineer names the permission) |
| `FLY_ORG` (optional) | no | GitHub > Environments > staging **and** production > Environment **variables** | only if not your Personal organization |
| `FLY_STAGING_APP`, `FLY_PRODUCTION_APP` (optional) | no | GitHub > Settings > Secrets and variables > Actions > **Variables** (repository) | only if a summary says the app name is taken |
| Session secret, entitlement signing key, production's database password | **yes** | nowhere: the Deploy button creates them inside Fly | generated during the first deploy |

Not needed now: an Anthropic (AI) key (the assistant stays off; the Deploy button never sets it).
`DOME_STRIPE_AUTOMATIC_TAX` is never set either: Stripe Tax stays off, because the prices are
tax-inclusive (section 11A, step 2).

## 16. What is still not done before production can be called ready

The Deploy button gets production running. These items are still open, and every production deploy
summary lists them again until an engineer closes them:

- a rehearsal of restoring the database from a backup;
- a rehearsal of recovering from a failed deploy;
- an outside uptime monitor on https://domeremote.com/healthz that alerts you;
- the database backup retention, written down in `docs/OPERATIONS.md`;
- a support contact (`VITE_DOME_SUPPORT_URL`);
- ~~an Auth0 email provider~~ **done 2026-10-20**: Resend on the verified sending domain
  `mail.domeremote.com`, From `DoMe Remote <no-reply@mail.domeremote.com>`, connected under Auth0
  **Branding > Email Provider**; Auth0's test email arrived. Resend's free plan allows 3,000 emails a
  month and at most 100 a day [vendor page, October 2026]. Still open: Google sign-in either off or with
  your own keys, and DoMe does not yet refuse sign-in for an unverified address
  (`cloud-api/dome_api/auth/oidc.py` reads `email` but never `email_verified`);
- signing in yourself on production (the button cannot test sign-in);
- branch protection with required review on the default branch, so that no code reaches the Deploy
  button without someone reading it first (an engineer sets this up; `docs/SECURITY.md` section 6).

Before `DOME_SCREEN_VIEW_ENABLED` is `true` in **production** (section 11B), also: the device round on
your PC and iPhone (`docs/ACCEPTANCE.md` Scenario 26), a signed Windows build that includes Screen view,
counsel's answer on H.264, and the privacy notice naming the connection relay provider as a
sub-processor (the draft on the Privacy page carries a FOUNDER note for it).

## 17. Code signing and releases *(per vendor docs, not yet exercised)*

This section makes DoMe for Windows downloadable as a **signed** program: Windows then shows your name as
the publisher instead of "Unknown publisher". Until you do it, domeremote.com/download keeps saying
"not yet published", and only the team's unsigned test builds exist.

> **Honest status (2026-10-20).** The release workflow on GitHub ("Release (signed Windows app)") and
> everything behind it are written and checked by automated tests, but **have never run**: there is no
> signing account, identity check or update key yet. Your first run (step 6) is the real test. The
> Microsoft and GitHub screens below come from their documentation, read on 2026-10-20 through the
> public copies of those pages on github.com; nobody has clicked through them for DoMe yet, and Azure's
> pages move often. If a button has moved, look for the same words nearby.

**Words you will see**

- **Code signing**: a digital stamp on DoMe's programs that says who made them and that nobody changed
  them since.
- **Azure Artifact Signing**: Microsoft's paid signing service (it was called "Trusted Signing" until
  January 2026). It does the stamping for DoMe; you never handle a certificate file.
- **Identity validation**: Microsoft checks your ID once, so the name in the stamp is really yours.
- **SmartScreen**: the blue "Windows protected your PC" window. It can appear even for signed programs
  while a version is new.
- **Update key**: a second, separate key that signs the small file describing each release, so DoMe
  and the website can tell a real release from a fake one.

### Step 1. Decide the name Windows will show

The cheapest route is an **individual** identity: the publisher Windows shows is **your legal name,
exactly as on your ID**. Microsoft offers this to individual developers in the **US or Canada**
(organizations also in the EU, UK and some other countries). If you later sign as a company, that is a
new identity and Windows' trust in DoMe (SmartScreen reputation) starts again from zero, so choose now.

**Cost:** Artifact Signing **Basic, $9.99 a month**, billed from the day you create the signing account
and **not pro-rated** (a part month costs the full month). It includes 5,000 signatures a month; each
DoMe release uses 4. GitHub stays free.

### Step 2. Azure: your signing account and your identity

1. Create an Azure account at https://azure.microsoft.com (a card is needed). When asked, the billing
   account type must be **Individual**, and its **legal name and address must match your ID exactly**.
2. In the Azure portal (https://portal.azure.com): **Subscriptions** > your subscription > **Resource
   providers** > find `Microsoft.CodeSigning` > **Register**.
3. Search for **Artifact Signing** > **Create**: choose your subscription, a new resource group (any
   name, for example `dome-signing`), an account name (for example `dome-signing`), region **East US**,
   and the **Basic** plan. Its endpoint is then `https://eus.codesigning.azure.net` (note it; step 4
   needs it).
4. Open the new signing account > **Access control (IAM)** > **Add role assignment** > role **Artifact
   Signing Identity Verifier** > assign it to yourself.
5. In the signing account: **Identity validation** > **New identity** > **Public Trust** > **Individual**.
   Type your name exactly as on your ID, an email address you can read (its link expires after 7 days)
   and your address as on your ID or a utility bill.
6. Follow the identity check (through Microsoft's partner AU10TIX): a PIN by email, your phone number,
   scan a QR code with your phone, a photo of your passport, driving licence or ID card plus a selfie,
   then the result is added to the **Microsoft Authenticator** app on your phone and shared back with
   Artifact Signing. You get three upload attempts.
7. Wait: Microsoft says **1 to 20 business days**. (Older forum answers from 2025 said individuals could
   not do this; Microsoft's current quickstart says they can.)
8. When the status says **Completed**: **Certificate profiles** > **Create** > **Public Trust**, name it
   `dome-windows`, and pick the identity you just validated. The name shown on the profile is what
   Windows will show; write it down exactly.

### Step 3. Let GitHub sign without a password

GitHub proves who it is to Azure with a short-lived token for each run, so no Azure password is ever
stored in GitHub.

1. In the Azure portal: **Microsoft Entra ID** > **App registrations** > **New registration**, name it
   `dome-github-release`, **Register**.
2. In that app: **Certificates & secrets** > **Federated credentials** > **Add credential** > scenario
   **GitHub Actions deploying Azure resources**: organization `Heshh999`, repository `DoMe`, entity type
   **Environment**, environment name `release`. Save.
3. Back on your signing account: **Access control (IAM)** > **Add role assignment** > role **Artifact
   Signing Certificate Profile Signer** > assign it to the app `dome-github-release`.
4. Copy three values (none of them is a secret): from the app's **Overview**, the **Directory (tenant)
   ID** and the **Application (client) ID**; from **Subscriptions**, the **Subscription ID**.

### Step 4. GitHub: the `release` environment

Open https://github.com/Heshh999/DoMe/settings/environments.

1. **New environment** named `release` (lower case, exactly), set up like section 3: tick **Required
   reviewers** with `Heshh999`, leave "Prevent self-review" unticked, **untick** "Allow administrators to
   bypass configured protection rules", **Save protection rules**.
2. **Deployment branches and tags** > **Selected branches and tags**: add one **Branch** rule with the
   default branch name (`claude/stoic-heisenberg-txq6ja` today). Add **no** tag rule. Releases are
   started only by you, with **Run workflow** on the default branch (step 6 and step 7). A version tag
   that anyone pushes can then never reach the signing step: GitHub refuses the `release` environment
   for it, so its run stops before anything is signed and never gets the update key or the signing
   identity.
3. **Environment variables** (none of these is secret):

| Name | Value |
| --- | --- |
| `AZURE_TENANT_ID` | the Directory (tenant) ID from step 3 |
| `AZURE_CLIENT_ID` | the Application (client) ID from step 3 |
| `AZURE_SUBSCRIPTION_ID` | the Subscription ID from step 3 |
| `ARTIFACT_SIGNING_ENDPOINT` | `https://eus.codesigning.azure.net` (the endpoint of your region) |
| `ARTIFACT_SIGNING_ACCOUNT` | the signing account's name (for example `dome-signing`) |
| `ARTIFACT_SIGNING_PROFILE` | `dome-windows` |
| `DOME_SIGNING_PUBLISHER` | the name on the certificate profile, exactly (step 2.8) |

4. Repository **Settings** > **General** > **Releases**: turn on **Immutable releases** (once a release
   is published, its files can never be swapped).

### Step 5. The update key

This key signs the small file that describes each release. It never goes to Azure.

1. On your own PC, open the **DoMe folder that holds both `testkit` and `pc-agent`** (the folder you
   extracted the test-kit ZIP into; `testkit` and `pc-agent` sit side by side in it). Open Windows
   PowerShell right there: in File Explorer, open that folder, click the address bar at the top, type
   `powershell` and press **Enter** (a blue window opens in that folder; not as administrator). Then run
   this one line:

   ```powershell
   cd pc-agent; uv run python packaging/release/make_update_key.py --out "$env:USERPROFILE\Desktop\dome-key"
   ```

   If it says "Cannot find path ... pc-agent", the window is in the wrong folder (for example inside
   `testkit`): close it and open PowerShell again from the folder that holds both.

2. Open the new file `dome-update-key.pem` on your Desktop (folder `dome-key`) with Notepad, copy
   **everything**, and paste it into GitHub > Settings > Environments > **release** > **Environment
   secrets** > **Add environment secret**, name `DOME_UPDATE_SIGNING_KEY`. Never paste it anywhere else.
3. Delete the `dome-key` folder (and empty the Recycle Bin).
4. Send the engineer the 64-character line the command printed (the **public** key; it is not a secret).
   The engineer pins it in DoMe and sets the release's version number. Releases cannot start before that.

### Step 6. First run, without publishing

1. GitHub > **Actions** > **Release (signed Windows app)** > **Run workflow**: the version the engineer
   gives you, **publish** left **off**.
2. GitHub asks you to approve the `release` environment. **Approve only a run you started yourself in
   the last few minutes** (section 3). Before you approve, check the run's page: next to the run's name
   it must show the default branch (`claude/stoic-heisenberg-txq6ja` today) and **workflow_dispatch**
   (Run workflow), with your name as the one who started it. A run that shows a tag (`v...`), another
   branch or another person: do not approve it, and tell the engineer.
3. When the run is green, open it and download **release-files** under **Artifacts**. Unzip it and run
   `DoMe-Setup-<version>.exe` on your PC. Before that, right-click it > **Properties** > **Digital
   Signatures**: it should show your name.
4. Tell the engineer what Windows showed. A blue SmartScreen window naming you as the publisher is
   expected for weeks; anything else (an unknown publisher, a red "blocked" window) is not.

If the run stops at its first step with "Release signing is not configured. Missing: ...", add exactly the
names it lists in the `release` environment (steps 4 and 5) and run it again. Nothing was signed.

### Step 7. Publish

1. Run the workflow again yourself with **publish** on, from the default branch, and approve it the
   same way (check the branch and that you started it). Publishing is always this: nobody publishes by
   pushing a version tag (step 4 keeps tags out of the `release` environment).
2. The release appears on https://github.com/Heshh999/DoMe/releases with the installer and its
   checksum.
3. The engineer copies two files from it into the website; when the checks pass, press **Deploy** for
   **production** (section 11). Only then does domeremote.com/download show the download button.

### Step 8. What to expect afterwards

- SmartScreen may warn about each new version for weeks: Microsoft says reputation grows with
  downloads ("hundreds of clean installs" and several weeks are typical), and that certificates bought
  for extra trust (EV) no longer skip this. Keep the same identity: changing it starts over.
- Each release uses 4 signatures of the 5,000 a month in Basic.
- To stop paying: delete the signing account in Azure. Releases you already published stay validly
  signed, because each signature carries a timestamp.
- The certificates Microsoft makes for each signing run are only valid for 3 days; the timestamp is what
  keeps a signature valid afterwards. Nothing to do on your side.

**Sources** (read 2026-10-20): Artifact Signing pricing https://azure.microsoft.com/en-us/pricing/details/artifact-signing/
(figures confirmed by https://devclass.com/2026/01/14/code-signing-windows-apps-may-be-easier-and-more-secure-with-new-azure-artifact-service/);
quickstart (eligibility, identity validation, roles) https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart;
signing integrations https://learn.microsoft.com/en-us/azure/artifact-signing/how-to-signing-integrations;
FAQ (no pro-rata) https://learn.microsoft.com/en-us/azure/artifact-signing/faq;
GitHub's guide to signing in to Azure without a password https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-azure;
immutable releases https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases;
SmartScreen reputation https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation.
