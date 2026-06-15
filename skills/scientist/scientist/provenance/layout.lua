--[[
layout.lua — fit a report's exhibits to the Tufte page (used by `sci report`, ROADMAP §5,
alongside endnotes.lua). Structural AST tweaks; pandoc's writers still typeset.

  * Tables — equal column widths summing to the full measure, so the LaTeX writer emits
    `p{...}` columns that fill `\linewidth` (a bare pipe table otherwise renders narrow).
    Harmless on HTML (relative column widths).

  * Figures (PDF/LaTeX only) — the page geometry is asymmetric (narrow body, wide right
    margin; see report.py). A normal figure would sit in the narrow body and a forced-wide
    one floats off to its own page. Instead, replace the float with an in-place block, left-
    anchored at the body edge and `\fullwidth` wide, so the figure extends into the right
    margin exactly where it is written — keeping `\captionof{figure}` numbering. The caption
    inlines are written back to LaTeX so their formatting / endnote markers survive.
--]]

function Table(t)
  local n = #t.colspecs
  if n > 0 then
    for i = 1, n do
      t.colspecs[i] = { t.colspecs[i][1], 1.0 / n }
    end
  end
  return t
end

function Figure(fig)
  if not FORMAT:match("latex") then
    return nil                                    -- HTML/docx: leave the figure as-is
  end
  local src
  pandoc.walk_block(fig, { Image = function(im) src = src or im.src end })
  if not src then return nil end
  local caption = pandoc.write(pandoc.Pandoc(fig.caption.long or {}), "latex")
  local tex = table.concat({
    "\\par\\medskip\\noindent\\makebox[\\linewidth][l]{%",
    "\\begin{minipage}{\\scifullwidth}\\centering",
    "\\includegraphics[width=\\scifullwidth,keepaspectratio]{" .. src .. "}",
    "\\captionof{figure}{" .. caption .. "}",
    "\\end{minipage}}\\par\\medskip",
  }, "\n")
  return pandoc.RawBlock("latex", tex)
end
