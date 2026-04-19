"""FastAPI app: profile + job description -> match % + tailored CV PDF.

Run locally with:
    uvicorn backend.main:app --reload

Endpoints:
    GET  /                    -> frontend
    GET  /api/profile         -> parsed profile
    POST /api/fetch-jd        -> fetch JD text from a URL (best-effort)
    POST /api/analyze         -> match score + matched/missing skills
    POST /api/generate        -> build tailored CV, returns metadata + download URL
    GET  /api/download/{id}   -> download the generated PDF
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, cv_builder, jd_fetcher, matcher, matcher_embedding, profile_loader


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FRONTEND_DIR = ROOT / "frontend"
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

PROFILES_DIR = DATA_DIR / "profiles"
PROFILES_DIR.mkdir(exist_ok=True)
PROFILE_PICTURES_DIR = DATA_DIR / "profile_pictures"
PROFILE_PICTURES_DIR.mkdir(exist_ok=True)
# CV template is shipped with the application code (not under DATA_DIR) so
# changes to the layout flow through a normal Docker image rebuild instead
# of needing to be hand-synced onto the Kubernetes persistent volume that
# backs /app/data.
TEMPLATE_PATH = Path(__file__).resolve().parent / "cv_template.md"

# Limits for the profile-picture upload endpoint.
MAX_PICTURE_BYTES = 5 * 1024 * 1024      # 5 MB raw upload
PICTURE_MAX_DIMENSION_PX = 512           # resized to fit inside 512x512

# "me" is the special alias for the current user's own profile — resolved
# at request time to the file keyed by their Clerk user id.
ME_PROFILE_ID = "me"

# Demo profile ids live under data/profiles/<slug>.md with lowercase+hyphen
# slugs. User profiles live under data/profiles/<clerk_user_id>.json and use
# Clerk's own id format (underscores + mixed case), which deliberately
# doesn't match this regex — so an attacker can't pass a Clerk id as
# `profile_id` and read someone else's data.
_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# User ids written to disk. Matches the Clerk "user_..." format.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


app = FastAPI(title="CV Generator", version="0.1.0")


# Diagnostic — tells the operator at boot whether Clerk got wired up, so a
# misplaced .env is obvious in the uvicorn logs instead of only showing up
# on the /login page as "Authentication is not configured".
print(
    f"[cv-generator] Auth: "
    f"{'ENABLED' if auth.auth_enabled() else 'DISABLED'}"
    f"  |  Clerk Frontend API: {auth.CLERK_FRONTEND_API or '(not set)'}",
    flush=True,
)

# Local dev only — the frontend is served from the same origin so CORS is
# really only useful if you point another client at the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----- Pydantic models ------------------------------------------------------

class FetchJDRequest(BaseModel):
    url: str


class FetchJDResponse(BaseModel):
    text: str
    detected_title: Optional[str] = None


class AnalyzeRequest(BaseModel):
    job_description: str = Field(..., min_length=20)
    job_title: Optional[str] = None
    extra_keywords: list[str] = Field(default_factory=list)
    profile_id: Optional[str] = None
    strategy: Optional[str] = "keyword"   # "keyword" (default) | "embedding"


class AnalyzeResponse(BaseModel):
    score: float
    matched_skills: list[str]
    missing_skills: list[str]
    matched_required: list[str]
    missing_required: list[str]
    jd_keywords: list[str]


class GenerateRequest(BaseModel):
    job_description: str = Field(..., min_length=20)
    job_title: Optional[str] = None
    company: Optional[str] = None
    extra_keywords: list[str] = Field(default_factory=list)
    # Primary QR URL — bigger, intended as the recruiter's "scan-me-first"
    # link (typically the user's website / portfolio). Optional.
    qr_target_url: Optional[str] = None
    # Optional secondary QR URL — rendered smaller, beneath the primary.
    # When both slots are filled, the CV header shows two QR codes stacked.
    # When only one slot is filled, the layout collapses to the original
    # single-QR design.
    qr_secondary_url: Optional[str] = None
    profile_id: Optional[str] = None
    strategy: Optional[str] = "keyword"


class GenerateResponse(BaseModel):
    cv_id: str
    score: float
    matched_skills: list[str]
    missing_skills: list[str]
    markdown: str
    download_url: str
    filename: str
    pdf_backend: str


# ----- Helpers --------------------------------------------------------------

def _safe_slug(text: str | None) -> str:
    """Turn free text into a filesystem-safe slug.

    Returns an empty string for empty/None input so the caller can decide
    whether to substitute a default or drop the component entirely.
    """
    if not text:
        return ""
    return re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-").lower()


def _user_profile_path(user_id: str) -> Path:
    """Path to the signed-in user's JSON profile. Validates the id strictly
    to block any path-traversal attempts (though Clerk ids are already
    well-formed)."""
    if not _USER_ID_RE.match(user_id or ""):
        raise HTTPException(status_code=400, detail="Malformed user id from token")
    return PROFILES_DIR / f"{user_id}.json"


def _current_user_id(authorization: Optional[str]) -> str:
    """Extract the Clerk `sub` claim from the bearer token. Raises 401 if
    the token is missing/invalid (auth.require_auth does this)."""
    payload = auth.require_auth(authorization)
    sub = payload.get("sub") or ""
    if not sub or sub == "anonymous":
        raise HTTPException(status_code=401, detail="Valid user session required")
    return sub


def _resolve_profile_path(
    profile_id: Optional[str],
    authorization: Optional[str],
) -> Path:
    """Map a profile id to an on-disk file.

    * ``"me"`` → data/profiles/<clerk_user_id>.json — requires a valid JWT.
    * ``"<slug>"`` (lowercase + hyphens) → data/profiles/<slug>.md (demo).

    Anything else (including Clerk-style ids typed in directly) is rejected.
    """
    pid = (profile_id or "").strip().lower()

    if pid == ME_PROFILE_ID:
        user_id = _current_user_id(authorization)
        return _user_profile_path(user_id)

    if not pid:
        raise HTTPException(status_code=400, detail="profile_id is required")

    if not _PROFILE_ID_RE.match(pid):
        raise HTTPException(status_code=400, detail=f"Invalid profile id: {pid!r}")

    path = PROFILES_DIR / f"{pid}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Profile not found: {pid}")
    return path


def _load_profile_or_400(
    profile_id: Optional[str],
    authorization: Optional[str],
) -> dict:
    path = _resolve_profile_path(profile_id, authorization)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Profile not found. If this is your own profile, create it first at /profile.",
        )
    try:
        return profile_loader.load_profile(path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to parse {path.name}: {e}") from e


# ----- API endpoints --------------------------------------------------------

@app.get("/health")
def health() -> dict:
    """Liveness probe for Docker / Kubernetes / load balancers.

    Deliberately minimal — does not touch the profile, template, or PDF
    backends. Kept *unauthenticated* so probes don't need credentials.
    """
    return {"status": "ok"}


# Auth policy:
#   * Demo profiles (*.md in data/profiles/) are fully public.
#   * The signed-in user's own profile (profile_id = "me") requires a valid
#     Clerk JWT and resolves to data/profiles/<clerk_user_id>.json.
#   * /api/profiles is public but ENRICHES its response with the current
#     user's profile if a valid bearer token is presented.

def _enforce_profile_auth(
    profile_id: Optional[str],
    authorization: Optional[str],
) -> None:
    """Require auth only when the request touches 'me'."""
    if (profile_id or "").strip().lower() == ME_PROFILE_ID:
        auth.require_auth(authorization)


@app.get("/api/profiles")
def list_profiles(authorization: Optional[str] = Header(None)) -> list[dict]:
    """Return profiles the caller can select from.

    Always includes bundled demo profiles. If the caller provides a valid
    Clerk bearer token AND has a saved profile on disk, that profile is
    prepended at id = "me" so the UI can pin it to the top of the dropdown.
    """
    out: list[dict] = []

    # User's own profile, if authenticated and present on disk.
    if authorization:
        try:
            user_id = _current_user_id(authorization)
            path = _user_profile_path(user_id)
            if path.exists():
                p = profile_loader.load_profile(path)
                out.append({
                    "id": ME_PROFILE_ID,
                    "name": p.get("name") or "My profile",
                    "title": p.get("title") or "",
                    "category": "My profile",
                })
        except HTTPException:
            # Bad / expired token — treat as anonymous and skip the entry.
            pass
        except Exception:  # noqa: BLE001
            pass

    # Bundled demo profiles.
    for f in sorted(PROFILES_DIR.glob("*.md")):
        try:
            p = profile_loader.load_profile(f)
            out.append({
                "id": f.stem,
                "name": p.get("name") or f.stem,
                "title": p.get("title") or "",
                "category": p.get("category") or f.stem.replace("-", " ").title(),
            })
        except Exception:  # noqa: BLE001
            continue

    return out


@app.get("/api/profile")
def get_profile(
    id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    return _load_profile_or_400(id, authorization)


# -------- Per-user profile CRUD ------------------------------------------
# Stored at data/profiles/<clerk_user_id>.json. Only the authenticated
# owner can read or mutate their own profile.

@app.get("/api/my-profile")
def get_my_profile(authorization: Optional[str] = Header(None)) -> dict:
    """Return the caller's saved profile, or 404 if they don't have one yet."""
    user_id = _current_user_id(authorization)
    path = _user_profile_path(user_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="No profile yet")
    return profile_loader.load_profile(path)


@app.post("/api/my-profile")
def save_my_profile(
    body: dict,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Create or fully replace the caller's profile."""
    user_id = _current_user_id(authorization)
    if not isinstance(body, dict) or not body.get("name"):
        raise HTTPException(status_code=400, detail="Profile must have at least a name")
    path = _user_profile_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Pretty-print + UTF-8 so humans can diff/edit the file out-of-band.
    path.write_text(
        json.dumps(body, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"status": "ok", "id": ME_PROFILE_ID}


@app.delete("/api/my-profile")
def delete_my_profile(authorization: Optional[str] = Header(None)) -> dict:
    user_id = _current_user_id(authorization)
    path = _user_profile_path(user_id)
    if path.exists():
        path.unlink()
    return {"status": "deleted"}


# -------- Profile picture upload ------------------------------------------
# Stored at data/profile_pictures/<clerk_user_id>.jpg (always JPEG, regardless
# of upload format). Normalised to RGB, centre-cropped, resized to
# PICTURE_MAX_DIMENSION_PX so every file has the same shape on disk.

def _user_picture_path(user_id: str) -> Path:
    if not _USER_ID_RE.match(user_id or ""):
        raise HTTPException(status_code=400, detail="Malformed user id from token")
    return PROFILE_PICTURES_DIR / f"{user_id}.jpg"


@app.post("/api/my-profile-picture")
async def upload_profile_picture(
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
) -> dict:
    """Upload / replace the caller's profile picture."""
    user_id = _current_user_id(authorization)

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > MAX_PICTURE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Picture too large (max {MAX_PICTURE_BYTES // 1024 // 1024} MB).",
        )

    # Lazy import — Pillow is already a dep via qrcode[pil].
    from io import BytesIO
    from PIL import Image

    try:
        img = Image.open(BytesIO(content))
        img.load()  # decode now so we catch errors here, not later
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Not a valid image: {e}")

    # Flatten alpha / paletted / LA modes onto white so the resulting JPEG
    # doesn't end up with black backgrounds where transparency was.
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Resize in place (preserves aspect ratio). The CV renderer will crop
    # to a square when embedding.
    img.thumbnail(
        (PICTURE_MAX_DIMENSION_PX, PICTURE_MAX_DIMENSION_PX),
        Image.LANCZOS,
    )

    path = _user_picture_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="JPEG", quality=88, optimize=True)

    return {"status": "ok", "size_bytes": path.stat().st_size,
            "width": img.width, "height": img.height}


@app.get("/api/my-profile-picture")
def get_profile_picture(authorization: Optional[str] = Header(None)):
    """Serve the caller's profile picture. 404 if they haven't uploaded one."""
    user_id = _current_user_id(authorization)
    path = _user_picture_path(user_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="No profile picture")
    # Cache for 60s — avoids flashing the placeholder between refreshes.
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=60"})


@app.delete("/api/my-profile-picture")
def delete_profile_picture(authorization: Optional[str] = Header(None)) -> dict:
    user_id = _current_user_id(authorization)
    path = _user_picture_path(user_id)
    if path.exists():
        path.unlink()
    return {"status": "deleted"}


@app.post("/api/fetch-jd", response_model=FetchJDResponse)
def fetch_jd(body: FetchJDRequest) -> FetchJDResponse:
    try:
        text, title = jd_fetcher.fetch_job_description(body.url)
    except ValueError as e:
        # auth wall / empty
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch URL: {e}. Paste the job description text instead.",
        ) from e
    return FetchJDResponse(text=text, detected_title=title)


def _run_matcher(
    profile: dict,
    job_description: str,
    extra_keywords: list[str],
    strategy: Optional[str],
):
    """Dispatch to the right matcher based on `strategy`.

    Unknown or unavailable strategies fall back to the keyword matcher
    with a helpful 400 so the frontend can show the real reason.
    """
    s = (strategy or "keyword").lower().strip()
    if s == "embedding":
        if not matcher_embedding.is_available():
            raise HTTPException(
                status_code=503,
                detail="Embedding strategy is not available on this server. "
                       "Install `fastembed` (pip install fastembed) to enable it.",
            )
        return matcher_embedding.match(
            profile, job_description, extra_keywords=extra_keywords
        )
    if s not in ("keyword", ""):
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {s!r}")
    return matcher.match(
        profile, job_description, extra_keywords=extra_keywords
    )


@app.get("/api/strategies")
def list_strategies() -> list[dict]:
    """Advertise which match strategies this server can run, so the UI
    can grey-out ones whose dependencies aren't installed."""
    return [
        {
            "id": "keyword",
            "name": "Keyword match",
            "description": (
                "Deterministic vocabulary-based match. Fast, offline, "
                "predictable. Default."
            ),
            "available": True,
        },
        {
            "id": "embedding",
            "name": "Semantic similarity",
            "description": (
                "Neural sentence embeddings + cosine similarity. Catches "
                "synonyms and paraphrasing but needs the fastembed package."
            ),
            "available": matcher_embedding.is_available(),
        },
    ]


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(
    body: AnalyzeRequest,
    authorization: Optional[str] = Header(None),
) -> AnalyzeResponse:
    profile = _load_profile_or_400(body.profile_id, authorization)
    result = _run_matcher(
        profile, body.job_description, body.extra_keywords, body.strategy,
    )
    return AnalyzeResponse(**asdict(result))


@app.post("/api/generate", response_model=GenerateResponse)
def generate(
    body: GenerateRequest,
    authorization: Optional[str] = Header(None),
) -> GenerateResponse:
    profile = _load_profile_or_400(body.profile_id, authorization)
    result = _run_matcher(
        profile, body.job_description, body.extra_keywords, body.strategy,
    )

    # If the caller is generating a CV from their OWN saved profile and has
    # a profile picture on disk, embed it. Demo profiles always fall back
    # to the neutral user-silhouette placeholder.
    photo_path: Optional[Path] = None
    if (body.profile_id or "").strip().lower() == ME_PROFILE_ID and authorization:
        try:
            user_id = _current_user_id(authorization)
            candidate = _user_picture_path(user_id)
            if candidate.exists():
                photo_path = candidate
        except HTTPException:
            pass

    md_text, pdf_bytes, backend = cv_builder.build_cv(
        profile, result, TEMPLATE_PATH,
        job_title=body.job_title,
        qr_target_url=body.qr_target_url,
        qr_secondary_url=body.qr_secondary_url,
        photo_path=photo_path,
    )

    cv_id = uuid.uuid4().hex[:10]
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    parts = [
        _safe_slug(profile.get("name")) or "cv",
        _safe_slug(body.company),
        _safe_slug(body.job_title),
        ts,
    ]
    # Drop any empty parts so an optional title/company doesn't leave an
    # underscore in the filename (e.g. "gabor-fekete_YYYYMMDD-HHMM.pdf"
    # rather than "gabor-fekete__job_YYYYMMDD-HHMM.pdf").
    slug = "_".join(p for p in parts if p)
    filename = f"{slug}.pdf"

    pdf_path = OUTPUT_DIR / f"{cv_id}__{filename}"
    md_path = OUTPUT_DIR / f"{cv_id}__{slug}.md"
    pdf_path.write_bytes(pdf_bytes)
    md_path.write_text(md_text, encoding="utf-8")

    return GenerateResponse(
        cv_id=cv_id,
        score=result.score,
        matched_skills=result.matched_skills,
        missing_skills=result.missing_skills,
        markdown=md_text,
        download_url=f"/api/download/{cv_id}",
        filename=filename,
        pdf_backend=backend,
    )


@app.get("/api/download/{cv_id}")
def download(cv_id: str):
    # Download URLs use random 10-char hex ids — effectively unguessable for
    # casual snooping. Keeping this route open means links work for anonymous
    # visitors who just generated a demo CV.
    # Look up by prefix (we stored "<id>__<filename>.pdf")
    matches = list(OUTPUT_DIR.glob(f"{cv_id}__*.pdf"))
    if not matches:
        raise HTTPException(status_code=404, detail="CV not found")
    path = matches[0]
    display_name = path.name.split("__", 1)[-1]
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=display_name,
        headers={"Content-Disposition": f'attachment; filename="{display_name}"'},
    )


# ----- Frontend -------------------------------------------------------------

def _render_frontend(filename: str) -> str:
    """Load a frontend HTML file and inject the Clerk config placeholders.

    Placeholders (plain string substitution — chosen so it doesn't clash with
    JavaScript template literals the way Jinja `{{ }}` would):
      * __CLERK_PUBLISHABLE_KEY__  — the Clerk PK (safe to ship to browser)
      * __CLERK_FRONTEND_API__     — derived from the PK, e.g. https://xxx.clerk.accounts.dev
      * __AUTH_ENABLED__           — "true" / "false"
    """
    path = FRONTEND_DIR / filename
    if not path.exists():
        return f"<h1>Frontend file missing: {filename}</h1>"
    text = path.read_text(encoding="utf-8")
    return (
        text.replace("__CLERK_PUBLISHABLE_KEY__", auth.CLERK_PUBLISHABLE_KEY)
            .replace("__CLERK_FRONTEND_API__", auth.CLERK_FRONTEND_API)
            .replace("__AUTH_ENABLED__", "true" if auth.auth_enabled() else "false")
    )


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(_render_frontend("index.html"))


@app.get("/login", response_class=HTMLResponse)
def login_page() -> HTMLResponse:
    return HTMLResponse(_render_frontend("login.html"))


@app.get("/profile", response_class=HTMLResponse)
def profile_page() -> HTMLResponse:
    return HTMLResponse(_render_frontend("profile.html"))


# Favicon — served at both /favicon.ico (what the browser asks for by default)
# and /favicon.svg (what we reference from the HTML). Modern browsers render
# SVGs when the Content-Type says so, regardless of the URL's extension.
@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    path = FRONTEND_DIR / "favicon.svg"
    return FileResponse(
        path,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# Serve static assets (css/js) if present
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
