# Working on these skills (guidance for agents)

This applies to **every** skill in this repo, and to any agent extending one. The
skills here are meant to get better each time they're used on real work — treat
maintenance as part of the task, not a separate chore.

## Improve the skill as you go

When you discover a gotcha, a sharper workflow, a failure mode, or a better default
while *using* a skill, **capture it in that skill's `SKILL.md` (or a file under its
`references/`) in the same session**, while it's fresh. The next agent should
inherit what you learned instead of rediscovering it. A "Gotchas / lessons" section
built from real incidents is worth more than any amount of up-front theorizing.

## Push rote, mechanical work into code — always

Do not perform repetitive or mechanical operations by hand (editing a database
directly, moving/renaming files one by one, multi-step manual fix-ups). Drive them
through the skill's tooling, and for one-off analysis write a small script that
calls it. **When you catch yourself doing the same mechanical thing by hand more
than once, add it to the skill as a command or helper.** Encoded operations are
faster, consistent, reusable, and testable; ad-hoc manual steps are slow and
error-prone. Most of a mature skill's commands started life as a repeated manual
step someone finally codified.

## Share your changes back — PR them to the skills repo

This repo lives on GitHub, and improvements should flow back to it, not stay
stranded in one local checkout. When you change a skill here — a new command, a
doc fix, a captured lesson, a bug fix — **open a pull request to the skills repo**
so the next person (and the next agent) gets it. Branch, commit with a clear
message, push, and open the PR; don't leave improvements only on disk. The whole
point of a shared skills collection is that learnings compound across uses — that
only happens if you push them back.

(Two different "upstreams", don't confuse them: improvements to a **skill** go to
*this* repo by PR; fixes to an external **dependency** go to *that project's* repo
by PR — see the next section.)

## Separate skill-specific from generic — push generic dependency work upstream by PR

- **Skill-specific** lessons and fixes belong in this repo, in that skill.
- **Generic, widely-useful** improvements — especially fixes or missing features in
  an **upstream dependency** — should be contributed back **by issue + PR to that
  project**, not left as a local workaround. A workaround unblocks you today; the
  upstream fix helps everyone and removes the workaround. (Example: the
  `bibliographer` skill's needs drove several upstream `libkit` fixes — each
  shipped upstream rather than hacked around locally.)

If a piece of guidance is itself generic (like this file), it lives at the repo
root so every skill shares it — don't copy it into each `SKILL.md`.

## Verify your changes

After changing a skill's code, exercise the affected path on throwaway data before
declaring success. Prefer tests that don't need external keys/services where the
design allows (e.g. injecting a fake backend). Keep the docs in sync with the code
in the same change: commands, flags, env vars, version pins, and examples.

## Keep stateful skills healthy

A skill that manages a persistent store (a library, an index, a database) drifts
over time. Give it a hygiene/audit capability and run it periodically — a fast
deterministic pass for structure, and, where correctness is semantic, a
parallel-agent pass that actually reads the data. Make audits emit a structured
worklist so fixes can be driven by code or fanned out across agents.
