"""Generate SYNTHETIC app/data/accounts.sample.json conforming to CONTRACT §3.
Deterministic (seeded). Every company is invented."""
import json, random, hashlib, sys
from datetime import datetime, timezone, date, timedelta

R = random.Random(20261011)
OUT = sys.argv[1]
N = 300
LLM_TOP = 40
WF, WI = 0.5, 0.5

GEO = {
    "Australia": {
        "NSW": ["Sydney", "Newcastle", "Wollongong", "Parramatta", "Penrith"],
        "VIC": ["Melbourne", "Geelong", "Ballarat", "Bendigo"],
        "QLD": ["Brisbane", "Gold Coast", "Townsville", "Toowoomba", "Cairns"],
        "WA": ["Perth", "Fremantle", "Bunbury"],
        "SA": ["Adelaide", "Mount Gambier"],
        "TAS": ["Hobart", "Launceston"],
        "ACT": ["Canberra"],
        "NT": ["Darwin"],
    },
    "New Zealand": {"Auckland": ["Auckland"], "Wellington": ["Wellington"], "Canterbury": ["Christchurch"]},
    "United Kingdom": {"England": ["London", "Manchester", "Leeds"], "Scotland": ["Edinburgh", "Glasgow"]},
    "Singapore": {None: ["Singapore"]},
    "United States": {"CA": ["San Francisco", "San Diego"], "TX": ["Austin"], "NY": ["New York"]},
}
COUNTRY_W = [("Australia", 78), ("New Zealand", 8), ("United Kingdom", 6), ("Singapore", 3), ("United States", 5)]
STATE_W = {"NSW": 32, "VIC": 27, "QLD": 18, "WA": 10, "SA": 6, "TAS": 3, "ACT": 3, "NT": 1}

INDUSTRIES = {
    # name: (short, fit points, regulated?, data sensitivity, name words, desc)
    "Hospital & Health Care": ("Healthcare", 33, "healthcare", "health records", ["Medical", "Health", "Clinic", "Dental", "Care", "Physio", "Radiology"], "Provides {x} across {n} sites, handling patient records and appointment systems."),
    "Financial Services": ("Financial Services", 34, "financial services", "payment and account data", ["Capital", "Wealth", "Finance", "Lending", "Advisory", "Credit"], "Offers {x} to retail and business clients, operating an online client portal."),
    "Insurance": ("Insurance", 32, "insurance", "policyholder PII", ["Insurance", "Underwriting", "Mutual", "Assurance"], "Underwrites and brokers {x}, with claims handled through a web portal."),
    "Legal Services": ("Legal", 30, "legal", "privileged client files", ["Legal", "Lawyers", "Law", "Partners"], "A {x} firm handling commercial, property and family matters."),
    "Accounting": ("Accounting", 29, "financial services", "tax file numbers", ["Accountants", "Tax", "Advisory", "Bookkeeping"], "Provides {x}, payroll and tax advice to SMEs."),
    "Education": ("Education", 27, "education", "student records", ["College", "Academy", "Grammar", "Learning", "Institute"], "Runs {x} for students, with a learning management system and parent portal."),
    "Government Administration": ("Government", 26, "government", "citizen records", ["Council", "Authority", "Services", "Board"], "A {x} delivering public services and online permit applications."),
    "Utilities": ("Utilities", 31, "critical infrastructure", "SCADA telemetry", ["Water", "Energy", "Power", "Grid"], "Operates {x} infrastructure with operational technology at remote sites."),
    "Logistics & Supply Chain": ("Logistics", 24, None, "shipment and customer data", ["Logistics", "Freight", "Transport", "Couriers", "Haulage"], "Moves {x} nationally with a fleet tracking and booking platform."),
    "Manufacturing": ("Manufacturing", 22, None, "IP and design files", ["Manufacturing", "Engineering", "Fabrication", "Industries", "Plastics"], "Manufactures {x} with an ERP-connected factory floor."),
    "Retail": ("Retail", 21, None, "card payment data", ["Retail", "Store", "Outfitters", "Supply", "Goods"], "Sells {x} online and through retail stores."),
    "Professional Services": ("Professional Services", 23, None, "client contracts", ["Consulting", "Group", "Partners", "Advisory"], "Delivers {x} consulting to corporate and government clients."),
    "Information Technology": ("IT Services", 25, None, "customer environments", ["Tech", "Systems", "Digital", "Software", "Cloud", "Labs"], "Builds and supports {x} for mid-sized businesses."),
    "Mining & Resources": ("Mining", 24, "critical infrastructure", "operational telemetry", ["Resources", "Mining", "Minerals", "Exploration"], "Operates {x} sites with remote OT and fleet systems."),
    "Construction": ("Construction", 17, None, "tender documents", ["Construction", "Builders", "Projects", "Civil"], "Delivers {x} projects across commercial and residential sites."),
    "Hospitality": ("Hospitality", 14, None, "guest payment data", ["Hotels", "Hospitality", "Venues", "Dining"], "Runs {x} venues with online bookings."),
    "Real Estate": ("Real Estate", 16, None, "tenant identity documents", ["Realty", "Property", "Estates", "Real Estate"], "Manages {x} sales and rentals with a tenant portal."),
    "Non-profit": ("Non-profit", 18, None, "donor data", ["Foundation", "Trust", "Community", "Alliance"], "A {x} charity supported by donors and volunteers."),
}
IND_W = {"Hospital & Health Care": 14, "Financial Services": 9, "Insurance": 4, "Legal Services": 7, "Accounting": 7, "Education": 7,
         "Government Administration": 3, "Utilities": 3, "Logistics & Supply Chain": 7, "Manufacturing": 9, "Retail": 9,
         "Professional Services": 8, "Information Technology": 8, "Mining & Resources": 4, "Construction": 7, "Hospitality": 5,
         "Real Estate": 5, "Non-profit": 4}
DESC_X = {"Hospital & Health Care": ["general practice and allied health", "diagnostic imaging", "dental care", "aged care"],
          "Financial Services": ["mortgage broking", "wealth management", "equipment finance", "superannuation advice"],
          "Insurance": ["commercial insurance", "health insurance", "trade insurance"],
          "Legal Services": ["mid-tier", "boutique", "regional"], "Accounting": ["bookkeeping", "audit", "business advisory"],
          "Education": ["K-12 schooling", "vocational training", "early learning"], "Government Administration": ["local council", "state agency"],
          "Utilities": ["water", "electricity distribution", "gas"], "Logistics & Supply Chain": ["freight", "cold-chain goods", "parcels"],
          "Manufacturing": ["food products", "industrial components", "packaging", "medical devices"], "Retail": ["outdoor gear", "homewares", "pharmacy goods", "fashion"],
          "Professional Services": ["engineering", "management", "HR"], "Information Technology": ["managed IT", "SaaS", "cloud"],
          "Mining & Resources": ["gold", "lithium", "iron ore"], "Construction": ["commercial", "civil", "residential"],
          "Hospitality": ["hotel", "pub and dining", "event"], "Real Estate": ["residential", "commercial"], "Non-profit": ["disability support", "community health", "environmental"]}
PREFIX = ["Harbour", "Southern", "Coastal", "Summit", "Bluegum", "Ironbark", "Wattle", "Riverstone", "Banksia", "Northgate", "Redgum",
          "Kestrel", "Meridian", "Granite", "Sterling", "Cobalt", "Juniper", "Lighthouse", "Eastbrook", "Silverwater", "Westfield",
          "Corella", "Pinnacle", "Lakeside", "Highland", "Tasman", "Anchor", "Waratah", "Orchard", "Kingfisher", "Saltbush", "Oakridge",
          "Bayview", "Frontier", "Clearwater", "Mosaic", "Parkline", "Horizon", "Brightwater", "Stonebridge", "Mallee", "Greenvale"]
TECH_POOL = ["Microsoft 365", "Google Workspace", "WordPress", "Shopify", "Salesforce", "Xero", "MYOB", "AWS", "Azure", "Cloudflare",
             "Microsoft Exchange Server", "Citrix", "Fortinet", "Okta", "CrowdStrike", "Zoom", "HubSpot", "Magento", "SAP", "Oracle",
             "Atlassian Jira", "Slack", "Windows Server 2012", "Cisco Meraki", "Sophos", "Mimecast"]
SECURITY_TECH = {"Okta", "CrowdStrike", "Fortinet", "Sophos", "Mimecast", "Cloudflare"}
LEGACY_TECH = {"Microsoft Exchange Server", "Windows Server 2012", "Citrix", "Magento"}
BANDS = [("1-10", 1, 10, 6), ("11-50", 11, 50, 26), ("51-200", 51, 200, 30), ("201-500", 201, 500, 18),
         ("501-1000", 501, 1000, 9), ("1001-5000", 1001, 5000, 8), ("5000+", 5001, 14000, 3)]
SIZE_FIT = {"1-10": 6, "11-50": 18, "51-200": 32, "201-500": 35, "501-1000": 30, "1001-5000": 22, "5000+": 14}
GEO_FIT = {"Australia": 12, "New Zealand": 9, "United Kingdom": 6, "Singapore": 7, "United States": 4}


def wchoice(pairs):
    tot = sum(w for _, w in pairs)
    r = R.uniform(0, tot)
    for v, w in pairs:
        r -= w
        if r <= 0:
            return v
    return pairs[-1][0]


def slug(s):
    return "".join(c for c in s.lower() if c.isalnum())


def tier(p):
    return "A" if p >= 75 else "B" if p >= 60 else "C" if p >= 40 else "D"


def size_label(band):
    if band in ("1-10", "11-50"):
        return "SMB"
    if band in ("51-200", "201-500", "501-1000"):
        return "Mid-market"
    return "Enterprise"


def persona(band):
    return {"1-10": "Owner / Managing Director", "11-50": "Operations or Practice Manager", "51-200": "Head of IT / IT Manager",
            "201-500": "IT Director", "501-1000": "IT Director", "1001-5000": "CISO / Head of Security", "5000+": "CISO / Head of Security"}[band]


used_names = set()
accounts = []
today = date(2026, 10, 1)
for i in range(N):
    ind = wchoice(list(IND_W.items()))
    short, ind_fit, regulated, sensitive, words, desc_t = INDUSTRIES[ind]
    while True:
        name = f"{R.choice(PREFIX)} {R.choice(words)}"
        if R.random() < 0.3:
            name += R.choice([" Group", " Pty Ltd", " Co", " Partners", " Services"])
        if name not in used_names:
            used_names.add(name)
            break
    country = wchoice(COUNTRY_W)
    if country == "Australia":
        state = wchoice(list(STATE_W.items()))
    else:
        state = R.choice(list(GEO[country].keys()))
    city = R.choice(GEO[country][state])
    band, lo, hi, _ = wchoice([((b, lo, hi, w), w) for b, lo, hi, w in BANDS])
    emp = R.randint(lo, hi) if R.random() > 0.05 else None
    if emp is None:
        band_out = band  # keep band (from a range) but count unknown
    tld = {"Australia": ".com.au", "New Zealand": ".co.nz", "United Kingdom": ".co.uk", "Singapore": ".com.sg", "United States": ".com"}[country]
    domain = slug(name.replace(" Pty Ltd", "")) + tld if R.random() > 0.04 else None
    founded = R.randint(1965, 2024) if R.random() > 0.08 else None
    ntech = R.randint(1, 9)
    techs = sorted(R.sample(TECH_POOL, ntech))
    growth = round(R.gauss(8, 15), 1) if R.random() > 0.25 else None
    funding = None
    last_funding = None
    if ind in ("Information Technology", "Financial Services", "Hospital & Health Care", "Retail") and R.random() < 0.18:
        funding = float(R.choice([1.5, 3, 5, 8, 12, 20, 35]) * 1e6)
        last_funding = (today - timedelta(days=R.randint(20, 900))).isoformat()
    jobs = R.randint(0, max(2, (emp or 50) // 15)) if R.random() > 0.3 else None
    revenue = round((emp or 30) * R.uniform(120e3, 260e3), -4) if R.random() > 0.4 else None
    x = R.choice(DESC_X[ind])
    desc = desc_t.format(x=x, n=R.randint(2, 14)) if R.random() > 0.05 else None

    # ---------- signals ----------
    sigs = []
    def add(id_, label, cat, w, ev):
        sigs.append({"id": id_, "label": label, "category": cat, "weight": w, "evidence": ev})
    if regulated == "critical infrastructure":
        add("critical_infrastructure", "Critical infrastructure operator", "regulatory", 20, f"industry = '{ind}' (SOCI Act sector)")
    elif regulated:
        add("regulated_industry", f"Regulated industry ({regulated})", "regulatory", 15, f"industry = '{ind}'")
    if country == "Australia" and revenue and revenue > 3e6:
        add("privacy_act_scope", "In scope of Privacy Act (turnover > A$3M)", "regulatory", 8, f"revenue_usd = {revenue:,.0f}")
    if ind in ("Hospital & Health Care", "Financial Services", "Insurance", "Legal Services", "Accounting", "Education", "Government Administration") or R.random() < 0.15:
        add("sensitive_data", f"Holds sensitive data ({sensitive})", "data_sensitivity", 12, f"industry = '{ind}'" + (f"; description mentions '{desc.split(',')[0][:48].lower()}'" if desc else ""))
    if "Shopify" in techs or "Magento" in techs or ind in ("Retail", "Hospitality"):
        t = "Shopify" if "Shopify" in techs else "Magento" if "Magento" in techs else None
        add("payment_data", "Takes card payments online", "data_sensitivity", 10, f"technologies include '{t}'" if t else f"industry = '{ind}' (online bookings / checkout)")
    if len(techs) >= 6:
        add("large_footprint", "Broad SaaS / web footprint", "attack_surface", 8, f"{len(techs)} technologies detected: {', '.join(techs[:4])}…")
    leg = sorted(set(techs) & LEGACY_TECH)
    if leg:
        add("legacy_tech", "Legacy or frequently exploited tech", "attack_surface", 12, f"technologies include '{leg[0]}'")
    if R.random() < 0.22:
        add("remote_access", "Exposed remote access", "attack_surface", 10, "keywords include 'remote access portal'" if "Citrix" not in techs else "technologies include 'Citrix'")
    if growth is not None and growth >= 12:
        add("headcount_growth", f"Headcount up {growth:.0f}% in 12 months", "growth", 18 if growth >= 25 else 13, f"employee_growth_pct = {growth}")
    if last_funding and (today - date.fromisoformat(last_funding)).days < 365:
        add("recent_funding", "Raised funding in the last 12 months", "growth", 15, f"last_funding_date = '{last_funding}', funding_total_usd = {funding:,.0f}")
    if jobs is not None and emp and jobs >= max(4, emp * 0.06):
        add("hiring_surge", f"Hiring surge ({jobs} open roles)", "growth", 8, f"job_postings = {jobs}")
    if not (set(techs) & SECURITY_TECH):
        add("no_security_stack", "No security tooling detected", "maturity_gap", 10, f"technologies ({len(techs)}) include none of: Okta, CrowdStrike, Fortinet, Sophos, Mimecast")
    if emp and 30 <= emp <= 400 and R.random() < 0.35:
        add("no_security_role", "No dedicated security role", "maturity_gap", 9, f"employee_count = {emp}; no security titles in job postings")
    if ind in ("Hospital & Health Care", "Legal Services", "Education", "Financial Services") and R.random() < 0.35:
        add("sector_breach_wave", f"Recent breaches in {short.lower()} sector", "timing", 10, f"industry = '{ind}'; peer incidents reported in last 90 days")
    if founded and founded >= 2020 and R.random() < 0.5:
        add("young_company", "Young company, security likely ad hoc", "timing", 6, f"founded_year = {founded}")
    if R.random() < 0.08:
        add("new_it_leader", "New IT leadership hire", "timing", 8, "job_postings include 'Head of Technology' (filled)")
    R.shuffle(sigs)
    sigs.sort(key=lambda s: -s["weight"])

    cats = ["regulatory", "data_sensitivity", "attack_surface", "growth", "maturity_gap", "timing"]
    ib = {c: sum(s["weight"] for s in sigs if s["category"] == c) for c in cats}
    intent = min(100, sum(ib.values()))
    data_pts = 15 if sensitive and any(s["category"] == "data_sensitivity" for s in sigs) else (7 if domain else 3)
    fb = {"size": SIZE_FIT[band] if emp else 12, "industry": ind_fit, "geo": GEO_FIT[country], "data": data_pts}
    fit = min(100, sum(fb.values()))
    pri = round(WF * fit + WI * intent)
    t = tier(pri)
    top_cat = max(cats, key=lambda c: ib[c]) if intent else None
    play = {"regulatory": "compliance", "data_sensitivity": "compliance", "growth": "scale_up", "attack_surface": "exposed_surface",
            "maturity_gap": "modernise", "timing": "compliance"}.get(top_cat, "nurture")
    if pri < 40:
        play = "nurture"
    acc = {
        "id": "syn-" + hashlib.sha1(name.encode()).hexdigest()[:10],
        "name": name, "domain": domain, "description": desc, "industry": ind, "sub_industry": None,
        "country": country, "state": state, "city": city,
        "employee_count": emp, "size_band": band, "revenue_usd": revenue, "founded_year": founded,
        "technologies": techs,
        "linkedin_url": f"https://www.linkedin.com/company/{slug(name)}" if R.random() > 0.3 else None,
        "website": f"https://{domain}" if domain else None,
        "employee_growth_pct": growth, "job_postings": jobs,
        "fit_score": fit, "intent_score": intent, "priority_score": pri, "tier": t,
        "fit_breakdown": fb, "intent_breakdown": ib,
        "segment": f"{size_label(band)} {short}", "play": play, "persona": persona(band),
        "signals": sigs, "llm": None,
    }
    accounts.append(acc)

accounts.sort(key=lambda a: -a["priority_score"])

PLAY_HOOK = {"compliance": "compliance obligations", "scale_up": "rapid growth", "exposed_surface": "exposed systems",
             "modernise": "security gaps", "nurture": "baseline security"}
for a in accounts[:LLM_TOP]:
    s = a["signals"]
    s0 = s[0]["label"].lower() if s else "its profile"
    s1 = s[1]["label"].lower() if len(s) > 1 else "its size"
    lvl = "high" if a["priority_score"] >= 72 else "medium"
    first = a["persona"].split(" / ")[0]
    loc = ", ".join(x for x in [a["city"], a["state"]] if x)
    a["llm"] = {
        "need_level": lvl,
        "confidence": round(R.uniform(0.66, 0.93), 2),
        "rationale": f"{a['name']} shows {s0} and {s1}, which together point to {'an urgent' if lvl == 'high' else 'a real but less urgent'} need for managed security. [Synthetic sample text]",
        "brief": "\n".join([
            f"- {a['segment']} organisation in {loc}" + (f" with about {a['employee_count']} staff." if a["employee_count"] else "."),
            f"- Strongest trigger: {s[0]['label']} ({s[0]['evidence']})." if s else "- No strong triggers.",
            f"- Secondary: {s1}; lead with the {a['play'].replace('_', ' ')} play.",
            f"- Talk to the {a['persona']}; open on {PLAY_HOOK[a['play']]} rather than product features.",
            "- [Synthetic sample brief, not model output]",
        ]),
        "outreach": {
            "subject": f"{a['name']}: {PLAY_HOOK[a['play']]} without adding headcount",
            "body": (f"Hi {{first_name}},\n\nI noticed {a['name']} {('is growing quickly' if a['play'] == 'scale_up' else 'operates in a sector with tightening security expectations')}. "
                     f"Teams like yours often tell us {s0} is hard to stay on top of with a lean IT function.\n\n"
                     f"We help {a['segment'].lower()} organisations get 24/7 threat detection and a clear compliance posture in weeks, not quarters.\n\n"
                     f"Would a 20-minute call next week be useful? I can share what similar organisations in {a['state'] or a['country']} changed first.\n\nBest,\n{{rep_name}}"),
            "persona": a["persona"],
        },
        "model": "synthetic-sample (no model called)",
        "prompt_versions": {"need-classifier": "v2", "account-brief": "v1", "outreach-draft": "v1"},
    }

def uniq(key):
    return sorted({a[key] for a in accounts if a.get(key)})

doc = {
    "meta": {
        "generated_at": datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc).isoformat(),
        "source": "SYNTHETIC sample generated for UI development (no real companies)",
        "synthetic": True,
        "rows_total": N, "rows_included": N,
        "scoring_version": "rules-v1",
        "weights": {"fit": WF, "intent": WI},
        "llm": {"enabled": True, "synthetic": True,
                "note": "AI fields on the top 40 accounts are illustrative placeholder text. No model was called.",
                "prompt_versions": {"need-classifier": "v2", "account-brief": "v1", "outreach-draft": "v1"},
                "calls": 0, "cost_usd": 0.0, "top_n": LLM_TOP},
    },
    "facets": {
        "industries": uniq("industry"), "countries": uniq("country"), "states": uniq("state"),
        "size_bands": [b[0] for b in BANDS if any(a["size_band"] == b[0] for a in accounts)],
        "signal_ids": sorted({s["id"] for a in accounts for s in a["signals"]}),
        "segments": uniq("segment"),
    },
    "accounts": accounts,
}
with open(OUT, "w") as f:
    json.dump(doc, f, indent=1)
from collections import Counter
print(Counter(a["tier"] for a in accounts), Counter(a["play"] for a in accounts))
