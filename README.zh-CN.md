# 运筹 · Foil

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.6.1-informational)](https://github.com/TiantianFlow/foil)
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

运筹把这种分工变成结构。主座负责规划，按角色派出工人席位，再合并它们的分支。任务文件 `board/tasks/<id>.md` 是任务说明，邮件是指向它的指针。`foil seat spawn --task` 写成那封邮件，并不写任务文件。你只和一个操作员 Agent 对话，管理的活由主座来做。

### 一个不轻信"做完了"的干净上下文检查

一个干了一小时的 Agent，会带着一路上相信过的一切来给自己的工作打分。它会跳过自己忘掉的那一步，相信自己写的测试，在没做完的时候说"做完了"。会话越长越糟：上下文越塞越满，质量下降，压缩还会丢掉细节。

在运筹里，每个席位都从干净的上下文开始，只看到自己的角色和任务。审查者从没看过实现者的思路，只看结果，所以它检查的是工作本身，而不是那套说法；主座在报告目标完成之前，会先请审查者检查。小而干净的上下文还让工作能持续更久、保持正确：每个席位只做一件事，计划、邮件和状态都在磁盘上，而不是在谁的记忆里。这就是"忠诚反对派"，也是 Foil 这个名字的由来：foil 指的是那个用反差照出另一个角色疏漏的角色。

### 每项工作都用最合适的模型，不限厂商

模型各有长短：有的更会推理，有的写代码更快，有的更便宜，有的这周还剩额度。在运筹里，每个角色是一个模板，里面是一份有序的 harness 列表，每一项是一个 id 和可选的模型。该角色每启动一次就取下一项，所以换掉的主座也会轮到下一项。主座可以用擅长推理的模型做规划，实现者可以用快速的模型，审查者可以来自另一家厂商，这又多了一层独立性：训练不同，盲区也不同。每个席位各自使用自己 CLI 的登录，工作因此分摊到你已经付费的多个订阅上。运筹从不保存你的密钥或登录信息。

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
    board[("<b>项目目录/board</b><br/>邮件 · 房间 · status.md")]
    tree[("<b>Git worktree</b><br/>分支 foil/implementer-1")]
  end

  you <-->|对话| operator
  operator -->|"foil init · seat spawn lead<br/>send · seat peek"| foil
  lead -->|"seat spawn · seat kill · send"| foil
  foil -->|"启动席位 · 给一个席位发邮件<br/>或写房间文件，提醒其他每个仍在运行的席位"| fleet
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
- **foil**：就是这个命令行工具。它在 tmux 里启动席位。`foil send` 写成一份邮件文件，并提醒那个收件人。`foil post` 写成一份房间文件，并提醒其他每个仍在运行的席位；回复只提醒在该帖子串里发过帖的席位，以及回复里用 `@名字` 点到的席位。主座可以用 `foil send user` 回复你，你用 `foil mail list` 读取。正文不会被打进窗口。两者都等待 200ms，然后发送一次 Enter。它自己不跑守护进程，也从不解读席位屏幕上显示的内容。`foil view` 是一个可选的只读网页，在浏览器里看。
- **主座、实现者、审查者**：在 tmux 窗口里无界面运行的 Agent CLI，每个都带着自己角色的指令启动。默认只有实现者拥有自己的 Git worktree 和分支。
- **看板**：邮件、房间线程、笔记和 `status.md`，都是普通文件，放在 `foil init` 报告打印的项目目录里。这个目录在仓库之外。席位用 `foil mail` 读邮件，用 `foil board` 读板上的其他文件，包括房间线程。

## 尽量不碍事的设计

- **由你自己的 Agent 来管理舰队。** 你不需要操作仪表盘，也不用亲手把 Agent 连在一起。你只要告诉自己常用的那个 Agent 想做什么，用哪个界面都行，只要它能在你的仓库里执行命令。它按一份技能文件行事：启动主座、查看进展、转达问题。
- **每个 CLI 都按原样使用。** 运筹用每个 CLI 自己公开的命令行参数启动它，并用你会用的方式和它交流：往它的窗口里打一行短短的提醒，消息本身放在文件里。它从不读屏幕去猜 Agent 在做什么，所以 CLI 升级很少会让它失效；接入新的 CLI 只需要一个小小的预设文件，不用写代码。
- **有 tmux 的地方就能跑。** 一个命令行工具，自己没有守护进程，另有一个可选的只读网页，可以在 Linux 或 macOS 上运行，本机或通过 SSH 连到远程机器都行。关掉终端，舰队照样工作；用 tmux 重新连上即可。
- **没有暗箱。** 邮件、任务、状态和经验都是普通文件，工作成果落在普通的 Git 分支上。你可以直接阅读、搜索或修改其中任何一项。它们经得住 tmux 会话被杀或机器重启，`foil seat resume` 会把席位重新拉起来。

## 演示

[docs/demo.zh-CN.md](docs/demo.zh-CN.md) 完整走一遍真实运行：一个失败的测试、一个主座、一个实现者和一个审查者，从 `foil init` 一直到修复被合并。

如果运筹帮你省了时间，在 GitHub 上[点一个 Star](https://github.com/TiantianFlow/foil) 能帮更多人找到它。我们只请求这一点。完成目标后，你的智能体可能会提几次，之后很少再提；你说不用，它就不再提。

## 开始使用

你需要 Python 3.11+、Git、tmux 3.2+，以及至少一个已在本机登录、受支持的 Agent CLI（Claude Code、Codex、Gemini、OpenCode 或 Grok）。运筹从不经手这次登录。信任按每个 harness、每条路径生效。席位的工作树在项目目录里、仓库之外，所以不会沿用仓库的信任。每个席位标签对每个 harness 都要信任一次。停在这些提示上的席位，从外面看就像一个正在工作的席位。操作员回答的是审批对话框：文件夹信任提示、登录提示，或一个停住席位、直到你或操作员批准的提示。更新对话框、首次运行对话框和意见征集由它报告给你，留给你来回答。全局预设在 `init` 之前就算数。其他 CLI 用 `<root>/harnesses.toml` 或 `<root>/harnesses/<id>.toml` 里的一个小预设文件接入。

人要做的只有一句话。在仓库里，把它交给你已经在用的编程 Agent：

```text
Install Foil from https://github.com/TiantianFlow/foil, onboard this repository with it, and start a fleet. My goal: <goal>.
```

要从 0.4 升到 0.5.4，在两个目标之间做，不要恢复 0.4 启动的席位。`foil init` 会在 `fleets/main/` 里还没有对应目录、或目录是空的时候，把项目根上的 `board/` 和 `run/` 移进去。它不读取 `templates/<role>.toml`。`init` 把每个还没有的默认角色写进 `<root>/roles.toml`，并在 `<root>/mapping.toml` 里为每个还没有的角色写一行映射。它不写项目里的 `roles.toml`、`mapping.toml` 或 `harnesses.toml`，并且删掉这些路径。新写的清单是 `permission = "auto"`，并且不写 `model`。`memory/` 里的教训和席位工作树都留着。`FOIL_FLEET` 默认是 `main`。要在两个目标之间更新，顺序是先停掉、再升级、最后 `foil init`：目标完成后运行 `foil seat kill --all`，再运行一次安装命令，然后在每个项目里运行 `foil init`，再启动主座。还留着 0.3.x 仓库内 `.foil/` 的项目，要在运行安装命令之前用那个旧的 `foil` 停掉。tmux 窗口已经不在的过期席位同样算在内。如果 `PATH` 上已经没有那个旧的 `foil`，就在旁边安装 0.3.4，这不会替换当前的 `foil`：`UV_TOOL_DIR=DIR UV_TOOL_BIN_DIR=DIR/bin uv tool install "git+https://github.com/TiantianFlow/foil.git@v0.3.4"`，然后运行 `DIR/bin/foil seat kill --all`。用 pipx 则是：`PIPX_HOME=DIR PIPX_BIN_DIR=DIR/bin pipx install "git+https://github.com/TiantianFlow/foil.git@v0.3.4"`，然后运行 `DIR/bin/foil seat kill --all`。那些席位还在时不要运行 `foil init`，也不要把那个目录挪走。上一个目标留在报告打印的项目目录里的 `board/status.md` 已经写着 `state: done`，所以启动前先记下它的 `updated` 行，等它变化。`foil seat kill --all` 之后两种情况都可能出现，所以要检查：运行 `tmux ls`。如果列出了任何会话，预设里点名的 `env` 变量来自该服务器，启动前用 `tmux set-environment -g NAME VALUE` 设置。如果它说没有服务器在运行，下一次启动会从你的 shell 拉起服务器。

- Agent 安装运筹并运行 `foil init`。如果命令打印 `foil: no harness installed`，它什么都没写。先安装上面的某个 CLI，再运行一次 `foil init`。
- 它读报告，运行 `foil roster list`，并请你在本仓库里把每个 harness id 运行一次，接受它的文件夹信任、更新、首次运行和意见征集对话框，或者请你确认已经完成。它用 `foil roster` 设置每个模板的 harness 列表和 `permission`。共用清单是 `<root>/harnesses.toml`、`<root>/roles.toml` 和 `<root>/mapping.toml`。`init` 不写项目里的 `roles.toml`、`mapping.toml` 或 `harnesses.toml`，并且删掉这些路径。`init` 为每个角色写入一条映射，并且 `permission = "auto"`。每个舰队共用这三份清单。`FOIL_FLEET` 选择舰队，默认是 `main`。`roster update ROLE harness=ID/MODEL,ID/MODEL` 一次设置角色的整个列表，`model` 只改只有一项的列表。在舰队里，两者都改写该舰队的 `overrides.toml`，下一次启动会带上它。角色每启动一次就取下一项，列表改过之后从第一项重新开始。只把主座改成 `auto` 不会让工人席位无人值守。限制一节说明 `auto` 仍可能让席位停在审批提示上。
- 它做首次运行检查，然后把目标发出去。
- 它自己从不做项目本身的工作。

没有拿到那句话就启动的 Agent，改用 `init` 打印的指针。当 `<root>/skills/operator.md` 存在时，那一行是：

```text
Read <root>/skills/operator.md and follow it. My goal: <goal>.
```

否则指针是打包的 `operator.md`。

持久安装操作员技能是可选的。把它复制到该 harness 发现技能的位置，放在用户级，这样它不会出现在 `git status` 里。下面的路径都没有对照该 harness 的当前文档核对过。

| Harness | 安装路径 | 已核对 |
|---|---|---|
| 任意 | 上面的指针行 | 必需；不是按 harness 安装 |
| Claude Code | `~/.claude/skills/foil-operator/SKILL.md` | 否 |
| grok、codex、opencode、gemini | 没有公布的路径 | 否 |

### 手动操作

同样的步骤，自己来敲。先安装运筹，再在仓库里：

```sh
uv tool install --upgrade foil-ai
cd your-repo
foil init
```

如果安装时提示 `Executable already exists: foil`，说明包改名为 `foil-ai` 之前的旧安装还在。先运行 `uv tool uninstall foil-orchestrator`，再运行一次安装命令。

读报告，用 `foil roster` 设置那三个模板。如果项目目录里已经有 `board/status.md`，先记下它的 `updated`。然后只启动一次主座：

```sh
foil seat spawn lead --task "Write board/status.md with state: done"
```

当项目目录里的 `board/status.md` 出现 `state: done`，把目标发给这个主座。如果这个文件本来就在，只有 `updated` 变了才算通过。不要再启动一次主座。主座的第一条提示已经带上了它的指令，所以消息里只放目标或回答。

```sh
foil send lead "the goal"
foil seat list
foil seat peek lead
```

项目目录里的 `board/status.md` 是主座自己的报告，也会列出要问你的问题；用 `foil send lead "..."` 回答。它带有一份清单，写明已经完成的和还剩下的，操作员在每次查看时、以及你问起时，报告这份清单。结束时，`foil seat kill --all` 会停掉每个席位，分支和 worktree 都会保留。被停掉的席位名字会一直保留，直到 `foil seat archive NAME`。这条命令释放名字，不删除 worktree、分支、邮箱或未提交的工作。`foil seat list --archived` 显示保存的 worktree 路径。留下的席位工作树在 `<项目目录>/worktrees/<label>`。干净的用 `git worktree remove <path>` 删掉。不干净的需要 `--force`，那会丢掉未提交的工作。

### 如果什么都没发生

运行 `foil seat peek lead`；刚启动或刚恢复的席位也这样看一眼。一个没有进展的席位，通常停在对话框上。操作员回答的是审批对话框：文件夹信任提示、登录提示，或一个停住席位、直到有人批准的提示。更新对话框、首次运行对话框和意见征集由它报告给你，留给你来回答。那些要你在窗口里处理（`tmux ls` 能列出运筹的会话，`tmux attach` 可以进入）。CLI 在窗格里完成不了的登录，先手动登录一次，然后 `foil seat kill lead`，再重新启动主座。如果 `foil seat list` 显示这个主座是 `dead`，`foil seat kill lead` 会清掉它。等它消失之后再做一次首次运行检查。

## 在浏览器里查看

在任意目录运行 `foil view`，它会为这台机器上的所有运筹项目提供一个只读网页，并打印网页地址。首页列出各个项目。每个项目页列出它的舰队和经验，它的设置页显示三份共用清单。每个舰队页按状态分组显示席位（`dead` 的排在最前，`killed` 的折叠起来），然后是它的 `status.md`、带修改时间的帖子和看板文件、覆盖，以及连接命令。看板文件在单独的页面打开，每个席位页显示它的原始窗格。它不往窗格里打任何按键，也不判断进度；进度仍然看 `board/status.md`。停掉它，每个席位都保持原样。

```sh
foil view
foil view --port 8790
```

不加 `--port` 时，它使用 7460 到 7509 之间第一个空闲端口。它只监听 `127.0.0.1`。要从另一台机器访问，用 SSH 转发端口，例如 `ssh -L 8790:127.0.0.1:8790 host`。

要从你在 Tailscale 上的其他设备打开它，在它前面放上 `tailscale serve`，例如 `tailscale serve --bg 8790`，然后打开这条命令打印的 `https://` 地址。没有任何东西监听你的局域网或 tailnet 地址；Tailscale 把每个请求转发到 `127.0.0.1`。`foil view` 在启动时检查 Tailscale，所以请在 Tailscale 运行之后，以及这台机器登录另一个账号或被加上标签之后，再启动或重启它。在 tailnet 名称下，它只响应 Tailscale 标明来自这台机器所属登录账号的请求。Funnel 流量、带标签的设备和其他用户的设备都得到空的 403。请使用上面的 HTTPS 形式，或 `--http`。`tailscale serve --tcp`、`--tls-terminated-tcp` 和 `tailscale funnel --tcp` 转发原始字节，不带这个检查读取的请求头，所以它们放进来的任何人都能读到这个页面。`tailscale serve reset` 停止共享。

### 让查看器一直运行

运筹没有守护进程模式。macOS 上的 launchd 和 Linux 上的 `systemd --user` 可以原样运行 `foil view`，登录时启动它，失败时重启它。你的操作员 Agent 可以替你填好这些文件。一个服务就覆盖这台机器上的所有项目。给它一个 7460-7509 之外的端口，这样临时运行的 `foil view` 不会占走它。服务运行期间，这台机器上的每个账号都能读到这个网页，只要你还登录着（见“限制”），所以只在不与他人共用的机器上设置它。把每个大写占位符换掉：

- `FOIL`：`command -v foil` 打印的绝对路径。
- `PORT`：端口。
- `YOUR_PATH`：在你自己的终端里运行 `echo $PATH` 打印的内容。服务启动时的 `PATH` 很少，没有你的 `PATH` 就找不到 tmux 和你的 Agent CLI。
- `HOME_DIR`：你的主目录的绝对路径。

如果你设置了 `FOIL_HOME`、`XDG_DATA_HOME` 或 `TMUX_TMPDIR`，在 `PATH` 旁边加上同样的值；`TMUX_TMPDIR` 不一致时，查看器找不到席位的窗口，会把每个席位都显示为 dead。升级运筹之后重启这个服务。

macOS，`~/Library/LaunchAgents/dev.foil.view.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>dev.foil.view</string>
  <key>ProgramArguments</key><array>
    <string>FOIL</string><string>view</string><string>--port</string><string>PORT</string>
  </array>
  <key>WorkingDirectory</key><string>HOME_DIR</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>YOUR_PATH</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>HOME_DIR/Library/Logs/foil-view.log</string>
  <key>StandardErrorPath</key><string>HOME_DIR/Library/Logs/foil-view.log</string>
</dict></plist>
```

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.foil.view.plist
launchctl bootout gui/$(id -u)/dev.foil.view
```

第一行启动它，第二行停止它。改过文件之后，先停止再启动。它在失败后重启，最多每 30 秒一次；正常停止后不重启。

Linux，`~/.config/systemd/user/foil-view.service`。把 `YOUR_PATH` 里的每个 `%` 写成 `%%`：

```ini
[Unit]
Description=Foil viewer

[Service]
ExecStart="FOIL" view --port PORT
WorkingDirectory=HOME_DIR
Environment="PATH=YOUR_PATH"
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

```sh
systemctl --user enable --now foil-view
systemctl --user disable --now foil-view
journalctl --user -u foil-view
```

它在你登录期间运行。

## 限制

运筹保护的是遵守说明的席位，不是把恶意进程隔离开。席位身份来自运筹启动它时设置的环境变量 `FOIL_SEAT_ID`。没有哪个参数能让一个席位自称是另一个席位。在这台机器上你能做的事，以你的身份运行的进程也能做。

每个席位的 git 会先运行运筹的 `pre-commit` 和 `pre-push` 钩子，再运行你仓库自己的钩子；这些钩子只设在该席位的环境里。它们拒绝席位在自己工作树之外提交，也拒绝新增你的主目录路径、这台机器的主机名、你的 git 邮箱（noreply 地址除外）或令牌的提交和推送。检查只是尽力而为，席位也可以用 `--no-verify` 跳过。请检查席位推送的内容。

模板默认是 `permission = "auto"`。省略 `permission` 时，席位无人值守地运行。把 `permission` 设成 `"ask"` 是模板重新选择提示的方式：那个席位会停在第一个审批提示上。把 `permission` 设成 `auto` 会插入该预设的自动批准参数。对 Claude，这些参数是 `--permission-mode` 和 `auto`。Claude Code 的 auto 模式仍可能在某些命令前询问，所以 auto 席位可能停在审批提示上。要看一眼新席位。那个席位仍然是你。

运筹从不解读窗口里的内容。`foil seat peek` 打印 tmux 原样捕获的文本，`foil seat list` 报告 `alive`、`dead` 或 `killed`，依据是席位的窗口还在不在，而不是屏幕上显示了什么。Agent 是否卡住、是否做完，由它们自己写在看板文件里。运筹不会抓取窗格，不会判断对话框是否出现，也不会发送按键。

`foil view` 没有登录。你的项目文件夹只有你能读，但 `127.0.0.1` 上的端口对这台机器上的每个账号都开放，所以查看器运行期间，任何登录这台机器的人都能读到它，包括每个项目的窗格文本和看板文件。它会尽力隐藏启动命令里看起来像凭据的参数。在别人也会登录的机器上，用完就停掉它。tailnet 检查只保护 tailnet 名称：这台机器上的账号仍然可以在 `127.0.0.1` 上读到这个页面。

即使窗口不在输入提示符，提醒也会被打进去。`foil send` 先把邮件写成文件，再往收件人的窗口打一行：发件人、一个空格、邮件文件的绝对路径。`foil post` 写成一份房间文件，再把同样的一行打进其他每个仍在运行的席位；如果是回复，只打进在该帖子串里发过帖的席位和回复里点到的席位。两者都等待 200ms，然后发送一次 Enter，这样把快速连击里的 Enter 当成未发送粘贴的 harness 会提交这一行，而不是把它留在输入行里。消息正文从不被打进窗口。`foil post --re PATH --decline` 写成一份房间文件，但不打任何按键。如果 Agent 并不在等输入，这些按键仍然会进入窗口。运筹不会先看窗口再决定打不打，也不会再发送一次 Enter。

## 进一步了解

- [docs/demo.zh-CN.md](docs/demo.zh-CN.md) — 一次真实运行，从 `foil init` 到修复被合并
- [docs/architecture.md](docs/architecture.md) — 组件、数据流和模块边界
- [skills/operator.md](skills/operator.md)、[skills/lead.md](skills/lead.md)、[skills/worker.md](skills/worker.md) — 每个角色运行哪些命令
- [CONTRIBUTING.md](CONTRIBUTING.md) — 搭建与检查
- [CHANGELOG.md](CHANGELOG.md) — 版本历史
