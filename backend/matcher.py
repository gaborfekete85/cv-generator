"""Match a parsed profile against a job description.

Strategy
--------
We deliberately avoid a loose "extract any plausible-looking token" heuristic
because it picks up HTML artifacts and fragments like "ismsunderstanding" or
"architectures.". Instead we rely on a **curated skill vocabulary**:

1. Build a flat profile corpus (skills, keywords, highlights, prose) in
   lowercase.
2. Build a vocabulary = base vocab (below) ∪ user's own skills ∪ user's
   experience keywords ∪ any per-request `extra_keywords` supplied by the
   frontend.
3. Scan the job description for every vocab entry, using word-boundary
   matching for simple terms and substring matching for multi-word / punctuated
   ones (e.g. "node.js", "ci/cd", "aml/kyc", "smart contract").
4. Separately extract all-uppercase acronyms (2–6 chars) so short domain
   codes like "MiCA", "AML", "KYC", "FINMA" are picked up even if they're
   missing from the vocab. Filter against a small allow-list of common
   English-ish acronyms.
5. For each matched JD keyword, check whether it also appears in the profile
   corpus → matched vs. missing.
6. Weight "required" sections (lines containing "required", "must have",
   "qualifications:", etc.) 2× when computing the score.

This gives high precision: the only terms that show up as skills are ones
we recognise as skills. Terms we don't recognise are ignored rather than
surfaced as nonsense.

If you find a technology is consistently being missed, either:
  * add it to your profile skills (it'll be added to the vocabulary), or
  * pass it via the `extra_keywords` field when calling /api/analyze or
    /api/generate, or
  * extend `BASE_SKILL_VOCAB` below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Curated vocabulary. Ordered roughly by domain for readability.
# Matching is case-insensitive. Multi-word / punctuated phrases use substring
# matching, single tokens use word-boundary matching.
# ---------------------------------------------------------------------------

BASE_SKILL_VOCAB: list[str] = [
    # --- Languages ---
    "python", "javascript", "typescript", "java", "kotlin",
    "c++", "c#", "go", "golang", "ruby", "rust", "php",
    "scala", "swift", "objective-c", "sql", "bash", "shell", "r",
    "dart", "elixir", "erlang", "haskell", "clojure", "lua", "perl",
    "solidity", "vyper", "move", "cairo",

    # --- Backend frameworks ---
    "fastapi", "flask", "django", "starlette", "pyramid", "tornado",
    "node.js", "nodejs", "express", "nestjs", "koa",
    "next.js", "nuxt", "remix",
    "spring", "spring boot", "spring cloud", "micronaut", "quarkus", "vert.x",
    ".net", "asp.net", "asp.net core", "entity framework",
    "rails", "ruby on rails", "sinatra",
    "laravel", "symfony",
    "gin", "echo framework", "fiber",
    "actix", "axum", "rocket",
    "phoenix",

    # --- Frontend frameworks ---
    "react", "react native", "redux", "mobx", "zustand",
    "vue", "vue.js", "vuex", "pinia",
    "angular", "rxjs", "ngrx",
    "svelte", "sveltekit", "solid.js",
    "tailwind", "tailwindcss", "bootstrap", "material-ui", "mui", "chakra",
    "storybook",

    # --- APIs / protocols ---
    "graphql", "apollo",
    "rest", "restful", "rest api", "rest apis", "api design", "openapi", "swagger",
    "grpc", "protobuf", "protocol buffers",
    "soap", "websocket", "websockets", "sse", "webrtc",

    # --- Data / ML / AI ---
    "pandas", "numpy", "scipy", "scikit-learn", "sklearn",
    "pytorch", "tensorflow", "keras", "jax", "huggingface", "transformers",
    "langchain", "llamaindex", "openai", "anthropic", "claude", "gpt",
    "llm", "llms", "rag", "vector database", "embeddings",
    "machine learning", "deep learning", "ml", "ai",
    "pinecone", "weaviate", "qdrant", "chroma", "milvus",
    "spark", "pyspark", "hadoop", "hdfs", "hive", "presto", "trino",
    "kafka", "kafka streams", "ksqldb", "confluent",
    "airflow", "prefect", "dagster",
    "dbt", "fivetran",
    "snowflake", "databricks", "bigquery", "redshift", "clickhouse", "duckdb",
    "tableau", "looker", "power bi", "metabase", "superset",
    "mlflow", "kubeflow", "weights & biases", "wandb",

    # --- Cloud ---
    "aws", "amazon web services", "gcp", "google cloud", "azure",
    "digitalocean", "heroku", "cloudflare", "vercel", "netlify",
    # AWS
    "ec2", "s3", "lambda", "api gateway", "sqs", "sns", "eventbridge",
    "dynamodb", "rds", "aurora", "elasticache",
    "ecs", "eks", "fargate",
    "cloudfront", "route53", "iam", "cloudformation", "cdk",
    "kinesis", "glue", "athena", "emr",
    # GCP
    "gke", "cloud run", "cloud functions", "pub/sub", "firestore",
    "bigtable", "cloud storage", "cloud sql", "dataflow", "dataproc",
    # Azure
    "aks", "azure functions", "cosmos db", "event hubs", "service bus",

    # --- DevOps / Infra ---
    "docker", "podman", "containerd", "container orchestration",
    "kubernetes", "k8s", "openshift", "rancher",
    "helm", "kustomize", "argocd", "flux",
    "istio", "linkerd", "consul", "envoy", "service mesh",
    "terraform", "ansible", "pulumi", "opentofu",
    "packer", "vagrant", "chef", "puppet",
    "ci/cd", "continuous integration", "continuous delivery", "continuous deployment",
    "jenkins", "github actions", "gitlab ci", "circleci", "teamcity", "drone",
    "prometheus", "grafana", "datadog", "new relic", "splunk", "dynatrace",
    "elasticsearch", "elastic", "kibana", "logstash", "elk", "opensearch",
    "loki", "fluentd", "fluent bit",
    "opentelemetry", "otel", "jaeger", "zipkin",
    "infrastructure as code", "iac", "gitops",
    "sre", "site reliability engineering", "platform engineering",

    # --- Databases ---
    "postgresql", "postgres", "mysql", "mariadb", "sqlite", "oracle", "db2",
    "sql server", "mssql",
    "mongodb", "couchbase", "cassandra", "scylladb",
    "redis", "memcached",
    "cosmosdb",
    "neo4j", "janusgraph", "arangodb",
    "influxdb", "timescaledb",
    "hbase", "bigtable",

    # --- Messaging / streaming ---
    "rabbitmq", "amqp", "activemq", "nats", "pulsar", "zeromq",
    "event-driven", "event driven", "event-driven architecture",
    "event sourcing", "cqrs", "saga", "saga pattern",

    # --- Security ---
    "oauth", "oauth2", "oidc", "openid connect", "saml",
    "jwt", "pki",
    "tls", "ssl", "mtls",
    "hashicorp vault", "keycloak", "auth0", "okta",
    "owasp", "pentest", "penetration testing",
    "sast", "dast", "snyk",
    "zero trust",

    # --- Architecture / practices ---
    "microservices", "microservice", "monolith",
    "domain-driven design", "domain driven design", "ddd",
    "hexagonal architecture", "clean architecture",
    "api design", "system design", "solution design", "architectural consistency",
    "tdd", "test driven development", "test-driven development",
    "bdd", "behavior driven development",
    "pair programming", "code review",
    "agile", "scrum", "kanban", "safe", "lean",
    "extreme programming",
    "devops", "devsecops",

    # --- Tools ---
    "git", "github", "gitlab", "bitbucket", "mercurial",
    "jira", "confluence", "asana", "linear", "trello", "notion",
    "slack", "teams",
    "linux", "unix", "macos", "windows",
    "intellij", "pycharm", "webstorm", "vscode", "visual studio code",
    "postman", "insomnia",
    "junit", "pytest", "jest", "mocha", "cypress", "playwright", "selenium",
    "testcontainers",

    # --- Blockchain / Web3 / Crypto ---
    "blockchain", "distributed ledger", "dlt",
    "smart contract", "smart contracts",
    "dapp", "dapps", "decentralized application", "dapp architecture",
    "ethereum", "bitcoin", "solana", "polygon", "avalanche",
    "binance smart chain", "bsc",
    "arbitrum", "optimism", "base network", "zk-sync", "starknet",
    "layer 1", "layer 2", "l1", "l2", "layer-2", "layer-1",
    "rollup", "rollups", "zk-rollup", "zk rollup", "optimistic rollup",
    "proof of work", "pow", "proof of stake", "pos",
    "consensus", "validator", "staking", "slashing",
    "hyperledger", "hyperledger fabric", "hyperledger besu",
    "corda", "r3 corda",
    "polkadot", "substrate", "cosmos", "cosmos sdk", "tendermint",
    "web3", "web 3", "web3.js", "ethers.js", "viem", "wagmi",
    "hardhat", "foundry", "truffle", "remix ide", "brownie", "anchor",
    "metamask", "walletconnect", "wallet integration",
    "nft", "nfts", "erc-20", "erc-721", "erc-1155",
    "defi", "decentralized finance",
    "dex", "decentralized exchange", "amm", "automated market maker",
    "uniswap", "aave", "compound protocol", "curve", "lido",
    "tokenization", "token", "stablecoin", "cbdc", "central bank digital currency",
    "crypto", "cryptocurrency", "digital asset", "digital assets",
    "crypto payment", "crypto payments", "digital payment", "digital payments",
    "custody", "self-custody", "self custody",
    "digital asset custody", "asset custody", "crypto custody",
    "multi-signature", "multisig", "hsm", "hardware security module",
    "mpc", "multi-party computation",
    "ipfs", "filecoin", "arweave",
    "chainlink", "oracle network", "oracles",
    "dao", "daos", "governance",
    "gas", "gas optimization", "evm", "wasm",
    "yield farming", "liquidity mining", "liquidity pool",
    "onchain", "on-chain", "offchain", "off-chain",
    "bridge", "cross-chain", "interoperability",
    "scam", "scams", "fraud detection",

    # --- Fintech / Banking / Finance ---
    "fintech", "banking", "private banking", "investment banking",
    "retail banking", "wealth management", "asset management",
    "banking system", "banking systems", "core banking",
    "trading", "algorithmic trading", "algo trading", "hft", "high-frequency trading",
    "brokerage", "exchange", "market making", "market maker",
    "securities", "equities", "bonds", "fixed income",
    "derivatives", "futures", "options", "swaps", "otc",
    "fx", "foreign exchange", "forex",
    "commodities",
    "payments", "payment processing", "payment gateway",
    "acquiring", "issuing",
    "treasury", "cash management", "liquidity management",
    "risk management", "credit risk", "market risk", "operational risk",
    "portfolio management",
    "settlement", "clearing",
    "kyc", "aml", "kyb", "cdd", "edd",
    "aml/kyc", "kyc/aml",
    "anti-money laundering", "know your customer",
    "sanctions screening", "transaction monitoring",
    "compliance", "regulatory", "regulation", "regulations",
    "regulatory compliance", "securities regulation", "securities regulations",
    "mica", "markets in crypto-assets",
    "mifid", "mifid ii",
    "psd2", "open banking",
    "iso 20022", "swift", "sepa", "ach", "wire transfer",
    "fix protocol",
    "gdpr", "ccpa", "sox", "sarbanes-oxley",
    "pci", "pci-dss", "pci dss",
    "finma", "fca", "sec", "cftc", "esma", "ecb",
    "basel", "basel iii",

    # --- Soft / leadership ---
    "team leadership", "technical leadership", "tech lead", "lead developer",
    "mentoring", "coaching", "mentorship",
    "stakeholder management", "cross-functional", "cross functional",
    "cross-functional collaboration",
    "product mindset", "product thinking",
    "communication", "collaboration",
    "self-starter", "self starter", "ownership",
    "problem solving", "problem-solving",
    "architectural decisions",
    "production support", "troubleshooting", "incident response",
    "on-call", "oncall",
    "release management",
    "sprint planning", "sprint delivery", "sprint retro",
    "agile development", "agile methodology", "agile methodologies",
]


# Acronyms that are pure English noise, not skills. Keep short — if in doubt
# leave it in the results so users can spot false positives and report them.
ACRONYM_STOPLIST: set[str] = {
    # Geopolitical
    "usa", "us", "uk", "eu", "uae",
    # Business titles
    "ceo", "cto", "cfo", "coo", "vp", "svp", "evp",
    # Vague/common
    "qa", "ie", "eg", "etc", "ex", "fyi", "tbd", "n/a", "na", "ok",
    "www", "url", "id",
    # File formats — rarely the *skill* a JD is asking for
    "pdf", "csv", "xml", "json", "yaml", "yml",
    # Misc
    "faq", "tos", "b2b", "b2c", "hr", "it",
    # Roman numerals — appear in things like "MiFID II" and get picked up
    # as a separate 2-char acronym unless we drop them here.
    "ii", "iii", "iv", "vi", "vii", "viii", "ix", "xi", "xii",
    # Sub-parts of compound terms we already recognise as phrases. If
    # "ci/cd" or "pci-dss" is matched we don't also want the lone halves
    # surfacing as their own "skills".
    "ci", "cd", "dss",
}


STOP_WORDS: set[str] = {
    # English function words (minimal — the curated vocab does the heavy lifting)
    "a", "an", "the", "and", "or", "but",
    "of", "in", "on", "at", "to", "for", "with", "by", "from", "as",
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "we", "you", "they", "our", "their", "your", "this", "that", "these", "those",
    "will", "would", "should", "can", "could", "may", "might", "must",
}


@dataclass
class MatchResult:
    score: float
    matched_skills: list[str]
    missing_skills: list[str]
    jd_keywords: list[str] = field(default_factory=list)
    matched_required: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PHRASE_RE = re.compile(r"[ ./+#-]")  # "multi-word / punctuated" phrase marker


def _normalize(text: str) -> str:
    """Lowercase the text. Used for matching prep.

    For phrase/substring matching (e.g. "securities regulations"), whitespace
    runs (including newlines across wrapped lines) need to be collapsed to a
    single space. We keep that responsibility in a dedicated helper below
    (`_normalize_for_phrases`) so callers that *want* newlines (e.g. the
    required-section detector) can opt out.
    """
    return text.lower()


def _normalize_for_phrases(text: str) -> str:
    """Lowercase + collapse all whitespace (incl. newlines) to single spaces.

    Without this, a JD that line-wraps "securities<newline>  regulations"
    fails a naïve ``"securities regulations" in text`` check.
    """
    return re.sub(r"\s+", " ", text.lower())


def _is_phrase(term: str) -> bool:
    """A "phrase" is any vocab entry with a space or punctuation in it.
    We match those with substring (so we handle things like "ci/cd" and
    "node.js" without fighting the regex word-boundary rules).
    """
    return bool(_PHRASE_RE.search(term))


def _term_in_text(term: str, text: str) -> bool:
    """Check if `term` appears in `text`.

    For phrase terms (anything with a space or punctuation), we normalise
    whitespace in the text first so line-wrapped phrases still match. For
    single tokens we use the original text — \\b word boundaries already
    handle newlines correctly.
    """
    term = term.strip()
    if not term:
        return False
    if _is_phrase(term):
        return term in _normalize_for_phrases(text)
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def _flatten_profile(profile: dict) -> tuple[str, set[str]]:
    """Return (lowercased corpus, explicit skills lowercased)."""
    parts: list[str] = []
    explicit: set[str] = set()

    summary = profile.get("summary") or ""
    parts.append(str(summary))

    skills = profile.get("skills") or {}
    if isinstance(skills, dict):
        for _group, items in skills.items():
            for s in items or []:
                explicit.add(_normalize(str(s)))
                parts.append(str(s))
    elif isinstance(skills, list):
        for s in skills:
            explicit.add(_normalize(str(s)))
            parts.append(str(s))

    for job in profile.get("experience") or []:
        parts.append(str(job.get("role", "")))
        parts.append(str(job.get("company", "")))
        for h in job.get("highlights") or []:
            parts.append(str(h))
        for kw in job.get("keywords") or []:
            explicit.add(_normalize(str(kw)))
            parts.append(str(kw))

    for ed in profile.get("education") or []:
        parts.append(str(ed.get("degree", "")))
        parts.append(str(ed.get("school", "")))

    for c in profile.get("certifications") or []:
        parts.append(str(c.get("name", "")))

    for p in profile.get("projects") or []:
        if isinstance(p, dict):
            parts.append(str(p.get("name", "")))
            parts.append(str(p.get("description", "")))
            for kw in p.get("keywords") or []:
                explicit.add(_normalize(str(kw)))
                parts.append(str(kw))
        else:
            parts.append(str(p))

    parts.append(profile.get("body") or "")

    return _normalize("\n".join(parts)), explicit


def _extract_vocab_hits(text: str, vocab: Iterable[str]) -> set[str]:
    """Find vocab entries that appear in `text` (already lowercased).

    We match phrases against a whitespace-collapsed copy of the text so that
    line-wrapped compounds like "securities<newline>regulations" still match.
    Single-token matches use the original text because \\b word boundaries
    already handle whitespace correctly.
    """
    text_phrase = _normalize_for_phrases(text)
    hits: set[str] = set()
    for phrase in vocab:
        p = (phrase or "").lower().strip()
        if not p:
            continue
        if _is_phrase(p):
            if p in text_phrase:
                hits.add(p)
        else:
            if re.search(rf"\b{re.escape(p)}\b", text) is not None:
                hits.add(p)
    return hits


def _extract_acronyms(original_text: str) -> set[str]:
    """Extract all-uppercase short acronyms (2–6 letters), optionally with
    digits or internal slashes (KYC, AML, MICA, PCI-DSS, AML/KYC, ISO20022).

    MiCA is a special case: mixed-case "Capitalized+ALLCAPS" style. We pick it
    up with a permissive pattern but drop anything that's <3 chars or in
    ACRONYM_STOPLIST.
    """
    patterns = [
        r"\b[A-Z]{2,6}\b",                  # AML, KYC, GDPR
        r"\b[A-Z]{2,6}/[A-Z]{2,6}\b",       # AML/KYC
        r"\b[A-Z]{2,6}-[A-Z]{2,6}\b",       # PCI-DSS
        r"\b[A-Z][a-z][A-Z]{2,5}\b",        # MiCA, MiFID (CamelCase-ish acronyms)
        r"\b[A-Z]{2,6}[0-9]{1,5}\b",        # ISO20022, MiFID2
        r"\b[A-Z]{2,4}\s?[0-9]{3,5}\b",     # "ISO 20022"
    ]
    out: set[str] = set()
    for pat in patterns:
        for m in re.findall(pat, original_text):
            t = m.strip().lower()
            if len(t.replace("/", "").replace("-", "").replace(" ", "")) < 2:
                continue
            if t in ACRONYM_STOPLIST:
                continue
            out.add(t)
    return out


def _detect_required_lines(jd: str) -> str:
    req_markers = (
        "required", "must have", "must-have", "requirement",
        "essential", "qualifications", "you have", "you must",
        "what you'll bring", "what you bring", "we expect",
    )
    lines = jd.splitlines()
    out: list[str] = []
    in_req_block = False
    for raw in lines:
        line = raw.strip().lower()
        if not line:
            in_req_block = False
            continue
        if any(m in line for m in req_markers):
            in_req_block = True
            out.append(line)
            continue
        if in_req_block and (line.startswith(("-", "*", "•", "·"))
                             or re.match(r"^\d+[\.\)]\s", line)
                             or len(line) < 200):
            out.append(line)
        else:
            in_req_block = False
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def match(
    profile: dict,
    job_description: str,
    extra_keywords: Iterable[str] | None = None,
) -> MatchResult:
    corpus, explicit_skills = _flatten_profile(profile)

    # Preserve the original text for acronym extraction (needs case);
    # everything else uses the lowercased copy.
    jd_raw = job_description
    jd = _normalize(job_description)

    # Build vocab
    vocab: set[str] = set(BASE_SKILL_VOCAB) | explicit_skills
    if extra_keywords:
        for kw in extra_keywords:
            kw_norm = _normalize(str(kw)).strip()
            if kw_norm:
                vocab.add(kw_norm)

    vocab_hits = _extract_vocab_hits(jd, vocab)
    acronym_hits = _extract_acronyms(jd_raw)

    # Drop acronyms that would duplicate or degrade a richer vocab hit:
    #  * exact match (e.g. "mica" is vocab AND acronym — keep vocab version),
    #  * or substring of a compound vocab hit (e.g. drop lone "dss" when
    #    "pci-dss" matched; drop lone "ii" when "mifid ii" matched).
    def _is_covered_by_vocab(a: str) -> bool:
        if a in vocab_hits:
            return True
        return any(a in v for v in vocab_hits if len(v) > len(a))

    acronym_hits = {a for a in acronym_hits if not _is_covered_by_vocab(a)}

    all_jd_keywords = sorted(vocab_hits | acronym_hits)

    matched: list[str] = []
    missing: list[str] = []
    for kw in all_jd_keywords:
        (matched if _term_in_text(kw, corpus) else missing).append(kw)

    # Required section scoring
    req_blob = _detect_required_lines(job_description)
    req_vocab_hits = _extract_vocab_hits(req_blob, vocab) if req_blob else set()
    # Also consider acronyms from the required section (re-run on the original
    # casing of those lines).
    req_lines_original = "\n".join(
        ln for ln in job_description.splitlines()
        if ln.strip().lower() in req_blob.splitlines()
    )
    req_acronym_hits = _extract_acronyms(req_lines_original) if req_lines_original else set()
    req_hits = (req_vocab_hits | req_acronym_hits) & set(all_jd_keywords)

    matched_required = sorted([k for k in req_hits if k in matched])
    missing_required = sorted([k for k in req_hits if k in missing])

    # Score
    if all_jd_keywords:
        base = len(matched) / len(all_jd_keywords)
    else:
        base = 0.0
    if req_hits:
        req_score = len(matched_required) / len(req_hits)
        score = (base + 2 * req_score) / 3
    else:
        score = base

    return MatchResult(
        score=round(score * 100, 1),
        matched_skills=sorted(matched),
        missing_skills=sorted(missing),
        jd_keywords=all_jd_keywords,
        matched_required=matched_required,
        missing_required=missing_required,
    )
