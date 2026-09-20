# TASKS.md — who does what, when

Hard stop: hacking ends **Sunday 11:00 ET** (HackMIT).

## Ownership

- **Zach** owns `pipeline/` and `web/data/`.
- **Arya** owns `web/` — everything except `web/data/`.
- Nobody edits the other's folder. Any change to `CONTRACT.md` is a message to the
  other person **first**, then the commit.
- Coordination is through this repo: CONTRACT.md (the interface), DESIGN.md (the
  screen), this file (the schedule), implementation-notes.md (Zach's plan + running
  log). Everything is written to be read cold.

## Arya — tonight

- [ ] Install GitHub CLI (`winget install --id GitHub.cli -e`), accept the
      collaborator invite (check email / github.com/notifications), `gh repo clone
      zacharyspeck/firebreak`.
- [ ] Page running on `web/mock/`: `python -m http.server -d web 8000`.
- [ ] **60-minute checkpoint: screenshot of the page on mock data** into the group
      chat.
- [ ] Draft the Plume submission: "What it does / How we built it".

## Arya — after concert

- [ ] Point the page at `web/data/` (`DATA_DIR` constant), verify it renders the real
      fire.
- [ ] Polish to DESIGN.md spec.
- [ ] 10-slide deck.
- [ ] 2-minute video.

## Zach — tonight

- [ ] Pipeline: fetch → grid → simulate → calibrate → export **baseline** — the real
      fire on the map (`web/data/` with baseline + placeholder solutions if needed).

## Zach — Sunday morning

- [ ] candidates → solve → export **solutions** at all budgets.
- [ ] Integrate with the page, run the full demo offline once, top to bottom.

## If blocked

- Arya blocked on data shape → `web/mock/` is the contract; build against it.
- Zach's pipeline slips → the demo still works end-to-end on mock; the pitch changes,
  the page doesn't.
