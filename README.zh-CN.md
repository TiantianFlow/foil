# 运筹 · Foil

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-informational)](https://github.com/TiantianFlow/foil)
[![CI](https://github.com/TiantianFlow/foil/actions/workflows/ci.yml/badge.svg)](https://github.com/TiantianFlow/foil/actions/workflows/ci.yml)

[English](README.md)

**你的 Agent 们的忠诚反对派。** 运筹在你自己的终端里协调一组互补的本地 CLI Agent 席位：一个席位动手实现，另一个席位从独立上下文提出质疑，由你掌控的主座负责配备人手和下达指令。邮件和状态落在磁盘上的持久文件里，席位跑在 tmux 中。没有托管控制面，没有聊天界面，没有 MCP，也没有需要登录的运筹账号。

这种反对不是角色扮演，而是结构上的独立。运筹中的席位是独立的 CLI 进程，不是共享同一个母模型上下文的 subagent。不同席位可以使用不同的 CLI、模型、人格、上下文和 worktree，让 reviewer 有机会发现同一类 Agent 容易共同复制的相关性盲区。这种多样性并不保证正确，但能让分歧和独立验证真实存在，而不只是同一段对话里的另一种语气。

如果你已经在本机同时跑着不止一个 CLI Agent，这些失败模式你一定不陌生：所有声音揉进同一段对话、tmux 会话被杀后上下文烟消云散、Agent 之间没有可持久交接的载体，以及不断有人劝你换上某个托管界面。运筹给出的答案是：继续留在终端里。

## 为什么选择运筹

- **互补席位，而不是一把混杂的声音。** 每个席位都是独立的 Agent CLI 进程，有自己的角色、上下文和工作目录。
- **可持久化的协同。** 邮件、确认回执和席位状态都是磁盘上带版本的文件。在接收方确认之前，消息一直保持 `queued`——无论当时有没有人盯着屏幕。
- **中断后可恢复。** 杀掉 tmux、重启机器、再回来：`foil resume` 会按固定优先级复活席位——先找活着的 tmux 窗口，再用 CLI 原生的会话记录，最后才是记录在案的全新启动。
- **本地优先，不碰凭据。** 席位以你已经在本地完成认证的 Agent CLI 运行在你自己的机器上。运筹从不索取、保存或打印任何服务商凭据，也没有运筹账号。

## 它如何运转

```mermaid
flowchart LR
  operator[你或控制器 CLI] --> foil[Foil]
  foil --> lead[主座]
  lead --> workers[tmux 中的工人席位]
  foil --> files[持久的邮箱与状态文件]
```

一次典型的协作是这样的：你把运筹指向自己的仓库，拉起一名主座。主座按需配备互补的专家席位——一个实现者负责写代码，一个 reviewer-challenger 从独立上下文向方案发起挑战。主座把任务作为持久的邮箱消息发出去，产出不达标时直接要求返工，最后把经受住质疑的证据整合起来。你可以用普通的 CLI 命令和 tmux 检查一切。运筹是灵活的协同，而不是一条固定的"研究→实现→评审"流水线。

## 快速开始

你需要 Python 3.11+、uv、tmux 3.2+ 和 Git。下面的示例还会使用已经完成本地认证的 `grok` 和 `opencode` CLI；运筹不会经手这些登录。

安装锁定版本的公开发行版：

```sh
uv tool install "git+https://github.com/TiantianFlow/foil.git@v0.1.0"
```

以后升级时，请改成新的发行标签并重新安装：

```sh
uv tool install --reinstall "git+https://github.com/TiantianFlow/foil.git@v0.1.0"
```

然后切换到你想让运筹协调的仓库。`foil init` 接受已有的 Git 仓库，并完整保留所有已跟踪和未跟踪的文件以及 Git 状态；对不是 Git 仓库的非空目录会直接拒绝。（空目录也可以——init 之后运行 `git init -b foil-demo`，给它一个 Git 身份。）

```sh
# 然后在你已有的 Git 仓库中
cd your-repo
foil init .
foil seat spawn --state-dir STATE_ROOT --fleet FLEET_ID --lead --seat lead --cli grok --role manager
foil seat spawn --state-dir STATE_ROOT --fleet FLEET_ID --seat implementer --cli grok --role implementer
foil seat spawn --state-dir STATE_ROOT --fleet FLEET_ID --seat reviewer-challenger --cli opencode --role reviewer-challenger --permission auto
foil send-message --state-dir STATE_ROOT --fleet FLEET_ID --seat reviewer-challenger --sender lead --body "Please challenge the current plan." --wake
```

从 `foil init` 打印的 JSON 里复制 `state_root` 和 `fleet_id`。第一个席位必须是主座，工人席位在其后。`foil send-message` 只负责把邮件落盘——想提醒某个席位，请加上 `--wake`，或先检查 tmux 再运行 `foil seat wake`。

## 运营一支舰队

**一支舰队对应一个 worktree。** 为项目保留一个干净、与远端 `main` 保持 fast-forward 的基准 checkout；为每支舰队创建一个专用的功能 worktree，并在其中运行主座和工人席位。绝不要让舰队改动基准 checkout；如果它变脏了，应当报错并通知，而不是替它收拾。显式选择其他基准也完全可以——这是给你的操作守则，不是运筹强制的功能。

**由主座决定成员。** 每支舰队从一名主座开始。主座（或你）按需从角色库拉起工人席位。工人默认隔离：Foil 会从已提交的 HEAD 克隆出 `worktrees/<seat_id>`，因此未提交的文件不会被带进去，之后的提交也不会自动刷新这些克隆；`foil doctor` 会报告落后情况。需要共享目录时，使用高级选项 `--shared-cwd`。

**诚实的席位状态。** `working` 只表示 tmux 进程还活着——不代表模型正在思考。`foil status` 会把注册表与活着的 tmux 对账；`foil poll-status` 只读带版本的状态文件。

**中断与恢复。** `foil resume` 优先复用匹配的存活 tmux 窗口，其次是席位记录的原生会话，最后是记录在案的全新启动。`foil seat stop` 会保留席位记录，供 `foil resume` 在中断后继续；`foil seat remove` 才会删除记录。拉起席位时 `--permission` 默认为 `supervised`（CLI 会逐个请求批准）；`--permission auto` 才使用适配器声明的自动批准参数。

## 人格、角色与其他 CLI

`foil init` 会在 `.foil/roles/` 下生成角色库（manager、implementer、reviewer-challenger 等）。你也可以直接用本地 Markdown 人格目录来配备席位——人格文件原样使用，不加包装：

```sh
foil catalog-list --path ./personas
foil catalog-map --path ./personas --persona NAME --json
foil seat spawn --state-dir STATE_ROOT --fleet FLEET_ID --seat designer --role-file ./personas/designer.md
```

Grok 和 OpenCode 是内置预设。其他交互式 CLI 通过声明式席位档案接入——一个 TOML 文件掌管可执行文件、启动/恢复/初始化参数、会话捕获、权限标志、工作目录行为，以及按变量名声明的环境转发（值永不落盘）：

先把随仓库发布的 [`profiles/pi-interactive.toml`](profiles/pi-interactive.toml) 示例保存或复制到你要协调的仓库，再为相应席位传入它的路径：

```sh
foil seat spawn --state-dir STATE_ROOT --fleet FLEET_ID --seat researcher --profile ./profiles/pi-interactive.toml --role researcher
```

内置预设与档案是仅有的两条受支持路径——如果某个 CLI 既不是预设、你也没有用档案跑通过，就不要假设它可用。当你传入 `--model` 时，运筹会把你的选择转交给 CLI；CLI 自己的界面才是实际启动了哪个模型的最终依据。

## 安全与限制

- 运筹提供的是同一用户下的协作式保护，不是针对恶意行为的隔离。席位以你的身份、使用你的 CLI 凭据、在你的机器上运行。
- 运筹从不保存服务商凭据，也从不打印环境变量或终端缓冲区内容。
- 没有 MCP 服务器，没有托管服务，也没有任何需要登录的东西。

## 进一步了解

- [docs/walking-skeleton.md](docs/walking-skeleton.md)——可执行的端到端验证路径
- [docs/design/onboarding.md](docs/design/onboarding.md)——初始化与状态根契约
- [docs/design/profiles.md](docs/design/profiles.md)——声明式席位档案契约
- [skills/controller](skills/controller)、[skills/manager](skills/manager)、[skills/worker](skills/worker)——给驱动或加入舰队的 CLI 使用的可移植 skill
