"""Constraints of the person's request, checked on what Atlas produced.

model.py: the typed, atomic constraints and their validation; phrases.py:
what code reads by itself; views.py: one structured view of a reply or a
file (PPTX, DOCX, PDF, XLSX, HTML, SVG); checks.py: one checker per kind;
verify.py: the model reading, the verifier and the repair, each checked by
code; pipeline.py: the bounded pass of a turn. Design: docs/33-request-constraints.md.
"""
