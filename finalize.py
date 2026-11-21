"""Generation of final documents from riunione.json + transcript.

No LLM calls: everything comes from the already validated JSON, so it's possible
to regenerate files with new templates without redoing the AI work.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from docx import Document

from config import load_config


def meeting_markdown(result: dict[str, Any], transcript: str) -> str:
    lines = [f"# {result.get('titolo') or 'Meeting minutes'}", ""]
    if result.get("data"):
        lines += [f"**Date:** {result['data']}", ""]
    if result.get("partecipanti"):
        lines += ["**Participants:** " + ", ".join(result["partecipanti"]), ""]

    lines += ["## Summary", "", str(result.get("riassunto") or "(unavailable)"), ""]

    if result.get("decisioni"):
        lines += ["## Decisions", ""]
        lines += [f"- {item}" for item in result["decisioni"]]
        lines += [""]

    if result.get("azioni"):
        lines += ["## Actions", "", "| What | Who | Deadline |", "|---|---|---|"]
        for action in result["azioni"]:
            if isinstance(action, dict):
                cells = [
                    str(action.get("cosa", "")).replace("|", "/"),
                    str(action.get("chi", "")).replace("|", "/"),
                    str(action.get("scadenza", "")).replace("|", "/"),
                ]
                lines.append("| " + " | ".join(cells) + " |")
            else:
                lines.append(f"| {action} | | |")
        lines += [""]

    if result.get("argomenti"):
        lines += ["## Topics", ""]
        for topic in result["argomenti"]:
            if isinstance(topic, dict):
                lines.append(f"### {topic.get('titolo', '')}")
                for point in topic.get("punti", []):
                    lines.append(f"- {point}")
            else:
                lines.append(f"- {topic}")
            lines += [""]

    if result.get("mindmap_mermaid"):
        lines += ["## Mind map", "", "```mermaid", result["mindmap_mermaid"], "```", ""]
    if result.get("flusso_mermaid"):
        lines += ["## Diagram / flow", "", "```mermaid", result["flusso_mermaid"], "```", ""]

    if transcript:
        lines += ["## Full transcript", "", transcript, ""]
    return "\n".join(lines)


def render_pdf(result: dict[str, Any], transcript: str, out_dir: Path, root: Path) -> Path:
    from weasyprint import HTML

    env = Environment(
        loader=FileSystemLoader(root / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    html = env.get_template("riunione.html").render(
        titolo=result.get("titolo") or "Meeting minutes",
        data=result.get("data", ""),
        partecipanti=result.get("partecipanti", []),
        riassunto=result.get("riassunto", ""),
        decisioni=result.get("decisioni", []),
        azioni=result.get("azioni", []),
        argomenti=result.get("argomenti", []),
        trascrizione=transcript,
    )
    path = out_dir / "verbale.pdf"
    HTML(string=html, base_url=str(root)).write_pdf(str(path))
    return path


def render_docx(result: dict[str, Any], transcript: str, out_dir: Path) -> Path:
    doc = Document()
    doc.add_heading(result.get("titolo") or "Meeting minutes", 0)
    if result.get("data"):
        doc.add_paragraph(f"Date: {result['data']}")
    if result.get("partecipanti"):
        doc.add_paragraph("Participants: " + ", ".join(result["partecipanti"]))

    doc.add_heading("Summary", level=1)
    doc.add_paragraph(str(result.get("riassunto") or "(unavailable)"))

    if result.get("decisioni"):
        doc.add_heading("Decisions", level=1)
        for item in result["decisioni"]:
            doc.add_paragraph(str(item), style="List Bullet")

    if result.get("azioni"):
        doc.add_heading("Actions", level=1)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        for cell, label in zip(table.rows[0].cells, ["What", "Who", "Deadline"]):
            cell.text = label
        for action in result["azioni"]:
            cells = table.add_row().cells
            if isinstance(action, dict):
                for cell, key in zip(cells, ["cosa", "chi", "scadenza"]):
                    cell.text = str(action.get(key, ""))
            else:
                cells[0].text = str(action)

    if result.get("argomenti"):
        doc.add_heading("Topics", level=1)
        for topic in result["argomenti"]:
            if isinstance(topic, dict):
                doc.add_heading(str(topic.get("titolo", "")), level=2)
                for point in topic.get("punti", []):
                    doc.add_paragraph(str(point), style="List Bullet")
            else:
                doc.add_paragraph(str(topic), style="List Bullet")

    if result.get("mindmap_mermaid"):
        doc.add_heading("Mind map (Mermaid code)", level=1)
        doc.add_paragraph(str(result["mindmap_mermaid"]))
    if result.get("flusso_mermaid"):
        doc.add_heading("Diagram / flow (Mermaid code)", level=1)
        doc.add_paragraph(str(result["flusso_mermaid"]))

    if transcript:
        doc.add_page_break()
        doc.add_heading("Full transcript", level=1)
        doc.add_paragraph(transcript)

    path = out_dir / "verbale.docx"
    doc.save(str(path))
    return path


def render_mermaid(result: dict[str, Any], out_dir: Path, enabled: bool = True) -> list[Path]:
    """Always writes the .mmd files; tries to render SVG/PNG with mermaid-cli via npx."""
    sources = {
        "mappa-mentale": result.get("mindmap_mermaid", ""),
        "flusso": result.get("flusso_mermaid", ""),
    }
    paths: list[Path] = []
    for name, source in sources.items():
        if not source:
            continue
        mmd = out_dir / f"{name}.mmd"
        mmd.write_text(source.strip() + "\n", encoding="utf-8")
        paths.append(mmd)
        if not enabled or shutil.which("npx") is None:
            continue
        for ext in ("svg", "png"):
            target = out_dir / f"{name}.{ext}"
            try:
                subprocess.run(
                    [
                        "npx", "--yes", "@mermaid-js/mermaid-cli",
                        "-i", str(mmd), "-o", str(target), "-b", "white",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=180,
                )
                paths.append(target)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                # The .mmd remains: it can be rendered later with mermaid-cli or in an editor.
                break
    return paths


DOCUMENT_KINDS = ("md", "pdf", "docx", "diagrams", "mindmap", "flusso")


def _load_sources(meeting_dir: Path) -> tuple[dict[str, Any], str]:
    result_path = meeting_dir / "riunione.json"
    if not result_path.exists():
        raise FileNotFoundError(f"missing {result_path}")
    result: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    transcript_path = meeting_dir / "trascrizione.txt"
    transcript = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""
    return result, transcript


def _update_manifest(meeting_dir: Path, paths: list[Path]) -> dict[str, Any]:
    """Adds the generated files to the manifest without dropping previous entries."""
    out_dir = meeting_dir / "documenti"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest: dict[str, Any] = {"documenti": []}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, TypeError):
            manifest = {"documenti": []}
    known = set(manifest.get("documenti", []))
    for path in paths:
        if not path.exists():
            continue
        rel = str(path.relative_to(meeting_dir))
        if rel not in known:
            manifest.setdefault("documenti", []).append(rel)
            known.add(rel)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def generate_document(
    meeting_dir: Path,
    cfg: dict[str, Any],
    kind: str,
    render_diagrams: bool = True,
) -> dict[str, Any]:
    """Generates only the requested document type and updates the manifest."""
    meeting_dir = Path(meeting_dir)
    kind = str(kind).strip().lower()
    if kind not in DOCUMENT_KINDS:
        raise ValueError(f"unknown document kind: {kind}")
    result, transcript = _load_sources(meeting_dir)
    out_dir = meeting_dir / "documenti"
    out_dir.mkdir(parents=True, exist_ok=True)
    root = Path(cfg["_root"])

    if kind == "md":
        path = out_dir / "verbale.md"
        path.write_text(meeting_markdown(result, transcript), encoding="utf-8")
        paths = [path]
    elif kind == "pdf":
        paths = [render_pdf(result, transcript, out_dir, root)]
    elif kind == "docx":
        paths = [render_docx(result, transcript, out_dir)]
    elif kind == "diagrams":
        paths = render_mermaid(result, out_dir, render_diagrams)
    else:  # a single diagram: mindmap or flow
        key = "mindmap_mermaid" if kind == "mindmap" else "flusso_mermaid"
        paths = render_mermaid({key: result.get(key, "")}, out_dir, render_diagrams)
    return _update_manifest(meeting_dir, paths)


def finalize(meeting_dir: Path, cfg: dict[str, Any], render_diagrams: bool = True) -> dict[str, Any]:
    """Generates every document type (explicit 'generate all' action only)."""
    manifest: dict[str, Any] = {"documenti": []}
    for kind in ("md", "pdf", "docx", "diagrams"):
        manifest = generate_document(meeting_dir, cfg, kind, render_diagrams=render_diagrams)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Generates the final documents of a meeting")
    parser.add_argument("--meeting", required=True, help="output/<meeting-id> folder")
    parser.add_argument("--no-diagrams", action="store_true", help="only writes the .mmd files, without npx")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    manifest = finalize(Path(args.meeting), load_config(args.config), not args.no_diagrams)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
