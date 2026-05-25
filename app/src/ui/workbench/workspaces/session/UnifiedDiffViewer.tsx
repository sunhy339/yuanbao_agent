type DiffLineType = "add" | "remove" | "context" | "hunk" | "meta";

interface ParsedDiffLine {
  type: DiffLineType;
  content: string;
  oldLine?: number;
  newLine?: number;
}

export interface ParsedDiffFile {
  key: string;
  path: string;
  additions: number;
  deletions: number;
  lines: ParsedDiffLine[];
}

function normalizeDiffPath(path: string) {
  return path.replace(/^a\//, "").replace(/^b\//, "").trim() || "diff";
}

export function parseUnifiedDiff(diffText: string): ParsedDiffFile[] {
  const files: ParsedDiffFile[] = [];
  let current: ParsedDiffFile | null = null;
  let oldLine = 0;
  let newLine = 0;

  diffText.replace(/\r\n/g, "\n").split("\n").forEach((line, index) => {
    if (line.startsWith("diff --git ")) {
      const parts = line.split(/\s+/);
      const path = normalizeDiffPath(parts[3] || parts[2] || `file-${files.length + 1}`);
      current = {
        key: `${path}-${index}`,
        path,
        additions: 0,
        deletions: 0,
        lines: [{ type: "meta", content: line }],
      };
      files.push(current);
      oldLine = 0;
      newLine = 0;
      return;
    }

    if (!current) {
      current = {
        key: `diff-${index}`,
        path: "diff",
        additions: 0,
        deletions: 0,
        lines: [],
      };
      files.push(current);
    }

    if (line.startsWith("@@")) {
      const match = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      oldLine = match ? Number(match[1]) : 0;
      newLine = match ? Number(match[2]) : 0;
      current.lines.push({ type: "hunk", content: line });
      return;
    }

    if (line.startsWith("+++") || line.startsWith("---")) {
      current.lines.push({ type: "meta", content: line });
      return;
    }

    if (line.startsWith("+")) {
      current.additions += 1;
      current.lines.push({ type: "add", content: line.slice(1), newLine });
      newLine += 1;
      return;
    }

    if (line.startsWith("-")) {
      current.deletions += 1;
      current.lines.push({ type: "remove", content: line.slice(1), oldLine });
      oldLine += 1;
      return;
    }

    if (line.startsWith(" ") || line === "") {
      current.lines.push({ type: "context", content: line.startsWith(" ") ? line.slice(1) : line, oldLine, newLine });
      if (oldLine) oldLine += 1;
      if (newLine) newLine += 1;
      return;
    }

    current.lines.push({ type: "meta", content: line });
  });

  return files.filter((file) => file.lines.length > 0);
}

export function UnifiedDiffViewer({ diffText }: { diffText: string }) {
  const files = parseUnifiedDiff(diffText);
  if (!files.length) {
    return <p className="session-tool-muted">还没有可展示的差异内容。</p>;
  }

  return (
    <div className="session-diff-viewer" aria-label="真实差异">
      {files.slice(0, 24).map((file) => (
        <article className="session-diff-file" key={file.key}>
          <header>
            <strong title={file.path}>{file.path}</strong>
            <span>
              +{file.additions} -{file.deletions}
            </span>
          </header>
          <ol className="session-diff-lines">
            {file.lines.slice(0, 1000).map((line, index) => (
              <li className={`session-diff-line session-diff-line-${line.type}`} key={`${file.key}:${index}`}>
                <span className="session-diff-line-old">{line.oldLine ?? ""}</span>
                <span className="session-diff-line-new">{line.newLine ?? ""}</span>
                <code>{line.content || " "}</code>
              </li>
            ))}
          </ol>
        </article>
      ))}
    </div>
  );
}
