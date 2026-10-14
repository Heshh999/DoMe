# Putting DoMe online: the founder's setup guide

This guide takes you from "accounts not created" to "DoMe running at https://domeremote.com". You
never type a command. You create accounts, paste values into settings pages, and press one button on
GitHub called **Deploy**.

> **Honest status (2026-10-14).** The Deploy button and everything behind it are written and tested
> against stand-ins, but **have never run against the real Fly.io, Neon, Auth0 or Cloudflare**. Your
> first staging run is the real test. Each run ends with a plain-English summary that says what happened
> and what to click next. The vendor screens below come from each vendor's documentation, read on
> 2026-10-13 (Neon's on 2026-10-14). They are marked *(per vendor docs, not yet exercised)* because
> nobody has clicked through them for DoMe yet, and dashboards change. If a button has moved, look for
> the same words nearby.

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
| Database storage | included (free plan: 0.5 GB) | 10 GB at $0.28/GB: about $3 |
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
while a repository secret named `FLY_API_TOKEN`, `DOME_OIDC_CLIENT_SECRET` or `DATABASE_URL` exists,
and tells you to delete it and make a new token.

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

- 0.5 GB of storage: plenty for testing.
- 100 compute-hours a month. Neon counts size times time; at the smallest size (0.25) that is about
  400 hours awake, a little over half a month.
- The database sleeps after about 5 minutes without use and wakes on the next visit.
- A 6-hour restore window: Neon can rewind the database to any moment in the last 6 hours, no further.
- If the month's compute-hours run out, Neon pauses the database until the next month. Staging then
  stops working; production is not affected. So **do not leave a test PC linked to staging around the
  clock**: while it is connected, staging and its database stay awake.

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
   - Leave the branch, database and role as Neon shows them. If Neon offers several formats, choose
     the plain connection string.
   - Click the **copy** button. What you copied starts with `postgresql://`, contains the database
     password, has **no** `-pooler` in it, and ends with `?sslmode=require&channel_binding=require`
     (or just `?sslmode=require`).
4. Right away, open GitHub > Settings > Environments > **staging** > **Environment secrets** > **Add
   environment secret**. Name: `DATABASE_URL`. Value: paste. Click **Add secret**. Only staging needs
   it; production does not.

That address contains the database password: treat it like a token (section 1). If it ever leaks, or
you just want a new password: in Neon open the project > **Branches** > the default branch > **Roles &
Databases**, open the menu next to the role (normally `neondb_owner`) and choose **Reset password**.
Copy the connection string again (step 3), paste it over the staging secret `DATABASE_URL` (in GitHub,
click the pencil next to it, paste, **Update secret**), and run Deploy for staging. Every staging deploy stores the address from
GitHub in Fly again, so nothing else is needed.

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
   not use Neon and needs no `DATABASE_URL` secret.
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
| "... exist as repository secrets" | Delete them under Settings > **Secrets and variables** > **Actions** > **Repository secrets**. For `FLY_API_TOKEN`: revoke that token on the Fly Tokens page, create a new one, and paste it only as an **Environment secret** (section 4). For `DOME_OIDC_CLIENT_SECRET`: rotate it in Auth0 and paste the new one as an Environment secret. Run Deploy again. |
| "The GitHub Environment ... does not exist yet" or "... is not protected yet" | Set that environment up exactly as in section 3; the summary lists which setting is wrong. Run Deploy again. |
| "Staging keeps its data in a free Neon database, and its address ... is missing" | Do section 6, steps 3 and 4, then run Deploy for staging again. |
| "... is Neon's pooled address", "... is not a database connection string", "... does not include `sslmode=require`" or "... has stars where the password should be" | In Neon > **Connect**, turn **Connection pooling** off, copy with the **copy** button, and paste it over the staging secret `DATABASE_URL` (section 6, steps 3 and 4). Run Deploy again. |
| "Production has no database yet ... about $41 a month" | If you agree, run Deploy for production again with **create the database** ticked. |
| "The **create the database** tick is for production only" | Nothing to do: staging uses its Neon database and nothing was created. |
| "Staging is deployed but not healthy (HTTP 503)", or a staging deploy says `/healthz` is HTTP 503 | Open https://console.neon.tech. If the project says this month's compute-hours are used up, staging waits until next month (section 6). If you reset the Neon password, paste the new address into GitHub and run Deploy. Otherwise share the run link with an engineer. |
| "Could not read the apps of organization ..." or "Fly refused this token" | Copy the token again from Fly (whole value, with `FlyV1 `) into `FLY_API_TOKEN`. If you use another Fly organization, check the variable `FLY_ORG`. |
| "personal sign-in token (it starts with fo1_)" | Create an **organization** token on the Fly Tokens page (section 4) and paste it over the old one. |
| "The Fly app name ... belongs to someone else" | GitHub > Settings > **Secrets and variables** > **Actions** > **Variables** tab > **New repository variable**: `FLY_STAGING_APP` (or `FLY_PRODUCTION_APP`) = another name, for example `domeremote-staging-2`. Run Deploy again. |
| "Fly is still preparing the database" | Nothing is broken. Run Deploy again in about 10 minutes. |
| "Fly would not let this token create or list Managed Postgres databases" | Follow the dashboard steps in the summary: create the database on fly.io (name, region, plan, storage 10 GB and PostgreSQL 16 are listed; about $41 a month), then run Deploy again. |
| "Production only receives a commit that staging already runs" | Run Deploy for **staging** again, wait for it to finish green, then run production right away. If anyone changed the code in between, staging must be deployed again first. |
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
| `DATABASE_URL` (production) | **yes** | Only if a production summary asks: GitHub > Environments > **production** > Environment **secrets** | Fly > Managed Postgres > cluster > Connect (the pooled URL) |
| `DOME_OIDC_ISSUER` | no | GitHub > Environments > ... > Environment **variables** | Auth0 > Application > Settings > Domain |
| `DOME_OIDC_CLIENT_ID` | no | GitHub > Environments > ... > Environment **variables** | Auth0 > Application > Settings > Client ID |
| `VITE_DOME_SUPPORT_URL` (optional) | no (public) | GitHub > Environments > ... > Environment **variables** | your support mailbox |
| `DOME_SUPPORT_RESPONSE_EXPECTATION` (optional) | no | GitHub > Environments > ... > Environment **variables** | your promise, for example "within 2 business days" |
| `FLY_ORG` (optional) | no | GitHub > Environments > staging **and** production > Environment **variables** | only if not your Personal organization |
| `FLY_STAGING_APP`, `FLY_PRODUCTION_APP` (optional) | no | GitHub > Settings > Secrets and variables > Actions > **Variables** (repository) | only if a summary says the app name is taken |
| Session secret, entitlement signing key, production's database password | **yes** | nowhere: the Deploy button creates them inside Fly | generated during the first deploy |

Not needed now: an Anthropic (AI) key (the assistant stays off until paid plans exist), Stripe keys
(payments are not built yet).

## 16. What is still not done before production can be called ready

The Deploy button gets production running. These items are still open, and every production deploy
summary lists them again until an engineer closes them:

- a rehearsal of restoring the database from a backup;
- a rehearsal of recovering from a failed deploy;
- an outside uptime monitor on https://domeremote.com/healthz that alerts you;
- the database backup retention, written down in `docs/OPERATIONS.md`;
- a support contact (`VITE_DOME_SUPPORT_URL`);
- an Auth0 email provider, and Google sign-in either off or with your own keys;
- signing in yourself on production (the button cannot test sign-in);
- branch protection with required review on the default branch, so that no code reaches the Deploy
  button without someone reading it first (an engineer sets this up; `docs/SECURITY.md` section 6).
