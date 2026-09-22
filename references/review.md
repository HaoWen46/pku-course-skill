# Rebuild review

Reviewed the repository at `2b81e15910414cfff4733d23e95eda4be3d1cc83`, including its complete implementation and test suite. The earlier ZIP was different and was not used as the rebuild. The old suite passed on GitHub-hosted Python 3.11 and 3.13 while its live options command failed, demonstrating why mock coverage alone was insufficient.

Live inspection identified PKU's `span[data]` filter controls, which the previous parser did not recognize. A minimized regression fixture covers that observed structure; the complete live form is not embedded or logged.

The rebuilt source adapter, output layer, CLI, and independent tests address filter-menu isolation, misleading CAPTCHA classification, aligned source pagination with bounded continuation, numeric zero values, conflicting detail fields, field-scoped syllabus links, bounded index previews, stateless versioned references, and separate enumeration/detail completion. Help works without third-party dependencies; missing dependencies produce a structured error.

Security checks cover fixed HTTPS endpoints, no redirects, form encoding, a 2 MiB response limit, socket/read-loop deadlines, source error redaction, script/hidden-markup removal, hostile references, output traversal, symlinks, exclusive atomic publication, partial writes, and interruption. Retrieved text remains untrusted data; removing scripts is not a general prompt-injection defense for a consuming agent.

Unit tests use synthetic protocol fixtures. `tests/live.py` independently exercises options, search, get, and a narrowly bounded export against PKU. CI reports each separately. A green unit job is not a claim of live source availability; a failed live job is not silently skipped or converted into success. Consult the exact commit's Actions run for its result.

The filesystem assumptions are an agent-controlled directory, POSIX directory descriptors, and hard-link-capable storage. There is no hostile same-user-process isolation, power-loss durability certification, formal security audit, authenticated Elective verification, attachment extraction, or guarantee of complete weekly syllabi.
