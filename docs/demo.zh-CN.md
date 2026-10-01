# 演示：用三个 Agent 组成的团队修好一个失败的测试

这是一次真实运行，从 `foil init` 一直到修复被合并。它就是端到端测试套件里检查的
场景 1，用真实的 `foil` 命令、真实的 tmux 和真实的 Git 手动重放了一遍，只有
Agent 是脚本扮演的。

下面每条命令之后展示的，都是那次运行中运筹自己的输出。不管用哪个 harness，这些
输出都一样；每个 Agent 在自己窗口里显示的内容会不同，而且换成真实 Agent 后，
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
uv tool install "git+https://github.com/TiantianFlow/foil.git"
cd your-repo
foil init
```

```text
Read .foil/skills/operator.md and follow it. My goal: <goal>.
permission = "ask"
```

`foil init` 会写出 `.foil/`，并让它不出现在 `git status` 里。输出的第一行，是你稍后要
粘贴给自己的 Agent 的那句话；第二行是主座模板的权限设置。

## 2. 给每个角色选 harness 和权限

每个角色是 `.foil/templates/` 里的一个模板。init 会给三个模板选同一个已安装的 CLI。
你可以给每个角色换一个，比如主座用擅长规划的模型，审查者用另一家厂商的模型：

```toml
# .foil/templates/implementer.toml
harness = "codex"
persona = "personas/implementer.md"
worktree = true
permission = "auto"
```

`worktree = true` 让每个实现者拥有自己的 Git worktree 和分支。`permission = "ask"`
会让席位停在第一个审批提示上，在自己的窗口里等你处理。想让它们无人值守地运行，就在
每个需要独立工作的席位模板里设置 `permission = "auto"`。
有些 harness 仍会在执行某些命令前请求批准，所以要看一眼新席位。

## 3. 交出目标

在你自己的 Agent（Claude Code、Codex 或其他任何一个）里，在这个仓库中，粘贴
`foil init` 给出的那一行，并写上你的目标：

```text
Read .foil/skills/operator.md and follow it. My goal: make the tests pass.
```

这个 Agent 就成了操作员。它先确认主座能正常启动，再把目标发给主座。手动做同样的事：

```sh
foil seat spawn lead --task "Make the tests pass."
```

运筹会打开一个名为 `lead` 的 tmux 窗口，启动主座的 CLI，并把完整指令作为第一条提示。
任务会写成一封邮件文件，运筹再往主座窗口里打一行提醒：发件人和那封邮件的路径。

## 4. 看着团队组建起来

```sh
foil seat list
```

```text
implementer-1	implementer	alive	/path/to/your-repo/.foil/worktrees/implementer-1
lead	lead	alive
reviewer-1	reviewer	alive
```

主座派出了一个实现者和一个审查者。实现者在自己的 worktree 里、在分支
`foil/implementer-1` 上工作。`alive` 表示这个席位的 tmux 窗口还在，并且带着这个
席位的标记；它不表示 Agent 是否在忙。

## 5. 看一眼席位内部

```sh
foil seat peek lead --lines 8
```

```text
...
implementer-1 /path/to/your-repo/.foil/board/mail/lead/20260928T005856Z-implementer-1-df1d08d0.md
...
reviewer-1 /path/to/your-repo/.foil/board/mail/lead/20260928T005857Z-reviewer-1-d5045b2b.md
```

peek 原样打印那个窗口最下面的内容（这里有删节）。在 Agent 自己的输出中间，有两行
提醒：实现者报告了修复，审查者批准了它。换成真实的 harness，你会看到它的完整屏幕。
运筹从不解读屏幕上的内容。

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
`foil mail list` 和 `foil mail read`。每条消息仍是 `.foil/board/` 下的文件。
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
implementer-1	implementer	killed	/path/to/your-repo/.foil/worktrees/implementer-1
lead	lead	killed
reviewer-1	reviewer	killed
```

所有窗口都已关闭。分支和 worktree 会保留，`git status` 里也看不到任何运筹的文件。

## 如果什么都没发生

运行 `foil seat peek lead`。一个没有进展的席位，通常停在下面某一种画面上：

- 登录提示：先手动登录一次那个 CLI，然后 `foil seat kill lead`，再重新启动主座；
- 文件夹信任提示：在那个窗口里接受它，或者在舰队开始之前先在本仓库里把这个 CLI 运行一次；
- 审批提示：在那个窗口里批准（`tmux ls` 能列出运筹的会话，`tmux attach` 可以进入），
  或者设置 `permission = "auto"`；
- harness 自己的首次运行或意见征集对话框：用同样的办法回答它。

如果 tmux 挂了或机器重启了，`foil seat resume` 会重新启动所有没被 kill 的席位。
