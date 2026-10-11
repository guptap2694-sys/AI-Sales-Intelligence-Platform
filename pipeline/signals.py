"""Rule-based, deterministic, explainable buying/need signals (contract §2).

Perspective: a seller of cybersecurity software to SMB / mid-market, with an
Australian home market. Every signal quotes the field that triggered it in
`evidence`. A missing field never produces a signal and never raises.

All tunable vocabulary lives in SIGNAL_CONFIG below.
"""

from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from typing import Any

# --------------------------------------------------------------------------- #
# Tunable configuration
# --------------------------------------------------------------------------- #
SIGNAL_CONFIG: dict[str, Any] = {
    # Sector classification from industry / sub_industry text. First match wins,
    # so put specific sectors before broad ones (insurance before finance, etc.).
    "sectors": {
        "healthcare": ["hospital", "health care", "healthcare", "medical", "clinic", "dental", "pharma",
                       "pharmaceutical", "biotech", "aged care", "disability", "ndis", "allied health",
                       "physiotherapy", "veterinary", "pathology", "radiology", "mental health", "health"],
        "insurance": ["insurance", "insurer", "underwriting", "reinsurance", "broker"],
        "finance": ["financial services", "finance", "bank", "banking", "credit union", "lending", "lender",
                    "investment", "wealth", "superannuation", "super fund", "accounting", "fintech",
                    "payments", "capital markets", "venture capital", "private equity", "mortgage",
                    "asset management", "funds management", "stockbroking", "credit", "tax"],
        "legal": ["legal", "law firm", "law practice", "lawyers", "solicitors", "barristers", "conveyancing"],
        "government": ["government", "public sector", "defence", "defense", "military", "council",
                       "public administration", "public safety", "law enforcement", "federal", "aerospace"],
        "critical_infrastructure": ["utilities", "utility", "electricity", "energy", "oil", "gas", "water",
                                    "renewable", "power", "mining", "telecommunications", "telecom",
                                    "transport", "logistics", "ports", "airline", "aviation", "rail",
                                    "data centre", "data center", "food production", "grocery"],
        "education": ["education", "school", "university", "higher education", "e-learning", "elearning",
                      "training", "childcare", "early learning", "tertiary", "tafe", "rto"],
        "technology": ["software", "saas", "information technology", "it services", "computer",
                       "internet", "cloud", "cyber", "technology", "managed services", "msp", "web"],
        "retail_ecommerce": ["retail", "e-commerce", "ecommerce", "online retail", "consumer goods",
                             "apparel", "fashion", "wholesale", "supermarket"],
        "professional_services": ["consulting", "management consulting", "professional services", "recruitment",
                                  "staffing", "human resources", "marketing", "advertising", "architecture",
                                  "engineering services", "real estate", "property"],
        "manufacturing": ["manufacturing", "industrial", "machinery", "automotive", "chemicals", "electronics",
                          "construction", "building"],
        "hospitality": ["hospitality", "hotel", "restaurant", "food & beverage", "food and beverage", "tourism",
                        "leisure", "travel", "events", "cafe"],
        "nonprofit": ["non-profit", "nonprofit", "not-for-profit", "not for profit", "charity", "ngo",
                      "religious", "philanthropy"],
    },
    "sector_labels": {
        "healthcare": "Healthcare", "insurance": "Insurance", "finance": "Financial Services", "legal": "Legal",
        "government": "Government & Defence", "critical_infrastructure": "Critical Infrastructure",
        "education": "Education", "technology": "Technology", "retail_ecommerce": "Retail & E-commerce",
        "professional_services": "Professional Services", "manufacturing": "Manufacturing & Construction",
        "hospitality": "Hospitality", "nonprofit": "Non-profit", "other": "Other",
    },
    # Regulated sectors -> (signal weight, regimes to mention in the label).
    # Regulated sectors -> (signal weight, obligations named for non-AU accounts; AU uses _REGULATED_HOOK).
    "regulated_sectors": {
        "healthcare": (15, "HIPAA Security Rule / GDPR special-category data"),
        "finance": (15, "PCI DSS 4.0 / GLBA / DORA"),
        "insurance": (15, "NAIC data-security model law / GDPR"),
        "government": (14, "FedRAMP / CMMC / NIST 800-171"),
        "critical_infrastructure": (14, "NIS2 / NERC CIP"),
        "legal": (12, "client privilege / GDPR"),
        "education": (10, "FERPA / GDPR student data"),
    },
    # Explicit compliance frameworks mentioned in description/keywords.
    "compliance_terms": ["hipaa", "pci", "pci dss", "pci-dss", "soc 2", "soc2", "iso 27001", "iso27001",
                         "gdpr", "apra", "cps 234", "cps234", "essential eight", "essential 8", "irap",
                         "soci", "privacy act", "notifiable data breach", "ndb scheme", "fedramp", "nist",
                         "cmmc", "disp", "ism", "pspf", "cdr", "consumer data right", "aml", "austrac",
                         "afsl", "australian credit licence", "my health record", "tga", "hitrust"],
    # Companies whose customers are APRA-regulated (CPS 234 third-party requirements flow down).
    "financial_client_terms": ["banks", "banking clients", "financial institutions", "insurers",
                               "insurance companies", "super funds", "superannuation funds", "lenders",
                               "credit unions", "wealth managers", "asset managers", "financial services clients",
                               "financial services industry", "apra-regulated", "apra regulated"],
    # Software / IT vendors selling to businesses: SOC 2 / ISO 27001 is a procurement gate.
    "b2b_vendor_terms": ["saas", "software as a service", "b2b", "enterprise", "platform", "api",
                         "cloud-based", "businesses", "clients", "customers", "teams", "organisations",
                         "organizations", "managed services", "it services"],
    # Security / IT job titles (matched against any jobs-like field in `extra`, plus description/keywords).
    "security_role_terms": ["security engineer", "security analyst", "cyber security", "cybersecurity",
                            "information security", "infosec", "ciso", "head of security", "security manager",
                            "soc analyst", "grc", "governance risk", "compliance manager", "it manager",
                            "head of it", "it director", "systems administrator", "sysadmin",
                            "network engineer", "devsecops", "cloud security", "penetration tester"],
    "jobs_extra_column_hints": ["job", "role", "hiring", "vacanc", "position", "opening"],
    "gov_supplier_terms": ["government contract", "government clients", "government agencies", "defence",
                           "defense", "department of", "federal agencies", "state government",
                           "local government", "councils", "public sector clients", "tender", "panel supplier",
                           "dmp", "disp member"],
    # Countries treated as "Australia" for AU-specific regulation.
    "au_country_names": ["australia", "au", "aus"],
    # SOCI Act sectors (Security of Critical Infrastructure Act 2018, as amended 2021/22).
    # SOCI Act sectors (Security of Critical Infrastructure Act 2018, as amended 2021/22): our sector keys,
    # plus narrower terms required for sectors where only some members are critical (hospitals, universities).
    "soci_sectors": {
        "critical_infrastructure": None,  # energy, water, telco, transport, food & grocery, data storage
        "government": ["defence", "defense", "military", "aerospace", "space"],
        "healthcare": ["hospital", "hospitals", "pathology", "pharmaceutical", "medical supplies"],
        "education": ["university", "higher education", "tertiary"],
        "finance": ["financial market infrastructure", "payments", "clearing", "exchange", "bank", "banking"],
        "insurance": ["insurer", "insurance"],
    },
    # Privacy Act / NDB + Cyber Security Act 2024 ransomware reporting apply above A$3M turnover (~US$2M).
    # Without revenue we use an employee proxy (~15 staff at ~A$200k revenue per head).
    "privacy_act_revenue_usd": 2_000_000,
    "privacy_act_employee_proxy": 15,
    "data_sensitivity": {
        "health_records": ["patient", "patients", "medical records", "health records", "clinical", "telehealth",
                           "ehr", "emr", "pathology", "diagnostic", "pharmacy", "healthcare provider",
                           "aged care", "ndis", "mental health", "medical practice", "dental practice"],
        "payments": ["payments", "payment processing", "payment gateway", "credit card", "card payments",
                     "checkout", "billing", "invoicing", "lending", "loans", "buy now pay later", "bnpl",
                     "wallet", "remittance", "point of sale", "pos", "merchant", "banking", "trading platform",
                     "crypto", "cryptocurrency", "superannuation"],
        "pii": ["personal information", "personal data", "customer data", "identity", "kyc", "payroll",
                "hr software", "human resources", "recruitment", "candidates", "background checks", "crm",
                "member data", "membership", "customer records", "tenant", "insurance claims", "policyholders",
                "students", "student records", "childcare", "families", "donors", "citizens",
                "personal details", "loyalty program", "marketing data", "resident data", "residents", "client data",
                "customer accounts", "user data"],
        "confidential": ["legal", "law firm", "litigation", "intellectual property", "trade secrets",
                         "mergers", "due diligence", "confidential", "tax returns", "accounting",
                         "financial records", "wealth management", "financial planning", "client files",
                         "case files"],
    },
    "data_sensitivity_weights": {"health_records": 12, "payments": 12, "pii": 8, "confidential": 7},
    "attack_surface": {
        "ecommerce": ["e-commerce", "ecommerce", "online store", "online shop", "shop online", "webstore",
                      "shopify", "woocommerce", "magento", "bigcommerce", "online retailer", "marketplace",
                      "buy online", "delivered to your door"],
        "saas": ["saas", "software as a service", "cloud platform", "cloud-based", "web app", "web application",
                 "api", "platform", "mobile app", "app store", "customer portal", "subscription software"],
        "remote_services": ["online booking", "book online", "client portal", "patient portal", "telehealth",
                            "remote", "work from home", "hybrid workforce", "online banking", "self-service",
                            "member portal", "online services", "distributed team"],
        "multi_location": ["locations", "branches", "clinics", "stores", "offices across", "nationwide",
                           "national network", "across australia", "sites", "franchise", "franchises",
                           "outlets", "campuses", "depots", "warehouses"],
    },
    "attack_surface_weights": {"ecommerce": 8, "saas": 7, "remote_services": 5, "multi_location": 6},
    # Technology-stack signals (matched against `technologies`, case-insensitive substring).
    "tech": {
        "exposed_cms": ["wordpress", "magento", "joomla", "drupal", "woocommerce", "prestashop", "opencart",
                        "php", "cpanel", "wix", "squarespace"],
        "legacy_onprem": ["windows server 2008", "windows server 2012", "windows 7", "exchange server",
                          "microsoft exchange", "on-prem", "on premise", "on-premise", "sharepoint server",
                          "sql server 2008", "sql server 2012", "citrix", "pulse secure", "ivanti", "sonicwall",
                          "vmware esxi", "active directory", "myob accountright", "filemaker", "lotus notes",
                          "jquery 1.", "apache tomcat", "iis", "ftp", "remote desktop", "rdp", "teamviewer"],
        "cloud": ["aws", "amazon web services", "azure", "google cloud", "gcp", "digitalocean", "heroku",
                  "kubernetes", "docker", "s3", "cloudfront", "firebase", "vercel", "netlify"],
        # Controls cyber insurers now expect: identity/MFA, EDR, backups.
        "identity_mfa": ["okta", "duo", "auth0", "azure ad", "entra", "microsoft entra", "jumpcloud", "onelogin",
                         "ping identity", "google workspace", "1password", "lastpass", "keeper", "cyberark"],
        "edr": ["crowdstrike", "sentinelone", "microsoft defender", "defender for", "carbon black", "sophos",
                "trend micro", "eset", "bitdefender", "huntress", "cylance", "webroot", "malwarebytes",
                "cortex xdr", "arctic wolf"],
        "backup": ["veeam", "datto", "acronis", "backblaze", "rubrik", "cohesity", "druva", "commvault",
                   "carbonite", "axcient", "spanning", "aws backup", "azure backup", "barracuda backup"],
        "security": ["crowdstrike", "sentinelone", "okta", "duo", "zscaler", "palo alto", "fortinet",
                     "fortigate", "checkpoint", "check point", "sophos", "microsoft defender", "defender for",
                     "carbon black", "trend micro", "eset", "bitdefender", "mimecast", "proofpoint",
                     "knowbe4", "splunk", "sumo logic", "rapid7", "tenable", "qualys", "1password", "lastpass",
                     "auth0", "cloudflare", "imperva", "akamai", "darktrace", "arctic wolf", "huntress",
                     "sentinel", "wiz", "snyk", "jumpcloud", "cisco umbrella", "netskope", "vanta", "drata"],
    },
    # Words that suggest the company already invests in security.
    "security_terms": ["security", "secure", "cyber", "cybersecurity", "iso 27001", "soc 2", "soc2",
                       "penetration testing", "encryption", "encrypted", "zero trust", "mfa",
                       "multi-factor", "siem", "soc ", "essential eight"],
    "growth_terms": ["rapidly growing", "fast-growing", "fast growing", "rapid growth", "expanding", "expansion",
                     "scale-up", "scaleup", "scaling", "hypergrowth", "series a", "series b", "series c",
                     "venture-backed", "vc-backed", "we're hiring", "now hiring", "growing team", "new offices",
                     "acquired", "acquisition"],
    "incident_terms": ["data breach", "cyber attack", "cyberattack", "ransomware", "security incident",
                       "hacked", "breach notification"],
    # Numeric thresholds.
    "thresholds": {
        "growth_strong_pct": 20.0, "growth_moderate_pct": 10.0, "hypergrowth_pct": 35.0,
        "hiring_postings": 10, "rapid_hiring_ratio": 0.05, "rapid_hiring_min_postings": 5,
        "recent_funding_months": 18, "funded_min_usd": 1_000_000,
        "young_company_years": 7, "young_company_min_employees": 20,
        "maturity_gap_min": 50, "maturity_gap_max": 500,
        "min_employees_for_gap": 20,
    },
}

CATEGORIES = ("regulatory", "data_sensitivity", "attack_surface", "growth", "maturity_gap", "timing")


# --------------------------------------------------------------------------- #
# Matching helpers
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=None)
def _rx(terms: tuple[str, ...]) -> re.Pattern[str]:
    """Word-boundary regex over a term list (longest first, so 'pci dss' beats 'pci').

    A trailing plural 's' is allowed, so 'restaurant' also matches 'Restaurants'.
    """
    alts = "|".join(re.escape(t.strip()) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])({alts})s?(?![a-z0-9])", re.I)


def find_terms(text: str | None, terms: list[str]) -> list[str]:
    """Distinct matched terms in order of appearance."""
    if not text:
        return []
    seen: list[str] = []
    for m in _rx(tuple(terms)).finditer(text):
        t = m.group(1).lower()
        if t not in seen:
            seen.append(t)
    return seen


def _snippet(text: str, term: str, width: int = 50) -> str:
    """'…short context around term…' for evidence."""
    m = re.search(re.escape(term), text, re.I)
    if not m:
        return text[: width * 2]
    a, b = max(0, m.start() - width), min(len(text), m.end() + width)
    return ("…" if a else "") + text[a:b].strip() + ("…" if b < len(text) else "")


def _text_fields(c: dict[str, Any]) -> list[tuple[str, str]]:
    """(field name, text) pairs to keyword-match, most specific first."""
    out = []
    for f in ("description", "sub_industry", "industry"):
        if c.get(f):
            out.append((f, str(c[f])))
    if c.get("keywords"):
        out.append(("keywords", ", ".join(map(str, c["keywords"]))))
    return out


def _match_text(c: dict[str, Any], terms: list[str]) -> tuple[list[str], str | None]:
    """Match terms across text fields; return (terms, evidence string for the first hit)."""
    hits: list[str] = []
    evidence = None
    for field, text in _text_fields(c):
        found = find_terms(text, terms)
        if found and evidence is None:
            evidence = f"{field} mentions '{found[0]}': \"{_snippet(text, found[0])}\""
        hits += [t for t in found if t not in hits]
    return hits, evidence


def _match_tech(c: dict[str, Any], terms: list[str]) -> list[str]:
    """Technologies (original spelling) containing any of the terms."""
    techs = c.get("technologies") or []
    return [t for t in techs if any(term in str(t).lower() for term in terms)]


def classify_sector(c: dict[str, Any]) -> str:
    """Map industry / sub_industry (then keywords) onto a sector key, or 'other'."""
    sectors: dict[str, list[str]] = SIGNAL_CONFIG["sectors"]
    for field in ("industry", "sub_industry", "industry_code_label"):
        text = c.get(field)
        if not text:
            continue
        for sector, terms in sectors.items():
            if find_terms(str(text), terms):
                return sector
    kw = ", ".join(map(str, c.get("keywords") or []))
    if kw:
        for sector, terms in sectors.items():
            if find_terms(kw, terms):
                return sector
    return "other"


def is_australian(c: dict[str, Any]) -> bool:
    country = (c.get("country") or "").strip().lower()
    return country in SIGNAL_CONFIG["au_country_names"]


def _months_since(iso: str | None, as_of: dt.date) -> float | None:
    if not iso:
        return None
    try:
        d = dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    return (as_of - d).days / 30.44


def _sig(sid: str, label: str, category: str, weight: int, evidence: str) -> dict[str, Any]:
    return {"id": sid, "label": label, "category": category, "weight": weight, "evidence": evidence}


# --------------------------------------------------------------------------- #
# Signal rules
# --------------------------------------------------------------------------- #
_SENSITIVITY_LABELS = {
    "health_records": "health records (Privacy Act sensitive information / NDB scheme)",
    "payments": "payment / financial data (PCI DSS 4.0 scope)",
    "pii": "personal information (Privacy Act / NDB scheme)",
    "confidential": "confidential client data (privilege, financial records)",
}
_SURFACE_LABELS = {
    "ecommerce": "E-commerce payment pages (PCI DSS 4.0 req. 6.4.3 / 11.6.1)",
    "saas": "SaaS / customer-facing platform",
    "remote_services": "Remote / online customer services",
    "multi_location": "Multiple locations / distributed sites",
}
_SURFACE_IDS = {"ecommerce": "ecommerce_surface", "saas": "saas_platform", "remote_services": "remote_services",
                "multi_location": "multi_location"}
_REGULATED_HOOK = {
    "healthcare": "NDB scheme: health is the #1 breach-reporting sector",
    "finance": "APRA CPS 234 / AFSL obligations; PCI DSS 4.0",
    "insurance": "APRA CPS 234 information-security standard",
    "government": "ISM / PSPF / Essential Eight ML2",
    "critical_infrastructure": "SOCI Act CIRMP obligations",
    "legal": "client privilege + NDB scheme",
    "education": "student data under the Privacy Act / NDB scheme",
}


def _jobs_text(c: dict[str, Any]) -> tuple[str | None, str | None]:
    """Text of any jobs-like raw column carried in `extra` (e.g. 'Open Roles', 'job_titles')."""
    hints = SIGNAL_CONFIG["jobs_extra_column_hints"]
    for col, val in (c.get("extra") or {}).items():
        if any(h in col.lower() for h in hints) and isinstance(val, (str, list)):
            text = ", ".join(map(str, val)) if isinstance(val, list) else val
            if text.strip():
                return col, text
    return None, None


def compute_signals(c: dict[str, Any], as_of: dt.date | None = None) -> list[dict[str, Any]]:
    """All signals for one canonical company record. Pure and deterministic given `as_of`."""
    as_of = as_of or dt.date.today()
    cfg, th = SIGNAL_CONFIG, SIGNAL_CONFIG["thresholds"]
    out: list[dict[str, Any]] = []
    sector = classify_sector(c)
    au = is_australian(c)
    emp = c.get("employee_count")
    country_ev = f"country = '{c.get('country')}'"
    industry_ev = f"industry = '{c.get('industry') or c.get('sub_industry')}'"

    # ---- regulatory -------------------------------------------------------
    if sector in cfg["regulated_sectors"]:
        w, regimes = cfg["regulated_sectors"][sector]
        hook = _REGULATED_HOOK[sector] if au else regimes
        out.append(_sig("regulated_industry", f"Regulated industry: {cfg['sector_labels'][sector]} ({hook})",
                        "regulatory", w, industry_ev))
    terms, ev = _match_text(c, cfg["compliance_terms"])
    if terms:
        out.append(_sig("compliance_framework_mentioned",
                        f"Names compliance obligations ({', '.join(t.upper() for t in terms[:3])})",
                        "regulatory", 10, ev or ""))
    if au and sector in ("finance", "insurance"):
        out.append(_sig("apra_cps234", "Likely APRA-regulated: CPS 234 information-security standard "
                                       "(72-hour incident notification)", "regulatory", 8,
                        f"{country_ev}, {industry_ev}"))
    if au and sector in cfg["soci_sectors"]:
        narrow = cfg["soci_sectors"][sector]
        text_ev = None
        if narrow is not None:
            hit, text_ev = _match_text(c, narrow)
            if not hit:
                narrow = False
        if narrow is not False:
            out.append(_sig("critical_infrastructure",
                            "SOCI Act critical-infrastructure sector (CIRMP aligned to Essential Eight / ISO 27001)",
                            "regulatory", 8 if (emp or 0) >= 200 else 5,
                            f"{country_ev}, {industry_ev}" + (f"; {text_ev}" if text_ev else "")))
    if au:
        rev = c.get("revenue_usd")
        obligation = "Privacy Act / NDB scheme + Cyber Security Act 2024 ransomware-payment reporting"
        if rev is not None:
            if rev >= cfg["privacy_act_revenue_usd"]:
                out.append(_sig("privacy_act_threshold", f"Above A$3M turnover: {obligation}", "regulatory", 6,
                                f"{country_ev}, revenue_usd = {rev:,.0f} (≥ ~US$2M ≈ A$3M)"))
        elif emp is not None and emp >= cfg["privacy_act_employee_proxy"]:
            out.append(_sig("privacy_act_threshold", f"Likely above A$3M turnover: {obligation}", "regulatory", 4,
                            f"{country_ev}, employee_count = {emp} (no revenue field; proxy ≥ "
                            f"{cfg['privacy_act_employee_proxy']} staff ≈ A$3M turnover)"))
        elif sector == "healthcare":
            out.append(_sig("privacy_act_threshold", "Health service provider: Privacy Act / NDB scheme applies "
                                                     "regardless of turnover", "regulatory", 5,
                            f"{country_ev}, {industry_ev}"))
    if sector not in ("finance", "insurance"):
        terms, ev = _match_text(c, cfg["financial_client_terms"])
        if terms:
            out.append(_sig("financial_supply_chain", "Supplies banks/insurers/super funds: APRA CPS 234 third-party "
                                                      "security requirements flow down", "regulatory", 8, ev or ""))
    terms, ev = _match_text(c, cfg["gov_supplier_terms"])
    if terms and sector != "government":
        out.append(_sig("government_supplier", "Supplies government/defence: ISM, Essential Eight and DISP "
                                               "flow-down requirements", "regulatory", 8, ev or ""))
    if sector == "technology":
        terms, ev = _match_text(c, cfg["b2b_vendor_terms"])
        if terms:
            out.append(_sig("saas_vendor_soc2", "B2B software/IT vendor: SOC 2 / ISO 27001 is a procurement gate "
                                                "for enterprise deals", "regulatory", 7, f"{industry_ev}; {ev}"))

    # ---- data sensitivity -------------------------------------------------
    for key, terms_list in cfg["data_sensitivity"].items():
        terms, ev = _match_text(c, terms_list)
        if terms:
            out.append(_sig(f"handles_{key}", f"Handles {_SENSITIVITY_LABELS[key]}", "data_sensitivity",
                            cfg["data_sensitivity_weights"][key], ev or ""))

    # ---- attack surface ---------------------------------------------------
    ecom_tech = _match_tech(c, ["shopify", "magento", "woocommerce", "bigcommerce", "stripe", "paypal", "afterpay",
                                "square", "neto", "prestashop", "opencart"])
    for key, terms_list in cfg["attack_surface"].items():
        terms, ev = _match_text(c, terms_list)
        if key == "ecommerce" and not terms and ecom_tech:
            ev = f"technologies include {', '.join(repr(t) for t in ecom_tech[:3])}"
            terms = ecom_tech
        if terms:
            out.append(_sig(_SURFACE_IDS[key], _SURFACE_LABELS[key], "attack_surface",
                            cfg["attack_surface_weights"][key], ev or ""))
    cms = _match_tech(c, cfg["tech"]["exposed_cms"])
    if cms:
        out.append(_sig("exposed_cms", f"Commonly exploited web platform ({', '.join(cms[:3])})", "attack_surface",
                        7, f"technologies include {', '.join(repr(t) for t in cms[:3])}"))
    cloud = _match_tech(c, cfg["tech"]["cloud"])
    if cloud:
        out.append(_sig("cloud_footprint", "Public-cloud footprint (misconfiguration risk)", "attack_surface", 4,
                        f"technologies include {', '.join(repr(t) for t in cloud[:3])}"))

    # ---- growth -----------------------------------------------------------
    g = c.get("employee_growth_pct")
    if g is not None:
        if g >= th["growth_strong_pct"]:
            out.append(_sig("rapid_growth", f"Strong headcount growth ({g:.0f}%): identities and endpoints "
                                            "outpace controls", "growth", 10, f"employee_growth_pct = {g}"))
        elif g >= th["growth_moderate_pct"]:
            out.append(_sig("rapid_growth", f"Headcount growing ({g:.0f}%)", "growth", 6,
                            f"employee_growth_pct = {g}"))
    jp = c.get("job_postings")
    if jp is not None and jp >= th["hiring_postings"]:
        out.append(_sig("hiring_signal", f"Actively hiring ({jp} open roles)", "growth", 6, f"job_postings = {jp}"))
    ft = c.get("funding_total_usd")
    if ft is not None and ft >= th["funded_min_usd"]:
        out.append(_sig("venture_funded", f"Externally funded (US${ft / 1e6:,.1f}M raised; investor due diligence)",
                        "growth", 5, f"funding_total_usd = {ft:,.0f}"))
    age = c.get("company_age_years")
    if age is None and isinstance(c.get("founded_year"), int):
        age = as_of.year - c["founded_year"]
    if age is not None and age <= th["young_company_years"] and (emp or 0) >= th["young_company_min_employees"]:
        out.append(_sig("young_scaling", f"Young company scaling ({age} yrs, {emp} staff)", "growth", 6,
                        f"founded_year = {c.get('founded_year')}, employee_count = {emp}"))
    terms, ev = _match_text(c, cfg["growth_terms"])
    if terms:
        out.append(_sig("growth_language", f"Describes growth/expansion ('{terms[0]}')", "growth", 4, ev or ""))

    # ---- maturity gap -----------------------------------------------------
    techs = c.get("technologies") or []
    has_security_tech = bool(_match_tech(c, cfg["tech"]["security"] + cfg["tech"]["edr"]))
    if emp is not None and th["maturity_gap_min"] <= emp <= th["maturity_gap_max"]:
        out.append(_sig("target_without_soc", "Big enough to be a target, too small for an in-house SOC",
                        "maturity_gap", 6, f"employee_count = {emp} ({th['maturity_gap_min']}–"
                                           f"{th['maturity_gap_max']} band)"))
    if techs and not has_security_tech and (emp or 0) >= th["min_employees_for_gap"]:
        out.append(_sig("maturity_gap_no_security", "No security tooling detected in tech stack (below the "
                                                    "security poverty line)", "maturity_gap", 8,
                        f"technologies ({len(techs)}) = {', '.join(map(str, techs[:5]))}"
                        f"{'…' if len(techs) > 5 else ''}; none are security products"))
    if techs:
        missing = [name for name, key in (("identity/MFA", "identity_mfa"), ("EDR", "edr"), ("backup", "backup"))
                   if not _match_tech(c, cfg["tech"][key])]
        if missing:
            out.append(_sig("insurance_control_gap", f"Missing controls cyber insurers require "
                                                     f"({', '.join(missing)})", "maturity_gap",
                            2 + 2 * len(missing), f"technologies ({len(techs)}) list no {', '.join(missing)} "
                                                  "product"))
    legacy = _match_tech(c, cfg["tech"]["legacy_onprem"])
    if legacy:
        out.append(_sig("legacy_stack", f"Legacy / on-prem tech in stack ({', '.join(legacy[:3])})",
                        "maturity_gap", 7, f"technologies include {', '.join(repr(t) for t in legacy[:3])}"))
    desc = c.get("description")
    if desc and (emp or 0) >= th["min_employees_for_gap"] and not _match_text(c, cfg["security_terms"])[0]:
        out.append(_sig("no_security_messaging", "No security/compliance language in public profile",
                        "maturity_gap", 3, "description and keywords contain no security terms"))

    # ---- timing -----------------------------------------------------------
    months = _months_since(c.get("last_funding_date"), as_of)
    if months is not None and 0 <= months <= th["recent_funding_months"]:
        out.append(_sig("recent_funding", f"Raised funding {months:.0f} months ago (new budget, investor DD)",
                        "timing", 10, f"last_funding_date = {c.get('last_funding_date')}"))
    if jp is not None and emp and jp >= th["rapid_hiring_min_postings"] and jp / emp >= th["rapid_hiring_ratio"]:
        out.append(_sig("rapid_hiring", f"Hiring fast relative to size ({jp} roles / {emp} staff)", "timing", 7,
                        f"job_postings = {jp}, employee_count = {emp}"))
    col, jobs = _jobs_text(c)
    roles = find_terms(jobs, cfg["security_role_terms"]) if jobs else []
    if roles:
        out.append(_sig("hiring_security_roles", f"Hiring security/IT roles ('{roles[0]}'): active project",
                        "timing", 10, f"{col} mentions '{roles[0]}': \"{_snippet(jobs, roles[0])}\""))
    else:
        hiring_ctx = ["hiring", "recruiting", "join our team", "careers", "we are looking for"]
        if _match_text(c, hiring_ctx)[0]:
            roles, ev = _match_text(c, cfg["security_role_terms"])
            if roles:
                out.append(_sig("hiring_security_roles", f"Hiring security/IT roles ('{roles[0]}')", "timing", 8,
                                ev or ""))
    if g is not None and g >= th["hypergrowth_pct"]:
        out.append(_sig("hypergrowth", f"Hypergrowth ({g:.0f}% headcount): controls lag headcount", "timing", 6,
                        f"employee_growth_pct = {g}"))
    terms, ev = _match_text(c, cfg["incident_terms"])
    if terms:
        out.append(_sig("incident_mention", f"Public mention of a security incident ('{terms[0]}')", "timing", 10,
                        ev or ""))
    return out
