import React, { useState, useEffect, useRef, useCallback } from "react";
import { createWidgetApiClient, type WidgetMessage, type WidgetConversation } from "./api/client";
import { reconcileMessages, PollingController } from "./utils/reconciliation";
import { toSafeErrorMessage } from "./utils/errors";
import { t } from "./utils/i18n";
import { generateUUID } from "./utils/uuid";
import { FormattedMessage } from "./components/FormattedMessage";

export interface WidgetConfig {
  apiBaseUrl: string;
  position?: "bottom-right" | "bottom-left";
  accentColor?: string;
  title?: string;
  customerToken?: string;
}

export default function Widget({ config }: { config: WidgetConfig }) {
  const [isOpen, setIsOpen] = useState(false);
  const [isFullScreen, setIsFullScreen] = useState(false);
  const [messages, setMessages] = useState<WidgetMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [conversation, setConversation] = useState<WidgetConversation | null>(null);
  const [sessionInitialized, setSessionInitialized] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Optional contact details state
  const [showContactForm, setShowContactForm] = useState(false);
  const [contactName, setContactName] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [isSavingContact, setIsSavingContact] = useState(false);
  const [contactSaved, setContactSaved] = useState(false);

  // Refs
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const messageCursorRef = useRef<string | null>(null);

  const title = config.title || t("widget.title");

  // In-memory API client instance
  const [apiClient] = useState(() => createWidgetApiClient(config.apiBaseUrl));

  // ---------------------------------------------------------------------------
  // 1. Session initialization
  // ---------------------------------------------------------------------------
  const initSession = useCallback(async () => {
    if (sessionInitialized) return;
    try {
      await apiClient.createSession(
        config.customerToken ? { customer_token: config.customerToken } : undefined
      );
      setSessionInitialized(true);

      // Fetch initial conversation detail
      const conv = await apiClient.getConversation();
      setConversation(conv);

      // Add default welcome message if no messages yet
      const welcomeMsg: WidgetMessage = {
        id: "welcome-message",
        conversation_id: conv.id,
        sender_type: "assistant",
        content: t("widget.welcome"),
        created_at: conv.created_at,
      };
      setMessages([welcomeMsg]);

      // Fetch existing messages and catch up historical pages
      let allFetched: WidgetMessage[] = [];
      let cursor: string | null = null;
      let hasMore = true;
      while (hasMore) {
        const msgsResp = await apiClient.listMessages({ cursor: cursor || undefined, limit: 50 });
        allFetched = [...allFetched, ...msgsResp.items];
        cursor = msgsResp.next_cursor;
        hasMore = msgsResp.has_more && Boolean(cursor);
      }
      if (cursor) {
        messageCursorRef.current = cursor;
      }
      if (allFetched.length > 0) {
        setMessages((prev) => reconcileMessages(prev, allFetched));
      }
    } catch (err: unknown) {
      setErrorMessage(toSafeErrorMessage(err));
    }
  }, [apiClient, sessionInitialized]);

  // ---------------------------------------------------------------------------
  // 2. Polling for messages while panel is open & visible
  // ---------------------------------------------------------------------------
  const pollServer = useCallback(async (signal: AbortSignal) => {
    if (!apiClient.getSessionToken()) return;
    try {
      const [conv, msgResp] = await Promise.all([
        apiClient.getConversation(signal),
        apiClient.listMessages({ cursor: messageCursorRef.current || undefined, limit: 50 }, signal),
      ]);

      setConversation(conv);
      if (msgResp.items.length > 0) {
        setMessages((prev) => reconcileMessages(prev, msgResp.items));
      }
      if (msgResp.next_cursor) {
        messageCursorRef.current = msgResp.next_cursor;
      }
    } catch {
      // Ignore polling abort errors cleanly
    }
  }, [apiClient]);

  useEffect(() => {
    if (!isOpen) return;

    // Init session on first open
    if (!sessionInitialized) {
      initSession();
    }

    const polling = new PollingController({
      intervalMs: 3000,
      onPoll: pollServer,
      isActive: () => isOpen,
    });

    polling.start();
    return () => {
      polling.stop();
    };
  }, [isOpen, sessionInitialized, initSession, pollServer]);

  // Scroll to bottom on new messages
  useEffect(() => {
    if (isOpen && messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages.length, isOpen]);

  // Focus textarea when panel opens
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => {
        textareaRef.current?.focus();
      }, 100);
    }
  }, [isOpen]);

  // Escape key closes panel
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) {
        setIsOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen]);

  // ---------------------------------------------------------------------------
  // 3. Send message
  // ---------------------------------------------------------------------------
  const handleSendMessage = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const trimmed = inputValue.trim();
    if (!trimmed || isSending) return;

    if (conversation?.status === "closed") {
      setErrorMessage(t("widget.closedNotice"));
      return;
    }

    setIsSending(true);
    setErrorMessage(null);

    const clientMsgId = generateUUID();

    try {
      const sentMsg = await apiClient.sendMessage({
        content: trimmed,
        client_message_id: clientMsgId,
      });

      setMessages((prev) => reconcileMessages(prev, [sentMsg]));
      setInputValue("");
      textareaRef.current?.focus();
    } catch (err: unknown) {
      setErrorMessage(toSafeErrorMessage(err));
    } finally {
      setIsSending(false);
    }
  };

  const handleInputKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // ---------------------------------------------------------------------------
  // 4. Save contact details
  // ---------------------------------------------------------------------------
  const handleSaveContact = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!contactName.trim() && !contactEmail.trim()) return;

    setIsSavingContact(true);
    try {
      await apiClient.updateProfile({
        name: contactName.trim() || null,
        email: contactEmail.trim() || null,
      });
      setContactSaved(true);
      setShowContactForm(false);
    } catch (err: unknown) {
      setErrorMessage(toSafeErrorMessage(err));
    } finally {
      setIsSavingContact(false);
    }
  };


  const isClosed = conversation?.status === "closed";

  return (
    <div id="mcss-widget-root">
      {/* Floating launcher */}
      <button
        type="button"
        onClick={() => {
          if (isOpen && isFullScreen) {
            setIsFullScreen(false);
          }
          setIsOpen(!isOpen);
        }}
        className={`mcss-launcher-btn${isOpen && isFullScreen ? " mcss-launcher-fullscreen" : ""}`}
        aria-expanded={isOpen}
        aria-label={isOpen ? t("widget.closeChat") : t("widget.openChat")}
      >
        {isOpen ? (
          <svg width="24" height="24" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        ) : (
          <svg width="28" height="28" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="2"
              d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
            />
          </svg>
        )}
      </button>

      {/* Chat Panel */}
      {isOpen && (
        <div
          ref={panelRef}
          role="dialog"
          aria-label={title}
          className={`mcss-panel${isFullScreen ? " mcss-panel-fullscreen" : ""}`}
        >
          {/* Header */}
          <header className="mcss-header">
            <h2>{title}</h2>
            <div className="mcss-header-actions">
              <button
                type="button"
                onClick={() => setIsFullScreen(!isFullScreen)}
                className="mcss-fullscreen-btn"
                aria-label={isFullScreen ? t("widget.exitFullscreen") : t("widget.fullscreen")}
                title={isFullScreen ? t("widget.exitFullscreen") : t("widget.fullscreen")}
              >
                {isFullScreen ? (
                  <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 9L4 4m0 0v5m0-5h5m6 0h5m0 0v5m0-5l-5 5M9 15l-5 5m0 0v-5m0 5h5m6 0h5m0 0v-5m0 5l-5-5" />
                  </svg>
                ) : (
                  <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 8V4m0 0h4M4 4l5 5m11-5h-4m4 0v4m0-4l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5h-4m4 0v-4m0 4l-5-5" />
                  </svg>
                )}
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsFullScreen(false);
                  setIsOpen(false);
                }}
                className="mcss-close-btn"
                aria-label={t("widget.close")}
              >
                <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </header>

          {/* Optional contact details bar */}
          <div className="mcss-contact-bar">
            {!contactSaved ? (
              <>
                <button
                  type="button"
                  onClick={() => setShowContactForm(!showContactForm)}
                  className="mcss-contact-toggle"
                >
                  {showContactForm ? "Cancel" : t("widget.shareContact")}
                </button>
                {showContactForm && (
                  <form onSubmit={handleSaveContact} className="mcss-contact-form">
                    <input
                      type="text"
                      placeholder={t("widget.namePlaceholder")}
                      value={contactName}
                      onChange={(e) => setContactName(e.target.value)}
                      className="mcss-contact-input"
                      maxLength={100}
                    />
                    <input
                      type="email"
                      placeholder={t("widget.emailPlaceholder")}
                      value={contactEmail}
                      onChange={(e) => setContactEmail(e.target.value)}
                      className="mcss-contact-input"
                      maxLength={254}
                    />
                    <button
                      type="submit"
                      disabled={isSavingContact}
                      className="mcss-contact-save-btn"
                    >
                      {isSavingContact ? t("widget.savingContact") : t("widget.saveContact")}
                    </button>
                  </form>
                )}
              </>
            ) : (
              <span style={{ color: "#059669" }}>✓ {t("widget.contactSaved")}</span>
            )}
          </div>

          {/* Messages timeline */}
          <div
            role="log"
            aria-live="polite"
            className="mcss-messages-container"
          >
            {messages.map((msg) => (
              <div
                key={msg.id}
                className={`mcss-message ${msg.sender_type}`}
              >
                {msg.sender_type === "staff" && (
                  <span className="mcss-sender-label">Support Agent</span>
                )}
                {msg.sender_type === "assistant" && (
                  <span className="mcss-sender-label">{t("widget.title")}</span>
                )}

                <div className="mcss-bubble">
                  <FormattedMessage content={msg.content} />
                </div>

                {/* Source chips if any */}
                {msg.sender_type === "assistant" && (() => {
                  const meta = (msg as any).metadata || (msg as any).metadata_json || {};
                  const sources: any[] = meta.sources || [];
                  const citedIds: string[] = meta.cited_chunk_ids || [];
                  if (sources.length === 0 && citedIds.length === 0) return null;

                  return (
                    <div className="mcss-source-chips">
                      {sources.length > 0
                        ? sources.map((s: any, idx: number) => {
                            const chipLabel = s.title || s.doc_title || `Source ${idx + 1}`;
                            return (
                              <span
                                key={s.evidence_id || s.chunk_id || idx}
                                className="mcss-source-chip"
                                title={chipLabel}
                              >
                                {chipLabel}
                              </span>
                            );
                          })
                        : citedIds.map((chunkId: string, idx: number) => (
                            <span key={chunkId || idx} className="mcss-source-chip">
                              {`Source ${idx + 1}`}
                            </span>
                          ))}
                    </div>
                  );
                })()}

                <span className="mcss-time-label">
                  {new Date(msg.created_at).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          {/* Footer composer */}
          <footer className="mcss-footer">
            {errorMessage && (
              <div
                role="alert"
                style={{ color: "#dc2626", fontSize: "11px", marginBottom: "6px" }}
              >
                {errorMessage}
              </div>
            )}

            {isClosed ? (
              <div className="mcss-closed-box">
                {t("widget.closedNotice")}
              </div>
            ) : (
              <form onSubmit={handleSendMessage} className="mcss-form">
                <textarea
                  ref={textareaRef}
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  onKeyDown={handleInputKeyDown}
                  disabled={isSending}
                  placeholder={t("widget.placeholder")}
                  rows={1}
                  maxLength={2000}
                  className="mcss-input"
                  aria-label={t("widget.placeholder")}
                />
                <button
                  type="submit"
                  disabled={isSending || !inputValue.trim()}
                  className="mcss-send-btn"
                >
                  {isSending ? t("widget.sending") : t("widget.send")}
                </button>
              </form>
            )}
          </footer>
        </div>
      )}
    </div>
  );
}
