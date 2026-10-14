# Putting DoMe online: the founder's setup guide

This guide takes you from "accounts not created" to "DoMe running at https://domeremote.com". You
never type a command. You create accounts, paste values into settings pages, and press one button on
GitHub called **Deploy**.

> **Honest status (2026-10-14).** The Deploy button and everything behind it are written and tested
> against stand-ins, but **have never run against the real Fly.io, Auth0 or Cloudflare**. Your first
> staging run is the real test. Each run ends with a plain-English summary that says what happened and
> what to click next. The vendor screens below come from each vendor's documentation, read on
> 2026-10-13. They are marked *(per vendor docs, not yet exercised)* because nobody has clicked through
> them for DoMe yet, and dashboards change. If a button has moved, look for the same words nearby.

Order: **staging first, then production.** Staging is a full copy at https://staging.domeremote.com,
where you can try things safely. Production only accepts the exact version that already runs well on
staging.

---

## 0. Before you start

**Time:** about 2 hours for staging (mostly waiting), about 1 hour for production.

**Monthly cost** (Fly.io prices read 2026-10-13; check fly.io/docs/about/pricing):

| Item | Per environment | Both environments |
| --- | --- | --- |
| Fly Managed Postgres, Basic plan (the database) | $38 | $76 |
| Database storage, 10 GB at $0.28/GB | about $3 | about $6 |
| One always-on machine (shared-cpu-1x, 512 MB) | $3.69 | $7.38 |
| Internet addresses (shared IPv4 + IPv6) and the first 10 certificates | free | free |
| Data sent to users in North America | $0.02 per GB | |
| **Total** | **about $45** | **about $90** |
| Auth0 (sign-in), Free plan up to 25,000 monthly active users | $0 | $0 |
| Cloudflare DNS and redirect rules | $0 | $0 |
| GitHub Actions (the Deploy button) on a public repository | $0 | $0 |

Fly.io has **no spending cap and no billing alerts**. **Deleting an app does not delete its database**:
the database keeps costing $38 a month until you delete it separately (section 12).

**Keep the GitHub repository public.** On a free GitHub account, a private repository loses
Environments and their secrets, so the Deploy button stops working. On the paid plans, private
repositories lose the "required reviewer" approval step. If you ever want the repository private,
upgrade your GitHub plan first and ask an engineer to check the setup.

## 1. Three rules about passwords and tokens

1. Paste tokens, passwords and secrets **only** into the exact field named in this guide, on
   github.com, fly.io or auth0.com.
2. **Never** paste one into a chat, an email, a GitHub issue or a document, and never give one to
   anyone, Claude included. Claude will never ask you for one. If something asks, it is wrong.
3. If one leaks anyway, **revoke it where it was made first** (the Fly Tokens page, or Auth0 >
   Credentials > Rotate). Then paste the new one into GitHub and press Deploy.

Names like `FLY_API_TOKEN` are labels, not secrets, so it is fine to write them anywhere.

## 2. Fly.io: account and card *(per vendor docs, not yet exercised)*

1. Go to **fly.io**, sign up, and confirm your email.
2. Open **Billing** and add a credit card **before the first deploy**. Without a card, Fly's free trial
   stops every machine after 5 minutes, so DoMe would look broken. Adding a card ends the trial.
   Fly may place a temporary hold of under $10.
3. Use your **Personal** organization. Do not create apps or databases by hand: the Deploy button
   does that. If you deliberately use another Fly organization, add its short name (the "slug") as
   the GitHub variable `FLY_ORG` in step 3.

## 3. GitHub: two environments *(per vendor docs, not yet exercised)*

Open https://github.com/Heshh999/DoMe/settings/environments (repository **Settings** >
**Environments**). Do the following twice: once with the name `staging`, then once with `production`
(lower case, exactly).

1. Click **New environment**, type the name, and click **Configure environment**.
2. Tick **Required reviewers**, type `Heshh999` (you), and **leave "Prevent self-review" unticked**.
   You are the only reviewer, and ticking it would lock you out. Click **Save protection rules**.
3. Under **Deployment branches and tags**, choose **Selected branches and tags**. Then **Add deployment
   branch or tag rule**, type the default branch name exactly as it is today:
   `claude/stoic-heisenberg-txq6ja`, and click **Add rule**.

Why both environments need your approval: the Fly token you store in each (next step) can manage
**every** app and database in your Fly organization, production included. With you as the required
reviewer, no run ever receives a token until you click **Approve**. That holds even if someone else
with access to the repository, or an AI assistant, starts a run.

If the default branch is ever renamed (for example to `main`), these two rules must be changed to the
new name, or every deploy is refused.

## 4. Fly tokens into GitHub *(per vendor docs, not yet exercised)*

1. On fly.io, open the dashboard. Choose your organization in the organization drop-down, then click
   **Tokens**.
2. Create an **organization token** named `github-staging`. If an expiry is offered, choose 1 year
   and put a reminder in your calendar.
3. Copy the **whole** value, including `FlyV1 ` at the start and the space after it. Right away, open
   GitHub > Settings > Environments > **staging** > **Environment secrets** > **Add environment
   secret**. Name: `FLY_API_TOKEN`. Value: paste. Click **Add secret**.
4. Repeat with a second token named `github-production`, pasted into the **production** environment as
   `FLY_API_TOKEN`.

It must be an **Environment secret** of that environment, not a "repository secret". If a Deploy
summary says a value is missing, this mix-up is the most likely cause.

## 5. Auth0 for staging *(per vendor docs, not yet exercised)*

1. Sign up at **auth0.com**. Create a tenant named, for example, `domeremote-staging`, region **US**.
   The name cannot be changed later. Under **Settings > General**, set the **Environment Tag** to
   **Development**.
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

## 6. First staging deploy

1. Open https://github.com/Heshh999/DoMe/actions, click **Deploy** in the left list, then **Run
   workflow** on the right.
2. Leave the branch as it is (the default branch). Choose **environment: staging**, **action:
   deploy**, and tick **create the database** (this is your agreement to the $38 a month). Click the
   green **Run workflow**.
3. The first job, "Checks before approval", takes about a minute. Then the run waits for you: click
   **Review deployments**, tick **staging**, and click **Approve and deploy**.
4. Wait 15-25 minutes. Creating the database alone can take 10 or more.
5. Open the run and read its **Summary**. It lists what was created, what was already there, and
   **What to do next**. Red is fine on a first try: the summary says what to fix, and you can press
   Run workflow again at any time. A second run never creates anything twice and never changes the
   generated passwords.

## 7. Cloudflare record for staging *(per vendor docs, not yet exercised)*

Do exactly what the summary's **What to do next** says. Normally that is:

1. Cloudflare > **domeremote.com** > **DNS** > **Records**. If a record named `staging` already
   exists (type A, AAAA or CNAME), delete it first.
2. **Add record**: Type **CNAME**, Name `staging`, Target exactly as the summary shows (normally
   `domeremote-staging.fly.dev`).
3. Click the orange cloud so it turns **grey ("DNS only")**. This matters: with the orange cloud, Fly
   cannot issue the security certificate. Click **Save**.

## 8. Check staging and sign in

1. About 10 minutes later, run **Deploy** again with **environment: staging** and **action:
   status**. Status needs no approval and changes nothing.
2. When the summary says **"Staging is live at https://staging.domeremote.com"**, open that address,
   create an account, and sign in. The Deploy button cannot test sign-in; only you can.
3. If you sign out and sign in again, Auth0 may let you straight back in without asking. That is
   Auth0 remembering you, and it is expected.
4. Linking a Windows PC to **staging** needs the test kit or a development build. The normal
   installer always connects to domeremote.com (production).

## 9. Auth0 for production *(per vendor docs, not yet exercised)*

Same as section 5, with these differences:

- A **separate tenant**, for example `domeremote`, with **Environment Tag: Production**.
- Application name `DoMe`. **Allowed Callback URLs**: exactly `https://domeremote.com/v1/auth/callback`.
- **Credentials > Authentication Method: Client Secret (Post)**.
- In the application's **Connections** tab, turn **off** `google-oauth2` and keep
  **Username-Password-Authentication** on. Auth0's shared Google keys are not meant for production.
  Turn Google back on only after an engineer has set up your own Google keys.
- Before real customers: set up an email provider in Auth0 for sign-up and password-reset emails
  (Auth0's built-in email is for testing).
- Paste `DOME_OIDC_ISSUER` and `DOME_OIDC_CLIENT_ID` (Variables) and `DOME_OIDC_CLIENT_SECRET` (Secret)
  into the GitHub **production** environment.

## 10. Production deploy

1. **Actions > Deploy > Run workflow**: **environment: production**, **action: deploy**, tick
   **create the database**.
2. The first job checks that staging already runs this exact version and is healthy. If not, it
   stops before asking you for anything and tells you to deploy staging first.
3. When asked: **Review deployments** > tick **production** > **Approve and deploy**.
4. When it finishes, add the two records the summary lists in Cloudflare > **domeremote.com** >
   **DNS** > **Records**. First delete any existing A, AAAA or CNAME record named `@`. Then:
   - Type **A**, Name `@`, IPv4 address from the summary, **grey cloud (DNS only)**
   - Type **AAAA**, Name `@`, IPv6 address from the summary, **grey cloud (DNS only)**
5. Run **status** for production until it says **"Production is live at https://domeremote.com"**.
   Then sign up and sign in there yourself.

## 11. Redirects and DNSSEC *(per vendor docs, not yet exercised)*

Only `https://domeremote.com` serves DoMe. The other names send visitors there.

**In the domeremote.com zone:**

1. **DNS > Records > Add record**: Type **AAAA**, Name `www`, IPv6 address `100::`, **orange cloud
   (Proxied) ON**. (This record only exists so Cloudflare can answer and redirect.)
2. **Rules > Overview > Create rule > Redirect Rule** > **Wildcard pattern**:
   Request URL `http*://www.domeremote.com/*`, Target URL `https://domeremote.com/${2}`, Status code
   **301**, tick **Preserve query string**, then **Deploy**.

**In the domeremote.app zone:**

1. Add two records, both **orange cloud ON**: Type **AAAA**, Name `@`, `100::`; and Type **AAAA**, Name
   `www`, `100::`.
2. Two redirect rules as above, with Request URL `http*://domeremote.app/*` and
   `http*://www.domeremote.app/*`, both with Target URL `https://domeremote.com/${2}`, **301**, and
   Preserve query string.

**DNSSEC, in each of the two zones:** **DNS > Settings > Enable DNSSEC**. Cloudflare finishes it by
itself within 1-2 days.

## 12. Stopping or undoing *(per vendor docs, not yet exercised)*

To stop paying for an environment, do **both** of the following on fly.io:

1. Delete the app (`domeremote-staging` or `domeremote-production`): open the app > **Settings** >
   delete.
2. Delete its database **separately**: **Managed Postgres** > `domeremote-staging-db` or
   `domeremote-production-db` > delete. Deleting the app does not do this, and the database data is
   gone for good.

Then revoke the Fly tokens (Tokens page), delete the GitHub Environment (Settings > Environments), the
Auth0 application or tenant, and the Cloudflare records.

## 13. If a run stops: what the summary means

Each run ends with a headline: **finished**, **Stopped: needs you**, or **Failed: needs an
engineer**. When you need help, share the **link to the run**, never a token or a password.

| The summary says | What to do |
| --- | --- |
| "Some values this deploy needs are missing or not usable" | Add exactly the names listed, on the tab it names. Check they are under Settings > **Environments** > the right environment, on the right tab (**secrets** vs **variables**), not under repository secrets. |
| "This environment has no database yet ... about $38 a month" | If you agree, run Deploy again with **create the database** ticked. |
| "Could not read the apps of organization ..." or "Fly refused this token" | Copy the token again from Fly (whole value, with `FlyV1 `) into `FLY_API_TOKEN`. If you use another Fly organization, check the variable `FLY_ORG`. |
| "personal sign-in token (it starts with fo1_)" | Create an **organization** token on the Fly Tokens page (section 4) and paste it over the old one. |
| "The Fly app name ... belongs to someone else" | GitHub > Settings > **Secrets and variables** > **Actions** > **Variables** tab > **New repository variable**: `FLY_STAGING_APP` (or `FLY_PRODUCTION_APP`) = another name, for example `domeremote-staging-2`. Run Deploy again. |
| "Fly is still preparing the database" | Nothing is broken. Run Deploy again in about 10 minutes. |
| "Fly would not let this token create or list Managed Postgres databases" | Follow the dashboard steps in the summary: create the database on fly.io (name, region and plan are listed), then run Deploy again. |
| "Production only receives a commit that staging already runs" | Run Deploy for **staging** first (same branch, same moment), wait for it to finish green, then production. |
| "The sign-in provider ... could not be reached" or "names a different issuer" | Check `DOME_OIDC_ISSUER` is the Auth0 **Domain** of the right tenant. |
| "looks like the Client Secret, not the Client ID" | Rotate the secret in Auth0 (Credentials), put the new secret under **Secrets** and the Client ID under **Variables**. |
| "no DNS record yet" | Add the Cloudflare record(s) listed (sections 7 and 10). |
| "DNS record is proxied by Cloudflare" | Click the record's orange cloud so it turns grey ("DNS only"). |
| "DNS points somewhere else" | Replace the record(s) with the ones listed. |
| "certificate not issued yet" | Wait 10 minutes, then run **status**. |
| "Failed: needs an engineer" | Share the run link with an engineer. Do not retry production repeatedly. |
| GitHub shows "branch not allowed" or never asks you to approve | Check section 3, step 3: the deployment branch rule must name the default branch exactly. |
| There is no **Run workflow** button | The repository must stay public, and the Deploy workflow must be on the default branch. Ask an engineer. |

## 14. Where every value lives

| Name | Secret? | Where you paste it | Where it comes from |
| --- | --- | --- | --- |
| `FLY_API_TOKEN` | **yes** | GitHub > Environments > staging / production > Environment **secrets** | Fly > organization > Tokens (one per environment) |
| `DOME_OIDC_CLIENT_SECRET` | **yes** | GitHub > Environments > staging / production > Environment **secrets** | Auth0 > Application > Settings > Client Secret |
| `DATABASE_URL` | **yes** | Only if a summary asks: GitHub > Environments > ... > Environment **secrets** | Fly > Managed Postgres > cluster > Connect (pooled URL) |
| `DOME_OIDC_ISSUER` | no | GitHub > Environments > ... > Environment **variables** | Auth0 > Application > Settings > Domain |
| `DOME_OIDC_CLIENT_ID` | no | GitHub > Environments > ... > Environment **variables** | Auth0 > Application > Settings > Client ID |
| `VITE_DOME_SUPPORT_URL` (optional) | no (public) | GitHub > Environments > ... > Environment **variables** | your support mailbox |
| `DOME_SUPPORT_RESPONSE_EXPECTATION` (optional) | no | GitHub > Environments > ... > Environment **variables** | your promise, for example "within 2 business days" |
| `FLY_ORG` (optional) | no | GitHub > Environments > ... > Environment **variables** | only if not your Personal organization |
| `FLY_STAGING_APP`, `FLY_PRODUCTION_APP` (optional) | no | GitHub > Settings > Secrets and variables > Actions > **Variables** (repository) | only if a summary says the app name is taken |
| Session secret, entitlement signing key, database password | **yes** | nowhere: the Deploy button creates them inside Fly | generated during the first deploy |

Not needed now: an Anthropic (AI) key (the assistant stays off until paid plans exist), Stripe keys
(payments are not built yet).

## 15. What is still not done before production can be called ready

The Deploy button gets production running. These items are still open, and every production deploy
summary lists them again until an engineer closes them:

- a rehearsal of restoring the database from a backup;
- a rehearsal of recovering from a failed deploy;
- an outside uptime monitor on https://domeremote.com/healthz that alerts you;
- the database backup retention, written down in `docs/OPERATIONS.md`;
- a support contact (`VITE_DOME_SUPPORT_URL`);
- an Auth0 email provider, and Google sign-in either off or with your own keys;
- signing in yourself on production (the button cannot test sign-in).
