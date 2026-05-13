# PhDHub

PhDHub is a localized AI workspace for PhD applicants. It connects resume/RP management, email triage, professor tracking, interview preparation, and interview review into one workflow, so you can reduce context switching and follow-up misses.

## UI Preview (Carousel)

![PhDHub UI Carousel](fig/carousel.gif)

## Features

- Resume Management (My Resume)
  - Upload, switch, delete, and preview multiple PDF resumes.
  - AI-generated resume analysis (strengths, weaknesses, improvements) with per-resume caching.

- RP Management (My RP)
  - Upload, switch, delete, and preview multiple RP PDFs.
  - AI-generated RP analysis (good points, weaknesses, improvements).

- Smart Email Center (AI Email)
  - IMAP email fetching, cache loading, and manual force refresh.
  - Automatic classification of PhD-related emails (sent inquiry, positive/neutral/negative reply, interview, etc.).
  - Extract professor profile fields from email + homepage content and sync to the professor database.

- Outreach Dashboard
  - Shows 7-day and all-time metrics (sent, replies, positive/neutral/negative, interview scheduled).
  - Stage-based progress tracking and visualization.

- Professor Database (Professor DB)
  - Unified management for professor/school/department/country/research direction/stage/timestamps.
  - Supports filtering, timezone display, and record maintenance.

- Interview Prep
  - One-click generation of high-frequency interview questions.
  - Personalized interview advice based on resume + professor homepage + paper signals.
  - Mock interview dialogue, follow-up questioning, scoring, and review.
  - High-frequency point bank (question + suggested answer + key points).

- System Config
  - Qwen/Gemini provider switching and auto-saved API keys.
  - Email connection setup (IMAP/SMTP).

## Dependency Installation

> Recommended Python 3.10+ (minimum 3.9).

1. Clone the repository

```bash
git clone <your-repo-url>
cd PhDHub
```

2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
```

3. Install dependencies

```bash
pip install -U pip
pip install -r requirements.txt
```

4. Start the app

```bash
bash run.sh
```

## Start with Docker Compose

If Docker / Docker Compose is installed, you can run PhDHub in a container:

```bash
docker compose up -d --build
```

Then open: http://localhost:8501

Useful commands:

```bash
# Follow logs
docker compose logs -f phdhub

# Stop the service while keeping the data volume
docker compose down

# Use another host port, for example 8502
PHDHUB_PORT=8502 docker compose up -d
```

Application data is persisted in the Docker volume `phdhub_data` (mounted at `/data` in the container). To remove persisted data, run `docker compose down -v`.

## Configuration

After first launch, open `系统配置 / System Config` and fill in:

- Email account, IMAP/SMTP, and app password (recommended)
- AI provider (Qwen or Gemini) and the corresponding API key

---

作者主页: https://d2simon.github.io/
研究方向：计算机视觉

作者也在申请博士中，如果你正在招收 26-27 届博士，期待我们建立联系。如果有任何你觉得实用的功能或者遇到什么 bug，可以在 issue 中提出。

Author Homepage: https://d2simon.github.io/
Research Direction: Computer Vision.

I am also applying for PhD programs. If you are recruiting PhD students for the 2026-2027 intake, I would be glad to connect with you. If you find any useful feature ideas or run into bugs, please open an issue.

希望大家都能拿到心仪的 PhD offer。
Wishing everyone the best in getting their ideal PhD offer.
