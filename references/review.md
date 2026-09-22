# Review scope

Reviewed the complete source adapter, output layer, CLI, existing 95-test suite, live check, and skill instructions at `27901fdd58b052fceb82d2b7852de57e8d865c81`. The previous ZIP is not the source of this migration. The original 95 tests also passed under an actual clean uv 0.10.0 installation before changes to the test contracts.

## uv migration

One `pyproject.toml` and uv-generated `uv.lock` replace the old dependency list. The lock includes all transitive versions and artifact hashes from PyPI. Runtime commands and CI use `--locked`, not `--frozen`, so inconsistent manifests fail instead of silently changing the lock. The project is not packaged or installed globally; uv manages its local environment. No course-data cache was added.

Fixed misleading missing-module errors: an incomplete skill installation now differs from missing third-party dependencies. The live check now invokes the documented uv launcher from another directory, verifies real pagination and relative output, and uses explicit checks that cannot disappear under Python optimization. It does not treat a launcher failure as a partial course retrieval.

CI separately runs protocol/security tests, real uv launcher tests, a dependency advisory audit, and live PKU retrieval. Launcher cases cover a new environment, a foreign project's invalid metadata, spaces and Chinese characters in the installation path, exact locked versions, help and error exit codes, caller-relative files, and refusal to execute with stale or missing locks. The working tree must remain unchanged after validation. Consult the exact commit's Actions run; test definitions are not evidence of a passing run.

## Retained boundaries

Reviewed fixed HTTPS endpoints, no redirects, bounded responses and stdout, encoded parameters, stateless references, course/term/section identity, outline versus introduction semantics, pagination changes, exclusive file writes, symlink/traversal rejection, and interrupted/partial exports. Existing regression coverage is retained. Source text remains untrusted data; HTML cleanup does not certify prompt-injection resistance in a consuming agent.

This is a targeted review and test suite, not a vulnerability-free guarantee or formal security audit. Advisory checks only cover known dependency reports. POSIX hard-link-capable storage and an agent-controlled output directory are required; hostile same-user processes and power-loss durability are outside the guarantee. Network availability, changing catalogs, authenticated Elective access, attachment extraction, and full weekly-syllabus coverage remain separate limitations.
