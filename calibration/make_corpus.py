#!/usr/bin/env python3
"""Build the fixed M2 labeled corpus (corpus-v1) for the four harness labels.

The corpus is authored, not model-derived: each example's `gold` is the primary
action the sender needs, independent of what the pinned model predicts. Labeling
policy (also recorded in calibration/README.md):

  billing    money/account administration on an EXISTING subscription: charges,
             invoices, refunds, payment methods, tax, cancellation/renewal,
             seat/plan changes on an existing paid account.
  sales      pre-purchase/commercial evaluation or NEW revenue: pricing, demo,
             quote, plan selection, trial, contract, partnership/reseller/OEM,
             procurement for a new purchase.
  technical  product malfunction/configuration/usage: errors, outages,
             integrations, SDK/API, deployment, login, performance, data sync.
  other      non-product inbound: press, careers, legal/privacy/security
             disclosure, research, community, brand, sponsorship, vendor
             proposals.

Distractors do not change the gold: an explicit refund request caused by a bug is
billing; a request to fix the bug is technical.

Every emitted state is `ROUTING_GUIDE + message`. The guide is caller-owned input,
not harness magic: label uids alone were insufficient for the 1.7B (a 56-example
bare-text pilot reached only 46.4% accuracy, collapsing sales into billing/other).
The guide is therefore part of the calibrated input distribution; it is not held
out from the corpus and semantic overlap with examples is expected in few-shot
prompting. The corpus file also carries the original `message` for audit.

Splits are deterministic (stratified 20/10/10 per label = 80 train / 40 validation
/ 40 test) from the fixed seed. The emitted JSONL is the source of truth; this
script only records how it was laid out. Run from anywhere:

    python3 calibration/make_corpus.py
"""

import hashlib
import json
import pathlib
import random

LABELS = ("billing", "other", "sales", "technical")
SEED = 20261023
SPLIT_COUNTS = (("train", 20), ("validation", 10), ("test", 10))

# Caller-owned routing guide prepended to every state. The primitive's system
# prompt only lists label uids; a 4-way department router needs the labels pinned
# to meanings. A 56-example bare-text pilot showed the 1.7B collapsing sales into
# billing/other (46.4% accuracy; 14 of 15 sales wrong); this guide made a 10-case
# probe 9/10. The guide is part of the deployed input distribution and is
# calibrated as such.
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

TEXTS = {
    "billing": [
        "I was charged twice for the same invoice this month and need the duplicate refunded.",
        "Where can I download a VAT receipt for my last payment?",
        "My card expired and the renewal payment failed; how do I update it?",
        "Please cancel my subscription at the end of the current billing period.",
        "Why did the price on my invoice increase without notice?",
        "I need a refund for the annual plan I bought yesterday.",
        "Can you switch my account from monthly to annual billing?",
        "The invoice lists three seats but we only use two.",
        "Our purchase order number is missing from the invoice.",
        "I want to update the billing email on the account.",
        "The payment was declined even though the card has funds.",
        "Please send a copy of all invoices from last year.",
        "I was promised a discount but the charge is still full price.",
        "How do I remove a seat to lower next month's bill?",
        "My company needs a W-9 before we can pay the invoice.",
        "The refund I requested last week has not appeared yet.",
        "Why am I being billed for an API plan I canceled?",
        "Can I pay by bank transfer instead of credit card?",
        "The tax amount on the invoice looks wrong for our state.",
        "I need to change the currency on my subscription.",
        "Our finance team needs the invoice addressed to a different legal entity.",
        "The free trial charged me before it ended.",
        "Please apply the credit note to the next invoice.",
        "I lost access to the billing portal and need to update my card.",
        "What is the prorated charge for adding two users mid-cycle?",
        "The receipt shows the wrong company name.",
        "I want to dispute a charge from June.",
        "Can you split this invoice into two separate payments?",
        "My subscription renewed but I meant to cancel it.",
        "Do you offer invoicing with net 30 terms?",
        "The checkout page charged sales tax twice.",
        "I need the bank details for a wire transfer to pay this invoice.",
        "Why is there a separate charge for storage this month?",
        "Please stop automatic renewal on our account.",
        "The coupon code was accepted but not applied to the total.",
        "I need a refund because the plan does not include the feature we were promised.",
        "Our account was suspended for non-payment but we paid yesterday.",
        "Can I get an itemized breakdown of the annual charge?",
        "The billing contact left the company; who should receive invoices?",
        "Please update the billing address and tax ID on our account.",
    ],
    "technical": [
        "The API returns 500 errors whenever I upload a file larger than 10 MB.",
        "Our webhook endpoint stopped receiving events after the last deploy.",
        "I cannot log in even after resetting my password.",
        "The dashboard is stuck on a blank screen in Chrome.",
        "Requests to the EU region are timing out intermittently.",
        "The SDK throws a type error when I pass a custom header.",
        "Our data sync job has been failing every night this week.",
        "How do I rotate an API key without downtime?",
        "The search endpoint returns stale results for several minutes.",
        "I need help configuring SSO with Okta.",
        "The mobile app crashes on launch after the latest update.",
        "Your status page says operational but we see elevated latency.",
        "The CSV export is missing the last column.",
        "A deployment deleted our environment variables.",
        "The rate limit headers are missing from responses.",
        "I cannot connect to the database from the new VPC.",
        "The OAuth callback redirects to the wrong URL.",
        "Our build fails with a segmentation fault in the CLI.",
        "The websocket disconnects every few minutes.",
        "How do I enable debug logging for the agent?",
        "The API returns a 403 even though the token has admin scope.",
        "Timezone conversion is wrong in the scheduled report.",
        "We see duplicate messages after enabling retries.",
        "The GraphQL query works in staging but not production.",
        "The file upload completes but the asset never appears.",
        "I need to restore a deleted project from backup.",
        "The browser extension cannot reach localhost.",
        "Our CI pipeline cannot pull the private container image.",
        "The API pagination cursor repeats the same page.",
        "After upgrading the library, all timestamps shifted by one hour.",
        "The server logs show out-of-memory errors under load.",
        "I cannot invite a teammate because the email never arrives.",
        "The webhook signature validation fails in Python.",
        "Our DNS change has not propagated to your service.",
        "The CLI hangs when I run the deploy command.",
        "The API rejects JSON with a 422 but the payload looks valid.",
        "We need to increase the request timeout for a long-running job.",
        "The audit log is missing events from yesterday.",
        "The service worker caches an old version of the app.",
        "The invoice PDF endpoint returns 500 for every request.",
    ],
    "sales": [
        "Can you send pricing for the enterprise plan with 200 seats?",
        "I would like to schedule a demo for our engineering team.",
        "Do you offer a nonprofit discount?",
        "What is the difference between the team and business plans?",
        "We are evaluating vendors and need a quote for 50 users.",
        "Can we get a trial extension before deciding?",
        "I want to buy the annual plan for my startup.",
        "Do you have a reseller program for agencies?",
        "Please connect me with sales about a volume discount.",
        "Our procurement team needs a contract and security questionnaire.",
        "Is there an educational license for a university lab?",
        "Can we add a custom SLA to the enterprise agreement?",
        "I need a comparison of your plans for a board presentation.",
        "What would it cost to migrate 500 users from our current vendor?",
        "Do you offer on-premise deployment for large customers?",
        "I am interested in a partnership or OEM agreement.",
        "Can we pay annually and get a lower per-seat price?",
        "Please send a proposal for a pilot with 20 users.",
        "Is there a startup program with credits?",
        "We need a quote that includes premium support.",
        "Can your team join a call to discuss our requirements?",
        "What is the minimum contract length for the enterprise tier?",
        "I want to upgrade from the free plan to a paid team plan.",
        "Do you have a government or public-sector purchasing vehicle?",
        "Can we get a sandbox environment for a proof of concept?",
        "Our CFO wants to know the total cost for three years.",
        "Are there partner margins for referring new customers?",
        "I need a feature list to compare against a competitor.",
        "Can we negotiate the renewal price for our 80 seats?",
        "Please send the sales deck and pricing sheet.",
        "Do you offer training packages with the enterprise plan?",
        "We are opening a new subsidiary and need a separate contract.",
        "What support tiers are available for business customers?",
        "I want to book a technical discovery call before buying.",
        "Can we start with a department-level pilot and expand later?",
        "Our security team needs SSO and audit logs before we can sign the enterprise contract.",
        "Our RFP requires a completed vendor questionnaire.",
        "Can you introduce us to a reference customer in our industry?",
        "I need approval from procurement; what documents do you provide?",
        "We want to standardize on your product across five teams.",
    ],
    "other": [
        "Where is your company headquartered?",
        "How do I apply for a job at your company?",
        "I am a journalist and would like to interview your CEO.",
        "Do you have a security disclosure policy for researchers?",
        "Can you sponsor our local developer conference?",
        "I need your company's diversity and inclusion report.",
        "Who should I contact about a data processing agreement?",
        "Are you hiring remote engineers in Europe?",
        "I want to report a vulnerability in your website.",
        "Can you share your environmental sustainability policy?",
        "I am writing a research paper and need a press kit.",
        "Do you offer internships for university students?",
        "Please remove my personal data from your marketing list.",
        "I want to file a complaint about a support interaction.",
        "Can your legal team sign a mutual NDA before a conversation?",
        "Where can I find your accessibility statement?",
        "I am from a charity and want to know about community programs.",
        "Do you publish an annual transparency report?",
        "Where can I find the list of subprocessors you use?",
        "Can I visit your office for a school project?",
        "Who handles corporate communications at your company?",
        "Where can I find your code of conduct?",
        "Please send information about your bug bounty program.",
        "Do you have a customer advisory board?",
        "I am a student and need data for a class project.",
        "How do I contact your privacy officer?",
        "Can you donate software licenses to a nonprofit?",
        "I want to invite your team to speak at a meetup.",
        "Where can I download your logo and brand guidelines?",
        "Does your company have an open-source foundation?",
        "I received a phishing email claiming to be from you.",
        "Can you confirm whether a recruiter actually works for you?",
        "I need the mailing address for legal notices.",
        "Are your offices wheelchair accessible?",
        "I am writing an article about your API and need a comment from your team.",
        "How do I request a speaker for an event?",
        "Where can I find your annual report for investors?",
        "I am a vendor and want to submit a proposal for services.",
        "Can you explain your policy on AI training data?",
        "I want to know if your company is publicly traded.",
    ],
}


def main() -> int:
    here = pathlib.Path(__file__).parent
    for label in LABELS:
        texts = TEXTS[label]
        assert len(texts) == 40, f"{label}: {len(texts)} texts, expected 40"
        assert len(set(texts)) == len(texts), f"{label}: duplicate text"
        for text in texts:
            assert text and text == text.strip(), f"{label}: blank/padded text"
            assert "\n" not in text and "\r" not in text, f"{label}: multiline text"
            assert text.isascii(), f"{label}: non-ASCII text: {text!r}"
    all_texts = [t for label in LABELS for t in TEXTS[label]]
    assert len(set(all_texts)) == len(all_texts), "duplicate text across labels"

    rng = random.Random(SEED)
    rows = []
    for label in LABELS:
        items = list(TEXTS[label])
        rng.shuffle(items)
        pos = 0
        for split, count in SPLIT_COUNTS:
            for text in items[pos : pos + count]:
                rows.append(
                    {
                        "state": {"text": ROUTING_GUIDE + text},
                        "message": text,
                        "labels": list(LABELS),
                        "gold": label,
                        "split": split,
                    }
                )
            pos += count
        assert pos == 40, f"{label}: split counts do not cover 40"

    random.Random(SEED + 1).shuffle(rows)
    ordered = []
    for i, row in enumerate(rows, start=1):
        ordered.append(
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
        json.dumps(row, separators=(",", ":"), ensure_ascii=True) + "\n" for row in ordered
    )
    corpus_path = here / "corpus-v1.jsonl"
    corpus_path.write_text(payload)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    per_label = {label: sum(1 for r in ordered if r["gold"] == label) for label in LABELS}
    per_split = {
        split: sum(1 for r in ordered if r["split"] == split)
        for split, _ in SPLIT_COUNTS
    }
    per_cell = {
        f"{label}/{split}": sum(
            1 for r in ordered if r["gold"] == label and r["split"] == split
        )
        for label in LABELS
        for split, _ in SPLIT_COUNTS
    }
    meta = {
        "schema": "umpire-calibration-corpus/0.1",
        "version": "corpus-v1",
        "created": "2026-10-23",
        "labels": list(LABELS),
        "examples": len(ordered),
        "seed": SEED,
        "split_counts": {split: count for split, count in SPLIT_COUNTS},
        "per_label": per_label,
        "per_split": per_split,
        "per_label_split": per_cell,
        "sha256": f"sha256:{digest}",
        "generator": "calibration/make_corpus.py",
        "state_format": "routing_guide + message",
        "routing_guide": ROUTING_GUIDE,
        "routing_guide_sha256": "sha256:"
        + hashlib.sha256(ROUTING_GUIDE.encode("utf-8")).hexdigest(),
        "labeling": {
            "policy": "gold = primary action requested; explicit ask beats mentioned context",
            "billing": "money/account administration on an existing subscription",
            "sales": "pre-purchase evaluation or new revenue conversation",
            "technical": "product malfunction/configuration/usage",
            "other": "non-product inbound (press, careers, legal/privacy/security, community, brand)",
        },
    }
    (here / "corpus-v1.meta.json").write_text(
        json.dumps(meta, indent=2) + "\n"
    )
    print(
        f"wrote {corpus_path} ({len(ordered)} examples, sha256:{digest[:16]})\n"
        f"  per label: {per_label}\n"
        f"  per split: {per_split}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
