# CV Generator

A small FastAPI app that takes a job description (URL or pasted text), matches
it against your profile, and generates a tailored CV as a downloadable PDF.

## How it works

1. You keep your working history, skills, and background in
   [`data/profile.md`](data/profile.md). The top is YAML (structured) and the
   bottom is free-form prose.
2. You paste (or fetch) a job description in the web UI.
3. The matcher extracts keywords from the JD and compares them against your
   profile to produce a percentage match plus lists of matched / missing skills.
4. The CV builder renders [`data/cv_template.md`](data/cv_template.md) with
   Jinja2, emphasising the skills that match the job, then converts it to PDF.
5. The generated PDF is downloadable directly from the browser.

## Run with Docker (easiest)

The container bundles everything — including the WeasyPrint native libraries
— so you don't need to install Python, pip, Pango, or anything else on the
host. If you have Docker installed, this is the simplest way to run the app.

### Using docker compose (recommended)

From the project root:

```bash
docker compose up --build
```

Then open <http://localhost:8000>.

What the compose file does:

- Builds the image from the local `Dockerfile`.
- Publishes port `8000` on the host.
- Mounts `./data` into the container so edits to `profile.md` and
  `cv_template.md` are picked up immediately — no rebuild needed.
- Mounts `./output` so generated PDFs land on the host filesystem and survive
  container restarts.
- Sets `CV_PDF_BACKEND=auto` so the nicer WeasyPrint backend is used.

To stop it: `Ctrl-C`, then `docker compose down`.

### Using plain docker

If you prefer not to use compose:

```bash
docker build -t cv-generator .
docker run --rm -p 8000:8000 \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/output:/app/output" \
  cv-generator
```

### Forcing a PDF backend in Docker

Pass the env var to compose or docker run:

```bash
# compose
CV_PDF_BACKEND=xhtml2pdf docker compose up

# docker run
docker run --rm -p 8000:8000 -e CV_PDF_BACKEND=xhtml2pdf \
  -v "$(pwd)/data:/app/data" -v "$(pwd)/output:/app/output" \
  cv-generator
```

### Rebuilding after changing `requirements.txt`

Compose caches the pip layer. After editing `requirements.txt`:

```bash
docker compose build --no-cache
docker compose up
```

### Why the multi-stage Dockerfile?

Some of the transitive dependencies (notably `pycairo`, pulled in via
`xhtml2pdf → svglib → rlPyCairo`) don't ship prebuilt wheels for every
Python / architecture combination — on Apple-Silicon Linux images for
Python 3.12, pip falls back to compiling `pycairo` from source.

The `builder` stage in the `Dockerfile` therefore installs a C toolchain
plus the Cairo / Pango / HarfBuzz development headers, compiles any missing
wheels into `/wheels`, and the `runtime` stage installs those wheels with
`pip install --no-index --find-links /wheels`. Result: the final image
doesn't ship gcc or dev headers, but the build always succeeds regardless
of which wheels PyPI happens to have available for your arch.

If a future dependency breaks the build with something like *"cairo.h: No
such file"* or *"Package not found"*, extend the builder stage's `apt-get`
line with the corresponding `-dev` package.

## Install

```bash
cd cv-generator
python -m venv .venv
source .venv/bin/activate
# pip install -r requirements.txt
python -m pip install -r requirements.txt --index-url https://pypi.org/simple
uvicorn backend.main:app --reload
```

That's it — the app ships with a pure-Python PDF backend (`xhtml2pdf`) so it
runs on any machine without needing system libraries.

### Optional: better-looking PDFs with WeasyPrint

WeasyPrint renders nicer output (better typography, full CSS support) but needs
the Pango/Cairo native libraries. The pip install will pull in the Python
package, but the native libs come from your OS:

**macOS (Homebrew):**

```bash
brew install pango
```

If you're using Anaconda Python on macOS (which doesn't see Homebrew libs by
default) you have two options:

1. **Use a stdlib / Homebrew-friendly Python**. Install pyenv or the python
   from python.org, create the venv with that, and `brew install pango` will
   be picked up automatically.
2. **Or point Anaconda at Homebrew libs**. Before `uvicorn`, export:

   ```bash
   # Intel Macs:
   export DYLD_FALLBACK_LIBRARY_PATH=/usr/local/lib:$DYLD_FALLBACK_LIBRARY_PATH
   # Apple Silicon:
   export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib:$DYLD_FALLBACK_LIBRARY_PATH
   ```

3. **Or install WeasyPrint via conda** (it brings its own native libs):

   ```bash
   conda install -c conda-forge weasyprint
   ```

**Debian/Ubuntu:**

```bash
sudo apt-get install -y libpango-1.0-0 libpangoft2-1.0-0
```

**Don't want to deal with any of that?** Do nothing. The app detects whether
WeasyPrint can load at runtime and silently falls back to `xhtml2pdf`. The
response from `/api/generate` includes a `pdf_backend` field so you can see
which was used, and the frontend shows it too ("Saved as … (rendered with
xhtml2pdf).").

### Force a backend

Set `CV_PDF_BACKEND` to `weasyprint`, `xhtml2pdf`, or `auto` (default):

```bash
CV_PDF_BACKEND=xhtml2pdf uvicorn backend.main:app --reload
```

## Run

```bash
uvicorn backend.main:app --reload
```

Open http://127.0.0.1:8000 in your browser.

## Update your profile

Edit `data/profile.md`. The YAML front matter controls structured sections
(skills, experience, education, …). The Markdown body below is free-form
background — it's also used when matching against job descriptions, so
mention the tech, industries, and methodologies you want to surface there.

## Change the CV layout

Edit `data/cv_template.md`. It's a Jinja2 template; the variables available are
`profile` (your parsed profile), `match` (the match result), `tailored_summary`
(a short per-job summary string), `highlighted_skills` (skill groups reordered
so matched skills come first), and `ordered_experience` (experience in
reverse-chronological order, which is what recruiters expect).

Styling is defined in `backend/cv_builder.py` — there are two stylesheets,
`CV_CSS_WEASYPRINT` and `CV_CSS_XHTML2PDF`, because the two backends support
slightly different CSS. Edit the one that matches the backend you're using.

## LinkedIn URLs

LinkedIn blocks unauthenticated fetches, so the URL field is best-effort — if
it fails, paste the job description text directly.

## Kubernetes Deployment

### Manual STEP: Create a secret which we do not want to be part of the source code
```
kubectl -n cv-generator create secret generic cv-generator-clerk \
  --from-literal=CLERK_PUBLISHABLE_KEY='pk_test_...' \
  --from-literal=CLERK_SECRET_KEY='sk_test_...' 
```
#### ALternatively if .env fils configured with the required keys
```
kubectl -n cv-generator create secret generic cv-generator-clerk --from-env-file=.env
```

### Deployment
```
cd pkgs/k8s
$ ./redeploy.sh
```

## Project layout

```
cv-generator/
├── backend/
│   ├── main.py            # FastAPI app + routes
│   ├── matcher.py         # Keyword matching + scoring
│   ├── cv_builder.py      # MD template -> PDF (WeasyPrint or xhtml2pdf)
│   ├── jd_fetcher.py      # Best-effort URL fetcher
│   ├── assets.py          # QR code + user-icon image generation
│   └── profile_loader.py  # Parse profile.md (YAML + body)
├── frontend/
│   └── index.html         # Single-page UI
├── data/
│   ├── profile.md         # ← edit this
│   └── cv_template.md     # ← CV layout
├── output/                # Generated PDFs land here
├── Dockerfile             # Container image definition
├── docker-compose.yml     # One-shot run with volumes
├── .dockerignore
├── requirements.txt
└── README.md
```
