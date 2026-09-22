# pku-course-skill

Self-contained PKU course retrieval skill. Agent instructions and command contracts: [SKILL.md](SKILL.md).

## Install

Clone the complete repository into the agent's skills directory. For a repository-local Codex installation:

```sh
mkdir -p .agents/skills
git clone https://github.com/HaoWen46/pku-course-skill.git .agents/skills/pku-course-skill
python -m pip install -r .agents/skills/pku-course-skill/requirements.txt
```

The private repository requires your existing GitHub authentication. Python 3.11+ and POSIX file output are required. Invoke scripts by their installed path from any working directory.

## Verify

```sh
python -m unittest discover -s tests -v
python tests/live.py
```

Unit tests use independent protocol fixtures. The separate live smoke test contacts PKU, exercises all four commands, and fails on blocked access or missing expected data; it never substitutes fixtures. See [source contracts](references/sources.md) and [review scope](references/review.md).
