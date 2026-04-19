"""Render a tailored CV from profile + match result -> PDF bytes.

Two PDF backends are supported:

  * **WeasyPrint** (preferred): best quality, but requires the Pango/Cairo
    system libraries (``brew install pango`` on macOS, ``apt-get install
    libpango-1.0-0 libpangoft2-1.0-0`` on Debian/Ubuntu).
  * **xhtml2pdf** (fallback): pure-Python, zero system deps. Slightly less
    polished but works anywhere ``pip install`` works.

At import time we try to load WeasyPrint. If that fails (missing system libs,
which is a common error on macOS with Anaconda Python), we silently fall back
to xhtml2pdf so the app still runs. You can also force a backend via the
``CV_PDF_BACKEND`` env var: ``weasyprint``, ``xhtml2pdf``, or ``auto``
(default).
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path

import markdown as md_lib
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import assets
from .matcher import MatchResult


log = logging.getLogger(__name__)


# ---------- CSS ------------------------------------------------------------

# WeasyPrint supports modern CSS (letter-spacing, text-transform, flex, etc.).
CV_CSS_WEASYPRINT = """
@page { size: A4; margin: 14mm 16mm 18mm 16mm; }
html { font-family: "Helvetica", "Arial", sans-serif; font-size: 10.5pt; color: #1f2937; }
h1 { font-size: 22pt; margin: 0 0 2pt 0; color: #111827; }
/* Section titles — blue for a more modern, "trendy" feel while staying
   print-friendly. The underline picks up a lighter blue so it reads as
   an accent, not a hard rule. */
h2 { font-size: 12.5pt; margin: 14pt 0 4pt 0; color: #1e3a8a;
     border-bottom: 1.5px solid #60a5fa; padding-bottom: 2pt;
     text-transform: uppercase; letter-spacing: 0.6pt; }
h3 { font-size: 11pt; margin: 8pt 0 1pt 0; color: #111827; }
p  { margin: 3pt 0; line-height: 1.35; }
ul { margin: 3pt 0 6pt 16pt; padding: 0; }
li { margin: 1pt 0; line-height: 1.35; }
a  { color: #2563eb; text-decoration: none; }
strong { color: #111827; }
em { color: #4b5563; }
hr { border: 0; border-top: 1px solid #d1d5db; margin: 8pt 0 6pt 0; }

/* --- Header table --- */
table.header-table { width: 100%; border-collapse: collapse; margin: 0 0 4pt 0; }
table.header-table td { border: 0; padding: 0; vertical-align: middle; }
.header-side img { max-width: 110px; max-height: 110px; }
.header-main { text-align: center; padding: 0 10pt; }
.cv-name { font-size: 24pt; font-weight: bold; color: #0f172a; line-height: 1.1; }
.cv-subtitle { font-size: 11.5pt; color: #475569; margin-top: 3pt; font-weight: 600; }
.cv-contact { font-size: 10pt; color: #475569; margin-top: 3pt; }
.cv-contact a { color: #2563eb; }

/* QR code label — WeasyPrint gets the 3-part thought-bubble trail
   overlay. The fallback label (`.qr-badge-below`) is hidden in this
   backend; it's only used by xhtml2pdf. */
.qr-wrap { position: relative; display: inline-block; }
.qr-bubble {
  position: absolute;
  border-radius: 50%;
  background: linear-gradient(135deg, #3b82f6, #ec4899);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.18);
}
.qr-bubble-sm { width: 4pt; height: 4pt; top: -1pt; right: 6pt; }
.qr-bubble-md { width: 7pt; height: 7pt; top: -8pt; right: -4pt; }
.qr-badge-overlay {
  position: absolute;
  top: -18pt;
  right: -22pt;
  padding: 2.5pt 10pt;
  font-size: 7.5pt;
  font-weight: 700;
  color: #ffffff;
  background: linear-gradient(135deg, #3b82f6, #ec4899);
  border-radius: 999px;
  box-shadow: 0 2px 4px rgba(15, 23, 42, 0.22);
  white-space: nowrap;
}
.qr-badge-below { display: none; }
/* Inline QR rendered next to the LinkedIn anchor in the contact line.
   `vertical-align: middle` keeps it centred on the text baseline. */
.inline-qr {
  vertical-align: middle;
  margin-left: 5pt;
  margin-right: 2pt;
}
/* LinkedIn variant — solid/branded blue gradient. Declared AFTER the
   default `.qr-badge-overlay` rule so the cascade picks the blue
   background on ties. The fallback `.qr-badge-below` rule below in the
   xhtml2pdf stylesheet has its own override using `background-color`. */
.qr-badge-overlay.qr-badge-linkedin,
.qr-badge-linkedin {
  background: linear-gradient(135deg, #1e3a8a, #2563eb);
}
"""

# xhtml2pdf has a simpler CSS engine (no letter-spacing, no text-transform,
# and it prefers explicit border-*-width declarations).
CV_CSS_XHTML2PDF = """
@page { size: A4; margin: 14mm 16mm 18mm 16mm; }
body { font-family: Helvetica; font-size: 10.5pt; color: #1f2937; }
h1 { font-size: 22pt; margin-top: 0; margin-bottom: 2pt; color: #111827; }
/* Section titles in blue. xhtml2pdf prefers explicit per-side border
   declarations over the shorthand. */
h2 { font-size: 11.5pt; margin-top: 14pt; margin-bottom: 4pt; color: #1e3a8a;
     border-bottom-width: 1.5px; border-bottom-style: solid; border-bottom-color: #60a5fa;
     padding-bottom: 2pt; }
h3 { font-size: 11pt; margin-top: 8pt; margin-bottom: 1pt; color: #111827; }
p  { margin-top: 3pt; margin-bottom: 3pt; line-height: 1.35; }
ul { margin-top: 3pt; margin-bottom: 6pt; margin-left: 16pt; padding-left: 0; }
li { margin-top: 1pt; margin-bottom: 1pt; line-height: 1.35; }
a  { color: #2563eb; text-decoration: none; }
strong { color: #111827; }
em { color: #4b5563; }
hr { border-top-width: 1px; border-top-style: solid; border-top-color: #d1d5db;
     margin-top: 8pt; margin-bottom: 6pt; }

/* --- Header table --- */
table.header-table { width: 100%; margin-bottom: 4pt; }
.header-main { text-align: center; }
.cv-name { font-size: 24pt; font-weight: bold; color: #0f172a; }
.cv-subtitle { font-size: 11.5pt; color: #475569; margin-top: 3pt; font-weight: bold; }
.cv-contact { font-size: 10pt; color: #475569; margin-top: 3pt; }
.cv-contact a { color: #2563eb; }

/* xhtml2pdf's CSS engine doesn't support absolute positioning reliably
   inside table cells. We therefore hide the WeasyPrint-only overlay
   (trail dots + floating pill) and render a centred pill UNDER the QR
   via the fallback row in the nested table inside the template. */
.qr-bubble { display: none; }
.qr-badge-overlay { display: none; }
.qr-badge-below {
  display: inline-block;
  margin-top: 4pt;
  padding-top: 2pt; padding-bottom: 2pt;
  padding-left: 9pt; padding-right: 9pt;
  font-size: 8pt;
  font-weight: bold;
  color: #ffffff;
  background-color: #ec4899;
}
/* LinkedIn variant — branded blue. xhtml2pdf doesn't render gradients
   reliably so we use a solid LinkedIn-ish blue. Declared AFTER the base
   .qr-badge-below rule so the cascade picks blue on ties. */
.qr-badge-linkedin { background-color: #2563eb; }
/* Inline QR next to the LinkedIn anchor in the contact line. xhtml2pdf
   has flaky `vertical-align` support on inline images, so we just nudge
   it with side margins and accept whichever baseline the engine picks
   (it lands close to centre because the image height is comparable to
   the contact line's font-size + leading). */
.inline-qr { margin-left: 5pt; margin-right: 2pt; vertical-align: middle; }
"""


# ---------- Template rendering helpers -------------------------------------

import re as _re

# Proper capitalisation for acronyms & branded names. Anything not in this
# table falls through to Title Case. Extend when you notice a skill showing
# up as e.g. "Aws" or "Ci/Cd" in the generated CV.
_SKILL_DISPLAY: dict[str, str] = {
    "aws": "AWS", "gcp": "GCP", "ci/cd": "CI/CD", "k8s": "K8s",
    "sql": "SQL", "nosql": "NoSQL", "html": "HTML", "css": "CSS",
    "api": "API", "rest": "REST", "rest api": "REST API", "rest apis": "REST APIs",
    "graphql": "GraphQL", "grpc": "gRPC", "json": "JSON", "xml": "XML",
    "jwt": "JWT", "oauth": "OAuth", "oauth2": "OAuth2", "saml": "SAML",
    "tls": "TLS", "ssl": "SSL", "mtls": "mTLS",
    "iam": "IAM", "rds": "RDS", "s3": "S3", "ec2": "EC2", "ecs": "ECS",
    "eks": "EKS", "vpc": "VPC", "cloudwatch": "CloudWatch", "cloudfront": "CloudFront",
    "elk": "ELK", "gke": "GKE", "aks": "AKS",
    "kyc": "KYC", "aml": "AML", "aml/kyc": "AML/KYC", "mica": "MiCA",
    "mifid": "MiFID", "mifid ii": "MiFID II", "psd2": "PSD2",
    "iso 20022": "ISO 20022", "sepa": "SEPA", "gdpr": "GDPR",
    "pci": "PCI", "pci-dss": "PCI-DSS", "finma": "FINMA",
    "hft": "HFT", "otc": "OTC", "fx": "FX", "dex": "DEX", "amm": "AMM",
    "nft": "NFT", "dapp": "DApp", "dapps": "DApps", "defi": "DeFi",
    "evm": "EVM", "mpc": "MPC", "hsm": "HSM", "dao": "DAO", "dlt": "DLT",
    "tdd": "TDD", "bdd": "BDD", "ddd": "DDD",
    "ml": "ML", "ai": "AI", "llm": "LLM", "llms": "LLMs", "rag": "RAG",
    "node.js": "Node.js", "next.js": "Next.js", "vue.js": "Vue.js",
    "asp.net": "ASP.NET", ".net": ".NET",
    "postgresql": "PostgreSQL", "mongodb": "MongoDB", "mysql": "MySQL",
    "github": "GitHub", "gitlab": "GitLab", "github actions": "GitHub Actions",
    "javascript": "JavaScript", "typescript": "TypeScript",
    "fastapi": "FastAPI", "pytorch": "PyTorch", "tensorflow": "TensorFlow",
    "grafana": "Grafana", "prometheus": "Prometheus",
    "cloudwatch": "CloudWatch", "datadog": "Datadog", "splunk": "Splunk",
    "devops": "DevOps", "gitops": "GitOps", "sre": "SRE", "iac": "IaC",
    "terraform": "Terraform", "ansible": "Ansible", "kubernetes": "Kubernetes",
    "docker": "Docker", "jenkins": "Jenkins", "circleci": "CircleCI",
}


def _display_skill(skill: str) -> str:
    """Display-friendly name for a user-entered skill, preserving the user's
    capitalisation if it wasn't entered in pure lowercase.
    """
    s = str(skill).strip()
    if not s:
        return s
    # If the user typed "FastAPI" or "Node.js" themselves, leave it alone.
    if s != s.lower():
        return s
    # Pure lowercase → try the display table, else fall back to Title-ish case.
    return _prettify(s)


def _prettify(skill: str) -> str:
    """Return a nicely-capitalised version of a normalised skill string."""
    key = skill.lower()
    if key in _SKILL_DISPLAY:
        return _SKILL_DISPLAY[key]
    # Preserve dots, slashes, hyphens but title-case around them.
    return " ".join(w.capitalize() if w.isalpha() else w for w in key.split(" "))


def _count_matches_in_text(text: str, matched_set: set[str]) -> int:
    """How many matched skill terms appear in `text`."""
    t = text.lower()
    count = 0
    for m in matched_set:
        if _re.search(r"[ ./+#-]", m):
            if m in t:
                count += 1
        else:
            if _re.search(rf"\b{_re.escape(m)}\b", t):
                count += 1
    return count


def _order_experience_by_match(profile: dict, match: MatchResult) -> list[dict]:
    """Return experience in the profile's reverse-chronological order, but
    within each role sort the highlight bullets so those containing matched
    JD keywords float to the top.

    Recruiters expect reverse-chronological roles, so we never re-order roles
    themselves — only the bullets inside them.
    """
    matched_set = {m.lower() for m in match.matched_skills}
    out: list[dict] = []
    for job in profile.get("experience") or []:
        job_copy = dict(job)  # don't mutate the caller's profile
        highlights = list(job.get("highlights") or [])
        # Stable sort: -match_count keeps ties in original order
        highlights.sort(key=lambda h: -_count_matches_in_text(str(h), matched_set))
        job_copy["highlights"] = highlights
        out.append(job_copy)
    return out


def _highlight_skills(profile: dict, match: MatchResult) -> dict[str, list[str]]:
    """Return skill groups filtered to ONLY the skills that overlap with the
    job description.

    Why filter instead of reorder? A tailored CV reads tighter when the
    Skills block shows exactly the overlap with the role — any extra
    skills are noise a recruiter has to scan past. Groups that end up
    empty are dropped from the output so the template can skip the
    section entirely.

    Matching is done via the match result's `matched_skills` set (which
    comes from either the keyword matcher or the embedding matcher —
    whichever strategy the caller chose).
    """
    matched_set = {m.lower() for m in match.matched_skills}
    skills = profile.get("skills") or {}

    if isinstance(skills, list):
        items = [s for s in skills if str(s).lower() in matched_set]
        items.sort(key=lambda s: str(s).lower())
        return {"skills": [_display_skill(s) for s in items]} if items else {}

    out: dict[str, list[str]] = {}
    for group, items in skills.items():
        matched_in_group = [
            s for s in (items or [])
            if str(s).lower() in matched_set
        ]
        if not matched_in_group:
            continue
        matched_in_group.sort(key=lambda s: str(s).lower())
        out[group] = [_display_skill(s) for s in matched_in_group]
    return out


def _qr_label(url: str | None) -> str:
    """Derive a short caption for the QR code bubble from the target URL.

    Returns "" if there's no URL — the template renders nothing in that case.
    We keep the detection dumb-but-obvious: anything whose host contains
    `linkedin.com` is labelled "LinkedIn", otherwise it's "Portfolio website".
    Useful special cases (GitHub, personal site) can be added here later.

    The template also branches on this string to pick the badge colour
    (LinkedIn → blue, everything else → the default pink/blue gradient),
    so keep the literal "LinkedIn" return value in sync with the
    `{% if qr_label == 'LinkedIn' %}` check in `cv_template.md`.
    """
    if not url or not url.strip():
        return ""
    u = url.lower()
    if "linkedin.com" in u:
        return "LinkedIn"
    if "github.com" in u:
        return "GitHub"
    return "Portfolio website"


def _tailored_summary(profile: dict, match: MatchResult, job_title: str | None) -> str:
    """Return the user's own summary, followed by a discreet "Core stack" line
    that surfaces the top JD-matched skills.

    We deliberately avoid meta-phrasing like "Directly relevant to this role"
    or "Strong fit for this X role" — those read as AI-generated on a real
    CV. Instead, the base summary stays in the user's voice, and a short
    stack callout at the end of the block plays the same role as a classic
    "Key skills" footer — a well-known CV convention.

    Set CV_SUMMARY_STACK=none to suppress the callout entirely (summary is
    then returned verbatim).
    """
    base = (profile.get("summary") or "").strip()

    if os.environ.get("CV_SUMMARY_STACK", "on").lower() in ("none", "off", "false", "0"):
        return base

    # Pick top matched skills, preferring ones the user explicitly listed.
    user_skill_set: set[str] = set()
    skills_block = profile.get("skills") or {}
    if isinstance(skills_block, dict):
        for items in skills_block.values():
            for s in items or []:
                user_skill_set.add(str(s).lower())
    elif isinstance(skills_block, list):
        user_skill_set = {str(s).lower() for s in skills_block}

    matched = match.matched_skills
    prioritised = [m for m in matched if m in user_skill_set] + \
                  [m for m in matched if m not in user_skill_set]
    top = [_prettify(s) for s in prioritised[:6]]
    if not top:
        return base

    # Rendered as Markdown → bold label + comma-separated tech names.
    # Looks like a standard CV "Key skills" footer.
    stack_line = f"**Core stack:** {', '.join(top)}."

    if not base:
        return stack_line
    # Blank line between base paragraph and stack line so Markdown renders
    # them as separate blocks; ends up as its own line in the PDF.
    return f"{base}\n\n{stack_line}"


def render_markdown(
    profile: dict,
    match: MatchResult,
    template_path: str | Path,
    job_title: str | None = None,
    qr_target_url: str | None = None,
    qr_secondary_url: str | None = None,
    photo_path: str | Path | None = None,
) -> str:
    template_path = Path(template_path)
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(template_path.name)

    # Defensive normalisation: treat blank strings as None and, if only the
    # secondary slot is filled, promote it to primary. Keeps the template's
    # "primary present?" check meaningful — the dual-QR layout only triggers
    # when there are actually two distinct URLs to render.
    primary = (qr_target_url or "").strip() or None
    secondary = (qr_secondary_url or "").strip() or None
    if not primary and secondary:
        primary, secondary = secondary, None
    # Don't render the same QR twice if the user accidentally supplies both
    # slots with the same URL.
    if primary and secondary and primary.lower() == secondary.lower():
        secondary = None

    qr_data_uri = assets.generate_qr_data_uri(primary) if primary else ""
    qr_secondary_data_uri = (
        assets.generate_qr_data_uri(secondary) if secondary else ""
    )

    # Photo:
    #   * If the caller passed an existing photo_path → embed it.
    #   * Otherwise fall back to the neutral user-silhouette placeholder.
    photo_data_uri: str
    if photo_path and Path(photo_path).exists():
        try:
            photo_data_uri = assets.photo_data_uri_from_file(photo_path)
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to load photo %s (%s); using placeholder", photo_path, e)
            photo_data_uri = assets.generate_user_icon_data_uri()
    else:
        photo_data_uri = assets.generate_user_icon_data_uri()

    return template.render(
        profile=profile,
        match=match,
        tailored_summary=_tailored_summary(profile, match, job_title),
        highlighted_skills=_highlight_skills(profile, match),
        ordered_experience=_order_experience_by_match(profile, match),
        qr_data_uri=qr_data_uri,
        qr_label=_qr_label(primary),
        qr_secondary_data_uri=qr_secondary_data_uri,
        qr_secondary_label=_qr_label(secondary),
        photo_data_uri=photo_data_uri,
    )


# ---------- PDF backends ---------------------------------------------------

_WEASYPRINT_OK: bool | None = None   # lazy-probed on first use


def _try_weasyprint() -> bool:
    """Probe for a working WeasyPrint install (imports + native libs)."""
    global _WEASYPRINT_OK
    if _WEASYPRINT_OK is not None:
        return _WEASYPRINT_OK
    try:
        # Importing WeasyPrint triggers the native-library lookup.
        from weasyprint import HTML  # noqa: F401
        _WEASYPRINT_OK = True
    except Exception as e:  # noqa: BLE001  — ImportError, OSError, anything
        log.info("WeasyPrint unavailable (%s) — falling back to xhtml2pdf.", e)
        _WEASYPRINT_OK = False
    return _WEASYPRINT_OK


def _render_with_weasyprint(html_body: str) -> bytes:
    from weasyprint import CSS, HTML  # imported lazily
    doc = f"""<!doctype html>
<html><head><meta charset="utf-8"></head>
<body>{html_body}</body></html>"""
    return HTML(string=doc).write_pdf(stylesheets=[CSS(string=CV_CSS_WEASYPRINT)])


def _render_with_xhtml2pdf(html_body: str) -> bytes:
    from xhtml2pdf import pisa  # imported lazily

    # xhtml2pdf needs the stylesheet inlined and doesn't honor text-transform,
    # so we don't try. The h2 heading styling is "good enough".
    doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <style>{CV_CSS_XHTML2PDF}</style>
</head>
<body>{html_body}</body>
</html>"""

    buf = io.BytesIO()
    result = pisa.CreatePDF(src=doc, dest=buf, encoding="utf-8")
    if result.err:
        raise RuntimeError(
            f"xhtml2pdf failed to render PDF ({result.err} errors). "
            "Check the generated markdown for unusual characters or markup."
        )
    return buf.getvalue()


def _select_backend() -> str:
    """Return 'weasyprint' or 'xhtml2pdf' based on env var and availability."""
    pref = (os.environ.get("CV_PDF_BACKEND") or "auto").lower()
    if pref == "weasyprint":
        # honor the user's explicit choice even if it might fail — the
        # resulting OSError will be clearer than silently falling back.
        return "weasyprint"
    if pref == "xhtml2pdf":
        return "xhtml2pdf"
    # auto: prefer WeasyPrint if available, else fall back.
    return "weasyprint" if _try_weasyprint() else "xhtml2pdf"


def markdown_to_pdf(markdown_text: str) -> tuple[bytes, str]:
    """Render markdown to PDF. Returns (pdf_bytes, backend_used)."""
    html_body = md_lib.markdown(
        markdown_text,
        extensions=["extra", "sane_lists"],
    )
    backend = _select_backend()
    if backend == "weasyprint":
        return _render_with_weasyprint(html_body), "weasyprint"
    return _render_with_xhtml2pdf(html_body), "xhtml2pdf"


def build_cv(
    profile: dict,
    match: MatchResult,
    template_path: str | Path,
    job_title: str | None = None,
    qr_target_url: str | None = None,
    qr_secondary_url: str | None = None,
    photo_path: str | Path | None = None,
) -> tuple[str, bytes, str]:
    """Returns (markdown_text, pdf_bytes, backend_used)."""
    md_text = render_markdown(
        profile, match, template_path,
        job_title=job_title,
        qr_target_url=qr_target_url,
        qr_secondary_url=qr_secondary_url,
        photo_path=photo_path,
    )
    pdf_bytes, backend = markdown_to_pdf(md_text)
    return md_text, pdf_bytes, backend
