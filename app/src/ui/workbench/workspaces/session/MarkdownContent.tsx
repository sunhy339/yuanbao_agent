import { type ReactNode } from "react";

function normalizeHeadingMarkerSpacing(line: string) {
  const heading = line.match(/^(\s*)(#{1,6}(?:\s+#{1,6})*)\s+(.+)$/);
  if (!heading) {
    return line;
  }
  const level = Math.min(6, heading[2].replace(/[^#]/g, "").length);
  return `${heading[1]}${"#".repeat(level)} ${heading[3]}`;
}

function normalizeMarkdownContent(content: string) {
  const lines = content
    .replace(/\r\n/g, "\n")
    .replace(/^(\s*#{1,6})(?=\S)/gm, "$1 ")
    .replace(/([^\n])(\s+#{1,6})(?=\S)/g, "$1\n$2 ")
    .replace(/([^\n])(\s+[-*]\s+)/g, "$1\n$2")
    .replace(/([^\n])(\s+\d+\.\s+)/g, "$1\n$2")
    .replace(/\n{3,}/g, "\n\n")
    .split("\n");

  const normalized: string[] = [];
  for (const rawLine of lines) {
    const line = normalizeHeadingMarkerSpacing(rawLine);
    const trimmed = line.trim();
    const previous = normalized.at(-1)?.trim();
    const nextIsDivider = /^[-*_]{3,}$/.test(trimmed);
    const duplicateTransition =
      nextIsDivider &&
      (previous === trimmed || previous === "---" || /^[-*_]{3,}$/.test(previous ?? ""));

    if (duplicateTransition) {
      continue;
    }

    normalized.push(line);
  }

  return normalized.join("\n").replace(/\n{3,}/g, "\n\n");
}

function isSafeLink(url: string) {
  return /^(https?:|mailto:)/i.test(url);
}

function isSafeImageUrl(url: string) {
  return /^(https?:|data:image\/|blob:|file:)/i.test(url) || url.startsWith("/") || /^[a-zA-Z]:[\\/]/.test(url);
}

function normalizeImageUrl(url: string) {
  return url.trim().replace(/\\/g, "/");
}

function renderInlineMarkdown(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(!\[[^\]]*\]\([^)]+\)|\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(text.slice(cursor, match.index));
    }

    const token = match[0];
    const key = `${keyPrefix}-${match.index}`;
    if (token.startsWith("**") && token.endsWith("**")) {
      nodes.push(<strong key={key}>{renderInlineMarkdown(token.slice(2, -2), `${key}-strong`)}</strong>);
    } else if (token.startsWith("`") && token.endsWith("`")) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("![")) {
      const imageMatch = token.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
      if (imageMatch && isSafeImageUrl(imageMatch[2])) {
        const alt = imageMatch[1] || "image";
        nodes.push(
          <img
            alt={alt}
            className="markdown-image markdown-image-inline"
            key={key}
            loading="lazy"
            src={normalizeImageUrl(imageMatch[2])}
          />,
        );
      } else {
        nodes.push(token);
      }
    } else {
      const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (linkMatch && isSafeLink(linkMatch[2])) {
        nodes.push(
          <a href={linkMatch[2]} key={key} rel="noreferrer" target="_blank">
            {linkMatch[1]}
          </a>,
        );
      } else {
        nodes.push(token);
      }
    }

    cursor = match.index + token.length;
  }

  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }

  return nodes;
}

function isTableDivider(line: string) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function isHorizontalRule(line: string) {
  return /^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line);
}

function parseTableRow(line: string) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isMarkdownBlockStart(line: string) {
  return (
    /^#{1,6}\s+/.test(line) ||
    isHorizontalRule(line) ||
    /^!\[[^\]]*\]\([^)]+\)\s*$/.test(line) ||
    /^[-*]\s+/.test(line) ||
    /^\d+\.\s+/.test(line) ||
    /^```/.test(line) ||
    (line.includes("|") && isTableDivider(line))
  );
}

function joinParagraphLines(lines: string[]) {
  return lines
    .map((line) => line.trim())
    .filter(Boolean)
    .join("\n");
}

export function MarkdownContent({ content }: { content: string }) {
  const lines = normalizeMarkdownContent(content).split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (isHorizontalRule(line)) {
      blocks.push(<hr className="markdown-divider" key={`hr-${index}`} />);
      index += 1;
      continue;
    }

    const fence = line.match(/^```\s*([\w-]+)?\s*$/);
    if (fence) {
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      blocks.push(
        <pre className="markdown-code-block" key={`code-${index}`}>
          <code>{codeLines.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const children = renderInlineMarkdown(heading[2], `heading-${index}`);
      blocks.push(
        level <= 1 ? (
          <h2 key={`h-${index}`}>{children}</h2>
        ) : level === 2 ? (
          <h3 key={`h-${index}`}>{children}</h3>
        ) : (
          <h4 key={`h-${index}`}>{children}</h4>
        ),
      );
      index += 1;
      continue;
    }

    const image = line.match(/^!\[([^\]]*)\]\(([^)]+)\)\s*$/);
    if (image && isSafeImageUrl(image[2])) {
      blocks.push(
        <figure className="markdown-image-frame" key={`image-${index}`}>
          <img alt={image[1] || "image"} className="markdown-image" loading="lazy" src={normalizeImageUrl(image[2])} />
        </figure>,
      );
      index += 1;
      continue;
    }

    if (line.includes("|") && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
      const headers = parseTableRow(line);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(parseTableRow(lines[index]));
        index += 1;
      }
      blocks.push(
        <div className="markdown-table-wrap" key={`table-${index}`}>
          <table>
            <thead>
              <tr>
                {headers.map((header, cellIndex) => (
                  <th key={`${header}-${cellIndex}`}>{renderInlineMarkdown(header, `th-${index}-${cellIndex}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`row-${index}-${rowIndex}`}>
                  {row.map((cell, cellIndex) => (
                    <td key={`cell-${index}-${rowIndex}-${cellIndex}`}>
                      {renderInlineMarkdown(cell, `td-${index}-${rowIndex}-${cellIndex}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (/^[-*]\s+/.test(line) || /^\d+\.\s+/.test(line)) {
      const ordered = /^\d+\.\s+/.test(line);
      const items: string[] = [];
      while (index < lines.length) {
        const currentLine = lines[index];
        const itemPattern = ordered ? /^\d+\.\s+/ : /^[-*]\s+/;
        if (itemPattern.test(currentLine)) {
          items.push(currentLine.replace(itemPattern, ""));
          index += 1;
          continue;
        }

        if (
          currentLine.trim() === "" &&
          index + 1 < lines.length &&
          itemPattern.test(lines[index + 1])
        ) {
          index += 1;
          continue;
        }

        break;
      }
      const ListTag = ordered ? "ol" : "ul";
      blocks.push(
        <ListTag key={`list-${index}`}>
          {items.map((item, itemIndex) => (
            <li key={`${itemIndex}-${item.slice(0, 12)}`}>{renderInlineMarkdown(item, `li-${index}-${itemIndex}`)}</li>
          ))}
        </ListTag>,
      );
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isMarkdownBlockStart(lines[index])) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    blocks.push(<p key={`p-${index}`}>{renderInlineMarkdown(joinParagraphLines(paragraphLines), `p-${index}`)}</p>);
  }

  return <div className="markdown-content">{blocks}</div>;
}
