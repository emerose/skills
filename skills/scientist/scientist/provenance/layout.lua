--[[
layout.lua — give a report's exhibits more horizontal room (no LaTeX package needed).

Used by `sci report`'s renderer (ROADMAP §5), alongside endnotes.lua. Two AST tweaks, so
pandoc's writers still do all the typesetting:

  * Tables  — set equal column widths summing to the full measure, so the LaTeX writer
    emits `p{...}` columns that fill `\linewidth` (a bare pipe table otherwise renders at
    its natural, often narrow, width). Harmless on HTML (relative column widths).
  * Figures — for the PDF (LaTeX) target only, default an unsized image to slightly wider
    than the text block (`115%`); a centred figure then bleeds symmetrically into the
    margins, so the hero comparison plot uses the page. Left alone for HTML/docx (a >100%
    width would overflow the container there).

An author who sets an explicit image width keeps it.
--]]

local FIGURE_WIDTH = "115%"

function Table(t)
  local n = #t.colspecs
  if n > 0 then
    for i = 1, n do
      t.colspecs[i] = { t.colspecs[i][1], 1.0 / n }
    end
  end
  return t
end

function Image(img)
  if FORMAT:match("latex") and not img.attributes.width then
    img.attributes.width = FIGURE_WIDTH
  end
  return img
end
