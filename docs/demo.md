# Demo

This repository does not include a recording. The commands below are the quick start from the README. They use Claude, one mainstream harness.

You need Python 3.11+, Git, tmux 3.2+, and the `claude` CLI already logged in. Foil never sees that login.

## Install and initialize

```sh
uv tool install "git+https://github.com/TiantianFlow/foil.git"
cd your-repo
foil init .
```

`foil init` checks that Git and tmux exist and that the current directory is a Git repository. It creates `.foil`, default templates, and a Git exclude entry. Running it again does not overwrite templates that are already there.

Open `.foil/templates/lead.toml`. Init set `harness` to the first installed CLI among `grok`, `claude`, `codex`, `opencode`, and `gemini`. For this walkthrough every template should say:

```toml
harness = "claude"
permission = "ask"
```

Change `harness` in `.foil/templates/lead.toml`, `.foil/templates/implementer.toml`, and `.foil/templates/reviewer.toml` when init picked a different CLI. Leave `permission` as `ask`. `auto` would add Claude's `--permission-mode auto` and let that seat act without asking.

## Spawn the lead

```sh
foil seat spawn lead --task "Summarize this repository in board/status.md"
```

This creates the seat named `lead` in a tmux window. The task is the lead's first mail file. Foil then types one line into that window: `user`, a space, and the absolute path of the mail file, then Enter. It types that line even when the pane is not at a prompt. The task text itself is not typed.

## Look, don't interpret the pane

```sh
foil seat list
foil seat peek lead
```

`foil seat list` prints the lead's name, template, state, and worktree. `alive` means the stored tmux window id still exists. The list does not say whether the model is busy.

`foil seat peek lead` prints the raw last 40 lines of that pane, the same text tmux captured. Foil does not decide from those lines whether the lead is stuck or finished.

The lead's report is the file it writes at `.foil/board/status.md`.

## Stop

```sh
foil seat kill --all
```

Every seat's window is stopped and marked killed. Branches and worktrees are left in place.

## 中文

仓库里没有录像。下面的命令和英文快速开始相同，用的是 Claude。

需要 Python 3.11+、Git、tmux 3.2+，以及已经登录的 `claude` CLI。运筹看不到这次登录。

```sh
uv tool install "git+https://github.com/TiantianFlow/foil.git"
cd your-repo
foil init .
```

`foil init` 确认 Git 和 tmux 存在，且当前目录是 Git 仓库。它创建 `.foil`、默认模板和 Git exclude 条目。再跑一次不会覆盖已经存在的模板。

打开 `.foil/templates/lead.toml`。init 会把 `harness` 设成 `grok`、`claude`、`codex`、`opencode`、`gemini` 里本机第一个已安装的 CLI。这篇演练里每个模板都应写成：

```toml
harness = "claude"
permission = "ask"
```

如果 init 选了别的 CLI，就改 `.foil/templates/lead.toml`、`.foil/templates/implementer.toml` 和 `.foil/templates/reviewer.toml` 里的 `harness`。`permission` 保持 `ask`。设成 `auto` 会加上 Claude 的 `--permission-mode auto`，那个席位就可以不再询问直接行动。

```sh
foil seat spawn lead --task "Summarize this repository in board/status.md"
```

这会在 tmux 窗口里创建名为 `lead` 的席位。任务是主座的第一封邮件。运筹随后往窗口打一行：`user`、一个空格、邮件文件的绝对路径，然后 Enter。窗格不在提示符时也会打。任务正文本身不会被打进去。

```sh
foil seat list
foil seat peek lead
```

`foil seat list` 打印主座的名字、模板、状态和 worktree。`alive` 表示保存的 tmux 窗口 id 还在。列表不说明模型是否在忙。

`foil seat peek lead` 打印该窗格原样的最后 40 行，就是 tmux 捕获的文本。运筹不根据这些行判断主座是卡住了还是做完了。

主座的报告在它写的 `.foil/board/status.md`。

```sh
foil seat kill --all
```

每个席位的窗口都会被停掉并标成 killed。分支和 worktree 会留在原地。
