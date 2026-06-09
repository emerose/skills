"""scientist — consolidated scientific-data package.

Umbrella for the skill's runtime: provenance core, lab-file readers, the
raw→data extractor, the libkit-backed store, the typed experiment accessor,
and the claim-grounding harness. Subpackages are imported explicitly
(``from scientist.experiments import k1_000000``, ``from scientist.grounding
import strength``); this marker re-exports nothing.
"""
