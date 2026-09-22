# pku-course-skill

English | [简体中文](README.zh-CN.md)

Search Peking University (北京大学 / 北大) courses by academic term, course name, instructor, or department, and save available descriptions and teaching outlines to local files.

**For agents:** [SKILL.md](SKILL.md) defines the commands and output contract. This README is the user overview.

## What it does

A read-only agent skill with a standalone CLI, using PKU's public Dean course catalog. Useful for finding an instructor's classes, checking a course's teaching content, or collecting a department's semester offerings.

| Command | Purpose |
| --- | --- |
| `options` | List available academic terms or departments. |
| `search` | Return a small page of course names, instructors, and retrieval references. |
| `get` | Save one offering's metadata and available teaching content as JSON. |
| `export` | Save all matching offerings to an index and separate course files. |

Original Chinese names and instructor text are preserved. The tool does not generate summaries, rank courses, or maintain a course cache or database.

## Install

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.10.0+ and Linux, macOS, or WSL. uv manages Python 3.11+ and the locked dependencies; no manual environment activation. Install the complete repository in your agent's skills directory, for example:

```sh
mkdir -p .agents/skills
git clone https://github.com/HaoWen46/pku-course-skill.git .agents/skills/pku-course-skill
SKILL_DIR="$(cd .agents/skills/pku-course-skill && pwd -P)"
uv run --locked --project "$SKILL_DIR" "$SKILL_DIR/scripts/pku.py" options
```

Use your existing GitHub authentication to clone a private repository. Keep `pyproject.toml`, `uv.lock`, and the bundled scripts together; do not copy only `SKILL.md` or add this project to a parent uv workspace's members. Course requests go directly to PKU; the skill accepts no account credentials.

## Use

With the skill installed, ask your agent something like:

> Find PKU machine-learning courses for academic year 2026–2027, semester 1, and save their available teaching information under `./results/pku-ml`.

For terminal use, keep `SKILL_DIR` from installation. Set `TERM` to a value returned by `options`, `DEPT` to an exact value or label from `options --field departments`, and `REF` to an unchanged reference returned by `search`.

```sh
uv run --locked --project "$SKILL_DIR" "$SKILL_DIR/scripts/pku.py" options --field departments
uv run --locked --project "$SKILL_DIR" "$SKILL_DIR/scripts/pku.py" search --term "$TERM" --query "机器学习"
mkdir -p ./results
uv run --locked --project "$SKILL_DIR" "$SKILL_DIR/scripts/pku.py" get "$REF" --out ./results/course.json
uv run --locked --project "$SKILL_DIR" "$SKILL_DIR/scripts/pku.py" export --term "$TERM" --department "$DEPT" --out ./results/pku-courses
```

`2026-2027-1` denotes academic year 2026–2027, semester 1; use only terms actually listed by the source. Add `--teacher "NAME"` to filter instructors. `options` and `search` return at most 10 items within 4 KiB; continue with the returned `next_offset` and unchanged filters until it is `null`.

## Saved results

`--out` accepts an absolute path or a path relative to your current working directory, not the skill directory. Parents must exist and destinations must be new; existing outputs, symlinked paths, and `..` traversal are rejected.

```text
results/pku-courses/
├── index.jsonl
├── courses/
│   └── 1.json
└── receipt.json
```

The index contains only `file`, `name`, and `teachers`; long names and instructor text are shortened previews. Course files retain full values, course codes, term and section, available teaching fields, source URLs, and retrieval status. `get` and `export` print only small receipts, not the course content. Read selected index rows and course files as needed.

Check `receipt.json` for completion and errors. An `INCOMPLETE` marker means the export did not finish successfully; a missing receipt is not proof of success.

## Scope and limitations

Public Dean data only. A course introduction is not a full syllabus. `syllabus_status` reports an explicit outline (`full`), an introduction (`intro_only`), an unextracted outline link (`link_only`), or empty teaching fields (`missing`). Retrieval failures are reported separately. `full` means explicit outline text was retrieved, not that a weekly plan is available. Authenticated Elective access and attachment extraction are not implemented.

This tool cannot enroll in or drop courses, and department offerings do not establish a major's requirements. Source availability and contents can change; access denials, verification challenges, and rate limits stop retrieval rather than trigger a bypass. Retrieved text and links remain untrusted data.

## Verify from the repository root

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked tests/uv_smoke.py
uv run --locked tests/live.py
```

Unit tests use protocol fixtures; launcher tests exercise uv and lock enforcement. The separate live test runs all four commands against PKU and fails rather than substituting fixtures. See [source contracts](references/sources.md) and [review scope](references/review.md) for implementation assumptions and verification limits.
