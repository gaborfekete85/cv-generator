"""Embedding-based similarity matcher — alternative to ``matcher.py``.

How it works
------------
1. The profile is split into small chunks: summary line, each experience
   role, each skill group, each highlight bullet, the prose body.
2. The job description is split into paragraph- / sentence-sized chunks.
3. Every chunk is embedded with a small sentence-transformer model.
4. Cosine similarity between each JD chunk and the closest profile chunk
   gives a per-chunk "best match" score; we average those and re-scale to
   a 0–100 percentage.
5. For the matched / missing skills lists we reuse the vocab-based JD
   keyword extractor from ``matcher.py`` (so the UI surface stays
   identical), but decide whether each keyword is *matched* by embedding
   the keyword itself and checking cosine similarity against the profile
   chunks. This means synonyms and variants (K8s ↔ Kubernetes, GCP ↔
   Google Cloud) are correctly credited as matches.

Strengths over the keyword matcher:
  * Catches synonyms / paraphrasing (semantic, not lexical).
  * The percentage reflects overall fit, not just keyword coverage.

Weaknesses:
  * Requires the ``fastembed`` package (ONNX-based, ~100 MB installed).
  * First request downloads ~22 MB of model weights from HuggingFace.
  * Non-deterministic across minor phrasing changes — less predictable
    than the keyword matcher.

The keyword matcher remains the default, so this file's dependency is
genuinely optional: if ``fastembed`` isn't installed, ``is_available()``
returns False and the API gracefully refuses the embedding strategy.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from .matcher import (
    ACRONYM_STOPLIST,
    BASE_SKILL_VOCAB,
    MatchResult,
    _extract_acronyms,
    _extract_vocab_hits,
    _flatten_profile,
    _normalize,
)


log = logging.getLogger(__name__)

# Small + fast + good quality. Weights are ~22MB. Other options:
#   "sentence-transformers/all-MiniLM-L6-v2"    ~22MB, classic baseline
#   "BAAI/bge-small-en-v1.5"                    ~32MB, higher quality
# We pick the MiniLM default because fastembed ships it as a built-in.
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Cosine-similarity thresholds (empirical):
#   * RESCALE_LO / HI: map mean cosine [LO, HI] -> [0 %, 100 %] for the
#     headline score. Below LO is "basically no overlap", above HI is
#     "near-identical content" — both are rare.
#   * MATCH_THRESHOLD: keyword is "matched" if any profile chunk has
#     cos-sim ≥ this to the keyword's embedding.
_RESCALE_LO = 0.30
_RESCALE_HI = 0.80
_MATCH_THRESHOLD = 0.55


# -------- Availability + model loading ------------------------------------

def is_available() -> bool:
    """True iff the optional embedding deps are installed in this Python."""
    try:
        import fastembed  # noqa: F401
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(maxsize=1)
def _get_model():
    from fastembed import TextEmbedding
    log.info("Loading embedding model: %s", DEFAULT_MODEL)
    return TextEmbedding(model_name=DEFAULT_MODEL)


def _embed(texts: list[str]):
    """Embed a list of strings → (N, d) numpy array of L2-normalised vectors."""
    import numpy as np
    if not texts:
        return np.zeros((0, 384), dtype="float32")
    model = _get_model()
    vecs = list(model.embed(texts))
    return np.vstack(vecs).astype("float32")


# -------- Chunking --------------------------------------------------------

def _chunks_from_profile(profile: dict) -> list[str]:
    out: list[str] = []

    summary = (profile.get("summary") or "").strip()
    if summary:
        out.append(summary)

    body = (profile.get("body") or "").strip()
    if body:
        for para in re.split(r"\n{2,}", body):
            para = para.strip()
            if len(para) >= 20:
                out.append(para)

    skills = profile.get("skills") or {}
    if isinstance(skills, dict):
        for group, items in skills.items():
            if isinstance(items, list) and items:
                out.append(
                    f"{str(group).replace('_', ' ')}: "
                    + ", ".join(str(i) for i in items)
                )
    elif isinstance(skills, list):
        if skills:
            out.append("Skills: " + ", ".join(str(s) for s in skills))

    for job in profile.get("experience") or []:
        header = f"{job.get('role', '')} at {job.get('company', '')}".strip(" at ")
        if header:
            out.append(header)
        for h in job.get("highlights") or []:
            if h and str(h).strip():
                out.append(str(h).strip())
        kws = job.get("keywords") or []
        if kws:
            out.append("Technologies used: " + ", ".join(str(k) for k in kws))

    for ed in profile.get("education") or []:
        bit = (f"{ed.get('degree', '')} — {ed.get('school', '')}").strip(" —")
        if bit:
            out.append(bit)

    for c in profile.get("certifications") or []:
        name = c.get("name", "")
        if name:
            out.append(name)

    for p in profile.get("projects") or []:
        if isinstance(p, str) and p.strip():
            out.append(p.strip())
        elif isinstance(p, dict):
            label = p.get("name") or p.get("description") or ""
            if label:
                out.append(label)

    return [c for c in out if c]


def _chunks_from_jd(jd: str) -> list[str]:
    out: list[str] = []
    for para in re.split(r"\n\s*\n", jd or ""):
        para = para.strip()
        if not para:
            continue
        if 20 <= len(para) <= 500:
            out.append(para)
        elif len(para) > 500:
            for sent in re.split(r"(?<=[.!?])\s+", para):
                sent = sent.strip()
                if 20 <= len(sent) <= 400:
                    out.append(sent)
    # De-dupe while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped


# -------- Main entry point ------------------------------------------------

def match(
    profile: dict,
    job_description: str,
    extra_keywords: Iterable[str] | None = None,
) -> MatchResult:
    """Score profile ↔ JD using sentence embeddings.

    Returns a ``MatchResult`` with the same shape as the keyword matcher,
    so the API and frontend don't need to special-case anything.
    """
    if not is_available():
        raise RuntimeError(
            "Embedding strategy not available — install fastembed "
            "(pip install fastembed) and retry."
        )
    import numpy as np

    p_chunks = _chunks_from_profile(profile)
    j_chunks = _chunks_from_jd(job_description)

    if not p_chunks or not j_chunks:
        return MatchResult(
            score=0.0, matched_skills=[], missing_skills=[],
            jd_keywords=[], matched_required=[], missing_required=[],
        )

    p_emb = _embed(p_chunks)  # (n_p, d)
    j_emb = _embed(j_chunks)  # (n_j, d)

    # Cosine similarity — fastembed vectors are L2-normalised already.
    sim = j_emb @ p_emb.T        # (n_j, n_p)
    best_per_jd = sim.max(axis=1)  # best profile chunk for each JD chunk
    mean_sim = float(best_per_jd.mean())

    # Rescale to a 0–100 percentage using an empirical window where users
    # actually care about deltas. Saturate outside the window.
    raw = (mean_sim - _RESCALE_LO) / (_RESCALE_HI - _RESCALE_LO)
    score = max(0.0, min(100.0, raw * 100.0))

    # -- Matched / missing via keyword extraction + embedding check --
    # Re-use the vocab + acronym logic from matcher.py so the UI sees
    # the same "what the JD is asking for" list regardless of strategy.
    jd_lower = _normalize(job_description)
    _, explicit = _flatten_profile(profile)
    vocab: set[str] = set(BASE_SKILL_VOCAB) | explicit
    if extra_keywords:
        for kw in extra_keywords:
            k = str(kw).strip().lower()
            if k:
                vocab.add(k)

    vocab_hits = _extract_vocab_hits(jd_lower, vocab)
    acronym_hits = _extract_acronyms(job_description)
    acronym_hits = {
        a for a in acronym_hits
        if a not in vocab_hits and a not in ACRONYM_STOPLIST
    }
    jd_keywords = sorted(vocab_hits | acronym_hits)

    matched: list[str] = []
    missing: list[str] = []
    if jd_keywords:
        kw_emb = _embed(jd_keywords)      # (n_k, d)
        kw_sim = kw_emb @ p_emb.T          # (n_k, n_p)
        kw_max = kw_sim.max(axis=1)
        for i, kw in enumerate(jd_keywords):
            (matched if float(kw_max[i]) >= _MATCH_THRESHOLD else missing).append(kw)

    return MatchResult(
        score=round(score, 1),
        matched_skills=sorted(matched),
        missing_skills=sorted(missing),
        jd_keywords=jd_keywords,
        # We don't compute a "required" slice in embedding mode — the
        # concept is already baked into the semantic score.
        matched_required=[],
        missing_required=[],
    )
