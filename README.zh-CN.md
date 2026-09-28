# 运筹 · Foil

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.2.0-informational)](https://github.com/TiantianFlow/foil)
[![CI](https://github.com/TiantianFlow/foil/actions/workflows/ci.yml/badge.svg)](https://github.com/TiantianFlow/foil/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS-lightgrey)](pyproject.toml)
[![Runtime dependencies](https://img.shields.io/badge/runtime%20dependencies-none-brightgreen)](pyproject.toml)

[English](README.md)

**你的 Agent 们的忠诚反对派。**

运筹把你已经在用的编程 Agent（Claude Code、Codex、Gemini、OpenCode、Grok）组成一支小团队。你只和一个 Agent 对话，它把目标交给主座。主座规划工作，把每个任务交给有自己 Git 分支的工人席位，让另一个 Agent 检查结果，再把结果合并。每个 Agent 都跑在 tmux 里，每条消息都是你能直接读的文件。

## 为什么选择运筹

### 不必再亲手管理你的 Agent

行之有效的做法是分工：一个 Agent 规划，一个实现，一个审查。Claude Code 甚至自带 `opusplan` 设置，用 Opus 规划、用 Sonnet 动手。但跨工具时，这种分工通常意味着由你来打杂：把计划复制到另一个终端，再把结果转述回来，还要提醒每个 Agent 之前定过什么。

运筹把这种分工变成结构。主座负责规划，按角色派出工人席位，用邮件下发任务，再合并它们的分支。你只和一个操作员 Agent 对话，管理的活由主座来做。

### 每项工作都用最合适的模型，不限厂商

模型各有长短：有的更会推理，有的写代码更快，有的更便宜，有的这周还剩额度。在运筹里，每个角色是一个模板，写明用哪个 harness 和哪个模型。主座可以用擅长推理的模型做规划，实现者可以用快速的模型，审查者可以来自另一家厂商。

最后这一点就是"忠诚反对派"。模型审查自己写的代码，往往会沿用自己的假设；另一家厂商的审查者盲区不同，能抓到不同的错误。每个席位还各自使用自己 CLI 的登录，工作因此分摊到你已经付费的多个订阅上。运筹从不保存你的密钥或登录信息。

### 每个上下文都保持精简，状态落在磁盘上

一次长会话会把一切都装进去：计划、读过的每个文件、走过的每条死路。上下文越满，质量越差，压缩还会丢细节。运筹的每个席位只看到自己的角色和任务。任务、结果和状态都是磁盘上的文件，经得住上下文压缩、tmux 会话被杀或重启。`foil seat resume` 会把死掉的席位拉起来。

| | 一次 Agent 会话 | 运筹 |
|---|---|---|
| 谁来管理工作 | 你，在几个终端之间来回 | 主座 |
| 模型 | 一个模型，一家厂商 | 每个角色各选 harness 和模型 |
| 审查 | 作者自己检查自己 | 独立的席位，也可以来自另一家厂商 |
| 上下文 | 一段越来越长的对话 | 每个席位一个聚焦的上下文 |
| 崩溃之后 | CLI 存下了什么就剩什么 | 磁盘上的邮件、状态和分支；`foil seat resume` |
| 你能看到什么 | 一个对话窗口 | 你的操作员 Agent，加上用 `foil seat peek` 看任意席位 |

## 它如何运转

```mermaid
flowchart TB
  you(["你"])
  operator["<b>操作员 Agent</b><br/>你与之对话的 harness<br/>例如 Claude Code 或 Codex"]
  foil[["<b>foil</b><br/>命令行工具"]]

  subgraph fleet["tmux 会话 · 无界面的 Agent，各自用你选的 harness 和模型"]
    lead["<b>主座</b><br/>规划 · 分派 · 合并"]
    impl["<b>实现者</b><br/>写代码"]
    rev["<b>审查者</b><br/>检查工作"]
  end

  subgraph disk["磁盘上"]
    board[("<b>.foil/board</b><br/>邮件 · status.md")]
    tree[("<b>Git worktree</b><br/>分支 foil/implementer-1")]
  end

  you <-->|对话| operator
  operator -->|"foil init · seat spawn lead<br/>send · seat peek"| foil
  lead -->|"seat spawn · seat kill · send"| foil
  foil -->|"启动席位 · 写邮件 · 打一行提醒"| fleet
  fleet <-->|读写| disk
  impl -->|提交| tree
  lead -->|合并| tree
  operator -.->|读 status.md| board

  classDef human fill:#fde68a,stroke:#b45309,color:#1f2937
  classDef yours fill:#bfdbfe,stroke:#1d4ed8,color:#1f2937
  classDef tool fill:#e5e7eb,stroke:#374151,color:#1f2937
  classDef seat fill:#bbf7d0,stroke:#15803d,color:#1f2937
  classDef data fill:#fbcfe8,stroke:#be185d,color:#1f2937
  class you human
  class operator yours
  class foil tool
  class lead,impl,rev seat
  class board,tree data
```

- **你**只和操作员 Agent 对话。
- **操作员 Agent**：你喜欢的任何 harness，带着它原本的界面。它按操作员技能行事，自己从不做项目本身的工作。
- **foil**：就是这个命令行工具。它在 tmux 里启动席位、写邮件，并往收件人的窗口里打一行提醒。它不跑守护进程，也从不解读席位屏幕上显示的内容。
- **主座、实现者、审查者**：在 tmux 窗口里无界面运行的 Agent CLI，每个都带着自己角色的指令启动。默认只有实现者拥有自己的 Git worktree 和分支。
- **.foil/board**：邮件、笔记和 `status.md`，都是席位读写的普通文件。

## 演示

[docs/demo.zh-CN.md](docs/demo.zh-CN.md) 完整走一遍真实运行：一个失败的测试、一个主座、一个实现者和一个审查者，从 `foil init` 一直到修复被合并。

## 快速开始

你需要 Python 3.11+、Git、tmux 3.2+，以及已经在本机登录的 Claude CLI。运筹不经手这次登录。

安装运筹 0.2.0：

```sh
uv tool install "git+https://github.com/TiantianFlow/foil.git"
```

在一个 Git 仓库里：

```sh
cd your-repo
foil init .
```

`foil init` 会写好 `.foil`，包括模板和 `.foil/skills/operator.md`。它把 `harness` 设成 `grok`、`claude`、`codex`、`opencode`、`gemini` 里本机第一个已安装的 CLI。这篇说明用 Claude。如果 init 选了别的 harness，把 `.foil/templates/lead.toml`、`.foil/templates/implementer.toml` 和 `.foil/templates/reviewer.toml` 里的值改成 `harness = "claude"`。保留 `permission = "ask"`。

把 `.foil/skills/operator.md` 加载到你的 harness，并按这个技能去做。把目标告诉这个 harness。技能会启动舰队、查看进展、转达你说的话，并在结束时拆掉舰队。它只启动一次主座。如果主座已经在运行，它用 `foil send` 把目标送出去，而不是再启动一次主座。

同一组命令，如果你自己来跑：

```sh
foil seat spawn lead --task "Summarize this repository in board/status.md"
foil seat list
foil seat peek lead
```

主座自己的报告在 `.foil/board/status.md`。要把一个回答交给主座：

```sh
foil send lead "the answer"
```

结束时：

```sh
foil seat kill --all
```

这会停掉每个席位。它不删除分支，也不删除 worktree。

## 入门

这是从安装到主座开始工作的路径。

1. 安装运筹。你需要 Python 3.11+、Git、tmux，以及至少一个已经登录的 harness CLI。运筹不经手这次登录。

```sh
uv tool install "git+https://github.com/TiantianFlow/foil.git"
```

2. 在仓库里运行 `foil init`。它会写好 `.foil/`，并选一个已安装的 harness。输出的末尾是指针那一行，以及主座模板的 permission。

```sh
cd your-repo
foil init
```

3. 在仓库里的 harness 中粘贴下面这一行。把 `<goal>` 换成目标。这不需要在 harness 里安装任何东西。

```text
Read .foil/skills/operator.md and follow it. My goal: <goal>.
```

持久安装是可选的。把技能复制到该 harness 发现技能的位置，放在用户级，这样它不会出现在 `git status` 里。下面的路径都没有对照该 harness 的当前文档核对过。

| Harness | 安装路径 | 已核对 |
|---|---|---|
| 任意 | 上面的指针行 | 必需；不是按 harness 安装 |
| Claude Code | `~/.claude/skills/foil-operator/SKILL.md` | 否 |
| grok、codex、opencode、gemini | 没有公布的路径 | 否 |

4. 在启动主座之前，给舰队会用到的每个模板设置 `permission`：`.foil/templates/lead.toml`、`.foil/templates/implementer.toml` 和 `.foil/templates/reviewer.toml`。每个模板有自己的 permission。默认是 `permission = "ask"`：该席位会停在第一次批准提示，并在那个窗格里等待。只把主座改成 `auto` 不会让工人席位无人值守。要让哪个席位无人值守，就把它的模板设成 `auto`。用 `ask` 时，查看新席位的窗格里有没有批准提示。

5. 主座的第一次提示已经带上说明：角色技能、persona、命令和看板约定。它告诉主座，每次被唤醒都要重读自己的说明文件。`--task` 里只放目标。

6. 确认主座在工作。只启动一次主座，让它写下包含 `state: done` 的 `board/status.md`，然后等几分钟。如果快速开始已经启动了主座，就跳过这次启动。

```sh
foil seat spawn lead --task "Write board/status.md with state: done"
```

如果 `.foil/board/status.md` 没有出现，`foil seat peek lead` 会显示登录提示、批准提示或一条错误。当文件里出现 `state: done` 时，把人的目标发给这个主座。不要再启动一次主座。

```sh
foil send lead "the goal"
```

## 限制

运筹保护的是遵守说明的席位，不是把恶意进程隔离开。席位身份来自运筹启动它时设置的环境变量 `FOIL_SEAT_ID`。没有哪个参数能让一个席位自称是另一个席位。在这台机器上你能做的事，以你的身份运行的进程也能做。

模板默认是 `permission = "ask"`，harness 在行动前会询问。把 `permission` 设成 `auto` 会插入该预设的自动批准参数。对 Claude，这些参数是 `--permission-mode` 和 `auto`，harness 可以不经询问就改文件、跑命令。那个席位仍然是你。

运筹从不解读窗格。`foil seat peek` 打印 tmux 原样捕获的文本。`foil seat list` 报告 `alive`、`dead` 或 `killed`，依据是保存的窗口还在不在，而不是屏幕上的文字意味着什么。Agent 是否卡住、是否做完，由它们自己写在看板文件里。

即使窗格不在输入提示符，nudge 也会被打进去。`foil send` 先把邮件写成文件，再往接收方窗口打一行：发送者、一个空格、邮件文件的绝对路径，然后是 Enter。消息正文从不被打进窗格。如果 Agent 并不在等输入，这些按键仍然会进入窗格。运筹不会先看窗格再决定打不打。

## 进一步了解

- [docs/demo.zh-CN.md](docs/demo.zh-CN.md) — 一次真实运行，从 `foil init` 到修复被合并
- [docs/architecture.md](docs/architecture.md) — 组件、数据流和模块边界
- [skills/operator.md](skills/operator.md)、[skills/lead.md](skills/lead.md)、[skills/worker.md](skills/worker.md) — 每个角色运行哪些命令
- [CONTRIBUTING.md](CONTRIBUTING.md) — 搭建与检查
- [CHANGELOG.md](CHANGELOG.md) — 版本历史
