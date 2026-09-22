# Sources and validation

Checked 2026-09-22. Public browser retrieval exposed PKU search filters and a detail example, but direct HTTP execution from the development environment failed DNS resolution. Tests use synthetic HTML and JSON fixtures, not captured live responses. Live search requests, current raw DOM selectors, and end-to-end PKU availability remain unverified; do not describe fixture success as a live crawl.

- Search page: https://dean.pku.edu.cn/service/web/courseSearch.php
- Read-only search operation: POST https://dean.pku.edu.cn/service/web/courseSearch_do.php with `coursename`, `teachername`, `yearandseme`, `coursetype=0`, `yuanxi`, and `startrow`.
- Public detail: GET https://dean.pku.edu.cn/service/web/courseDetail.php with `flag=1` and the source's `zxjhbh`.
- Public detail example: https://dean.pku.edu.cn/service/web/courseDetail.php?flag=1&zxjhbh=BZ2324202035670_12063
- Published implementation documenting the request and response contract: https://docs.rs/pku-claspider/latest/src/pku_claspider/dean.rs.html
- Skill packaging: https://learn.chatgpt.com/docs/build-skills
- HTML parser: https://www.crummy.com/software/BeautifulSoup/bs4/doc/
- HTTP behavior: https://docs.python.org/3/library/urllib.request.html

The search page includes CAPTCHA UI. Endpoint availability is not permission to evade verification. This client recognizes returned challenges and stops; it does not implement login, CAPTCHA solving, or an authenticated browser session. Full authenticated Elective retrieval needs a separately verified, authorized integration and is not claimed here.

The adapter expects source term values like `26-27-1`, a ten-row search page, JSON with `status`, `count`, and `courselist`, and course rows with `kch`, `kcmc`, `jxbh`, `teacher`, and optional `zxjhbh`. Two-digit academic years are interpreted in the 2000s, with century rollover handled; no historical twentieth-century coverage is claimed. Terms must actually appear in the source form.

The options parser supports ordinary select options and labeled custom dropdowns using value/data-value/data-val/rel attributes. Unsupported DOM shapes fail explicitly. Detail parsing uses recognized labels and verifies the requested course code. It preserves introduction/outline distinctions, rejects wrong-course pages, and retains unknown outline dates as null. It does not expand curricula, infer instructor lists, fetch attachments, or claim an exhaustive teaching plan.

## Checks

Run `python -m unittest discover -s tests -v`. Tests cover all commands with fake transport, term mapping, stateless references, section identity, source schema errors, repeated/changed pagination, bounded stdout and responses, access failures, outline classification, file collisions, symlink parents, path traversal, atomic writes, and partial export reporting.

Requests use fixed HTTPS endpoints, normal TLS verification, no redirects, a 2 MiB response cap, socket timeout, and read deadline. Query values are form-encoded. Source errors are never echoed as executable instructions. Course JSON is published through an exclusive atomic hard link from a temporary file; exports retain an incomplete marker until successful finalization. Temporary files are not a cache. There is no password, cookie, shell-execution, or HTML-rendering feature.

This is a targeted code review and regression suite, not a security certification. A live catalog can change between requests even when totals stay constant; no snapshot guarantee is made. Run in an agent-controlled output directory. Hard-link-capable POSIX storage is required. A hostile process with the same operating-system permissions is outside the filesystem isolation guarantee.
