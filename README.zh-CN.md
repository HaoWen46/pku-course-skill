# pku-course-skill

[English](README.md) | 简体中文

按学年学期、课程名称、授课教师或开课院系查询北京大学课程，并将可获取的课程简介和教学大纲保存到本地文件。

**供 Agent 使用：**[SKILL.md](SKILL.md) 定义命令及输出规范。本 README 面向用户介绍功能和用法。

## 功能

这是一个只读的 Agent 技能，也可作为独立命令行工具使用，数据来自北大教务部的公开课程查询。适合查找某位教师的课程、查看课程教学内容，或收集某院系一学期的开课信息。

| 命令 | 用途 |
| --- | --- |
| `options` | 列出可用的学年学期或院系。 |
| `search` | 分页返回课程名称、授课教师和用于获取详情的引用标识。 |
| `get` | 将一个开课班次的基本信息及可获取的教学内容保存为 JSON。 |
| `export` | 将所有匹配的开课记录保存为索引和独立课程文件。 |

保留原始中文课程名和教师文本，不生成摘要、不对课程排序，也不维护课程缓存或数据库。

## 安装

需要 [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.10.0+，支持 Linux、macOS 或 WSL。uv 管理 Python 3.11+ 和锁定的依赖，无需手动激活环境。将完整仓库安装到 Agent 的技能目录，例如：

```sh
mkdir -p .agents/skills
git clone https://github.com/HaoWen46/pku-course-skill.git .agents/skills/pku-course-skill
SKILL_DIR="$(cd .agents/skills/pku-course-skill && pwd -P)"
uv run --locked --project "$SKILL_DIR" "$SKILL_DIR/scripts/pku.py" options
```

克隆私有仓库时使用现有的 GitHub 身份验证。保留 `pyproject.toml`、`uv.lock` 及配套脚本，不要只复制 `SKILL.md`，也不要将此项目纳入上级 uv 工作区的成员列表。课程请求直接访问北大数据源；此技能不接收账号凭据。

## 使用

安装技能后，可以向 Agent 提出这样的请求：

> 查找北大 2026–2027 学年第 1 学期与机器学习有关的课程，并将可获取的教学资料保存到 `./results/pku-ml`。

在终端中使用时，沿用安装步骤设置的 `SKILL_DIR`。将 `TERM` 设置为 `options` 返回的学期值，`DEPT` 设置为 `options --field departments` 返回的完整院系值或名称，`REF` 设置为 `search` 返回的原始引用标识。

```sh
uv run --locked --project "$SKILL_DIR" "$SKILL_DIR/scripts/pku.py" options --field departments
uv run --locked --project "$SKILL_DIR" "$SKILL_DIR/scripts/pku.py" search --term "$TERM" --query "机器学习"
mkdir -p ./results
uv run --locked --project "$SKILL_DIR" "$SKILL_DIR/scripts/pku.py" get "$REF" --out ./results/course.json
uv run --locked --project "$SKILL_DIR" "$SKILL_DIR/scripts/pku.py" export --term "$TERM" --department "$DEPT" --out ./results/pku-courses
```

`2026-2027-1` 表示 2026–2027 学年第 1 学期；只能使用数据源实际列出的学期。添加 `--teacher "NAME"` 可按教师筛选。`options` 和 `search` 每次最多返回 10 条记录，输出不超过 4 KiB；保持筛选条件不变，使用返回的 `next_offset` 继续查询，直到它为 `null`。

## 保存结果

`--out` 接受绝对路径，或相对于当前工作目录的路径，而非相对于技能目录的路径。父目录必须已存在，目标文件或目录必须尚不存在；已有输出、包含符号链接的路径及 `..` 路径跳转均会被拒绝。

```text
results/pku-courses/
├── index.jsonl
├── courses/
│   └── 1.json
└── receipt.json
```

索引只包含 `file`、`name` 和 `teachers`；过长的课程名和教师文本会缩短为预览。课程文件保留完整值、课程号、学期与班次、可获取的教学字段、来源链接及获取状态。`get` 和 `export` 只输出简短的结果回执，不在终端输出课程正文。按需读取索引行和课程文件即可。

检查 `receipt.json` 中的完成状态和错误。存在 `INCOMPLETE` 标记表示导出未成功完成；缺少回执也不能视为成功。

## 范围与限制

仅使用教务部公开数据。课程简介不等于完整教学大纲。`syllabus_status` 中，`full` 表示明确的大纲正文，`intro_only` 表示只有简介，`link_only` 表示只有尚未提取的教学大纲链接，`missing` 表示教学字段为空。获取失败单独报告。`full` 表示已获取明确的教学大纲正文，不保证包含逐周教学计划。尚未实现需要登录的选课系统访问及附件内容提取。

此工具不能选课或退课，院系开课列表也不等于某专业的培养方案。数据源的可访问性和内容可能变化；遇到访问拒绝、验证要求或限流时会停止获取，不会尝试绕过。获取的文本和链接始终作为不可信数据处理。

## 在仓库根目录验证

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked tests/uv_smoke.py
uv run --locked tests/live.py
```

单元测试使用预设的协议测试数据；启动器测试检查 uv 及锁文件约束。独立的在线测试实际访问北大并运行全部四个命令，失败时直接报错，不以模拟数据代替。实现假设和验证限制见[数据源规范](references/sources.md)及[审查范围](references/review.md)。
