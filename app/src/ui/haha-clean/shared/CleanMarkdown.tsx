import { memo, type ReactNode } from "react";
import { CleanMermaid, shouldRenderMermaid } from "./CleanMermaid";

function escapeHtml(value: string) {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inlineHtml(value: string) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}

export const CleanInlineMarkdown = memo(function CleanInlineMarkdown({ content }: { content: string }) {
  return <span dangerouslySetInnerHTML={{ __html: inlineHtml(content) }} />;
});

function isSafeImageUrl(url: string) {
  return /^(https?:|data:image\/|blob:|file:)/i.test(url) || url.startsWith("/") || /^[a-zA-Z]:[\\/]/.test(url);
}

function normalizeImageUrl(url: string) {
  return url.replace(/\\/g, "/");
}

function languageFromFence(info: string) {
  return info.trim().split(/\s+/)[0] || "text";
}

function normalizeHeadingText(value: string) {
  return value.replace(/^#+\s*/, "").replace(/\s*#+$/, "").trim();
}

function normalizeHeadingMarkerSpacing(line: string) {
  const heading = line.match(/^(\s*)(#{1,6}(?:\s+#{1,6})*)\s+(.+)$/);
  if (!heading) {
    return line;
  }
  const level = Math.min(6, heading[2].replace(/[^#]/g, "").length);
  return `${heading[1]}${"#".repeat(level)} ${heading[3]}`;
}

function stripReasoningTags(content: string): string {
  let cleaned = content
    .replace(/<thought(?:[\s\S]*?)>[\s\S]*?<\/thought>/gi, "")
    .replace(/<thinking(?:[\s\S]*?)>[\s\S]*?<\/thinking>/gi, "");

  cleaned = cleaned
    .replace(/<thought(?:[\s\S]*?)>[\s\S]*$/gi, "")
    .replace(/<thinking(?:[\s\S]*?)>[\s\S]*$/gi, "");

  cleaned = cleaned
    .replace(/<(?:t(?:h(?:o(?:u(?:g(?:h(?:t)?)?)?)?)?|i(?:n(?:k(?:i(?:n(?:g)?)?)?)?)?)?)?$/gi, "");

  return cleaned;
}

function normalizeMarkdownContent(content: string) {
  const cleanedContent = stripReasoningTags(content);
  const normalizedLines: string[] = [];
  let inFence = false;

  const lines = cleanedContent
    .replace(/\r\n/g, "\n")
    .replace(/^(\s*#{1,6})(?=\S)/gm, "$1 ")
    .split("\n");

  for (const rawLine of lines) {
    const fence = /^\s*```/.test(rawLine);
    if (fence) {
      normalizedLines.push(rawLine);
      inFence = !inFence;
      continue;
    }

    if (inFence) {
      normalizedLines.push(rawLine);
      continue;
    }

    const isIndentedBlockLine = /^\s+(?:[-*]|\d+\.)\s+/.test(rawLine);
    const blockNormalizedLine = isIndentedBlockLine
      ? rawLine
      : rawLine
      .replace(/([^\n])(\s+#{1,6})(?=\S)/g, "$1\n$2 ")
      .replace(/^(\s*#{1,6}\s+.+?)(\s+[-*]\s+|\s+\d+\.\s+)/gm, "$1\n$2")
      .replace(/([^\n])(\s+[-*]\s+)/g, "$1\n$2")
      .replace(/([^\n])(\s+\d+\.\s+)/g, "$1\n$2");

    blockNormalizedLine
      .split("\n")
      .forEach((line) => normalizedLines.push(normalizeHeadingMarkerSpacing(line)));
  }

  return normalizedLines.join("\n").replace(/\n{3,}/g, "\n\n");
}

const keywords = new Set([
  "as",
  "async",
  "await",
  "break",
  "case",
  "catch",
  "class",
  "const",
  "continue",
  "def",
  "elif",
  "else",
  "except",
  "export",
  "finally",
  "for",
  "from",
  "function",
  "if",
  "import",
  "in",
  "interface",
  "let",
  "new",
  "not",
  "or",
  "private",
  "protected",
  "public",
  "return",
  "static",
  "switch",
  "try",
  "type",
  "var",
  "while",
  "with",
]);

const builtins = new Set([
  "Any",
  "False",
  "None",
  "True",
  "bool",
  "dict",
  "false",
  "int",
  "list",
  "null",
  "number",
  "str",
  "string",
  "true",
  "undefined",
]);

function codeLineClass(line: string) {
  const value = line.trimStart();
  if (value.startsWith("@@")) return "hc-code-line-hunk";
  if (value.startsWith("+") && !value.startsWith("+++")) return "hc-code-line-added";
  if (value.startsWith("-") && !value.startsWith("---")) return "hc-code-line-deleted";
  if (/^(diff --git|index |--- |\+\+\+ )/.test(value)) return "hc-code-line-meta";
  return undefined;
}

function highlightLine(line: string, keyPrefix: string): ReactNode {
  if (line.trimStart().startsWith("@@")) {
    return <span className="hc-code-hunk">{line}</span>;
  }

  const tokens: ReactNode[] = [];
  const pattern =
    /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\/\/.*|#.*|\/\*.*?\*\/|\b\d+(?:\.\d+)?\b|\b[A-Za-z_$][\w$]*\b|[{}[\]().,:;+\-*/%=<>!|&]+)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(line))) {
    if (match.index > last) {
      tokens.push(<span key={`t-${last}`}>{line.slice(last, match.index)}</span>);
    }
    const value = match[0];
    const key = `${keyPrefix}-${match.index}`;
    if (/^(\/\/|#|\/\*)/.test(value)) {
      tokens.push(<span className="hc-code-comment" key={key}>{value}</span>);
    } else if (/^["'`]/.test(value)) {
      const nextNonSpace = line.slice(match.index + value.length).match(/^\s*:/);
      tokens.push(<span className={nextNonSpace ? "hc-code-property" : "hc-code-string"} key={key}>{value}</span>);
    } else if (/^\d/.test(value)) {
      tokens.push(<span className="hc-code-number" key={key}>{value}</span>);
    } else if (keywords.has(value)) {
      tokens.push(<span className="hc-code-keyword" key={key}>{value}</span>);
    } else if (builtins.has(value)) {
      tokens.push(<span className="hc-code-builtin" key={key}>{value}</span>);
    } else if (/^[{}[\]().,:;+\-*/%=<>!|&]+$/.test(value)) {
      tokens.push(<span className="hc-code-operator" key={key}>{value}</span>);
    } else {
      tokens.push(value);
    }
    last = match.index + value.length;
  }
  if (last < line.length) {
    tokens.push(<span key={`e-${last}`}>{line.slice(last)}</span>);
  }
  return tokens.length ? tokens : " ";
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  if (shouldRenderMermaid(language, code)) {
    return <CleanMermaid code={code} />;
  }

  const lines = code.replace(/\r\n/g, "\n").split("\n");
  const label = language === "text" ? "代码" : language;
  return (
    <figure className="hc-code">
      <figcaption>
        <span>{label}</span>
        <button
          type="button"
          aria-label={`复制 ${label} 代码块`}
          onClick={() => void navigator.clipboard?.writeText(code)}
        >
          复制
        </button>
      </figcaption>
      <ol>
        {lines.map((line, index) => (
          <li className={codeLineClass(line)} key={`${index}:${line.slice(0, 24)}`}>
            <span className="hc-code-line-no">{index + 1}</span>
            <code>{highlightLine(line, `${language}-${index}`)}</code>
          </li>
        ))}
      </ol>
    </figure>
  );
}

function TableBlock({ rows }: { rows: string[][] }) {
  if (!rows.length) return null;
  const [head, ...body] = rows;
  return (
    <div className="hc-table-wrap">
      <table>
        <thead>
          <tr>
            {head.map((cell, index) => (
              <th key={`${index}:${cell}`} dangerouslySetInnerHTML={{ __html: inlineHtml(cell.trim()) }} />
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, rowIndex) => (
            <tr key={`${rowIndex}:${row.join("|")}`}>
              {row.map((cell, index) => (
                <td key={`${index}:${cell}`} dangerouslySetInnerHTML={{ __html: inlineHtml(cell.trim()) }} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ImageBlock({ alt, src }: { alt: string; src: string }) {
  return (
    <figure className="hc-markdown-image">
      <img alt={alt || "image"} loading="lazy" src={normalizeImageUrl(src)} />
      {alt ? <figcaption>{alt}</figcaption> : null}
    </figure>
  );
}

function flushParagraph(lines: string[], nodes: JSX.Element[]) {
  if (!lines.length) return;
  const text = lines.join("\n");
  nodes.push(<p key={`p-${nodes.length}`} dangerouslySetInnerHTML={{ __html: inlineHtml(text) }} />);
  lines.length = 0;
}

type MarkdownListItem = {
  id: number;
  indent: number;
  ordered: boolean;
  checked?: boolean;
  text: string;
  children: MarkdownListItem[];
};

function parseListItem(line: string) {
  const bullet = /^(\s*)[-*]\s+(?:\[( |x|X)\]\s+)?(.+)$/.exec(line);
  if (bullet) {
    return {
      indent: bullet[1].replace(/\t/g, "  ").length,
      ordered: false,
      checked: bullet[2] ? bullet[2].toLowerCase() === "x" : undefined,
      text: bullet[3],
    };
  }
  const ordered = /^(\s*)\d+\.\s+(.+)$/.exec(line);
  if (ordered) {
    return {
      indent: ordered[1].replace(/\t/g, "  ").length,
      ordered: true,
      text: ordered[2],
    };
  }
  return null;
}

function collectList(lines: string[], startIndex: number) {
  const root: MarkdownListItem = { id: -1, indent: -1, ordered: false, text: "", children: [] };
  const stack: MarkdownListItem[] = [root];
  let index = startIndex;
  let id = 0;

  while (index < lines.length) {
    const parsed = parseListItem(lines[index] ?? "");
    if (!parsed) break;
    const item: MarkdownListItem = { ...parsed, id, children: [] };
    id += 1;
    while (stack.length > 1 && item.indent <= stack[stack.length - 1].indent) {
      stack.pop();
    }
    stack[stack.length - 1].children.push(item);
    stack.push(item);
    index += 1;
  }

  return { items: root.children, nextIndex: index };
}

function groupListItems(items: MarkdownListItem[]) {
  const groups: Array<{ ordered: boolean; items: MarkdownListItem[] }> = [];
  items.forEach((item) => {
    const last = groups.at(-1);
    if (last && last.ordered === item.ordered) {
      last.items.push(item);
      return;
    }
    groups.push({ ordered: item.ordered, items: [item] });
  });
  return groups;
}

function renderListGroups(items: MarkdownListItem[], keyPrefix: string): JSX.Element[] {
  return groupListItems(items).map((group, groupIndex) => {
    const Tag = group.ordered ? "ol" : "ul";
    return (
      <Tag key={`${keyPrefix}-g-${groupIndex}`}>
        {group.items.map((item) => (
          <li
            key={`${keyPrefix}-i-${item.id}`}
            className={item.checked !== undefined ? "hc-task-item" : undefined}
          >
            {item.checked !== undefined ? <input type="checkbox" checked={item.checked} readOnly /> : null}
            <div className="hc-list-item-body">
              <span dangerouslySetInnerHTML={{ __html: inlineHtml(item.text) }} />
              {item.children.length ? renderListGroups(item.children, `${keyPrefix}-${item.id}`) : null}
            </div>
          </li>
        ))}
      </Tag>
    );
  });
}

export const CleanMarkdown = memo(function CleanMarkdown({ content }: { content: string }) {
  const nodes: JSX.Element[] = [];
  const paragraph: string[] = [];
  const lines = normalizeMarkdownContent(content).split("\n");
  let index = 0;

  while (index < lines.length) {
    const line = lines[index] ?? "";
    const fence = /^```(.*)$/.exec(line.trim());
    if (fence) {
      flushParagraph(paragraph, nodes);
      const language = languageFromFence(fence[1] ?? "");
      index += 1;
      const codeLines: string[] = [];
      while (index < lines.length && !/^```/.test((lines[index] ?? "").trim())) {
        codeLines.push(lines[index] ?? "");
        index += 1;
      }
      if (index < lines.length) index += 1;
      nodes.push(<CodeBlock key={`code-${nodes.length}`} code={codeLines.join("\n")} language={language} />);
      continue;
    }

    const image = /^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$/.exec(line);
    if (image && isSafeImageUrl(image[2])) {
      flushParagraph(paragraph, nodes);
      nodes.push(<ImageBlock key={`img-${nodes.length}`} alt={image[1]} src={image[2]} />);
      index += 1;
      continue;
    }

    const heading = /^(#{1,6})\s*(.+?)\s*$/.exec(line.trim());
    if (heading) {
      flushParagraph(paragraph, nodes);
      const Tag = `h${Math.min(heading[1].length + 1, 4)}` as keyof JSX.IntrinsicElements;
      nodes.push(<Tag key={`h-${nodes.length}`} dangerouslySetInnerHTML={{ __html: inlineHtml(normalizeHeadingText(heading[2])) }} />);
      index += 1;
      continue;
    }

    if (/^\s*\|.+\|\s*$/.test(line) && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1] ?? "")) {
      flushParagraph(paragraph, nodes);
      const rows: string[][] = [];
      rows.push(line.trim().replace(/^\||\|$/g, "").split("|"));
      index += 2;
      while (index < lines.length && /^\s*\|.+\|\s*$/.test(lines[index] ?? "")) {
        rows.push((lines[index] ?? "").trim().replace(/^\||\|$/g, "").split("|"));
        index += 1;
      }
      nodes.push(<TableBlock key={`table-${nodes.length}`} rows={rows} />);
      continue;
    }

    const quote = /^>\s?(.+)$/.exec(line);
    if (quote) {
      flushParagraph(paragraph, nodes);
      const quotes: string[] = [];
      while (index < lines.length) {
        const item = /^>\s?(.+)$/.exec(lines[index] ?? "");
        if (!item) break;
        quotes.push(item[1]);
        index += 1;
      }
      nodes.push(<blockquote key={`q-${nodes.length}`} dangerouslySetInnerHTML={{ __html: inlineHtml(quotes.join("\n")) }} />);
      continue;
    }

    if (parseListItem(line)) {
      flushParagraph(paragraph, nodes);
      const list = collectList(lines, index);
      nodes.push(...renderListGroups(list.items, `list-${nodes.length}`));
      index = list.nextIndex;
      continue;
    }

    if (!line.trim()) {
      flushParagraph(paragraph, nodes);
      index += 1;
      continue;
    }

    paragraph.push(line);
    index += 1;
  }

  flushParagraph(paragraph, nodes);
  return <div className="hc-markdown">{nodes}</div>;
});
