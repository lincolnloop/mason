---
name: notes
description: 'This skill should be used when asked to "save this analysis", "keep this as a note", "save the assessment", "what notes do we have on <topic>", "mark the note adopted" or "drop that proposal" for a project, or before starting a design review, investigation, option paper or assessment that earlier analyses may already cover. It keeps analyses in mason, per project, out of the project''s history, with a status that says what became of each.'
version: 1.0.0
metadata:
  mason-requires: '>=0.3,<0.4'
---

# Notes

An analysis (a design review, an investigation, an option paper, an assessment of a library) is
worth keeping, but most never become work, and a `docs/notes/` folder puts every one of them in
the project's history and every developer's checkout. mason keeps them instead, as notes, per
project, in the developer's own data directory. A note that is adopted moves into the project in
the form the project keeps (a decision record, a design page, a PR description), and the note
links to it.

`mason` is mason's command line, on the PATH. Every command takes `--repo <owner>/<repo>`, or
works out the project from a checkout in the current directory.

## Before an analysis

List the project's notes, and read any on the same subject, so the new analysis builds on them
rather than repeating them:

```sh
mason note list --repo <owner>/<repo>
mason note show <key> --repo <owner>/<repo>
```

Say which earlier notes it builds on. If an earlier note's conclusion no longer holds, say so,
and supersede it once the new note is saved (below).

## Saving a note

When the person asks to keep an analysis, write it as markdown to a file outside the project
(the session's scratch directory, or `/tmp`), then:

```sh
mason note save <file.md> --repo <owner>/<repo> --title '<a title naming the subject>' [--tag <tag> ...] [--status proposed]
```

It prints the note's key (`<date>-<slug>`). `draft` is the default status; `proposed` is for an
analysis that recommends work someone has to decide on. A note is plain text in the developer's
home directory and may be published: no secrets, and no personal data from production.

To revise a note, save a new version; the earlier one is kept:

```sh
mason note save <file.md> --repo <owner>/<repo> --update <key>
```

Never write the note into the project's repository, unless the person asks for that.

## What happened to it

```sh
mason note status <key> adopted --repo <owner>/<repo> --link <the PR, issue or page that keeps it>
mason note status <key> dropped --repo <owner>/<repo>
mason note status <key> superseded --repo <owner>/<repo> --by <the newer note's key>
```

When a note is adopted, what the project needs is written into the project, for the project's
readers, and the note links to it. The note itself stays in mason.

## Sharing a note

A note is the developer's. To share one, publish a copy; the note stays the master:

```sh
mason publish notes/<key> --repo <owner>/<repo> --to discussion:<category>
mason publish notes/<key> --repo <owner>/<repo> --to file:<directory>
```

Publish only when the person asks.
