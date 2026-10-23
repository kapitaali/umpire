#!/usr/bin/env python3
"""Build corpus-v2 by extending corpus-v1 with 320 new authored examples.

The v1 rows (ids ex-001..ex-160) are preserved byte-for-byte in content and keep
their original split assignments.  New examples add 80 per label, split 40/20/20
per label, so corpus-v2 is 480 examples with a 240/120/120 split and a 120-example
test set.  The routing guide and label policy are unchanged.

The point of preserving v1 is auditability: the v1 raw capture remains valid for
those rows, so only the 320 new rows need a live capture.  The v2 ledger/records
are the v1 pair plus the new decisions.

Run from anywhere:

    python3 calibration/make_corpus_v2.py
"""

import hashlib
import json
import pathlib
import random

HERE = pathlib.Path(__file__).resolve().parent
LABELS = ("billing", "other", "sales", "technical")
SEED = 20261023
V1 = HERE / "corpus-v1.jsonl"
V1_META = HERE / "corpus-v1.meta.json"
V2 = HERE / "corpus-v2.jsonl"
V2_META = HERE / "corpus-v2.meta.json"

# Same caller-owned guide as v1.  Importing make_corpus would be convenient but
# this script is deliberately self-contained so it can regenerate v2 without
# re-running the v1 generator.
ROUTING_GUIDE = (
    "Route the customer message to exactly one department: billing, technical, sales, or other.\n"
    "Examples:\n"
    "I was charged twice for the same invoice. -> billing\n"
    "Please update the billing address on our account. -> billing\n"
    "I need a refund for last month. -> billing\n"
    "The API returns 500 errors on upload. -> technical\n"
    "Our webhook stopped receiving events after a deploy. -> technical\n"
    "Duplicate events appear when retries are enabled. -> technical\n"
    "Can I get a quote for 50 seats? -> sales\n"
    "I want to schedule a demo of the enterprise plan. -> sales\n"
    "How do your plans compare? -> sales\n"
    "Are you hiring remote engineers? -> other\n"
    "I need your press kit for an article. -> other\n"
    "How do I submit feedback or a complaint? -> other\n"
    "Message: "
)

SPLIT_COUNTS = (("train", 40), ("validation", 20), ("test", 20))

EXTRA = {
    "billing": [
        "Our card was charged twice for the February renewal and I need one charge reversed.",
        "Please send the invoice for the team plan to our accounts payable address.",
        "I need to change the credit card on file before the next billing date.",
        "Can you explain the usage overage charge on this month's bill?",
        "We want to downgrade from the business plan to the team plan at renewal.",
        "The annual invoice should be split across two cost centers.",
        "Our tax exemption certificate is missing from the account; can you apply it?",
        "I need a copy of the receipt for the March payment.",
        "Why was our subscription renewed when auto-renew was turned off?",
        "Please remove two seats from our account effective next month.",
        "The invoice total does not match the quote we signed.",
        "Can you issue a credit note for the unused portion of the annual plan?",
        "Our payment failed because the card was replaced; how do we retry?",
        "I need to update the legal entity name on the billing account.",
        "The billing portal shows a balance due even though we paid by wire.",
        "Please change our billing frequency from monthly to quarterly.",
        "We were charged a late fee after the invoice arrived late.",
        "Can we pay the outstanding invoice in three installments?",
        "The discount code from our renewal contract is not on the invoice.",
        "I need the VAT number corrected on the last three invoices.",
        "Please cancel the add-on storage plan; we no longer need it.",
        "Our finance team needs a consolidated invoice for all five workspaces.",
        "The charge on our statement is from a currency conversion; can you explain it?",
        "I want to reactivate the subscription we paused last month.",
        "Please send a dunning notice to a different billing contact.",
        "The refund was issued to the old card; can you trace it?",
        "We need a purchase order number added to the invoice.",
        "Can you change the billing term to net 45 days?",
        "The plan renewal price is higher than the rate in our contract.",
        "I need to dispute a charge from the previous billing cycle.",
        "Please apply the prepaid credit to the next invoice.",
        "Our account is on a spend cap but we were still charged overages.",
        "Can you generate an itemized invoice by user for the quarter?",
        "I need to update the billing phone number for fraud alerts.",
        "The invoice PDF is password protected; please send an unlocked copy.",
        "We need to move the subscription to a different billing country.",
        "Please stop sending paper invoices to our old office.",
        "The trial converted to paid without a reminder; can we get a refund?",
        "Can you add a second billing contact for invoice notifications?",
        "I need the bank reference number for our latest payment.",
        "Our subscription was canceled but we still have access; will we be charged?",
        "Please correct the seat count on the renewal invoice from 25 to 22.",
        "The tax line on the invoice uses the wrong jurisdiction.",
        "Can you issue a refund for the duplicate seat we purchased by mistake?",
        "I need to change the payment method for the annual renewal.",
        "Why is there a separate charge for premium support this month?",
        "Our company requires an invoice before we can submit payment.",
        "Please extend the payment due date by one week.",
        "The coupon applied at checkout disappeared after the plan change.",
        "I need a statement of account for the last twelve months.",
        "Can you split the invoice between our US and EU entities?",
        "The billing email is bouncing; please update it to the finance alias.",
        "We need to cancel the enterprise plan at the end of the term.",
        "The prorated charge for the upgrade looks too high.",
        "Please refund the onboarding fee we were charged twice.",
        "I need to know the exact renewal date and amount for our plan.",
        "Can you add a cost center code to every invoice line?",
        "The payment receipt does not show the invoice number.",
        "We need to change the subscription owner to a different admin.",
        "Please remove the inactive user from the next seat count.",
        "The invoice was sent to the wrong department; can you resend it?",
        "I want to pay the annual plan in two equal installments.",
        "Our card was declined for a small verification charge; is the account at risk?",
        "Can you reverse the charge for the add-on we canceled before use?",
        "The billing address is outdated after our office move.",
        "I need the tax breakdown for each line item on the invoice.",
        "Please apply a loyalty discount to our upcoming renewal.",
        "The subscription renewal failed and we lost access; can you restore it?",
        "Can you send a formal past-due notice for our records?",
        "I need to update the company registration number on the invoice.",
        "The annual plan was charged at the monthly rate; please correct it.",
        "Please close the billing account for the subsidiary we sold.",
        "We need an invoice in a different currency for our local books.",
        "The refund confirmation says completed but the funds are missing.",
        "Can you waive the reinstatement fee after our payment cleared?",
        "I need to change the billing cycle to align with our fiscal year.",
        "The invoice includes a seat we removed before the billing period started.",
        "Please send the W-9 and remittance details for our vendor setup.",
        "Our subscription is paused but the invoice still shows a full charge.",
        "Can you confirm the cancellation date and that no further charges will occur?",
    ],
    "technical": [
        "The API returns a 502 when I send a batch of more than 100 records.",
        "Our GitHub integration stopped syncing pull requests after we changed the org name.",
        "I cannot complete the SSO login loop; it redirects back to the login page.",
        "The webhook payload is missing the event type field.",
        "Our scheduled export ran twice and created duplicate files.",
        "The dashboard charts show no data for the last 24 hours.",
        "I need to increase the timeout for a long-running API request.",
        "The CLI returns permission denied when I try to deploy.",
        "Our container fails to start because the health check times out.",
        "The mobile app logs me out every time I switch networks.",
        "The search API returns results from the wrong workspace.",
        "We see 429 responses even though we are under the documented limit.",
        "The webhook signature check fails after rotating the secret.",
        "I cannot connect to the database through the private endpoint.",
        "The CSV import stops at row 5,000 without an error.",
        "Our OAuth token refresh returns invalid_grant.",
        "The file upload succeeds but the thumbnail never generates.",
        "The API pagination cursor returns the first page again.",
        "Our Slack notifications are delayed by more than an hour.",
        "The service worker serves an old build after deployment.",
        "I need to restore a workspace deleted by mistake yesterday.",
        "The SAML assertion is missing the email attribute.",
        "Our CI job cannot authenticate to the container registry.",
        "The GraphQL mutation returns a 200 but does not update the record.",
        "The browser console shows a CORS error on the callback endpoint.",
        "Requests to the EU endpoint fail with a TLS handshake error.",
        "The audit log does not include API key creation events.",
        "Our cron job runs in the wrong timezone after the DST change.",
        "The API returns duplicate rows when I request the second page.",
        "I cannot revoke an API key because the button is disabled.",
        "The PDF export cuts off the last page of the report.",
        "Our webhook endpoint receives events out of order.",
        "The SDK retries a non-idempotent request and creates duplicates.",
        "The admin console shows a blank page after the latest release.",
        "I need to configure a custom domain for the status page.",
        "The API key rotation guide does not match the current UI.",
        "Our data sync fails with a foreign key constraint error.",
        "The websocket connection closes after 60 seconds of inactivity.",
        "The email invitation link expires before the user can accept it.",
        "The API response includes a null field that the schema says is required.",
        "Our staging environment cannot reach the production webhook.",
        "The image resize job consumes all available memory.",
        "I need to enable debug logs for a specific workspace.",
        "The password reset email never arrives for one user.",
        "The API rejects a valid JSON payload with a 400 error.",
        "Our reverse proxy strips the authorization header.",
        "The database migration hangs at the index creation step.",
        "The status page reports an incident that is already resolved.",
        "The API rate limit headers are missing from the response.",
        "Our SSO users are assigned the wrong default role.",
        "The webhook retries for three days after a permanent failure.",
        "The CLI stores credentials in the wrong directory.",
        "The API returns stale data after a write for several seconds.",
        "Our Kubernetes pod crashes with an out-of-memory error.",
        "The file download link returns a 403 after the first use.",
        "The webhook event includes the test payload in production.",
        "I need to change the region for an existing workspace.",
        "The API date filter ignores the timezone offset.",
        "The dashboard date range resets after every refresh.",
        "Our integration cannot parse the new webhook schema.",
        "The API returns a 500 when the optional field is omitted.",
        "The mobile app cannot upload photos from the camera.",
        "Our DNS record is not verified even after 48 hours.",
        "The API key has the wrong scopes after an upgrade.",
        "The export job completes but the file is empty.",
        "The webhook endpoint receives a challenge request but no events.",
        "Our team cannot access the audit log export.",
        "The API retry-after header is missing on 429 responses.",
        "The login page loops when cookies are blocked.",
        "The background job queue is backed up with old tasks.",
        "The API returns a 409 when creating a resource with a unique name.",
        "Our SCIM provisioning deactivates users who are still active.",
        "The webhook payload is truncated for large events.",
        "The API OpenAPI schema does not match the live response.",
        "The browser extension cannot authenticate after the token refresh.",
        "Our data warehouse connector fails on a column type change.",
        "The API returns a 413 for a file below the documented limit.",
        "The notification preferences are ignored for mention alerts.",
        "The API cursor pagination skips records after a delete.",
        "Our production deployment cannot pull the latest model artifact.",
    ],
    "sales": [
        "We are comparing three vendors and need your enterprise pricing for 150 seats.",
        "Can you schedule a technical demo for our platform team next week?",
        "I need a quote for a 12-month contract with premium support.",
        "What is the price difference between the team and business plans?",
        "We want to run a 30-day pilot with 25 users before buying.",
        "Can you send an order form for the annual plan?",
        "Our procurement team needs your MSA and DPA templates.",
        "Do you offer volume discounts for 500 seats?",
        "I want to buy 40 additional seats on our enterprise contract.",
        "We need a hosted sandbox to evaluate the API before we commit.",
        "Please connect us with a reference customer in financial services.",
        "We need a security questionnaire completed before our purchase review.",
        "Can you provide a three-year total cost of ownership estimate?",
        "I want to upgrade from the team plan to the enterprise tier.",
        "Do you have a startup program with credits for our first year?",
        "We need a quote that includes implementation services.",
        "Can your team join an RFP briefing on Friday?",
        "What support tiers are available for a 24/7 operation?",
        "We are opening a new office and need a separate contract.",
        "Can we negotiate a multi-year renewal with a price lock?",
        "I need a feature comparison against your main competitor.",
        "Do you offer on-premises deployment for regulated customers?",
        "We want to buy through a reseller in our region.",
        "Can you provide a partner margin schedule for agencies?",
        "Our CFO needs a business case template for the purchase.",
        "I need a demo of the audit log and SSO features before we decide.",
        "Can we extend the trial by two weeks to finish our evaluation?",
        "What is the minimum seat count for the enterprise plan?",
        "We need a dedicated instance and a custom SLA.",
        "Can you send a proposal for a department-wide rollout?",
        "I want to standardize on your product across four business units.",
        "Do you offer training packages for new administrators?",
        "Our security team requires a SOC 2 report before we sign.",
        "Can you provide a migration plan from our current vendor?",
        "We need a purchase order and invoice in our procurement system.",
        "What is the process for adding a new legal entity to our contract?",
        "Can we start with a small pilot and expand after 90 days?",
        "I need pricing for API usage above the included quota.",
        "Do you have a government procurement vehicle or GSA schedule?",
        "Can you introduce us to your partner for implementation services?",
        "We are evaluating a replacement and need a side-by-side demo.",
        "I want to book an executive briefing for our leadership team.",
        "Can you send the security whitepaper and architecture diagram?",
        "Our procurement portal requires a vendor registration form.",
        "What is the renewal price if we increase seats by 20 percent?",
        "Do you offer a nonprofit or education discount?",
        "We need a proof-of-concept success plan with clear milestones.",
        "Can you provide customer references for a healthcare deployment?",
        "I need a quote for 1,000 API calls per minute.",
        "Can we purchase through AWS Marketplace?",
        "Our legal team wants to review the limitation of liability clause.",
        "What onboarding assistance is included in the enterprise plan?",
        "Can you support a private cloud deployment in our VPC?",
        "We need a data residency commitment for the EU.",
        "I want to schedule a pricing call with our procurement lead.",
        "Can you send a renewal quote that includes five new departments?",
        "Do you have a partner program for system integrators?",
        "We need a custom integration built before we can purchase.",
        "What is the lead time for a dedicated instance?",
        "Can we get a trial of the enterprise features for 10 users?",
        "I need a comparison of support response times by tier.",
        "Our board approved the budget; please send the contract.",
        "Can you provide a purchase order number for our finance team?",
        "We want to consolidate three subscriptions into one enterprise agreement.",
        "Do you offer a price match against a competing quote?",
        "I need a demo focused on our compliance workflows.",
        "Can you send a proposal for a 24-month commitment?",
        "We are expanding to a new region and need local billing.",
        "What is the cost to migrate 2 TB of data?",
        "Can you provide a dedicated solutions engineer for the pilot?",
        "Our team needs a technical validation before procurement approval.",
        "I want to buy the annual plan with a single invoice.",
        "Can you support a custom data processing addendum?",
        "We need a service-level agreement with credits for downtime.",
        "Do you have a marketplace listing for our cloud provider?",
        "I need a quote for 50 seats with SSO and audit logs.",
        "Can we add a new subsidiary to our existing agreement mid-term?",
        "Our procurement cycle closes soon; what is the fastest way to sign?",
        "We need a vendor risk assessment package for the purchase.",
        "Can you provide a customer case study for our internal approval?",
    ],
    "other": [
        "I am a reporter working on a story about small AI companies and need a comment.",
        "Where can I find your company press contact for a media inquiry?",
        "I would like to apply for the senior backend engineer role.",
        "Do you offer internships for graduate students in machine learning?",
        "I need to serve a legal notice on your registered agent.",
        "Please direct me to your data protection officer for a GDPR request.",
        "I want to request a copy of the personal data you hold about me.",
        "How do I report a security vulnerability in one of your products?",
        "I am researching vendor practices and would like your AI training data policy.",
        "Can you sponsor our university hackathon?",
        "I want to invite your CTO to speak at our industry meetup.",
        "I need the approved logo files for a partner event.",
        "I am writing a book and need permission to use your product screenshot.",
        "Does your company have a supplier diversity program?",
        "I need your accessibility statement for a public sector review.",
        "Where can I find your modern slavery statement?",
        "Can you share your environmental sustainability report?",
        "I want to nominate your company for a community award.",
        "How do I submit a complaint about a recent support interaction?",
        "I received an email claiming to be from your CEO and want to verify it.",
        "Can you confirm whether a recruiter is authorized to represent you?",
        "I need the mailing address for legal correspondence.",
        "Are your offices open to visitors for a school field trip?",
        "I am a student and need company data for a class project.",
        "Can you provide a speaker for our nonprofit fundraiser?",
        "I want to include your company in a documentary about local startups.",
        "Please send the latest investor fact sheet for our analyst note.",
        "I am an analyst and need a briefing on your market strategy.",
        "Can you explain your policy on political contributions?",
        "I want to request a donation of software licenses for a charity.",
        "How do I join your customer advisory board?",
        "I am a vendor and want to submit a proposal for office supplies.",
        "Can you share your code of conduct for event participants?",
        "I need to verify your company registration number for a legal filing.",
        "I want to report a phishing site using your brand.",
        "Where can I find your list of subprocessors?",
        "Can you confirm your headquarters address for a fact-checking article?",
        "I am organizing a conference and would like your team to exhibit.",
        "How do I request an interview with your founder?",
        "I need a statement from your company about a recent industry incident.",
        "Can you provide information about your bug bounty program?",
        "I want to submit a research collaboration proposal.",
        "Where can I find your transparency report?",
        "I am a teacher and want to use your product in a classroom.",
        "Can you send a letter confirming employment for a visa application?",
        "I need to contact your ethics hotline.",
        "I want to make a freedom of information request.",
        "Can you provide your company diversity metrics?",
        "I am a podcaster and would like to interview your head of product.",
        "Where can I find your policy on responsible AI?",
        "I want to use your API for a nonprofit research project.",
        "Can you share your business continuity plan with a partner?",
        "I need your company tax identification number for a vendor form.",
        "I want to invite your team to a community career fair.",
        "How do I submit a trademark infringement report?",
        "I am a journalist and need a background briefing on your funding.",
        "Can you provide a quote for a university lecture series?",
        "I want to report an accessibility issue on your website.",
        "Where can I find your policy on data retention?",
        "I need to serve a subpoena on your legal department.",
        "Can you confirm whether your company is hiring in Canada?",
        "I want to request a tour of your data center.",
        "I am writing a case study and need your customer consent form.",
        "How do I apply for your open-source maintainer program?",
        "Can you donate to our local food bank fundraiser?",
        "I need the name of your registered agent for a lawsuit.",
        "I want to file a complaint about an advertisement.",
        "Can you provide a statement on your supply chain practices?",
        "I am a researcher and need an interview about your hiring process.",
        "Where can I find your company conflict of interest policy?",
        "I want to request permission to use your trademark in a film.",
        "Can you connect me with your investor relations team?",
        "I need to update my marketing preferences and stop receiving emails.",
        "I want to report a fake job offer using your company name.",
        "Can you share your policy on animal testing in supply chains?",
        "I am organizing a charity auction and need a donation.",
        "How do I submit a whistleblower report?",
        "I need your company accessibility contact for a complaint.",
        "Can you provide a letter of support for a community grant?",
        "I want to know if your company has a political action committee.",
    ],
}


def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    v1_rows = [json.loads(line) for line in V1.read_text().splitlines() if line]
    v1_meta = json.loads(V1_META.read_text())
    if len(v1_rows) != 160:
        raise SystemExit(f"corpus-v1 must have 160 rows, found {len(v1_rows)}")
    if v1_meta["sha256"] != "sha256:" + sha256_file(V1):
        raise SystemExit("corpus-v1 hash does not match corpus-v1.meta.json")

    for label in LABELS:
        texts = EXTRA[label]
        assert len(texts) == 80, f"{label}: {len(texts)} extra texts, expected 80"
        assert len(set(texts)) == len(texts), f"{label}: duplicate extra text"
        for text in texts:
            assert text and text == text.strip(), f"{label}: blank/padded text"
            assert "\n" not in text and "\r" not in text, f"{label}: multiline text"
            assert text.isascii(), f"{label}: non-ASCII text: {text!r}"

    all_v1 = [r["message"] for r in v1_rows]
    all_extra = [t for label in LABELS for t in EXTRA[label]]
    assert len(set(all_v1 + all_extra)) == len(all_v1) + len(all_extra), "duplicate text across corpus"

    rng = random.Random(SEED + 2)
    extra_rows = []
    for label in LABELS:
        items = list(EXTRA[label])
        rng.shuffle(items)
        pos = 0
        for split, count in SPLIT_COUNTS:
            for text in items[pos : pos + count]:
                extra_rows.append(
                    {
                        "state": {"text": ROUTING_GUIDE + text},
                        "message": text,
                        "labels": list(LABELS),
                        "gold": label,
                        "split": split,
                    }
                )
            pos += count
        assert pos == 80, f"{label}: split counts do not cover 80"

    random.Random(SEED + 3).shuffle(extra_rows)
    rows = []
    for i, row in enumerate(v1_rows + extra_rows, start=1):
        rows.append(
            {
                "id": f"ex-{i:03d}",
                "state": row["state"],
                "message": row["message"],
                "labels": row["labels"],
                "gold": row["gold"],
                "split": row["split"],
            }
        )

    payload = "".join(
        json.dumps(row, separators=(",", ":"), ensure_ascii=True) + "\n" for row in rows
    )
    V2.write_text(payload)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    per_label = {label: sum(1 for r in rows if r["gold"] == label) for label in LABELS}
    per_split = {split: sum(1 for r in rows if r["split"] == split) for split, _ in SPLIT_COUNTS}
    per_cell = {
        f"{label}/{split}": sum(1 for r in rows if r["gold"] == label and r["split"] == split)
        for label in LABELS
        for split, _ in SPLIT_COUNTS
    }
    meta = {
        "schema": "umpire-calibration-corpus/0.1",
        "version": "corpus-v2",
        "created": "2026-10-23",
        "labels": list(LABELS),
        "examples": len(rows),
        "seed": SEED,
        "split_counts": {split: count for split, count in SPLIT_COUNTS},
        "per_label": per_label,
        "per_split": per_split,
        "per_label_split": per_cell,
        "sha256": f"sha256:{digest}",
        "generator": "calibration/make_corpus_v2.py",
        "base_corpus": {
            "path": "calibration/corpus-v1.jsonl",
            "sha256": v1_meta["sha256"],
            "examples": 160,
            "preserved": True,
        },
        "added_examples": len(extra_rows),
        "state_format": "routing_guide + message",
        "routing_guide": ROUTING_GUIDE,
        "routing_guide_sha256": "sha256:"
        + hashlib.sha256(ROUTING_GUIDE.encode("utf-8")).hexdigest(),
        "labeling": v1_meta["labeling"],
    }
    V2_META.write_text(json.dumps(meta, indent=2) + "\n")
    print(
        f"wrote {V2} ({len(rows)} examples, sha256:{digest[:16]})\n"
        f"  per label: {per_label}\n"
        f"  per split: {per_split}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
