# Source contracts

The public source is https://dean.pku.edu.cn/service/web/courseSearch.php. Its course-name input explicitly accepts a name keyword or course code. Filter values must come from the form, not a static list.

Search uses POST https://dean.pku.edu.cn/service/web/courseSearch_do.php with `coursename`, `teachername`, `yearandseme`, `coursetype=0`, `yuanxi`, and `startrow`. Raw terms such as `26-27-1` map to CLI terms such as `2026-2027-1`. Two-digit source years are interpreted in the 2000s, including century rollover; no twentieth-century catalog coverage is claimed. Pages contain at most ten rows and successful responses identify `status=ok`, `count`, and `courselist`.

Offering rows provide `kch`, `kcmc`, `jxbh`, `teacher`, and an optional `zxjhbh` detail identifier. Other available source fields are preserved in detail output. The request/response contract is also documented by the published implementation at https://docs.rs/pku-claspider/latest/src/pku_claspider/dean.rs.html; this repository does not import that implementation or implement its authenticated services.

Detail uses GET https://dean.pku.edu.cn/service/web/courseDetail.php with `flag=1` and the source's `zxjhbh`. An independently readable example is https://dean.pku.edu.cn/service/web/courseDetail.php?flag=1&zxjhbh=BZ2324202035670_12063. It supplies introductions rather than an explicit teaching outline; public availability of one detail does not establish complete syllabus coverage.

The form contains verification UI. A normal form widget is not a failed request; an actual challenge, denial, or sign-in response stops retrieval. No login, CAPTCHA solving, hidden-session extraction, attachment fetching, or alternative endpoint bypass is implemented. Temporary HTTP cookies exist only within one process and are never persisted or logged.

Source markup and catalog contents can change. Conflicting fields, unrecognized content, malformed totals, repeated offerings, and changed pagination totals are errors rather than successful empty results. No snapshot guarantee is possible across independent requests. For installation conventions, see https://developers.openai.com/codex/skills.
