# Research: how B2B teams prospect, and what makes a company need cybersecurity *now*

Scope: short, sourced desk research to ground the scoring model and UI. Figures are quoted
from the cited source. Where only secondary reporting was available it is marked *(secondary)*.
Each section ends with what it changes in our build.

---

## 1. How B2B sales teams prioritise accounts

**Fit and intent are scored separately.** Every major platform splits *"should we sell to them?"*
(fit: firmographics, technographics) from *"are they buying now?"* (intent: behaviour and events).
- 6sense runs an *Account Fit (ICP)* model trained on past opportunities, plus a separate *in-market
  buying stage* model (Awareness → Consideration → Decision → Purchase) ([SoftwareOne listing](https://platform.softwareone.com/product/predictive-marketing-analytics/PCP-2993-5703); [Abmatic](https://blog.abmatic.ai/third-party-intent-vs-account-scoring)) *(secondary)*.
- HubSpot keeps **fit** (industry, region, employee count, tech stack) separate from **engagement**.
  Fit scores don't decay; engagement scores do. Keeping them apart means a lot of activity can't hide a poor-fit account ([Glitter guide](https://www.glitter.io/guides/understanding-lead-scoring-in-hubspot-engagement-fit-and-combined-approaches); [Pedowitz](https://www.pedowitzgroup.com/how-do-i-identify-high-fit-accounts-in-hubspot)) *(secondary)*.
- Third-party intent (Bombora Company Surge, used inside Apollo and ZoomInfo) gives a weekly 0–100
  score per topic, measured against the account's *own* baseline. ≥60 is commonly read as a surge ([Komo](https://komo.ai/glossary/bombora-intent-data); [Bombora + Apollo](https://bombora.com/es/integration/apollo/)).
- Vendors say there is no universal hand-off threshold. You start with a rule, compare it with
  sales capacity and outcomes, and then tune it.

**Trigger events beat static lists.** Practitioner guides, Firmable's among them, list the same
signal families: **hiring, funding, leadership change, tech-stack change, headcount growth**.
They also say **stacked** signals matter more than any single one ([Firmable: B2B buying signals](https://firmable.com/resources/blog/b2b-buying-signals/); [Salesmotion](https://salesmotion.io/blog/b2b-buying-triggers-guide); [Landbase](https://landbase.com/blog/buying-signals-actually-predict-pipeline-data)).
Vendor conversion multipliers (e.g. "37% vs 19% win rate") could not be verified, so we don't use them.

**Speed matters.** In HBR's analysis of ~1.25M leads, firms that made contact within an hour were
~7× as likely to qualify the lead as firms that waited longer. HBR's 2011 audit found the average response time was 42 hours ([HBR, "The Short Life of Online Sales Leads", 2011](https://hbr.org/2011/03/the-short-life-of-online-sales-leads)).

**Buyers talk to vendors rarely.** Gartner is widely cited as finding that B2B buyers spend ~17% of
their buying time meeting suppliers ([Growth Method summary](https://www.growthmethod.com/gartner-b2b-buying-journey/)) *(secondary)*. The first touch has to be
relevant, because there won't be many.

→ **Implication:** keep `fit_score` and `intent_score` as **separate, visible numbers** and blend
them only into `priority_score` with weights the user can change (the ICP tuner). Intent comes from
**stacked, explainable trigger rules**, each quoting its evidence. That stands in for the live intent
feeds we don't have. Sort the default view by priority so reps act on the hottest accounts first.

---

## 2. Who gets attacked: size and sector

| Finding | Source |
|---|---|
| ASD received **84,700+ cybercrime reports** in FY2024–25, one every ~6 minutes, and responded to **1,200+ incidents** (+11%) | [ASD ACTR 2024–25 business factsheet](https://www.cyber.gov.au/sites/default/files/2025-10/Annual%20Cyber%20Threat%20Report%202024-25%20factsheet%20for%20businesses%20and%20organisations.pdf); [iTWire](https://itwire.com/business-it-news/security/asd-s-australian-cyber-security-centre-annual-cyber-threat-report-2024-2025.html) |
| Average self-reported cost per cybercrime report: **small $56,600 (+14%)**, **medium $97,200 (+55%)**, large $202,700 (+219%) | ASD factsheet (above); [Cyber Daily](https://www.cyberdaily.au/security/12768-annual-cyber-threat-report-2024-2025-by-the-numbers) |
| Most common business report type: email compromise (19%), then BEC fraud with financial loss (15%) | ASD factsheet |
| Malicious actors succeeded in **95% of healthcare and social assistance incidents** ASD responded to, versus ~52% across all sectors. Financial and insurance rose from 8th to 3rd most-targeted sector (7%) | [Defence Minister release](https://www.minister.defence.gov.au/media-releases/2025-10-14/annual-cyber-threat-report-highlights-persistent-threat-individuals-across-australian-economy); [iTWire](https://itwire.com/business-it-news/security/attacks-on-australia%E2%80%99s-critical-infrastructure-double) |
| ASD notified critical-infrastructure operators **190+ times** (+111%) | iTWire (above) |
| OAIC NDB, calendar 2025: **1,205 notifications** (all-time high, +8%), 716 from malicious or criminal attacks. Top sectors: **health 225 (19%)**, **finance 157**, Australian Government 118, business and professional associations 103, education 81, legal/accounting/management 81 | [OAIC media release](https://www.oaic.gov.au/news/media-centre/data-breach-notifications-increase-to-all-time-high-in-2025,-new-ndb-stats-show) |
| Verizon DBIR 2025: ransomware was present in **88% of SMB breaches vs 39% at large orgs**. Ransomware featured in 44% of all breaches. Third-party involvement doubled from 15% to 30% | [Verizon SMB snapshot](https://www.verizon.com/business/resources/infographics/2025-dbir-smb-snapshot.pdf); [Infosecurity Magazine](https://infosecurity-magazine.com/news/verizon-dbir-smb-ransomware-attacks); [ASIS](https://www.asisonline.org/security-management-magazine/latest-news/today-in-security/2025/april/verizon-dbir-2025/) |

**The security poverty line.** Wendy Nather coined the term in 2011 for organisations that lack the
budget, expertise or influence to reach basic security maturity. Typical examples are SMEs with no
dedicated security staff. In a Duo/YouGov survey, 36% of respondents put themselves at or below
the line ([Atlantic Council](https://www.atlanticcouncil.org/?p=451496); [BankInfoSecurity](https://www.bankinfosecurity.com/stay-above-security-poverty-line-a-4444)). In Ipsos research for Optus,
60% of Australian small businesses had no cyber plan, and one in five spent no time on prevention ([ARN](https://www.arnnet.com.au/article/4167510/aussie-small-businesses-still-not-prioritising-cyber-security.html)).

→ **Implication:** sector and size are real risk proxies, not just fit preferences.
Health, finance, professional services (legal/accounting), education and critical-infrastructure
sectors get high `regulated_industry` / `handles_pii` weights. **SMB and mid-market (~20–500
staff)** is the sweet spot. These companies are attacked heavily (88% ransomware share), have
material losses (medium-business cost +55%), and usually have **no security team**
(`maturity_gap_no_security`). Micro businesses (<10 staff) are at risk too but rarely buy, so they
get a fit penalty, not an exclusion.

---

## 3. Regulatory forcing functions

**Australia**
- **Privacy and Other Legislation Amendment Act 2024.** Adds a statutory tort for serious invasions of privacy (in force from June 2025), a new mid-tier civil penalty and infringement notices, alongside the existing maximum of A$50M / 30% of adjusted turnover. The **small business exemption (<A$3M turnover) remains for now**, though the government has agreed in principle to remove it ([CCP](https://ccp.com.au/news/privacy-act-reform-2024-notifiable-breach-teeth/); [Clym](https://www.clym.io/articles/australian-data-privacy-regulation-what-you-need-to-know)) *(secondary)*.
- **Notifiable Data Breaches scheme.** Covers entities subject to the Privacy Act. The volumes are in §2.
- **Cyber Security Act 2024: ransomware payment reporting.** In force **30 May 2025**. Applies to entities with turnover **> A$3M** (and to critical-infrastructure responsible entities regardless of turnover), with a **72-hour** reporting window. Active compliance began on 1 Jan 2026 ([MinterEllison](https://www.minterellison.com/articles/mandatory-ransomware-payment-reporting-obligations-in-force)).
- **APRA CPS 234.** Applies to APRA-regulated banks, insurers and super funds. Third parties handling their information assets must have their security capability assessed and contracted, and material incidents must be notified within 72 hours ([BDO](https://www.bdo.com.au/en-au/insights/cyber-security/apra-regulation-cps-234-what-is-it-and-how-does-it-apply-to-your-organisation); [Compliance Council](https://compliancecouncil.com.au/insights/what-are-the-information-security-requirements-of-cps-234/)). **Suppliers to the financial sector inherit the requirement.**
- **SOCI Act.** Covers 11 critical-infrastructure sectors (energy, water, health, transport, food and grocery, communications, financial services, etc.). Responsible entities must run a risk management program (CIRMP) aligned to ISO 27001, the **Essential Eight**, NIST CSF, C2M2 or AESCSF ([Hamilton Locke](https://hamiltonlocke.com.au/soci-critical-infrastructure-risk-management-program-rules-come-into-force/); [GoToSage](https://gotosage.com/insights/blog/navigating-the-soci-act-3-tips-to-achieve-cyber-security-compliance)).
- **Essential Eight.** Maturity Level 2 is mandatory for non-corporate Commonwealth entities. It is voluntary for private firms, but insurers and government supply chains increasingly push toward ML1–ML2 ([iTWire](https://itwire.com/business-it-news/security/essential-eight-maturity-model-what-applies-now-and-what-asd-is-changing); [ManageEngine](https://www.manageengine.com/compliance-manager/e8mm-compliance/what-is-E8MM-compliance.html)).

**Global (for accounts with offshore exposure)**
- **PCI DSS v4.0.** The 51 future-dated requirements became mandatory on **31 March 2025**, including 6.4.3 (authorising payment-page scripts) and 11.6.1 (detecting tampering on payment pages). These hit **every e-commerce merchant** ([PCI SSC](https://blog.pcisecuritystandards.org/coffee-with-the-council-podcast-guidance-for-pci-dss-e-commerce-requirements-effective-after-31-march-2025)).
- **NIS2 (EU).** Covers medium-sized and larger entities (≥50 staff or >€10M turnover) in 18 Annex I and II sectors ([CCB Belgium](https://ccb.belgium.be/en/node/2530)).
- **HIPAA Security Rule NPRM (Jan 2025).** Proposes mandatory encryption, MFA, annual pen tests and a 72-hour restore standard. It is **still proposed** ([Bradley](https://www.bradley.com/insights/publications/2025/03/top-10-takeaways-from-the-new-hipaa-security-rule-nprm)).
- **SOC 2.** Not a law, but a common procurement gate for SaaS and B2B tech vendors selling to enterprises.
- **Cyber insurance.** Underwriters now expect evidence, not just attestation, of **MFA, EDR and tested immutable backups**, plus patching, privileged-access limits and IR plans. Many also run external attack-surface scans ([Calyx IT](https://calyxit.com/cybersecurity-insurance-compliance-checklist/); [Stamm Tech](https://www.stammtech.com/feed/cyber-insurance-in-2025-the-7-controls-carriers-expect-and-how-to-get-bind-ready-in-30-days/)) *(vendor sources)*.

→ **Implication:** the **A$3M turnover line** matters twice. Above it, a company is under the Privacy
Act (NDB) and has ransomware-reporting duties (`privacy_act_threshold`). Below it, the company is
under-served and under-regulated: a good *nurture/education* play rather than compliance.
Regulation maps cleanly to a **`compliance` play** with a sector-specific hook (CPS 234 for finance,
SOCI/E8 for critical infrastructure, PCI for e-commerce, NDB for health). The LLM brief should name
the *specific* obligation, not just "compliance".

---

## 4. Who buys security, by company size

- In organisations under 5,000 staff, upper IT and security management make the final call ~40% of
  the time and the CISO only ~13%. Under 500 staff, the CEO or CFO is the next most common decider ([ActualTech Cybersecurity Buyers Report](https://www.actualtechmedia.com/blog/cybersecurity-buyers-report-part-4/)).
- Top management takes part in cyber-protection decisions in 65% of SMBs and 68% of enterprises ([Kaspersky](https://usa.kaspersky.com/about/press-releases/kaspersky-research-finds-top-executives-make-cybersecurity-decisions-in-two-thirds-of-smbs-and-enterprises)).

→ **Implication:** `persona` is set by size band. **1–50 staff:** Owner/CEO (often working with an
outsourced MSP). **51–200:** IT Manager, with the CFO/COO as economic buyer. **201–1000:** Head of
IT / IT Director (CISO if one exists). **1000+:** CISO / security leadership. Outreach copy changes
with the persona: risk and insurance for a CEO, workload and controls for an IT manager,
frameworks and board reporting for a CISO.

---

## 5. Finding → signal / feature map

Signal ids follow `pipeline/signals.py`. Rows marked † need fields we haven't confirmed yet
(to confirm once dataset columns are profiled).

| Research finding | Signal id / feature | Category | Rule sketch |
|---|---|---|---|
| Health, finance, government, education, legal top the NDB and ASD lists; healthcare intrusions succeed 95% of the time | `regulated_industry` | regulatory | industry/ANZSIC in health, finance/insurance, legal/accounting, education, utilities |
| Breach volume concentrates where personal and health data is held | `handles_pii` | data_sensitivity | industry or description/keywords mention patients, clients, members, payroll, records |
| APRA CPS 234 pushes requirements onto third parties | `financial_supply_chain` (new) | regulatory | description/keywords mention banks, insurers, super funds as clients |
| SOCI covers 11 CI sectors; CI notifications up 111% | `critical_infrastructure` (new) | regulatory | industry in energy, water, transport, telco, food and grocery, data centres |
| A$3M turnover brings the Privacy Act/NDB and ransomware reporting into scope | `privacy_act_threshold` (new) | regulatory | revenue_usd ≥ ~US$2M, or employee proxy † |
| PCI DSS 4.0 e-commerce requirements (6.4.3 / 11.6.1) live since Mar 2025 | `ecommerce_surface` | attack_surface | technologies contain Shopify/Magento/WooCommerce/Stripe, or keywords "online store" † |
| Third-party involvement in breaches doubled to 30%; SaaS vendors face SOC 2 gates | `saas_vendor_soc2` (new) | attack_surface | industry software/IT services + B2B keywords |
| Funding triggers new vendor evaluation | `recent_funding` | timing | last_funding_date within 18 months † |
| Headcount growth expands identities and endpoints faster than controls | `rapid_growth` | growth | employee_growth_pct ≥ 20% † |
| Hiring is a top trigger; security or IT job ads signal an active project | `hiring_signal` / `hiring_security_roles` (new) | timing | job_postings above size-relative threshold; role titles if available † |
| Security poverty line: SMEs with no security staff or tools | `maturity_gap_no_security` | maturity_gap | size 20–500 and no security vendor in technologies † |
| Insurers require MFA/EDR/backups | `insurance_control_gap` (new) | maturity_gap | technologies lack an identity, EDR or backup vendor † |
| Legacy on-prem stacks are harder to secure | `legacy_stack` → play `modernise` | maturity_gap | technologies contain on-prem Exchange, old CMS, etc. † |
| Medium-business losses up 55%; 88% ransomware share in SMB breaches | fit `size` component | (fit) | peak fit at 51–500, taper at both ends |
| Fit and intent kept separate (HubSpot, 6sense) | fit/intent breakdowns + ICP weight sliders | UI | `priority = wf·fit + wi·intent`, recomputed client-side |
| Speed-to-contact and signal decay | `timing` category; "new this week" sort | UI | recency of funding/growth; future: decay intent weights over time |
| Buyer differs by size | `persona` field and persona-aware outreach | LLM | size-band → persona map (§4) |
| Few vendor touchpoints, so relevance wins | grounded outreach that cites ≥1 signal's evidence | LLM | eval check: outreach must reference a real signal |
