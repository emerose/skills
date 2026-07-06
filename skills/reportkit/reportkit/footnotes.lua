--[[
footnotes.lua — two fixes to how citation footnotes are typeset in the PDF.

(1) Per-page dedup. Pandoc re-emits a footnote's *full text* at every reference, so a
    claim / paper cited more than once printed as several byte-identical numbered notes
    (e.g. footnotes 193 and 194, same text, one claim cited twice). This collapses repeats
    that land on the SAME PAGE into one numbered note cited N times — while a repeat that
    falls on a LATER page still prints its own full note, so a page always carries the text
    of every mark on it (locality; not a relocated endnotes section). The same-page test is
    made at typeset time (\getpagerefnumber vs the current page counter), so it needs LaTeX's
    page knowledge — hence a LaTeX-only render helper (\rptnote, see report.py's PDF header),
    resolved across xelatex's reference passes. Keyed on stringified content (like the old
    endnotes.lua / dedupe-footnotes.lua), formatting-insensitive.

(2) Adjacent-mark separator. Two citations on one sentence rendered as touching superscripts
    with no gap ("4041" for notes 40 and 41). A superscript comma is inserted between any two
    footnote marks that are immediately adjacent, giving "40,41" — the footmisc `multiple`
    behaviour, done here with no package (footmisc is not in a basic TeX install).

LaTeX/PDF only — that is the committed deliverable; other writers (HTML/docx) pass through
unchanged (pandoc's own footnote handling). Requires \usepackage{refcount} + the \rptnote
helper, both loaded by report.py's PDF header.
--]]
if not FORMAT:match("latex") then
  return {}
end

local key_id = {}   -- stringified note content -> small integer id (a safe csname/label key)
local key_occ = {}  -- id -> occurrences seen so far (labels are unique per (id, occ))
local next_id = 0

-- One note occurrence -> a \rptnote{<id>}{<occ>}{<body>} call. The LaTeX helper decides,
-- at typeset time, whether this occurrence reuses the id's active note (same page) or
-- prints a fresh full footnote (new page); Lua only supplies stable ids and the body.
local function note_latex(el)
  local key = pandoc.utils.stringify(pandoc.Div(el.content))
  local id = key_id[key]
  if not id then
    next_id = next_id + 1
    id = next_id
    key_id[key] = id
    key_occ[id] = 0
  end
  key_occ[id] = key_occ[id] + 1
  local body = pandoc.write(pandoc.Pandoc(el.content), "latex"):gsub("%s+$", "")
  return pandoc.RawInline("latex",
    "\\rptnote{" .. id .. "}{" .. key_occ[id] .. "}{" .. body .. "}")
end

-- Rewrite each inline sequence: convert every footnote to its \rptnote call, and slip a
-- superscript comma between two marks that are directly adjacent (nothing between them).
function Inlines(inlines)
  local out = pandoc.Inlines({})
  local prev_mark = false
  for _, el in ipairs(inlines) do
    if el.t == "Note" then
      if prev_mark then
        out:insert(pandoc.RawInline("latex", "\\textsuperscript{,}"))
      end
      out:insert(note_latex(el))
      prev_mark = true
    else
      out:insert(el)
      prev_mark = false
    end
  end
  return out
end
