# ReelForge backend

ReelForge is an AI studio for solo creators: turn an idea into a vertical Reel of up to 90 seconds (script, voiceover, visuals, captions, music) and post it to Instagram from one place.

This repository holds the backend and all project documents. The web app is in [reelforge-frontend](https://github.com/praveenmarch22/reelforge-frontend).

**Where the project stands:** [PROJECT_STATUS.md](PROJECT_STATUS.md). Milestones: [MILESTONES.md](MILESTONES.md).

## What is here today

The backend API has not been built yet. What exists is a working **generation pipeline prototype**: a command-line tool that turns a topic into a rendered MP4.

```
├── pipeline/       Python: topic → script → voiceover → visuals → music → timeline
├── renderer/       Remotion: timeline → vertical MP4 with captions and mixed audio
├── docs/           PRD, design documents, database schema, UI notes
├── MILESTONES.md
├── PROJECT_STATUS.md
└── PROTOTYPE.md    how to set up and run the prototype
```

See [PROTOTYPE.md](PROTOTYPE.md) to run it. It works offline with placeholder providers, and with free-tier API keys for real output.

## Documents

| Document | What it covers |
|---|---|
| [docs/PRD.md](docs/PRD.md) | What the product does, user flows, edge cases |
| [docs/TECHNICAL_DESIGN.md](docs/TECHNICAL_DESIGN.md) | Architecture, tech stack, jobs, integrations, AWS |
| [docs/API_DESIGN.md](docs/API_DESIGN.md) | All 70 endpoints |
| [docs/DB_DESIGN.md](docs/DB_DESIGN.md), [docs/schema.sql](docs/schema.sql) | 16 tables, tested on PostgreSQL 16 |
| [docs/PIPELINE_STRATEGY.md](docs/PIPELINE_STRATEGY.md) | How a Reel is generated |
| [docs/STITCH_PROMPTS.md](docs/STITCH_PROMPTS.md), [docs/UI_REVIEW.md](docs/UI_REVIEW.md) | The designed screens and review notes |
