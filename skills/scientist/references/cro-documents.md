# CRO documents: study plans and protocols

A CRO sends a draft study plan carrying its own comments — blanks for the sponsor to fill,
questions for the sponsor to answer, notes to its own scientists. The sponsor returns it with
tracked changes and answers, and the loop repeats for every study.

**The mechanics of marking up the document are not specific to science**: use the
[docx-markup](../docx-markup/SKILL.md) skill, which covers reading the author's comments before
the body, deciding what belongs in a tracked change versus a comment, replying inside threads,
voice, and the OOXML traps.

What belongs here is where the files go.

## Filing

- The CRO's original goes in the experiment's `protocol/`, and is **never overwritten** — it is
  the record of what they sent.
- The marked-up copy goes beside it, named so the two are distinguishable
  (`<study no> - Study Plan - <sponsor> markup <date>.docx`).
- The edit list that produced it goes under `analysis/`, with a short README naming the input
  file and the command, so a later revision can be re-marked rather than re-derived.
- When a marked-up copy is actually sent, commit **that** file — the version the CRO received,
  not the last one the tooling generated — and say so in the commit message.
- Source documents the CRO asks for (certificates or reports of analysis, safety data sheets)
  are raw vendor records: file them in `raw/`, and cite them when filling the study plan's test
  item table rather than copying values out of our own prior READMEs.

## Reading their comments pays off

On one CRL study-plan review, three conclusions changed once the comments were read alongside
the body: a formulation arrangement that looked like the CRO departing from the signed SOW was
a comment asking the sponsor to decide; a sponsor-approval date that looked like a claim was
flagged "to update" by the study director; and one comment transposed two tissue allocations
relative to the SOW, which needed querying rather than correcting. None of that is visible in
the prose.
