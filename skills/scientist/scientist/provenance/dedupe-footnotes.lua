--[[
dedupe-footnotes.lua — collapse identical footnotes so a note cited more than once
shares ONE numbered footnote (proper citation reuse) instead of pandoc's default,
which re-emits the note's *full text* at every reference (a footnote referenced N
times becomes N identical numbered footnotes — e.g. a litreview citing one grounded
[lit:] claim from four sections printed footnotes 1–4 with the same text).

Keeps per-page footnotes (locality, not a relocated endnotes section): the first
occurrence of a note stays a real `\footnote` and carries a `\label` capturing its
number; each later occurrence with byte-identical content becomes a
`\footnotemark[<that number>]`, so the same number reappears with no duplicated text.

LaTeX/PDF only — that is the committed deliverable. Other writers (HTML/docx) pass
through unchanged: pandoc still duplicates there, but the PDF is what is rendered and
shared. Requires `\usepackage{refcount}` (for `\getrefnumber`), loaded by report.py's
PDF header. Keyed on stringified content (formatting-insensitive), like endnotes.lua.
--]]
if not FORMAT:match("latex") then
  return {}
end

local seen = {}   -- note content (stringified) -> label of its first \footnote
local n = 0

function Note(el)
  local key = pandoc.utils.stringify(pandoc.Div(el.content))
  local label = seen[key]
  if label then
    -- a re-cite: same number, no duplicated text. \getrefnumber yields the first
    -- footnote's number (resolved on pandoc's second xelatex pass via the .aux).
    return pandoc.RawInline("latex", "\\footnotemark[\\getrefnumber{" .. label .. "}]")
  end
  n = n + 1
  label = "fndedup" .. n
  seen[key] = label
  -- first occurrence: a real footnote; \label lives INSIDE so it captures \thefootnote.
  local body = pandoc.write(pandoc.Pandoc(el.content), "latex"):gsub("%s+$", "")
  return pandoc.RawInline("latex", "\\footnote{" .. body .. "\\label{" .. label .. "}}")
end
