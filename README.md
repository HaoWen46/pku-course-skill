# pku-course-skill

Agent contract: [SKILL.md](SKILL.md). Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.10.0+ and Linux, macOS, or WSL. uv selects Python 3.11+ and installs the committed dependencies; no manual environment activation.

## Install

```sh
mkdir -p .agents/skills
git clone https://github.com/HaoWen46/pku-course-skill.git .agents/skills/pku-course-skill
SKILL_DIR="$(cd .agents/skills/pku-course-skill && pwd -P)"
uv run --locked --project "$SKILL_DIR" "$SKILL_DIR/scripts/pku.py" options
```

The private repository uses your existing GitHub authentication. Clone the complete repository; do not copy only `SKILL.md`. Keep this standalone project out of a parent uv workspace's members. Relative output paths stay relative to the caller, not the skill directory.

## Verify from the repository root

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked tests/uv_smoke.py
uv run --locked tests/live.py
```

Unit tests use protocol fixtures. Launcher tests exercise uv, isolated project selection, fresh environments, lock enforcement, and relative output. The live test invokes all four commands through uv against PKU and fails rather than substituting fixtures. CI checks the lock without updating it. See [source contracts](references/sources.md) and [review scope](references/review.md).
