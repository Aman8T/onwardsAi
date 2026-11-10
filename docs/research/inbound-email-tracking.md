# Per-user application inbox: which inbound email service to use

*Prices and limits were fetched on 2026-11-10. "Unverified" means the fact-check could not confirm it on an official page.*

## 1. Recommendation

**Use Resend Receiving on the subdomain `apply.leafout.in`.** Pick a plan by stage:
- Free tier for the build.
- Pro plan at $20/month once real users are on it.
- Turn on the paid overage setting before you pass 100k emails a month.

**Why Resend:**
- **No nameserver change.** It needs one MX record plus the usual domain-verification records in Hostinger hPanel. The root `leafout.in` MX records (mx1/mx2.hostinger.com) are not touched. ([custom domains](https://resend.com/docs/dashboard/receiving/custom-domains))
- **True catch-all.** Mail to any address at the receiving domain is accepted, so no per-user setup call is needed. ([receiving intro](https://resend.com/docs/dashboard/receiving/introduction))
- **Signed webhooks.** Each webhook carries an HMAC-SHA256 signature (via Svix), not just a shared password. Failed deliveries are retried at 0s, 5s, 5m, 30m, 2h, 5h, 10h and 10h, and can be replayed from the dashboard. ([retries](https://resend.com/docs/webhooks/retries-and-replays.md))
- **Official Python SDK covers receiving.** `resend` v2.53.0 includes `Emails.Receiving.get/list`, attachments, and `Webhooks.verify`. ([PyPI](https://pypi.org/project/resend/))
- **Free 30-day message store with an API.** We can list and read received mail, including the raw .eml file, without building storage first. ([retrieve](https://resend.com/docs/api-reference/emails/retrieve-received-email.md))
- **Replies later on the same account.** We can send from `jordan.k7m4q@apply.leafout.in` and keep the thread intact. ([reply](https://resend.com/docs/dashboard/receiving/reply-to-emails.md))
- **Same setup as a known competitor.** Simplify's application addresses live on `resend.letterfern.com`, which receives through AWS SES inbound. That points to Resend, but it is unverified.

**Cost.** Every received email counts as one email against both the daily and the monthly limit. ([quotas](https://resend.com/docs/knowledge-base/account-quotas-and-limits.md))

| Stage | Volume | Plan | Cost |
|---|---|---|---|
| Build / internal test | under 3k/month, under 100/day | Free (3,000/month, 100/day) ([pricing](https://resend.com/pricing)) | $0 |
| Prototype, 50 users | about 2k inbound + about 2k notification copies | Pro, 50k/month, no daily cap ([pricing](https://resend.com/pricing)) | **$20/month** |
| About 5k users, receive only | about 200k | Pro 100k ($35) + 100k overage at $0.90 per 1,000 ([pricing](https://resend.com/pricing)) | **about $125/month** |
| About 5k users, plus a copy to each user | about 400k | Pro + overage, or Scale 500k | about $305 or $350/month |

The 2k-plus-2k prototype load fits Free's monthly cap but can exceed 100 a day on busy days. Hence Pro.

The overage setting must be enabled by a team admin and is capped at 400% of the plan by default. ([quotas](https://resend.com/docs/knowledge-base/account-quotas-and-limits.md))

**Do not use Resend "Inboxes"** (hosted mailboxes, one per address). They cost $1 per inbox per month beyond 3, so about $5k/month at 5k users. ([inboxes](https://resend.com/docs/dashboard/inboxes/introduction.md))

**Runner-up: Postmark Pro, $16.50/month for 10k emails (sent plus received).** ([pricing](https://postmarkapp.com/pricing))
- **Strengths:** the webhook carries the full parsed email with attachments inline (35 MB total cap), so there is no second API fetch. The searchable message history can be kept up to 365 days. ([parse](https://postmarkapp.com/developer/user-guide/inbound/parse-an-email))
- **Weaknesses:** no HMAC signature, only basic auth in the URL and an IP allowlist ([webhooks overview](https://postmarkapp.com/developer/webhooks/webhooks-overview)). It is also dearer at scale, about $224/month at 200k ([pricing.txt](https://postmarkapp.com/pricing.txt)).
- **When to switch:** if prototype testing hits one of Resend's undocumented limits, i.e. mail lost at quota or a message size cap. The webhook handler shape is nearly the same.

**Later scale move: Amazon SES receiving in ap-south-1 (Mumbai).**
- Switch when inbound passes about 100k/month or when we need data kept in India.
- About $24 to $38/month at 200k inbound, à la carte. ([SES pricing](https://aws.amazon.com/ses/pricing/))
- Mumbai has a receiving endpoint. ([endpoints](https://docs.aws.amazon.com/general/latest/gr/ses.html))
- Costs: more AWS setup, and we parse raw MIME ourselves.

## 2. Comparison of all candidates

Effort: 1 = about an afternoon, 5 = a project.

| Tool | Free tier | Cheapest plan that includes inbound | Subdomain MX at Hostinger, no NS move | Catch-all | Delivery + auth | Can send | Effort |
|---|---|---|---|---|---|---|---|
| **Resend** | 3k/month, 100/day, inbound counts ([pricing](https://resend.com/pricing)) | Pro $20, 50k ([pricing](https://resend.com/pricing)) | Yes; its MX must be lowest priority on that host ([docs](https://resend.com/docs/dashboard/receiving/custom-domains)) | Yes | Webhook is metadata only, then GET the body; Svix HMAC ([received](https://resend.com/docs/webhooks/emails/received.md)) | Yes | 1 |
| Postmark | 100/month total; inbound on Free undocumented ([pricing](https://postmarkapp.com/pricing)) | Pro $16.50, 10k; Basic excludes inbound ([pricing](https://postmarkapp.com/pricing)) | Yes, MX to inbound.postmarkapp.com ([docs](https://postmarkapp.com/developer/user-guide/inbound/inbound-domain-forwarding)) | Yes | Full parsed JSON + base64 attachments; basic auth / IP only, no HMAC ([overview](https://postmarkapp.com/developer/webhooks/webhooks-overview)) | Yes | 1 |
| Amazon SES (Mumbai) | Up to $200 credits, 6 to 12 months ([free](https://aws.amazon.com/free/)) | Pay as you go: $0.10 per 1k + $0.09 per 1k chunks (à la carte) ([pricing](https://aws.amazon.com/ses/pricing/)) | Yes ([MX](https://docs.aws.amazon.com/ses/latest/dg/receiving-email-mx-record.html)) | Yes (receipt rule on the domain) | Raw MIME via S3 + SNS; SNS signature | Yes, after leaving sandbox | 3 |
| ForwardEmail.net | $0, unlimited inbound, webhooks; config is public DNS ([pricing](https://forwardemail.net/en/private-business-email?pricing=true)) | Enhanced $3 ([pricing](https://forwardemail.net/en/private-business-email?pricing=true)) | Yes (MX + TXT on `apply`), inferred, not tested | Yes | Parsed JSON + raw MIME; HMAC-SHA256 on paid plans only ([FAQ](https://forwardemail.net/en/faq)) | Paid plans, starts at 300/day | 1 |
| Mailgun | 100/day, 1 route ([pricing](https://www.mailgun.com/pricing/)) | Basic from $15, 10k, 5 routes ([pricing](https://www.mailgun.com/pricing/)) | Yes, mxa/mxb.mailgun.org ([verify](https://documentation.mailgun.com/docs/mailgun/user-manual/domains/domains-verify)) | Yes, catchall() | JSON / form / MIME; HMAC ([receive](https://documentation.mailgun.com/docs/mailgun/user-manual/receive-forward-store/receive-http)) | Yes | 2 |
| inbound.new | None ([pricing](https://inbound.new/pricing)) | $9, 5k ([pricing](https://inbound.new/pricing)) | Probably; standalone subdomain verification unverified | Yes | Parsed JSON; static token, no HMAC ([security](https://inbound.new/docs/api-reference/security.md)) | Yes | 1 |
| CloudMailin | 10k, 512KB; custom domain likely paid only (unverified) ([plans](https://www.cloudmailin.com/plans)) | Starter $25, 10k, 2MB ([plans](https://www.cloudmailin.com/plans)) | Yes, client1/2.cloudmailin.net ([docs](https://docs.cloudmailin.com/receiving_email/custom_domains/)) | Yes, paid plans | JSON / multipart / raw; static Authorization header | Yes; newer outbound, transactional only | 1 |
| SendGrid | 60-day trial only (unverified) ([pricing](https://www.twilio.com/en-us/products/email-api/pricing)) | Essentials from $19.95; inbound plan gating unverified | Yes, after domain authentication | Yes | Multipart; ECDSA / OAuth | Yes | 2 |
| MailerSend | 500/month; every forward counts ([pricing](https://www.mailersend.com/pricing)) | Hobby $7, 5k, 1 route ([pricing](https://www.mailersend.com/pricing)) | Yes; needs parent domain verified | Yes, with custom inbound domain | JSON + raw; inbound signature unverified | Yes | 2 |
| Brevo | 300/day; inbound inclusion unverified | Unverified | Yes; must differ from sending domain | Implied | JSON; token, no HMAC | Yes | 2 |
| SparkPost / Bird | 1k/month, inbound listed ([pricing](https://bird.com/pricing/email?sp=true)) | $15 deposit at $0.40 per 1k; metering of inbound unverified | Yes | Unverified | Raw MIME JSON; static token | Yes | 3 |
| Cloudflare Email Routing | Unlimited inbound ([limits](https://developers.cloudflare.com/email-routing/limits/)) | Workers Paid $5 ([pricing](https://developers.cloudflare.com/workers/platform/pricing/)) | **No.** Needs the whole zone on Cloudflare; subdomain setup is Enterprise only ([docs](https://developers.cloudflare.com/dns/zone-setups/subdomain-setup/)) | Apex domain only | A Worker posts JSON to us; our own secret | Beta, paid | 2 (needs a separate domain) |
| ImprovMX | No webhooks on Free ([pricing](https://improvmx.com/pricing)) | Premium $9 ([pricing](https://improvmx.com/pricing)) | Unverified (not in official docs) | Yes | JSON; static source IP only ([webhooks](https://improvmx.com/guides/webhooks)) | Yes | 2 |
| Hostinger Agentic Mail | None | About Rs 29/month per mailbox, 48-month prepay ([page](https://www.hostinger.com/in/agentic-mail)) | Probably (subdomain plan unverified) | Yes, into one mailbox | Truncated body, then fetch via API; auth docs conflict | From one mailbox only | 2 |
| Hostinger Email (IMAP) | Bundled | Not priced | Yes | Yes, one mailbox | IMAP polling only, no webhook | SMTP from one mailbox | 4 |
| Zoho Mail | 5 users, no IMAP ([pricing](https://www.zoho.com/mail/zohomail-pricing.html)) | Unverified (about Rs 59 per user via resellers) | Unverified | Yes, one mailbox ([docs](https://www.zoho.com/mail/help/adminconsole/catch-all-setup.html)) | Polling only | Per seat | 4 |
| Google Workspace | None | Rs 270 per user per month ([pricing](https://workspace.google.com/pricing)) | Yes, as a secondary domain | Yes, via default routing | Pub/Sub push with no content | Per seat | 4 |
| AgentMail | 3 inboxes ([pricing](https://www.agentmail.to/pricing)) | $20 for 10 inboxes, +$2 per inbox | Yes; DMARC with rua= required | **No**, one inbox per address | Svix HMAC | Yes | 2 |
| Nylas Agent Accounts | 3 accounts, no custom domain ([pricing](https://www.nylas.com/pricing/)) | $15 for 10 accounts | Yes | **No**, one account per address | HMAC | Yes | 2 |
| Mailtrap Inbound | 4k/month shared with sending ([pricing](https://mailtrap.io/pricing/)) | Basic $15, 10k | Custom receiving domain "coming soon" vs available: contradictory | Documented | JSON; HMAC | Yes | 3 |
| MailSlurp | 250 received/month, no custom domain ([pricing](https://app.mailslurp.com/pricing/)) | $199 for a custom domain, 5k received | Unverified | Yes | Unverified | Yes (test-focused) | 3 |
| Lettermint | 300/month, no inbound ([pricing](https://lettermint.co/pricing)) | EUR 10, 10k | Yes, lettermint.mx | Yes | Webhook; auth unverified | Yes | 2 |

## 3. Ruled out

- **Cloudflare Email Routing:** needs leafout.in's nameservers moved, catch-all works on the apex domain only, and forward() caps at 200 verified destinations ([limits](https://developers.cloudflare.com/email-routing/limits/)). Only viable on a separately bought domain.
- **AgentMail and Nylas Agent Accounts:** priced per mailbox, about $9.9k/month and $1 to 2k/month at 5k users ([AgentMail](https://www.agentmail.to/pricing), [Nylas](https://www.nylas.com/pricing/)). Revisit only for the few users who need full two-way mail.
- **Resend Inboxes:** $1 per inbox per month, so per-user use is about $5k/month ([inboxes](https://resend.com/docs/dashboard/inboxes/introduction.md)). Use plain catch-all receiving instead.
- **MailSlurp, Mailsac, Mailtrap Sandbox:** built for testing; MailSlurp caps received mail at 5k/month even at $199 ([pricing](https://app.mailslurp.com/pricing/)).
- **Hostinger Email (IMAP), Zoho, Google Workspace:** polling a single shared mailbox, with no real webhook. That fits FastAPI Cloud poorly and is too slow for one-time codes.
- **ImprovMX:** only 2 webhook retries before mail is dropped, auth by source IP only, and alias caps ([webhooks](https://improvmx.com/guides/webhooks)).
- **CloudMailin:** $25 floor for a custom domain with 2MB messages, realistically $45 for PDFs ([plans](https://www.cloudmailin.com/plans)). A 4xx response bounces mail back to the sender, and the 200k price is unpublished.
- **SendGrid:** no permanent free plan, inbound plan gating unverified, and verifying signatures on multipart bodies is fiddly.
- **Brevo, SparkPost/Bird, Mailtrap Inbound:** key facts unverified (pricing, catch-all or custom-domain status).
- **inbound.new:** cheap at $9 ([pricing](https://inbound.new/pricing)), but retry behaviour is contradictory, there is no HMAC, and verifying a standalone subdomain is unverified. A possible backup.
- **Hostinger Agentic Mail:** attractive for India billing, but the webhook body is truncated, auth docs conflict, retries are undocumented, and one mailbox has unknown capacity. Prototype-only even if verified.

## 4. Integration sketch (Resend + FastAPI)

### DNS records in Hostinger hPanel

Path: Domains > leafout.in > DNS / Nameservers > Manage DNS records. The Name field takes the label relative to leafout.in, i.e. `apply`, not `apply.leafout.in` ([Hostinger](https://www.hostinger.com/support/4443666-how-to-manage-mx-records-in-hpanel)).

| Type | Name | Value | Notes |
|---|---|---|---|
| MX | `apply` | Copy from the Resend dashboard (e.g. `receive.forge.rmta.net`), priority 10 | The only MX on `apply`. Resend drops mail if its receiving MX is not verified ([docs](https://resend.com/docs/dashboard/receiving/custom-domains)). |
| TXT | `resend._domainkey.apply` (typical) | DKIM key from the dashboard | Domain verification, which receiving needs and sending reuses. Exact name unverified; copy from the dashboard. |
| MX + TXT | `send.apply` (typical) | Bounce MX + SPF from the dashboard | Only needed to send from the subdomain. Exact names unverified. |
| TXT (optional, later) | `_dmarc.apply` | `v=DMARC1; p=none; rua=mailto:dmarc@leafout.in` | Reporting only. The root DMARC is `p=none` with no `sp=`, so the subdomain inherits it. |

**Do not touch the `@` MX records** (mx1/mx2.hostinger.com) or the root SPF. Hostinger's email guides say to delete old MX records before adding new ones; that applies to the root and does not apply here.

### Address scheme

Format: `{firstname}.{lastinitial}{5 random chars}@apply.leafout.in`, for example `jordan.k7m4q@apply.leafout.in`.
- **Characters:** lowercase base32 without 0/o/1/l/i, which gives about 33M combinations per name, so addresses cannot be enumerated.
- **Table:** `inbound_address(local_part UNIQUE, user_id, created_at, status)`.
- **Stable:** one per user, never reused or reassigned, kept working after the user churns. Greenhouse cannot change the email on a submitted application ([MyGreenhouse FAQ](https://support.greenhouse.io/hc/en-us/articles/43418495049499-MyGreenhouse-FAQ-for-Candidates)).
- **No `+tags`:** some forms reject them. Match the application by sender and company instead.
- **Unknown local parts:** store as quarantine metadata only; never classify them.

### Webhook route

```python
# backend/app/routes/inbound_email.py
import json, os, re, resend
from fastapi import APIRouter, Request, Response, BackgroundTasks
from starlette.concurrency import run_in_threadpool
from svix.webhooks import Webhook, WebhookVerificationError

router = APIRouter()
resend.api_key = os.environ["RESEND_API_KEY"]
WH = Webhook(os.environ["RESEND_WEBHOOK_SECRET"])          # whsec_...
DOMAIN = "apply.leafout.in"

@router.post("/api/inbound/resend")
async def inbound(request: Request, bg: BackgroundTasks):
    raw = await request.body()                              # verify on RAW bytes
    try:
        WH.verify(raw, dict(request.headers))               # svix-id / -timestamp / -signature
    except WebhookVerificationError:
        return Response(status_code=401)
    evt = json.loads(raw)
    if evt.get("type") != "email.received":
        return Response(status_code=200)
    d = evt["data"]
    rcpts = d.get("received_for") or d.get("to") or []
    rcpts = [rcpts] if isinstance(rcpts, str) else rcpts
    user = None
    for addr in rcpts:
        local, _, dom = addr.strip("<>").lower().rpartition("@")
        if dom == DOMAIN:
            user = await db.user_by_local_part(local)
            if user: break
    try:
        # idempotency: unique on provider_email_id AND message_id
        inserted = await db.insert_inbound_stub(
            provider_email_id=d["email_id"], message_id=d.get("message_id"),
            user_id=user.id if user else None, sender=d.get("from"),
            subject=d.get("subject"), status="pending" if user else "quarantine")
    except db.TransientError:
        return Response(status_code=503)                    # Resend retries
    if inserted and user:
        bg.add_task(process_email, d["email_id"], user.id)
    return Response(status_code=200)

async def process_email(email_id: str, user_id: int):
    full = await run_in_threadpool(resend.Emails.Receiving.get, email_id=email_id)
    text = full.get("text") or html_to_text(full.get("html") or "")
    code = extract_verification(text, full.get("subject", ""))   # see below
    if code:
        await db.save_verification(user_id, code, ttl_minutes=15)
        await db.mark(email_id, "verification"); return          # do not forward OTPs
    # reuse the logic behind POST /api/apply/email (classifier + stage update)
    result = await classify_and_update_application(
        user_id=user_id, sender=full["from"], subject=full["subject"], body=text,
        in_reply_to=header(full, "In-Reply-To"))
    await notify_user_copy(user_id, full, result)            # see forwarding
    await db.mark(email_id, "processed", classification=result.label,
                  application_id=result.application_id)
```

Notes on the route:
- **SDK functions.** `resend.Emails.Receiving.get` and the attachment calls are in the official SDK. The exact field names (`received_for` being a string or a list, `data` nesting) are unverified, so log one real payload first.
- **Svix 2.x:** `verify()` no longer returns the payload, hence the separate `json.loads`.
- **Rate limit:** 10 API requests/second per team, and each email needs at least one GET ([quotas](https://resend.com/docs/knowledge-base/account-quotas-and-limits.md)).
- **Attachments:** fetch only when the classifier needs them (e.g. an offer PDF). Download URLs expire after 1 hour ([attachments](https://resend.com/docs/dashboard/receiving/attachments.md)).
- **Durability:** BackgroundTasks run in the same process. Add a cron job every 10 minutes that calls `resend.Emails.Receiving.list()` (max 100 per page) and re-queues any `email_id` not yet `processed`. This covers crashes and deploys within the 30-day retention.

### What to store

- Metadata kept indefinitely: provider id, Message-ID, user, sender, subject, received_at, classification, application_id.
- Body text kept 90 days, encrypted at rest.
- Verification codes deleted on use or after 15 minutes.
- No attachments unless the user opens them.
- Resend itself keeps mail for 30 days ([pricing](https://resend.com/pricing)).

### Copy to the user's real inbox

Do not forward the raw email. Gmail warns that forwarded mail often fails SPF ([Google](https://support.google.com/a/answer/175365)). Instead:
- Send our own message with `resend.Emails.send` from `Leafout Inbox <inbox@apply.leafout.in>`.
- Set `reply_to` to the recruiter's address.
- Subject like "[Leafout] Acme replied: Interview availability". Include the classification, the original text, and an app link for attachments.
- Each copy counts toward the quota.
- Suppress copies for ATS confirmations and codes if users prefer a daily digest.

### ATS verification codes

This is likely why auto-apply stalls. Greenhouse may email a code mid-submit, depending on its bot score ([Greenhouse](https://support.greenhouse.io/hc/en-us/articles/115005448066)).

1. `extract_verification`: a 4 to 8 character code near words like "verification", "code" or "verify", or a verify link. Follow links only when the sender or link domain is on an ATS allowlist.
2. The auto-apply worker, after submitting, polls `verification_codes` for (user, company, received after submit time) for up to about 3 minutes, enters the code, and marks it used.
3. Only the latest Greenhouse code is valid, and a new one can be requested only after 10 minutes ([MyGreenhouse FAQ](https://support.greenhouse.io/hc/en-us/articles/43418495049499-MyGreenhouse-FAQ-for-Candidates)). Always take the newest code and never retry blindly.
4. Workday sends a verification link per employer, and the proxy address becomes the login there ([Workday](https://doc.workday.com/admin-guide/en-us/human-capital-management/recruiting/career-sites/gtv1538650489786.html)). We need a vault for per-tenant passwords, or password resets through the inbox.
5. Never log codes or links in plain text.

## 5. Risks and open questions

- **ATS acceptance of the domain.** Greenhouse and Ashby docs show no blocking of unknown email domains. Ashby's fraud signals are IP, VPN and LinkedIn, not email domain ([Ashby](https://docs.ashbyhq.com/what-tools-are-available-to-help-identify-fraudulent-applications-in-our-pipeline)). Lever and SmartRecruiters are unverified. Many submissions from our server IPs are the bigger risk. Some forms check that the domain has an MX record; ours will once the record is added.
- **Recruiters see a proxy address.**
  - Use human-looking local parts and keep the real email and phone on the resume.
  - Duplicate candidate records are likely when the user has applied before with their real email.
  - Users cannot log into MyGreenhouse or Workday with their own email.
  - Interview invites are time-sensitive, so the copy must arrive within minutes.
- **Domain choice is effectively permanent.** Addresses live on submitted applications forever. Decide now between `apply.leafout.in` and a separate neutral domain (as Simplify does with Letterfern). Either way, keep the MX and forwarding alive after users churn.
- **Deliverability.** Receiving needs no reputation, and the root DMARC is `p=none`. If we later send replies from user addresses, add DKIM/SPF for `apply`, monitor DMARC reports, and keep that sending separate from leafout.in marketing mail.
- **Privacy and consent.**
  - We read employers' mail to our users. This needs explicit opt-in consent under India's DPDP Act and a privacy-policy section.
  - Resend's processing region and India residency are unverified. If residency becomes a requirement, SES Mumbai is the path.
  - Whether received emails can be deleted from Resend before 30 days is unverified.
- **Retention.** Resend keeps 30 days, so we must copy what we need. Also decide what happens to mail for deleted accounts: keep forwarding, or bounce?
- **Spam to the catch-all.** Every received email, junk included, counts toward the quota.
  - Quarantine unknown local parts without processing.
  - Alert at 80% of quota.
  - Enable overage so mail is never silently dropped. What happens to inbound mail once the quota is used up is undocumented.
- **Unverified Resend limits.** Maximum inbound message and attachment size, and how long the raw .eml link lasts. Test by sending a 10 to 20 MB PDF in week one. If it fails, switch to Postmark (35 MB documented).
- **Payment.** Card billing in USD. Whether Indian cards work for recurring charges is unverified for Resend (and for Postmark, which is credit-card only per its [billing FAQ](https://postmarkapp.com/support/article/1285-pricing-billing-faq)).
- **Not fetched.** Cloudflare's new Email Service pricing page (developers.cloudflare.com/email-service/platform/pricing) was not fetched. It is only relevant if we buy a separate domain and host it on Cloudflare.