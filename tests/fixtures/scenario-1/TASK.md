# Make the tests pass

`check_calc.py` fails because `calc.py` subtracts. Do each step once. If the seat is already listed, do not spawn another.

1. If `foil seat list` does not contain `implementer-1`, run:

```sh
foil seat spawn implementer --task "Run sh fix.sh."
```

`fix.sh` fixes `calc.py`, commits on `foil/implementer-1`, and sends mail that says `fixed on foil/implementer-1`.

2. When that mail arrives, if `foil seat list` does not contain `reviewer-1`, run:

```sh
foil seat spawn reviewer --task "Run sh review.sh."
```

`review.sh` runs `check_calc.py` in the implementer worktree and sends mail that says `approved`.

3. When that mail arrives, merge and mark the work done:

```sh
git merge --no-edit -m "merge the fix" foil/implementer-1
```

Write `.foil/board/status.md` so it contains the line `state: done`. The merged `check_calc.py` must pass.
