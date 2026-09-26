# Staff a worker

If `foil seat list` does not contain `implementer-1`, run:

```sh
foil seat spawn implementer --task "Run sh propose.sh."
```

`propose.sh` proposes the lesson `check the merged tests`. A worker cannot accept it, so the script writes `refused` to `.foil/board/notes/accept.txt` and sends mail that starts with `proposed`.

When that mail arrives, run `sh accept.sh`.

`accept.sh` accepts that lesson and runs:

```sh
foil seat spawn reviewer --name reader --task "carry the lesson"
```

The instruction file for `reader` includes the accepted lesson `check the merged tests`.
