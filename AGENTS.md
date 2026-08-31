<!-- aver agent-connect: start -->
## Aver

This project is written in [Aver](https://github.com/jasisz/aver): a statically typed language for code that is cheap to generate and has to be cheap to trust. Source files are `.av`.

Two skills carry the material, installed by `aver agent-connect`:

- `.claude/skills/aver/SKILL.md` — the language: syntax, types, `match`, classified effects, modules, `verify` blocks, `decision` blocks.
- `.claude/skills/aver-tooling/SKILL.md` — the toolchain: `run`, `check`, `verify`, `context`, `shape`, `compile`, `proof`, `replay`, and `aver.toml` policy.

The two tools worth reaching for first are `aver --help` for the command surface and `aver context <entry.av> --budget 10kb` to read a program before opening its files. `aver agent-connect --print` writes the same language guide to stdout for an agent that prefers one file.

This section is maintained by `aver agent-connect`; edits inside the markers are overwritten on the next run.
<!-- aver agent-connect: end -->
