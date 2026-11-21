"""Generazione dei documenti finali a partire da riunione.json + trascrizione.

Nessuna chiamata LLM: tutto nasce dal JSON gia' validato, cosi' e' possibile
rigenerare i file con nuovi template senza rifare l'IA.
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
    lines = [f"# {result.get('titolo') or 'Verbale di riunione'}", ""]
    if result.get("data"):
        lines += [f"**Data:** {result['data']}", ""]
    if result.get("partecipanti"):
        lines += ["**Partecipanti:** " + ", ".join(result["partecipanti"]), ""]

    lines += ["## Riassunto", "", str(result.get("riassunto") or "(non disponibile)"), ""]

    if result.get("decisioni"):
        lines += ["## Decisioni", ""]
        lines += [f"- {item}" for item in result["decisioni"]]
        lines += [""]

    if result.get("azioni"):
        lines += ["## Azioni", "", "| Cosa | Chi | Scadenza |", "|---|---|---|"]
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
        lines += ["## Argomenti", ""]
        for topic in result["argomenti"]:
            if isinstance(topic, dict):
                lines.append(f"### {topic.get('titolo', '')}")
                for point in topic.get("punti", []):
                    lines.append(f"- {point}")
            else:
                lines.append(f"- {topic}")
            lines += [""]

    if result.get("mindmap_mermaid"):
        lines += ["## Mappa mentale", "", "```mermaid", result["mindmap_mermaid"], "```", ""]
    if result.get("flusso_mermaid"):
        lines += ["## Schema / flusso", "", "```mermaid", result["flusso_mermaid"], "```", ""]

    if transcript:
        lines += ["## Trascrizione completa", "", transcript, ""]
    return "\n".join(lines)


def render_pdf(result: dict[str, Any], transcript: str, out_dir: Path, root: Path) -> Path:
    from weasyprint import HTML

    env = Environment(
        loader=FileSystemLoader(root / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    html = env.get_template("riunione.html").render(
        titolo=result.get("titolo") or "Verbale di riunione",
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
    doc.add_heading(result.get("titolo") or "Verbale di riunione", 0)
    if result.get("data"):
        doc.add_paragraph(f"Data: {result['data']}")
    if result.get("partecipanti"):
        doc.add_paragraph("Partecipanti: " + ", ".join(result["partecipanti"]))

    doc.add_heading("Riassunto", level=1)
    doc.add_paragraph(str(result.get("riassunto") or "(non disponibile)"))

    if result.get("decisioni"):
        doc.add_heading("Decisioni", level=1)
        for item in result["decisioni"]:
            doc.add_paragraph(str(item), style="List Bullet")

    if result.get("azioni"):
        doc.add_heading("Azioni", level=1)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        for cell, label in zip(table.rows[0].cells, ["Cosa", "Chi", "Scadenza"]):
            cell.text = label
        for action in result["azioni"]:
            cells = table.add_row().cells
            if isinstance(action, dict):
                for cell, key in zip(cells, ["cosa", "chi", "scadenza"]):
                    cell.text = str(action.get(key, ""))
            else:
                cells[0].text = str(action)

    if result.get("argomenti"):
        doc.add_heading("Argomenti", level=1)
        for topic in result["argomenti"]:
            if isinstance(topic, dict):
                doc.add_heading(str(topic.get("titolo", "")), level=2)
                for point in topic.get("punti", []):
                    doc.add_paragraph(str(point), style="List Bullet")
            else:
                doc.add_paragraph(str(topic), style="List Bullet")

    if result.get("mindmap_mermaid"):
        doc.add_heading("Mappa mentale (codice Mermaid)", level=1)
        doc.add_paragraph(str(result["mindmap_mermaid"]))
    if result.get("flusso_mermaid"):
        doc.add_heading("Schema / flusso (codice Mermaid)", level=1)
        doc.add_paragraph(str(result["flusso_mermaid"]))

    if transcript:
        doc.add_page_break()
        doc.add_heading("Trascrizione completa", level=1)
        doc.add_paragraph(transcript)

    path = out_dir / "verbale.docx"
    doc.save(str(path))
    return path


def render_mermaid(result: dict[str, Any], out_dir: Path, enabled: bool = True) -> list[Path]:
    """Scrive sempre i .mmd; prova a renderizzare SVG/PNG con mermaid-cli via npx."""
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
                # Il .mmd resta: si puo' renderizzare dopo con mermaid-cli o in un editor.
                break
    return paths


def finalize(meeting_dir: Path, cfg: dict[str, Any], render_diagrams: bool = True) -> dict[str, Any]:
    meeting_dir = Path(meeting_dir)
    result_path = meeting_dir / "riunione.json"
    if not result_path.exists():
        raise FileNotFoundError(f"manca {result_path}")

    result: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    transcript_path = meeting_dir / "trascrizione.txt"
    transcript = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""

    out_dir = meeting_dir / "documenti"
    out_dir.mkdir(parents=True, exist_ok=True)
    root = Path(cfg["_root"])

    markdown = meeting_markdown(result, transcript)
    md_path = out_dir / "verbale.md"
    md_path.write_text(markdown, encoding="utf-8")

    created = [md_path, render_pdf(result, transcript, out_dir, root), render_docx(result, transcript, out_dir)]
    created += render_mermaid(result, out_dir, render_diagrams)

    manifest = {"documenti": [str(p.relative_to(meeting_dir)) for p in created if p.exists()]}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera i documenti finali di una riunione")
    parser.add_argument("--meeting", required=True, help="cartella output/<id-riunione>")
    parser.add_argument("--no-diagrams", action="store_true", help="scrive solo i .mmd, senza npx")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    manifest = finalize(Path(args.meeting), load_config(args.config), not args.no_diagrams)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
