# pku-course-skill

[English](README.md) | 简体中文

面向 Agent 的使用规范见 [SKILL.md](SKILL.md)。需要 [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.10.0+，支持 Linux、macOS 或 WSL。uv 选择 Python 3.11+ 并安装仓库中锁定的依赖，无需手动激活环境。

## 安装

```sh
mkdir -p .agents/skills
git clone https://github.com/HaoWen46/pku-course-skill.git .agents/skills/pku-course-skill
SKILL_DIR="$(cd .agents/skills/pku-course-skill && pwd -P)"
uv run --locked --project "$SKILL_DIR" "$SKILL_DIR/scripts/pku.py" options
```

私有仓库使用你现有的 GitHub 身份验证。请克隆完整仓库，不要只复制 `SKILL.md`。不要将此独立项目纳入上级 uv 工作区的成员列表。相对输出路径以调用时的工作目录为基准，而非 skill 目录。

## 在仓库根目录验证

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked tests/uv_smoke.py
uv run --locked tests/live.py
```

单元测试使用预设的协议测试数据。启动器测试实际运行 uv，检查项目环境隔离、全新环境、锁文件约束和相对输出路径。在线测试通过 uv 调用全部四个命令，实际访问北大数据源；失败时直接报错，不以模拟数据代替。CI 检查锁文件，不会更新它。详情见[数据源规范](references/sources.md)和[审查范围](references/review.md)。
