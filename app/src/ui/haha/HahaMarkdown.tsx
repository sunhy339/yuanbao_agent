import { memo } from "react";

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderInline(text: string) {
  const escaped = escapeHtml(text);
  return escaped
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}

function detectLanguage(info: string) {
  return info.trim().split(/\s+/)[0] || "text";
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  return (
    <figure className="haha-code-block">
      <figcaption>
        <span>{language}</span>
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard?.writeText(code);
          }}
        >
          复制
        </button>
      </figcaption>
      <pre><code>{code}</code></pre>
    </figure>
  );
}

function flushParagraph(lines: string[], nodes: JSX.Element[]) {
  if (!lines.length) return;
  const text = lines.join("\n");
  nodes.push(
    <p
      key={`p-${nodes.length}`}
      dangerouslySetInnerHTML={{ __html: renderInline(text) }}
    />,
  );
  lines.length = 0;
}

export const HahaMarkdown = memo(function HahaMarkdown({ content }: { content: string }) {
  const nodes: JSX.Element[] = [];
  const paragraph: string[] = [];
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  let index = 0;

  while (index < lines.length) {
    const line = lines[index] ?? "";
    const fence = /^```(.*)$/.exec(line.trim());
    if (fence) {
      flushParagraph(paragraph, nodes);
      const language = detectLanguage(fence[1] ?? "");
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
      const level = Math.min(heading[1].length + 1, 4);
      const Tag = `h${level}` as keyof JSX.IntrinsicElements;
      nodes.push(
        <Tag key={`h-${nodes.length}`} dangerouslySetInnerHTML={{ __html: renderInline(heading[2]) }} />,
      );
      index += 1;
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
            <li key={`${itemIndex}:${item}`} dangerouslySetInnerHTML={{ __html: renderInline(item) }} />
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
            <li key={`${itemIndex}:${item}`} dangerouslySetInnerHTML={{ __html: renderInline(item) }} />
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
  return <div className="haha-markdown">{nodes}</div>;
});
