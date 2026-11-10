"""The application inbox: one opt-in tracking address per person on the receiving subdomain, the Resend Receiving webhook,
verification codes read in code for the auto-apply worker, employer replies handed to the existing email classifier, and a
copy sent to the person's own inbox. Spec: docs/auto-apply.md §3.4; contract: shared/inbox.ts = contract/inbox.py.

    address    the local part (``jordan.k7m4q``): folded first name, last initial, five random characters
    svix       the webhook signature, checked in our own code
    mail       recipients on our domain, sender addresses, HTML to text
    codes      verification codes and ATS verification links (pure, no model)
    provider   `InboundMailProvider` and its Resend implementation (REST over httpx2, no SDK)
    service    `InboxService`: the person's side, receive, process, reconcile, and the auto-apply seam

Layering: ``core``, ``contract`` and ``storage.ports`` only. The classifier and the spend reservation reach it as a callable the
container builds (`service.Classify`), so it never imports ``apply`` or ``guard``.
"""
