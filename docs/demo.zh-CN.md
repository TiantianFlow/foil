# 演示：用三个 Agent 组成的团队修好一个失败的测试

这是一次真实运行，从 `foil init` 一直到修复被合并。它就是端到端测试套件里检查的
场景 1，用真实的 `foil` 命令、真实的 tmux 和真实的 Git 手动重放了一遍，只有
Agent 是脚本扮演的。

下面每条命令之后展示的，都是那次运行中运筹自己的输出，只是把测试用的 harness 换成了
第 2 步里的 harness；每个 Agent 在自己窗口里显示的内容会不同，而且换成真实 Agent 后，
整个过程要花几分钟而不是几秒。想在运筹的代码仓库里重放这次脚本化运行：

```sh
uv run --frozen --extra dev pytest tests/e2e/test_scenarios.py -k scenario_1
```

## 起点

一个 Git 仓库，其中 `check_calc.py` 会失败，因为 `calc.py` 把加法写成了减法。你需要
Python 3.11+、Git、tmux 3.2+，以及至少一个已经在本机登录的 Agent CLI。运筹从不经手
这次登录。

## 1. 安装并初始化

```sh
uv tool install --upgrade foil-ai
cd your-repo
foil init
```

```text
foil 0.7.0
...
Project folder: /var/foil/projects/your-repo-1a2b3c
Run foil seat join lead, then read the file it names. My goal: <goal>.
```

`foil init` 把项目目录写在仓库之外，工作区里什么都不写，所以 `git status` 保持干净。
输出的第一行是 `foil --version` 打印的版本号。中间的报告（这里有删节）列出已安装的
harness 和 init 写了什么。最后一行是你稍后要粘贴给自己的 Agent 的那句话，把 `<goal>`
换成你的目标。

## 2. 给每个角色选 harness 和权限

每个角色是全局清单 `<root>/roles.toml` 和 `<root>/mapping.toml` 里的一行。init 会给三个模板选同一个已安装的 CLI。
你可以给每个角色换一个，比如主座用擅长规划的模型，审查者用另一家厂商的模型。`foil roster update` 写入同样的行。`--project` 覆盖是项目目录下的同名文件，要在 `foil init` 之后再写，因为 init 会删掉它找到的项目副本。

```toml
# <root>/roles.toml
[[role]]
id = "implementer"
persona = "personas/implementer.md"
worktree = true
permission = "auto"

# <root>/mapping.toml
[[map]]
role = "implementer"
harness = "codex"
```

再写一个 `[[map]]` 行，并可以带上 `model`，这个角色之后每增加一个席位就取列表中的下一项。只有一项时就一直用这一项。

`worktree = true` 让每个实现者拥有自己的 Git worktree 和分支。省略 `permission` 时
取值是 `auto`，席位无人值守地运行。`permission = "ask"` 是显式选择：
席位无界面的每一轮都会拒绝需要审批的操作，席位报告它需要什么。
在 `auto` 下，有些 harness 仍可能拒绝某些命令。

## 3. 交出目标

在你自己的 Agent（Claude Code、Codex 或其他任何一个）里，在这个仓库中，粘贴
`foil init` 的最后一行，并把目标填进去：

```text
Run foil seat join lead, then read the file it names. My goal: make the tests pass.
```

这个 Agent 作为主座加入，成为一个没有窗口的席位，在你的对话里规划目标，并用 `foil mail wait`
接收舰队的消息。这个演示重放的是另一种方式：由运筹在 tmux 里启动主座，也就是你手动输入的这一条：

```sh
foil seat spawn lead --task "Make the tests pass."
```

运筹会打开一个名为 `lead` 的 tmux 窗口，启动主座的 CLI，并把完整指令作为第一条提示。
任务会在 CLI 启动前写成一封邮件文件，指令里写明这个文件并让主座先读它。主座的 CLI 无界面地运行，
每一轮一个进程：一轮结束后，运筹的运行器等待，有主座的邮件或房间帖子到达时，在同一个会话上开始下一轮。
运筹不往窗口里打任何字。

## 4. 看着团队组建起来

```sh
foil seat list
```

```text
implementer-1	implementer	working	/var/foil/projects/your-repo-1a2b3c/worktrees/implementer-1	codex	019a4c2e-7d3b-7f10-a2c4-5e8f9b1d0c63	You make the change the task asks for, and you do not review your own work.
lead	lead	idle		claude	3f1c2a9e-5b7d-4e21-9a0c-6d8b2f4e1a77	You oversee the fleet: you ask other seats for proposals, weigh them against the goal, and choose, and you do not do the work yourself.
reviewer-1	reviewer	working		claude	b84e0d52-1c9a-4f36-8e7b-2a5d6c3f9e10	You check the change the lead names and report findings, and you do not fix them.
```

主座派出了一个实现者和一个审查者。实现者在自己的 worktree 里、在分支
`foil/implementer-1` 上工作。`working` 表示席位窗口里有一轮正在运行，`idle` 表示它的运行器
在等邮件；一轮接一轮失败的席位是 `paused`。这些来自运行器自己的记录，而不是屏幕。最后三列是
这个席位启动时用的 harness（模板设了 model 时写成 `harness/model`）、它的会话 id，以及它的
角色 persona 的第一行。

## 5. 看一眼席位内部

```sh
foil seat peek lead --lines 8
```

```text
...
foil: turn ended (exit 0)
foil: turn started (turn)
...
foil: turn ended (exit 0)
foil: turn started (turn)
...
foil: turn ended (exit 0)
```

peek 原样打印那个窗口最下面的内容（这里有删节）。在 Agent 自己的输出之间，是运行器围着每一轮
打印的行：主座读到实现者的修复和审查者的批准的那几轮。换成真实的 harness，你会看到每一轮的完整输出。
运筹从不解读屏幕上的内容。

每一轮都从运行器写的一段提示开始。它列出新文件，短邮件的正文会直接附上，例如：

```text
New for lead (1):
2026-09-28T00:58:56Z implementer-1 /var/foil/projects/your-repo-1a2b3c/fleets/main/board/mail/lead/20260928T005856Z-implementer-1-df1d08d0.md
```

## 6. 读邮件和主座的报告

```sh
foil board list 'mail/lead/*.md'
foil board read status.md
```

```text
mail/lead/20260928T005855Z-user-40eda840.md
mail/lead/20260928T005856Z-implementer-1-df1d08d0.md
mail/lead/20260928T005857Z-reviewer-1-d5045b2b.md
```

```text
---
contract: status/v1
state: done
updated: 2026-09-26T00:00:00Z
questions: []
---

## Checklist

- [x] Fix the failing test (implementer-1)
- [x] Review the fix (reviewer-1)
- [x] Merge foil/implementer-1

The tests pass.
```

在舰队外用 `foil board list` 和 `foil board read`；座位读自己的邮箱时优先用
`foil mail list` 和 `foil mail read`。每条消息是项目目录里该舰队的 `board/` 下的文件。
`status.md` 是主座自己写的报告。主座需要你时，会把问题列在里面，你用
`foil send lead "..."` 回答。

## 7. 检查结果

```sh
git log --oneline --graph
python3 -m pytest -q check_calc.py
```

```text
* 1afdc55 fix add
* fc11425 base: failing test
```

```text
1 passed
```

主座合并了 `foil/implementer-1`，测试现在通过了。

## 8. 收尾

```sh
foil seat kill --all
foil seat list
```

```text
implementer-1	implementer	killed	/var/foil/projects/your-repo-1a2b3c/worktrees/implementer-1	codex	019a4c2e-7d3b-7f10-a2c4-5e8f9b1d0c63	You make the change the task asks for, and you do not review your own work.
lead	lead	killed		claude	3f1c2a9e-5b7d-4e21-9a0c-6d8b2f4e1a77	You oversee the fleet: you ask other seats for proposals, weigh them against the goal, and choose, and you do not do the work yourself.
reviewer-1	reviewer	killed		claude	b84e0d52-1c9a-4f36-8e7b-2a5d6c3f9e10	You check the change the lead names and report findings, and you do not fix them.
```

所有窗口都已关闭。分支和 worktree 会保留，`git status` 里也看不到任何运筹的文件。
留下的席位工作树在 `<项目目录>/worktrees/<label>`。干净的用 `git worktree remove <path>`
删掉。不干净的需要 `--force`，那会丢掉未提交的工作。

## 如果什么都没发生

运行 `foil seat list`，再运行 `foil seat peek lead`。没有席位会停在对话框上：每一轮都无界面运行，
需要人才能继续的一轮会结束。

- `paused`：失败的一轮重试三次也都失败了。运筹把原因发邮件给主座；主座自己就是这个席位时，发给你。
  修好原因，比如过期的登录（手动运行一次那个 CLI，接受它的更新、首次运行和意见征集对话框），
  再运行 `foil seat resume lead`。
- `dead`：窗口或它的运行器没了。运筹也会为此发邮件。`foil seat resume lead` 在同一个会话上重新启动它。

如果 tmux 挂了或机器重启了，`foil seat resume` 会重新启动所有没被 kill 的席位。
