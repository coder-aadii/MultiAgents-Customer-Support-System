import { useState, useEffect, useRef } from "react";

interface WidgetConfig {
  apiBaseUrl: string;
  position?: "bottom-right" | "bottom-left";
  accentColor?: string;
  title?: string;
}

interface Message {
  id: string;
  sender_type: "visitor" | "assistant" | "staff" | "system";
  content: string;
  created_at: string;
  metadata?: {
    source_ids?: string[];
    confidence?: number;
    handoff_reason?: string;
  };
}

export default function Widget({ config }: { config: WidgetConfig }) {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [_visitorId, setVisitorId] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const accentColor = config.accentColor || "#2563eb";
  const title = config.title || "MCSS Support";
  const apiBaseUrl = config.apiBaseUrl;

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const createSession = async () => {
    try {
      const storedSessionToken = localStorage.getItem("mcss_visitor_session_token");
      const response = await fetch(`${apiBaseUrl}/widget/sessions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(storedSessionToken ? { "X-MCSS-Visitor-Session": storedSessionToken } : {}),
        },
        body: JSON.stringify({}),
      });

      if (!response.ok) throw new Error("Failed to create session");

      const data = await response.json();
      setVisitorId(data.visitor_id);
      setConversationId(data.conversation_id);
      setToken(data.token);

      if (data.token) {
        localStorage.setItem("mcss_visitor_session_token", data.token);
      }

      if (data.welcome_message) {
        setMessages([
          {
            id: "welcome",
            sender_type: "assistant",
            content: data.welcome_message,
            created_at: new Date().toISOString(),
          },
        ]);
      }
    } catch (error) {
      console.error("Failed to create widget session:", error);
    }
  };

  const fetchMessages = async () => {
    if (!token || !conversationId) return;

    try {
      const response = await fetch(`${apiBaseUrl}/widget/messages?limit=50`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      if (response.ok) {
        const data = await response.json();
        setMessages(data.messages || []);
      }
    } catch (error) {
      console.error("Failed to fetch messages:", error);
    }
  };

  const sendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputValue.trim() || !token || loading) return;

    const content = inputValue.trim();
    setInputValue("");
    setLoading(true);

    try {
      await fetch(`${apiBaseUrl}/widget/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ content }),
      });
      await fetchMessages();
    } catch (error) {
      console.error("Failed to send message:", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    createSession();
  }, []);

  useEffect(() => {
    if (!isOpen || !token) return;

    const interval = setInterval(() => {
      if (document.visibilityState === "visible") {
        fetchMessages();
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [isOpen, token]);

  const toggleOpen = () => {
    setIsOpen(!isOpen);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(e);
    }
  };

  if (!isOpen) {
    return (
      <button
        onClick={toggleOpen}
        className="fixed bottom-5 right-5 z-50 w-14 h-14 rounded-full shadow-lg flex items-center justify-center"
        style={{ backgroundColor: accentColor }}
        aria-label={title}
        aria-expanded="false"
      >
        <svg className="w-7 h-7 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h12M8 12l4-4m-4 4l4 4m-10 3v-1a1 1 0 011-1h13a1 1 0 011 1v3a1 1 0 01-1 1H9a1 1 0 01-1-1z" />
        </svg>
      </button>
    );
  }

  return (
    <div
      className="fixed bottom-5 right-5 z-50 w-full max-w-sm md:max-w-[360px] h-[520px] bg-white rounded-xl shadow-2xl border border-gray-200 flex flex-col overflow-hidden"
      style={{ maxHeight: "100vh", width: window.innerWidth < 480 ? "calc(100vw - 20px)" : "360px" }}
      role="dialog"
      aria-label={title}
    >
      <header
        className="flex items-center justify-between px-4 py-3 border-b border-gray-200"
        style={{ backgroundColor: accentColor }}
      >
        <h2 className="text-white font-semibold">{title}</h2>
        <button
          onClick={toggleOpen}
          className="text-white hover:text-gray-200 p-1 rounded"
          aria-label="Close chat"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </header>

      <div className="flex-1 overflow-y-auto p-4 space-y-4" role="log" aria-live="polite">
        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex ${message.sender_type === "visitor" ? "justify-end" : "justify-start"}`}
          >
            <div
              className="max-w-[75%] px-4 py-2 rounded-2xl"
              style={{
                backgroundColor:
                  message.sender_type === "visitor"
                    ? accentColor
                    : message.sender_type === "assistant"
                    ? "#f3f4f6"
                    : message.sender_type === "staff"
                    ? "#e0e7ff"
                    : "#fef3c7",
                color: message.sender_type === "visitor" ? "white" : "inherit",
                borderRadius:
                  message.sender_type === "visitor"
                    ? "1.5rem 1.5rem 0 1.5rem"
                    : "1.5rem 1.5rem 1.5rem 0",
              }}
            >
              <p className="whitespace-pre-wrap text-sm">{message.content}</p>
              {message.sender_type === "assistant" && message.metadata?.source_ids && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {message.metadata.source_ids.map((_sourceId, idx) => (
                    <span
                      key={idx}
                      className="px-2 py-0.5 text-xs rounded bg-gray-200 text-gray-700"
                    >
                      Source {idx + 1}
                    </span>
                  ))}
                </div>
              )}
              {message.sender_type === "assistant" && message.metadata?.confidence !== undefined && (
                <div className="mt-1 text-xs opacity-70">
                  Confidence: {(message.metadata.confidence * 100).toFixed(0)}%
                </div>
              )}
              <div className="mt-1 text-xs opacity-60 text-right">
                {new Date(message.created_at).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </div>
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {messages.some((m) => m.metadata?.handoff_reason) && (
        <div className="px-4 py-2 bg-amber-50 border-t border-amber-200">
          <p className="text-sm text-amber-800 text-center">
            A team member will reply here
          </p>
        </div>
      )}

      <form onSubmit={sendMessage} className="p-4 border-t border-gray-200">
        <div className="flex gap-2">
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message..."
            className="flex-1 px-4 py-2 border border-gray-300 rounded-full text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
            disabled={loading}
            aria-label="Message"
          />
          <button
            type="submit"
            disabled={loading || !inputValue.trim()}
            className="px-4 py-2 rounded-full text-white font-medium text-sm"
            style={{ backgroundColor: accentColor }}
          >
            {loading ? "Sending..." : "Send"}
          </button>
        </div>
      </form>
    </div>
  );
}
