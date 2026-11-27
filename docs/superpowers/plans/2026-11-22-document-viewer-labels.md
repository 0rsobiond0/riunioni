# Document Viewer and Action Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make generated diagrams part of the all-documents workflow, hide technical files from the user-facing document list, render image files as graphics, and label the open action according to the selected menu.

**Architecture:** Keep JSON and Mermaid sources on disk for regeneration, but filter them from the frontend document list. Make the existing all-documents backend workflow request diagram rendering, and update the single open button label from the selected menu context.

**Tech Stack:** Python/FastAPI backend, vanilla JavaScript/HTML frontend, existing Mermaid CLI renderer, unittest.

**Spec:** User requested diagrams generated with all documents and a clearer dynamic open-button label.

## Global Constraints

- Technical files (`.json`, `.mmd`) remain available internally but are not shown as user documents.
- All-documents generation includes diagrams.
- Image outputs (`.svg`, `.png`) are presented as graphics.
- Open action labels must describe the selected content: live transcript, accurate transcript, or documents.
- Preserve existing API endpoints and document generation behavior.

## Review Focus

- Documents containing only technical files show a clear empty state rather than unusable links.
- Diagram generation failure leaves source `.mmd` available internally without breaking other documents.
- Selecting different menus updates the action label immediately.
- A meeting with no transcript but generated documents opens the document viewer.

---

### Task 1: Backend all-documents diagram generation

**Files:**
- Modify: `server.py` finalize worker call
- Test: existing Python test suite / static inspection

**Interfaces:**
- Existing `finalize(meeting_dir, cfg, render_diagrams)` remains unchanged.
- All-documents API invokes the worker with diagram rendering enabled.

- [ ] Change the all-documents worker invocation so `/api/finalize` generates diagrams along with PDF/DOCX/Markdown.
- [ ] Keep single-document generation behavior unchanged.
- [ ] Run the project test suite and compile checks.

### Task 2: Frontend document filtering and dynamic action label

**Files:**
- Modify: `static/display.html`
- Test: frontend source assertions plus project test suite

**Interfaces:**
- Filter `info.documents` to user-facing extensions: `pdf`, `docx`, `md`, `svg`, `png`.
- Use dynamic labels: `Open live transcript`, `Open accurate transcript`, `Open documents`.

- [ ] Add a helper to filter technical files from the document list.
- [ ] Render SVG/PNG links as image previews or graphics while retaining links for opening/downloading.
- [ ] Update the button label whenever selection changes and on initial state.
- [ ] Verify the relevant strings and behavior are present, then run all tests and `git diff --check`.
