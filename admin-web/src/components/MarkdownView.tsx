import React from "react";

interface MarkdownViewProps {
  content: string;
  className?: string;
  compact?: boolean;
}

function renderInline(text: string): React.ReactNode[] {
  const tokens: React.ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      tokens.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    const key = `${match.index}-${token}`;

    if (token.startsWith("`") && token.endsWith("`")) {
      tokens.push(
        <code
          key={key}
          className="font-mono text-xs bg-slate-100 text-purple-700 px-1 py-0.5 rounded border border-slate-200 font-medium"
        >
          {token.slice(1, -1)}
        </code>
      );
    } else if (token.startsWith("**") && token.endsWith("**")) {
      tokens.push(
        <strong key={key} className="font-semibold text-gray-900">
          {renderInline(token.slice(2, -2))}
        </strong>
      );
    } else if (token.startsWith("*") && token.endsWith("*")) {
      tokens.push(
        <em key={key} className="italic text-gray-800">
          {renderInline(token.slice(1, -1))}
        </em>
      );
    } else if (token.startsWith("[") && token.includes("](")) {
      const linkMatch = token.match(/\[([^\]]+)\]\(([^)]+)\)/);
      if (linkMatch) {
        tokens.push(
          <a
            key={key}
            href={linkMatch[2]}
            target="_blank"
            rel="noopener noreferrer"
            className="text-indigo-600 underline hover:text-indigo-800 font-medium"
          >
            {linkMatch[1]}
          </a>
        );
      } else {
        tokens.push(token);
      }
    } else {
      tokens.push(token);
    }
    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    tokens.push(text.slice(lastIndex));
  }

  return tokens;
}

interface Block {
  type: "h1" | "h2" | "h3" | "h4" | "blockquote" | "ul" | "ol" | "code" | "hr" | "p";
  content?: string;
  items?: string[];
  lang?: string;
}

function parseBlocks(markdown: string): Block[] {
  const lines = markdown.split(/\r?\n/);
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Blank line
    if (!line.trim()) {
      i++;
      continue;
    }

    // Fenced code block
    if (line.trim().startsWith("```")) {
      const lang = line.trim().slice(3).trim();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++; // consume closing ```
      blocks.push({
        type: "code",
        content: codeLines.join("\n"),
        lang,
      });
      continue;
    }

    // Horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
      blocks.push({ type: "hr" });
      i++;
      continue;
    }

    // Headings
    if (line.startsWith("# ")) {
      blocks.push({ type: "h1", content: line.slice(2).trim() });
      i++;
      continue;
    }
    if (line.startsWith("## ")) {
      blocks.push({ type: "h2", content: line.slice(3).trim() });
      i++;
      continue;
    }
    if (line.startsWith("### ")) {
      blocks.push({ type: "h3", content: line.slice(4).trim() });
      i++;
      continue;
    }
    if (line.startsWith("#### ")) {
      blocks.push({ type: "h4", content: line.slice(5).trim() });
      i++;
      continue;
    }

    // Blockquote (accumulate consecutive > lines)
    if (line.startsWith(">")) {
      const quoteLines: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) {
        quoteLines.push(lines[i].replace(/^>\s?/, ""));
        i++;
      }
      blocks.push({
        type: "blockquote",
        content: quoteLines.join(" "),
      });
      continue;
    }

    // Unordered List (- or *)
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*]\s+/, "").trim());
        i++;
      }
      blocks.push({ type: "ul", items });
      continue;
    }

    // Ordered List (1. )
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s+/, "").trim());
        i++;
      }
      blocks.push({ type: "ol", items });
      continue;
    }

    // Normal paragraph (accumulate until blank line or next block type)
    const pLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].trim().startsWith("```") &&
      !lines[i].startsWith("#") &&
      !lines[i].startsWith(">") &&
      !/^[-*]\s+/.test(lines[i]) &&
      !/^\d+\.\s+/.test(lines[i]) &&
      !/^(-{3,}|\*{3,}|_{3,})$/.test(lines[i].trim())
    ) {
      pLines.push(lines[i].trim());
      i++;
    }
    if (pLines.length > 0) {
      blocks.push({
        type: "p",
        content: pLines.join(" "),
      });
    }
  }

  return blocks;
}

export const MarkdownView: React.FC<MarkdownViewProps> = ({
  content,
  className = "",
  compact = false,
}) => {
  if (!content || !content.trim()) {
    return <span className="text-gray-400 italic text-sm">(Empty content)</span>;
  }

  const blocks = parseBlocks(content);

  return (
    <div
      className={`markdown-view space-y-2.5 text-gray-800 ${
        compact ? "text-xs" : "text-sm"
      } ${className}`}
    >
      {blocks.map((block, idx) => {
        switch (block.type) {
          case "h1":
            return (
              <h1
                key={idx}
                className={`${
                  compact ? "text-sm pb-1 mb-1" : "text-lg pb-1.5 mb-2"
                } font-bold text-gray-900 border-b border-gray-200`}
              >
                {renderInline(block.content || "")}
              </h1>
            );
          case "h2":
            return (
              <h2
                key={idx}
                className={`${
                  compact ? "text-xs mt-2 mb-1" : "text-sm mt-3 mb-1.5"
                } font-bold text-gray-900 flex items-center gap-1.5`}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 inline-block flex-shrink-0" />
                <span>{renderInline(block.content || "")}</span>
              </h2>
            );
          case "h3":
            return (
              <h3
                key={idx}
                className={`${
                  compact ? "text-[11px] mt-1.5" : "text-xs mt-2"
                } font-semibold text-gray-800`}
              >
                {renderInline(block.content || "")}
              </h3>
            );
          case "h4":
            return (
              <h4 key={idx} className="text-[11px] font-semibold text-gray-700 uppercase">
                {renderInline(block.content || "")}
              </h4>
            );
          case "blockquote":
            return (
              <blockquote
                key={idx}
                className={`border-l-3 border-indigo-400 bg-indigo-50/60 rounded-r text-gray-700 italic ${
                  compact ? "p-2 text-[11px]" : "p-3 text-xs leading-relaxed"
                }`}
              >
                {renderInline(block.content || "")}
              </blockquote>
            );
          case "ul":
            return (
              <ul
                key={idx}
                className={`list-disc list-inside space-y-1 text-gray-700 pl-1 ${
                  compact ? "text-xs" : "text-sm"
                }`}
              >
                {block.items?.map((item, itemIdx) => (
                  <li key={itemIdx}>{renderInline(item)}</li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol
                key={idx}
                className={`list-decimal list-inside space-y-1 text-gray-700 pl-1 ${
                  compact ? "text-xs" : "text-sm"
                }`}
              >
                {block.items?.map((item, itemIdx) => (
                  <li key={itemIdx}>{renderInline(item)}</li>
                ))}
              </ol>
            );
          case "code":
            return (
              <pre
                key={idx}
                className="bg-slate-900 text-slate-100 p-3 rounded-md text-xs font-mono overflow-x-auto my-2"
              >
                <code>{block.content}</code>
              </pre>
            );
          case "hr":
            return <hr key={idx} className="my-3 border-gray-200" />;
          case "p":
          default:
            return (
              <p
                key={idx}
                className={`leading-relaxed text-gray-800 ${
                  compact ? "text-xs" : "text-sm"
                }`}
              >
                {renderInline(block.content || "")}
              </p>
            );
        }
      })}
    </div>
  );
};

