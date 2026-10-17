# Q&A preparation: 15 hard questions

Facts to keep in mind: development-set results are 69/69 adversarial blocked and 29/29 benign completed (98 end-to-end cases, `deliverables/security_results.md`); the repo suite has 300 passing tests. Held-out blind numbers: see `deliverables/heldout_results.md` if present (not available when this was written).

**1. What stops the LLM from being jailbroken?**
Nothing needs to, by design. The LLM only emits typed intents (`ParsedRequest`); it has no tool that moves money, never builds an action, and never sees an IBAN it could substitute. Actions are expanded deterministically from the registry, then judged by a policy engine the model cannot talk to. A fully jailbroken parser can at worst propose a different intent, which still hits the same rules and the same confirmations.

**2. Is the guard just regexes?**
The ContentGuard is rule-based, yes, and we say so. It is the first layer, not the one we rely on. The load-bearing defences are structural: IBANs come only from the registry, `derived_from_untrusted` payments are an R4 deny, and execution needs confirmation bound to a params hash. If the regexes miss a novel injection, the text is still only data and cannot choose a destination.

**3. Why is the attack-blocked rate 100%? Is it overfit?**
It is a development-set number: 69 cases we wrote while building the guard, so some rules were shaped by them. We do not claim it generalises. That is why a separate blind held-out set is produced; the deck reports those numbers next to it. The more meaningful point is that many cases succeed through the structural invariants rather than detection.

**4. What did you not build?**
No real bank connection (sandbox ledger, simulated FX rates and SMS OTP), no public-chain anchoring, no federated risk scoring, one demo persona and one currency pair (USD/EUR), no KYC/AML screening, no production authentication. The ClaudeParser exists but the offline demo uses the deterministic rules parser.

**5. How would this work with a real bank?**
The `Ledger` and `ReceiptStore` are interfaces. A real deployment would implement them against open-banking/payment APIs (e.g. PSD2 payment initiation), replace the simulated OTP with the bank's strong customer authentication, and keep the policy engine as a gate in front. The registry would be the bank's verified payee list. We have not done this integration.

**6. Why not federated learning?**
A risk-scoring model needs cross-institution fraud labels and legal data-sharing agreements that a competition prototype cannot responsibly fake. Our decisions are explainable rules with named rule ids, which is what a compliance reviewer can audit. Federated risk scoring is listed as future work, not implemented.

**7. Why a hash chain instead of a blockchain?**
Tamper evidence needs an append-only, verifiable log, not consensus. Each receipt commits to intent, action, simulation, policy and confirmation hashes plus the previous receipt. It detects edits locally. It does not stop someone with full database control from rewriting the whole chain; anchoring hashes publicly would address that and is future work.

**8. What if the beneficiary registry itself is poisoned?**
Adding a payee is an R3 action needing OTP, and a payee under 72 hours old forces step-up on every payment. That narrows the window, but if the user approves a malicious payee with a valid OTP, the system trusts it. Registry integrity ultimately rests on the user and the bank's authentication.

**9. Does the user just click Approve on everything? Confirmation fatigue?**
That is why friction is proportionate: R2 is one tap, only R3 asks for OTP, and R4 can never be approved. Cards show the exact payee, amount, IBAN tail and policy checks, and the confirmation is bound to a params hash, so what is approved is what executes (a modified hash is rejected as `param_tamper`). We did not run a usability study.

**10. What about the FX provider lying about the rate?**
Quotes are parsed strictly: unknown fields are dropped and a `tool_output_poisoning` event is recorded; the simulation and the confirmation card show the numbers before anything executes. The FX rates are fixed in the sandbox, so we have not tested a live market feed or manipulated rates.

**11. Can one user access another user's money?**
`TENANT_ISOLATION` is an R4 rule: accounts and payees must belong to the session user, and the overview contains only own-tenant data. Our F3 tests (extraction and privilege escalation) check that the other customer's balance and IBAN never appear and that another tenant's session cannot confirm Lina's action.

**12. What happens on replay or double-click?**
Execution uses an idempotency key. A second confirmation returns `replayed` and balances do not change; tests cover double and triple confirm. A wrong or missing OTP never executes a step-up action.

**13. How is this compliant with financial regulation?**
We do not claim certification. We designed for compliance-friendly properties: explainable deterministic decisions with rule ids and policy version, strong-customer-authentication-style step-up, limits (4,000 per payment, 5,000 per day), a reserve floor, and an auditable receipt of every executed and denied action. KYC/AML, data residency and licensing are out of scope for the prototype.

**14. What is the role of the LLM, then? Why not just a form?**
Understanding an ambiguous goal ("pay tuition and rent, keep 600 euros") and planning a multi-step sequence including an FX leg, then explaining it in plain language. That is the interaction value. The rules parser handles the demo language offline; Claude (optional, falls back to rules on any error) widens coverage of phrasing. We have not measured the Claude parser's accuracy against the rules parser.

**15. What is actually new here?**
Not any single control. The combination: a hard split of proposal, authority and execution for a money agent; risk-proportionate confirmation tied to a params hash; and a Proof-of-Intent receipt for every action including denials, plus a built-in Adversarial tab so judges can attack it live.
