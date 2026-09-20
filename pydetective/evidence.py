"""
evidence.py
-----------
Evidence registry for the active case. Reads the `evidence_files` manifest
out of case.json, scans the case directory to confirm those files actually
exist on disk, and lets the player list / inspect them from the CLI.

Stage 1 scope:
    - List evidence (with a "discovered" flag).
    - Read a single evidence file's raw contents.
    - Track which files the player has opened this session.

Stage 4.5 scope (added):
    - peek(): a non-mutating read, used by partner.build_partner_context()
      to re-fetch already-discovered evidence content for Ravi's grounding
      context without touching discovery state (read() delegates to it).
"""

import csv
from pathlib import Path


class EvidenceRegistry:
    """
    Wraps the evidence manifest for one case and the player's discovery
    progress. Instantiated once per GameState (see game.py).
    """

    def __init__(self, case_dir: Path, manifest: list[dict]):
        """
        case_dir : folder containing the case's evidence files, e.g.
                   cases/CASE_001/evidence/
        manifest : list of {"filename": ..., "description": ...} dicts,
                   taken from case.json["evidence_files"].
        """
        self.case_dir = case_dir
        self.manifest = manifest
        self.discovered: set[str] = set()

    def list_entries(self) -> list[dict]:
        """
        Return the manifest enriched with on-disk existence and discovery
        status, e.g. for rendering a numbered list in the CLI.
        """
        entries = []
        for item in self.manifest:
            filename = item["filename"]
            path = self.case_dir / filename
            entries.append(
                {
                    "filename": filename,
                    "description": item.get("description", ""),
                    "exists": path.exists(),
                    "discovered": filename in self.discovered,
                }
            )
        return entries

    def peek(self, filename: str) -> str:
        """
        Return an evidence file's rendered content WITHOUT marking it as
        discovered -- for internal use (e.g. partner.build_partner_context
        re-fetching content of files already discovered) where mutating
        discovery state or triggering a "you found something!" side effect
        would be wrong. Raises the same exceptions as read().
        """
        valid_names = {item["filename"] for item in self.manifest}
        if filename not in valid_names:
            raise ValueError(f"'{filename}' is not part of this case's evidence.")

        path = self.case_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Evidence file missing on disk: {path}")

        if path.suffix.lower() == ".csv":
            return self._render_csv(path)
        return path.read_text(encoding="utf-8")

    def read(self, filename: str) -> str:
        """
        Return the raw text contents of an evidence file and mark it as
        discovered. Raises FileNotFoundError if it's missing on disk, and
        ValueError if it isn't part of this case's manifest at all (so the
        player can't read arbitrary files off disk).
        """
        content = self.peek(filename)
        self.discovered.add(filename)
        return content

    @staticmethod
    def _render_csv(path: Path) -> str:
        """Render a .csv evidence file as an aligned plain-text table."""
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if not rows:
            return "(empty file)"

        widths = [max(len(cell) for cell in col) for col in zip(*rows)]
        lines = []
        for i, row in enumerate(rows):
            line = " | ".join(cell.ljust(widths[j]) for j, cell in enumerate(row))
            lines.append(line)
            if i == 0:
                lines.append("-+-".join("-" * w for w in widths))
        return "\n".join(lines)

    def discovered_count(self) -> int:
        return len(self.discovered)

    def total_count(self) -> int:
        return len(self.manifest)
