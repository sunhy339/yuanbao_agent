import { memo } from "react";

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

function languageFromFence(info: string) {
  return info.trim().split(/\s+/)[0] || "text";
}

const keywordPattern =
  /\b(import|from|class|def|return|if|elif|else|for|while|try|except|finally|with|as|const|let|var|function|type|interface|export|async|await|new|public|private|protected|static|true|false|null|None|and|or|not)\b/g;

function highlightLine(line: string) {
  const tokens: JSX.Element[] = [];
  const pattern = /(#.*$|\/\/.*$|"(?:\\.|[^"])*"|'(?:\\.|[^'])*'|\b\d+(?:\.\d+)?\b|\b(?:import|from|class|def|return|if|elif|else|for|while|try|except|finally|with|as|const|let|var|function|type|interface|export|async|await|new|public|private|protected|static|true|false|null|None|and|or|not)\b)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(line))) {
    if (match.index > last) {
      tokens.push(<span key={`t-${last}`}>{line.slice(last, match.index)}</span>);
    }
    const value = match[0];
    const cls = value.startsWith("#") || value.startsWith("//")
      ? "hc-code-comment"
      : value.startsWith("'") || value.startsWith('"')
        ? "hc-code-string"
        : /^\d/.test(value)
          ? "hc-code-number"
          : keywordPattern.test(value)
            ? "hc-code-keyword"
            : "hc-code-keyword";
    keywordPattern.lastIndex = 0;
    tokens.push(<span className={cls} key={`m-${match.index}`}>{value}</span>);
    last = match.index + value.length;
  }
  if (last < line.length) {
    tokens.push(<span key={`e-${last}`}>{line.slice(last)}</span>);
  }
  return tokens.length ? tokens : " ";
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  const lines = code.replace(/\r\n/g, "\n").split("\n");
  return (
    <figure className="hc-code">
      <figcaption>
        <span>{language}</span>
        <button type="button" onClick={() => void navigator.clipboard?.writeText(code)}>复制</button>
      </figcaption>
      <ol>
        {lines.map((line, index) => (
          <li key={`${index}:${line.slice(0, 24)}`}>
            <span className="hc-code-line-no">{index + 1}</span>
            <code>{highlightLine(line)}</code>
          </li>
        ))}
      </ol>
    </figure>
  );
}

function flushParagraph(lines: string[], nodes: JSX.Element[]) {
  if (!lines.length) return;
  const text = lines.join("\n");
  nodes.push(<p key={`p-${nodes.length}`} dangerouslySetInnerHTML={{ __html: inlineHtml(text) }} />);
  lines.length = 0;
}

export const CleanMarkdown = memo(function CleanMarkdown({ content }: { content: string }) {
  const nodes: JSX.Element[] = [];
  const paragraph: string[] = [];
  const lines = content.replace(/\r\n/g, "\n").split("\n");
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

    const heading = /^(#{1,4})\s+(.+)$/.exec(line.trim());
    if (heading) {
      flushParagraph(paragraph, nodes);
      const Tag = `h${Math.min(heading[1].length + 1, 4)}` as keyof JSX.IntrinsicElements;
      nodes.push(<Tag key={`h-${nodes.length}`} dangerouslySetInnerHTML={{ __html: inlineHtml(heading[2]) }} />);
      index += 1;
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

    const bullet = /^\s*[-*]\s+(.+)$/.exec(line);
    if (bullet) {
      flushParagraph(paragraph, nodes);
      const items: string[] = [];
      while (index < lines.length) {
        const item = /^\s*[-*]\s+(.+)$/.exec(lines[index] ?? "");
        if (!item) break;
        items.push(item[1]);
        index += 1;
      }
      nodes.push(
        <ul key={`ul-${nodes.length}`}>
          {items.map((item, itemIndex) => (
            <li key={`${itemIndex}:${item}`} dangerouslySetInnerHTML={{ __html: inlineHtml(item) }} />
          ))}
        </ul>,
      );
      continue;
    }

    const ordered = /^\s*\d+\.\s+(.+)$/.exec(line);
    if (ordered) {
      flushParagraph(paragraph, nodes);
      const items: string[] = [];
      while (index < lines.length) {
        const item = /^\s*\d+\.\s+(.+)$/.exec(lines[index] ?? "");
        if (!item) break;
        items.push(item[1]);
        index += 1;
      }
      nodes.push(
        <ol key={`ol-${nodes.length}`}>
          {items.map((item, itemIndex) => (
            <li key={`${itemIndex}:${item}`} dangerouslySetInnerHTML={{ __html: inlineHtml(item) }} />
          ))}
        </ol>,
      );
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
