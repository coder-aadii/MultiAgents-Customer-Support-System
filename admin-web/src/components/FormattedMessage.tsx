import React from "react";

interface FormattedMessageProps {
  content: string;
  className?: string;
}

interface InlineToken {
  type: "text" | "bold" | "italic" | "code" | "link";
  text: string;
  url?: string;
}

function parseInlineTokens(text: string): InlineToken[] {
  const pattern = /(!?\[[^\]]+\]\([^\)]+\)|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_|`[^`]+`|https?:\/\/[^\s]+)/g;
  const tokens: InlineToken[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      tokens.push({ type: "text", text: text.slice(lastIndex, match.index) });
    }
    const raw = match[0];

    if ((raw.startsWith("**") && raw.endsWith("**")) || (raw.startsWith("__") && raw.endsWith("__"))) {
      tokens.push({ type: "bold", text: raw.slice(2, -2) });
    } else if ((raw.startsWith("*") && raw.endsWith("*")) || (raw.startsWith("_") && raw.endsWith("_"))) {
      tokens.push({ type: "italic", text: raw.slice(1, -1) });
    } else if (raw.startsWith("`") && raw.endsWith("`")) {
      tokens.push({ type: "code", text: raw.slice(1, -1) });
    } else if (raw.startsWith("[") && raw.includes("](")) {
      const closingBracket = raw.indexOf("]");
      const linkText = raw.slice(1, closingBracket);
      const url = raw.slice(closingBracket + 2, -1);
      tokens.push({ type: "link", text: linkText, url });
    } else if (raw.startsWith("http")) {
      let rawUrl = raw;
      let trailingPunct = "";
      while (/[.,!?:;)]$/.test(rawUrl)) {
        trailingPunct = rawUrl.slice(-1) + trailingPunct;
        rawUrl = rawUrl.slice(0, -1);
      }
      tokens.push({ type: "link", text: rawUrl, url: rawUrl });
      if (trailingPunct) {
        tokens.push({ type: "text", text: trailingPunct });
      }
    }
    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    tokens.push({ type: "text", text: text.slice(lastIndex) });
  }

  return tokens;
}

function renderInline(text: string, baseKey: string): React.ReactNode {
  const tokens = parseInlineTokens(text);
  return tokens.map((token, idx) => {
    const key = `${baseKey}-token-${idx}`;
    switch (token.type) {
      case "bold":
        return <strong key={key} className="font-semibold">{token.text}</strong>;
      case "italic":
        return <em key={key} className="italic">{token.text}</em>;
      case "code":
        return (
          <code
            key={key}
            className="px-1 py-0.5 rounded bg-black/10 dark:bg-white/10 font-mono text-[0.88em]"
          >
            {token.text}
          </code>
        );
      case "link": {
        const isSafe = token.url && (token.url.startsWith("http://") || token.url.startsWith("https://") || token.url.startsWith("#"));
        if (!isSafe) {
          return <span key={key}>{token.text}</span>;
        }
        return (
          <a
            key={key}
            href={token.url}
            target={token.url?.startsWith("#") ? undefined : "_blank"}
            rel={token.url?.startsWith("#") ? undefined : "noopener noreferrer"}
            className="underline font-medium hover:opacity-80"
          >
            {token.text}
          </a>
        );
      }
      case "text":
      default:
        return <React.Fragment key={key}>{token.text}</React.Fragment>;
    }
  });
}

export const FormattedMessage: React.FC<FormattedMessageProps> = ({ content, className }) => {
  if (!content) return null;

  const lines = content.split("\n");
  const blocks: Array<
    | { type: "paragraph"; text: string }
    | { type: "ul"; items: string[] }
    | { type: "ol"; items: string[] }
    | { type: "spacer" }
  > = [];

  let currentUl: string[] | null = null;
  let currentOl: string[] | null = null;

  for (const line of lines) {
    const ulMatch = line.match(/^\s*[-*•]\s+(.*)$/);
    const olMatch = line.match(/^\s*(\d+)\.\s+(.*)$/);

    if (ulMatch) {
      currentOl = null;
      if (!currentUl) {
        currentUl = [];
        blocks.push({ type: "ul", items: currentUl });
      }
      currentUl.push(ulMatch[1]);
    } else if (olMatch) {
      currentUl = null;
      if (!currentOl) {
        currentOl = [];
        blocks.push({ type: "ol", items: currentOl });
      }
      currentOl.push(olMatch[2]);
    } else {
      currentUl = null;
      currentOl = null;
      if (line.trim() === "") {
        blocks.push({ type: "spacer" });
      } else {
        blocks.push({ type: "paragraph", text: line });
      }
    }
  }

  return (
    <div className={className}>
      {blocks.map((block, idx) => {
        const key = `block-${idx}`;
        if (block.type === "spacer") {
          return <div key={key} className="h-1.5" />;
        }
        if (block.type === "ul") {
          return (
            <ul key={key} className="list-disc pl-5 my-1 space-y-0.5">
              {block.items.map((item, itemIdx) => (
                <li key={`${key}-item-${itemIdx}`}>
                  {renderInline(item, `${key}-item-${itemIdx}`)}
                </li>
              ))}
            </ul>
          );
        }
        if (block.type === "ol") {
          return (
            <ol key={key} className="list-decimal pl-5 my-1 space-y-0.5">
              {block.items.map((item, itemIdx) => (
                <li key={`${key}-item-${itemIdx}`}>
                  {renderInline(item, `${key}-item-${itemIdx}`)}
                </li>
              ))}
            </ol>
          );
        }
        return (
          <p key={key} className="my-0.5 leading-relaxed">
            {renderInline(block.text, key)}
          </p>
        );
      })}
    </div>
  );
};

