import React, { useState, useEffect, useRef, useCallback } from "react";
import { useTranslation } from "../hooks/useTranslation";
import { useAuth } from "../hooks/useAuth";
import {
  conversationsApi,
  messagesApi,
  type Conversation,
  type Message,
} from "../api/client";
import { reconcileMessages, PollingController } from "../utils/reconciliation";
import { toSafeErrorMessage } from "../utils/errors";
import { generateUUID } from "../utils/uuid";
import { LoadingState, EmptyState, ErrorState } from "../components/common/UIStates";

type StatusFilter = "all" | "open" | "waiting_for_staff" | "staff_active" | "closed";

export default function Inbox() {
  const { t } = useTranslation();
  const { user } = useAuth();

  // Conversation list state
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [listCursor, setListCursor] = useState<string | null>(null);
  const [listHasMore, setListHasMore] = useState(false);
  const [loadingMoreList, setLoadingMoreList] = useState(false);

  // Active conversation state
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);

  // Reply composer state
  const [replyText, setReplyText] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  // Take-over / Close action state
  const [isTakingOver, setIsTakingOver] = useState(false);
  const [isClosing, setIsClosing] = useState(false);

  // Mobile navigation toggle (list vs detail view)
  const [showMobileDetail, setShowMobileDetail] = useState(false);

  // Refs
  const timelineBottomRef = useRef<HTMLDivElement>(null);
  const replyInputRef = useRef<HTMLTextAreaElement>(null);
  const selectedConvIdRef = useRef<string | null>(null);
  selectedConvIdRef.current = selectedConvId;

  const messagesRef = useRef<Message[]>([]);
  messagesRef.current = messages;

  const activeConvCursorRef = useRef<string | null>(null);

  // ---------------------------------------------------------------------------
  // 1. Fetch conversations list
  // ---------------------------------------------------------------------------
  const fetchConversations = useCallback(
    async (signal?: AbortSignal, isInitial = false) => {
      try {
        if (isInitial) setListLoading(true);
        setListError(null);

        const filterStatus = statusFilter === "all" ? undefined : statusFilter;
        const resp = await conversationsApi.list(
          { status: filterStatus, limit: 25 },
          signal
        );

        if (isInitial) {
          setConversations(resp.items);
          setListCursor(resp.next_cursor);
          setListHasMore(resp.has_more);
        } else {
          // Reconcile conversations: update existing, add new ones, preserve older paginated ones
          setConversations((prev) => {
            const map = new Map<string, Conversation>();
            for (const c of prev) {
              map.set(c.id, c);
            }
            for (const c of resp.items) {
              map.set(c.id, { ...(map.get(c.id) || {}), ...c });
            }
            const updated = Array.from(map.values());
            updated.sort(
              (a, b) =>
                new Date(b.last_message_at).getTime() -
                new Date(a.last_message_at).getTime()
            );
            return updated;
          });
        }
      } catch (err: unknown) {
        if (signal?.aborted) return;
        setListError(toSafeErrorMessage(err));
      } finally {
        if (isInitial) setListLoading(false);
      }
    },
    [statusFilter]
  );

  // Load more conversations (cursor pagination)
  const loadMoreConversations = async () => {
    if (!listCursor || loadingMoreList) return;
    setLoadingMoreList(true);
    try {
      const filterStatus = statusFilter === "all" ? undefined : statusFilter;
      const resp = await conversationsApi.list({
        status: filterStatus,
        cursor: listCursor,
        limit: 25,
      });
      setConversations((prev) => [...prev, ...resp.items]);
      setListCursor(resp.next_cursor);
      setListHasMore(resp.has_more);
    } catch (err: unknown) {
      setListError(toSafeErrorMessage(err));
    } finally {
      setLoadingMoreList(false);
    }
  };

  // ---------------------------------------------------------------------------
  // 2. Fetch active conversation messages and details
  // ---------------------------------------------------------------------------
  const fetchMessagesForActive = useCallback(
    async (convId: string, signal?: AbortSignal, isInitial = false) => {
      try {
        if (isInitial) {
          setMessagesLoading(true);
          setMessagesError(null);
          activeConvCursorRef.current = null;
        }

        const currentCursor = isInitial ? undefined : activeConvCursorRef.current || undefined;

        // Fetch detail and initial/updated messages concurrently
        const [convDetail, msgResp] = await Promise.all([
          conversationsApi.get(convId, signal),
          messagesApi.list(convId, { cursor: currentCursor, limit: 50 }, signal),
        ]);

        setActiveConversation(convDetail);

        let newMessages = msgResp.items;
        let latestCursor = msgResp.next_cursor;

        // On initial load, catch up all historical message pages if more than 50
        if (isInitial && msgResp.has_more && latestCursor) {
          let hasMore = true;
          let pageCursor: string | null = latestCursor;
          while (hasMore && pageCursor) {
            const nextBatch = await messagesApi.list(convId, { cursor: pageCursor, limit: 50 }, signal);
            newMessages = [...newMessages, ...nextBatch.items];
            pageCursor = nextBatch.next_cursor;
            hasMore = nextBatch.has_more && Boolean(pageCursor);
            if (pageCursor) latestCursor = pageCursor;
          }
        }

        if (latestCursor) {
          activeConvCursorRef.current = latestCursor;
        }

        // Reconcile messages by immutable ID and stable sort
        if (newMessages.length > 0 || isInitial) {
          setMessages((prev) => reconcileMessages(prev, newMessages));
        }

        // Update conversation in the left list if changed
        setConversations((prevList) =>
          prevList.map((c) => (c.id === convDetail.id ? { ...c, ...convDetail } : c))
        );
      } catch (err: unknown) {
        if (signal?.aborted) return;
        setMessagesError(toSafeErrorMessage(err));
      } finally {
        if (isInitial) setMessagesLoading(false);
      }
    },
    []
  );

  // Select a conversation
  const selectConversation = (conv: Conversation) => {
    setSelectedConvId(conv.id);
    setActiveConversation(conv);
    activeConvCursorRef.current = null;
    setMessages([]);
    setReplyText("");
    setSendError(null);
    setShowMobileDetail(true);
    fetchMessagesForActive(conv.id, undefined, true);
  };

  // ---------------------------------------------------------------------------
  // 3. Polling controller for list and active conversation
  // ---------------------------------------------------------------------------
  useEffect(() => {
    fetchConversations(undefined, true);

    const polling = new PollingController({
      intervalMs: 3000,
      onPoll: async (signal) => {
        await fetchConversations(signal, false);

        const currentConvId = selectedConvIdRef.current;
        if (currentConvId) {
          await fetchMessagesForActive(currentConvId, signal, false);
        }
      },
    });

    polling.start();
    return () => {
      polling.stop();
    };
  }, [fetchConversations, fetchMessagesForActive]);

  // Scroll to bottom on new messages
  useEffect(() => {
    if (timelineBottomRef.current) {
      timelineBottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages.length]);

  // ---------------------------------------------------------------------------
  // 4. Take-over & Close Actions
  // ---------------------------------------------------------------------------
  const handleTakeOver = async () => {
    if (!selectedConvId || isTakingOver) return;
    setIsTakingOver(true);
    try {
      const updated = await conversationsApi.claim(selectedConvId);
      setActiveConversation(updated);
      setConversations((prev) =>
        prev.map((c) => (c.id === updated.id ? { ...c, ...updated } : c))
      );
    } catch (err: unknown) {
      setMessagesError(toSafeErrorMessage(err));
    } finally {
      setIsTakingOver(false);
    }
  };

  const handleClose = async () => {
    if (!selectedConvId || isClosing) return;
    if (!window.confirm(t("inbox.confirmClose"))) return;

    setIsClosing(true);
    try {
      const updated = await conversationsApi.close(selectedConvId);
      setActiveConversation(updated);
      setConversations((prev) =>
        prev.map((c) => (c.id === updated.id ? { ...c, ...updated } : c))
      );
    } catch (err: unknown) {
      setMessagesError(toSafeErrorMessage(err));
    } finally {
      setIsClosing(false);
    }
  };

  // ---------------------------------------------------------------------------
  // 5. Send message
  // ---------------------------------------------------------------------------
  const handleSendMessage = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!selectedConvId || isSending) return;

    const trimmed = replyText.trim();
    if (!trimmed) return;

    if (activeConversation?.status === "closed") {
      setSendError(t("inbox.conversationClosedNotice"));
      return;
    }

    setIsSending(true);
    setSendError(null);

    // Client-generated UUID for write idempotency
    const clientMessageId = generateUUID();

    try {
      const sentMsg = await messagesApi.send(selectedConvId, {
        content: trimmed,
        client_message_id: clientMessageId,
      });

      // Optimistically reconcile sent message
      setMessages((prev) => reconcileMessages(prev, [sentMsg]));
      setReplyText("");

      // Restore focus to textarea
      replyInputRef.current?.focus();
    } catch (err: unknown) {
      setSendError(toSafeErrorMessage(err));
    } finally {
      setIsSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // Helper for status badge colors
  const getStatusBadge = (status: string) => {
    switch (status) {
      case "open":
        return <span className="px-2 py-0.5 text-xs font-medium rounded bg-blue-100 text-blue-800">{t("inbox.statusOpen")}</span>;
      case "waiting_for_staff":
        return <span className="px-2 py-0.5 text-xs font-medium rounded bg-amber-100 text-amber-800">{t("inbox.statusWaiting")}</span>;
      case "staff_active":
        return <span className="px-2 py-0.5 text-xs font-medium rounded bg-green-100 text-green-800">{t("inbox.statusStaffActive")}</span>;
      case "closed":
        return <span className="px-2 py-0.5 text-xs font-medium rounded bg-gray-100 text-gray-800">{t("inbox.statusClosed")}</span>;
      default:
        return <span className="px-2 py-0.5 text-xs font-medium rounded bg-gray-100 text-gray-800">{status}</span>;
    }
  };

  const getSenderBadge = (senderType: string, senderUserId?: string | null) => {
    switch (senderType) {
      case "visitor":
        return <span className="text-xs font-semibold text-gray-600">{t("inbox.visitor")}</span>;
      case "staff":
        return (
          <span className="text-xs font-semibold text-blue-600">
            {senderUserId === user?.id ? `${t("inbox.staff")} (You)` : t("inbox.staff")}
          </span>
        );
      case "assistant":
        return <span className="text-xs font-semibold text-purple-600">{t("inbox.assistant")}</span>;
      case "system":
        return <span className="text-xs font-semibold text-gray-400">{t("inbox.system")}</span>;
      default:
        return <span className="text-xs font-semibold text-gray-500">{senderType}</span>;
    }
  };

  return (
    <div className="h-[calc(100vh-8rem)] flex flex-col md:flex-row bg-white rounded-lg border border-gray-200 overflow-hidden shadow-sm">
      {/* =================================================================== */}
      {/* Column 1: Filter tabs & Conversation List (Left)                    */}
      {/* =================================================================== */}
      <section
        aria-label="Conversation list"
        className={`w-full md:w-80 lg:w-96 border-r border-gray-200 flex flex-col bg-gray-50 flex-shrink-0 ${
          showMobileDetail ? "hidden md:flex" : "flex"
        }`}
      >
        {/* Status Filter Tabs */}
        <div className="p-3 border-b border-gray-200 bg-white">
          <label htmlFor="status-filter" className="sr-only">
            Filter conversations by status
          </label>
          <div className="flex flex-wrap gap-1">
            {(["all", "open", "waiting_for_staff", "staff_active", "closed"] as StatusFilter[]).map(
              (filterKey) => (
                <button
                  key={filterKey}
                  type="button"
                  onClick={() => setStatusFilter(filterKey)}
                  className={`px-2.5 py-1 text-xs font-medium rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                    statusFilter === filterKey
                      ? "bg-blue-600 text-white font-semibold"
                      : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                  }`}
                >
                  {filterKey === "all"
                    ? t("inbox.statusAll")
                    : filterKey === "open"
                    ? t("inbox.statusOpen")
                    : filterKey === "waiting_for_staff"
                    ? t("inbox.statusWaiting")
                    : filterKey === "staff_active"
                    ? t("inbox.statusStaffActive")
                    : t("inbox.statusClosed")}
                </button>
              )
            )}
          </div>
        </div>

        {/* Conversation Items */}
        <div className="flex-1 overflow-y-auto divide-y divide-gray-200">
          {listLoading && <LoadingState />}
          {listError && <ErrorState message={listError} onRetry={() => fetchConversations(undefined, true)} />}

          {!listLoading && !listError && conversations.length === 0 && (
            <div className="p-6 text-center text-sm text-gray-500">
              {t("inbox.noConversations")}
            </div>
          )}

          {!listLoading &&
            conversations.map((conv) => {
              const isSelected = conv.id === selectedConvId;
              const visitorDisplayName =
                conv.visitor?.name || conv.visitor?.email || t("inbox.anonymousVisitor");

              return (
                <button
                  key={conv.id}
                  type="button"
                  data-testid="conversation-item"
                  onClick={() => selectConversation(conv)}
                  className={`w-full text-left p-3.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                    isSelected
                      ? "bg-blue-50 border-l-4 border-blue-600"
                      : "hover:bg-gray-100 bg-white"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-sm text-gray-900 truncate max-w-[180px]">
                      {visitorDisplayName}
                    </span>
                    {getStatusBadge(conv.status)}
                  </div>
                  <div className="flex items-center justify-between mt-1.5 text-xs text-gray-500">
                    <span className="truncate max-w-[160px]">
                      {conv.title || `ID: ${conv.id.substring(0, 8)}`}
                    </span>
                    <time dateTime={conv.last_message_at}>
                      {new Date(conv.last_message_at).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </time>
                  </div>
                </button>
              );
            })}

          {listHasMore && (
            <div className="p-3 text-center">
              <button
                type="button"
                onClick={loadMoreConversations}
                disabled={loadingMoreList}
                className="text-xs text-blue-600 hover:text-blue-800 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
              >
                {loadingMoreList ? t("common.loading") : t("inbox.loadOlder")}
              </button>
            </div>
          )}
        </div>
      </section>

      {/* =================================================================== */}
      {/* Column 2: Active Conversation Timeline & Composer (Center)          */}
      {/* =================================================================== */}
      <section
        aria-label="Conversation timeline"
        className={`flex-1 flex flex-col bg-white overflow-hidden ${
          !showMobileDetail ? "hidden md:flex" : "flex"
        }`}
      >
        {!selectedConvId || !activeConversation ? (
          <div className="flex-1 flex items-center justify-center p-8">
            <EmptyState
              title={t("inbox.title")}
              description={t("inbox.selectConversationPrompt")}
            />
          </div>
        ) : (
          <>
            {/* Active Conversation Header */}
            <header className="px-4 py-3 border-b border-gray-200 flex items-center justify-between bg-white z-10 shadow-xs">
              <div className="flex items-center space-x-3">
                <button
                  type="button"
                  onClick={() => setShowMobileDetail(false)}
                  className="md:hidden text-sm text-blue-600 hover:text-blue-800 font-medium p-1"
                >
                  ← {t("inbox.backToList")}
                </button>
                <div>
                  <h2 className="text-base font-semibold text-gray-900 flex items-center gap-2">
                    <span>
                      {activeConversation.visitor?.name ||
                        activeConversation.visitor?.email ||
                        t("inbox.anonymousVisitor")}
                    </span>
                    {getStatusBadge(activeConversation.status)}
                  </h2>
                  <p className="text-xs text-gray-500">
                    ID: {activeConversation.id}
                  </p>
                </div>
              </div>

              {/* Take-over & Close controls */}
              <div className="flex items-center space-x-2">
                {activeConversation.status !== "closed" && (
                  <>
                    <button
                      type="button"
                      onClick={handleTakeOver}
                      disabled={isTakingOver || activeConversation.assigned_user_id === user?.id}
                      className="px-3 py-1.5 text-xs font-medium rounded-md bg-blue-50 text-blue-700 hover:bg-blue-100 disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                    >
                      {isTakingOver ? t("inbox.takingOver") : t("inbox.takeOver")}
                    </button>
                    <button
                      type="button"
                      onClick={handleClose}
                      disabled={isClosing}
                      className="px-3 py-1.5 text-xs font-medium rounded-md bg-red-50 text-red-700 hover:bg-red-100 disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
                    >
                      {isClosing ? t("inbox.closing") : t("inbox.closeConversation")}
                    </button>
                  </>
                )}
              </div>
            </header>

            {/* Timeline */}
            <div
              role="log"
              aria-live="polite"
              className="flex-1 overflow-y-auto p-4 space-y-4 bg-gray-50"
            >
              {messagesLoading && <LoadingState />}
              {messagesError && <ErrorState message={messagesError} />}

              {!messagesLoading &&
                messages.map((msg) => {
                  const isStaff = msg.sender_type === "staff";
                  const isVisitor = msg.sender_type === "visitor";
                  const isSystem = msg.sender_type === "system";

                  if (isSystem) {
                    return (
                      <div key={msg.id} className="text-center my-2">
                        <span className="text-xs text-gray-500 italic bg-gray-200 px-3 py-1 rounded-full">
                          {msg.content}
                        </span>
                      </div>
                    );
                  }

                  return (
                    <div
                      key={msg.id}
                      className={`flex flex-col ${isStaff ? "items-end" : "items-start"}`}
                    >
                      <div className="flex items-center space-x-2 mb-1 px-1">
                        {getSenderBadge(msg.sender_type, msg.sender_user_id)}
                        <time className="text-xs text-gray-400" dateTime={msg.created_at}>
                          {new Date(msg.created_at).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </time>
                      </div>

                      <div
                        className={`max-w-md lg:max-w-xl px-4 py-2.5 rounded-2xl text-sm whitespace-pre-wrap break-words ${
                          isStaff
                            ? "bg-blue-600 text-white rounded-tr-none"
                            : isVisitor
                            ? "bg-white text-gray-900 border border-gray-200 rounded-tl-none shadow-xs"
                            : "bg-purple-50 text-purple-900 border border-purple-200 rounded-tl-none"
                        }`}
                      >
                        {msg.content}

                        {/* Assistant Source Chips & Confidence */}
                        {msg.sender_type === "assistant" && msg.metadata && (
                          <div className="mt-2 pt-2 border-t border-purple-200 flex flex-wrap items-center gap-1.5">
                            {msg.metadata.cited_chunk_ids &&
                              msg.metadata.cited_chunk_ids.map((chunkId, idx) => (
                                <span
                                  key={chunkId || idx}
                                  className="px-2 py-0.5 text-xs bg-purple-200 text-purple-800 rounded font-medium"
                                >
                                  Source {idx + 1}
                                </span>
                              ))}
                            {msg.metadata.confidence !== undefined && (
                              <span className="text-xs text-purple-700 ml-auto font-mono">
                                {t("inbox.confidence")}: {(msg.metadata.confidence * 100).toFixed(0)}%
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              <div ref={timelineBottomRef} />
            </div>

            {/* Composer */}
            <div className="p-3 border-t border-gray-200 bg-white">
              {sendError && (
                <div role="alert" className="mb-2 text-xs text-red-600 font-medium">
                  {sendError}
                </div>
              )}

              {activeConversation.status === "closed" ? (
                <div className="p-3 text-center bg-gray-100 rounded-md text-sm text-gray-600 font-medium">
                  {t("inbox.conversationClosedNotice")}
                </div>
              ) : (
                <form onSubmit={handleSendMessage} className="space-y-2">
                  <label htmlFor="reply-composer" className="sr-only">
                    {t("inbox.replyPlaceholder")}
                  </label>
                  <textarea
                    id="reply-composer"
                    ref={replyInputRef}
                    value={replyText}
                    onChange={(e) => setReplyText(e.target.value)}
                    onKeyDown={handleKeyDown}
                    disabled={isSending}
                    placeholder={t("inbox.replyPlaceholder")}
                    rows={2}
                    maxLength={2000}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 resize-none disabled:bg-gray-100 disabled:cursor-not-allowed"
                  />
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-400">
                      {replyText.length}/2000
                    </span>
                    <button
                      type="submit"
                      disabled={isSending || !replyText.trim()}
                      className="px-4 py-1.5 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      {isSending ? t("inbox.sendingReply") : t("inbox.sendReply")}
                    </button>
                  </div>
                </form>
              )}
            </div>
          </>
        )}
      </section>

      {/* =================================================================== */}
      {/* Column 3: Visitor & Conversation Details (Right)                    */}
      {/* =================================================================== */}
      {activeConversation && (
        <aside
          aria-label="Conversation details"
          className="hidden lg:flex w-72 border-l border-gray-200 flex-col bg-gray-50 p-4 space-y-6 flex-shrink-0 overflow-y-auto"
        >
          <div>
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
              {t("inbox.visitorDetails")}
            </h3>
            <div className="bg-white p-3 rounded-md border border-gray-200 space-y-1.5 text-xs">
              <div>
                <span className="text-gray-500">Name: </span>
                <span className="font-medium text-gray-900">
                  {activeConversation.visitor?.name || "—"}
                </span>
              </div>
              <div>
                <span className="text-gray-500">Email: </span>
                <span className="font-medium text-gray-900">
                  {activeConversation.visitor?.email || "—"}
                </span>
              </div>
              <div>
                <span className="text-gray-500">Visitor ID: </span>
                <span className="font-mono text-gray-600 break-all">
                  {activeConversation.visitor_id}
                </span>
              </div>
            </div>
          </div>

          <div>
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
              {t("inbox.conversationDetails")}
            </h3>
            <div className="bg-white p-3 rounded-md border border-gray-200 space-y-1.5 text-xs">
              <div>
                <span className="text-gray-500">{t("common.status")}: </span>
                {getStatusBadge(activeConversation.status)}
              </div>
              <div>
                <span className="text-gray-500">Assigned: </span>
                <span className="font-medium text-gray-900">
                  {activeConversation.assignee?.display_name || t("inbox.unassigned")}
                </span>
              </div>
              <div>
                <span className="text-gray-500">{t("inbox.created")}: </span>
                <span className="text-gray-700">
                  {new Date(activeConversation.created_at).toLocaleString()}
                </span>
              </div>
              <div>
                <span className="text-gray-500">{t("inbox.lastActivity")}: </span>
                <span className="text-gray-700">
                  {new Date(activeConversation.last_message_at).toLocaleString()}
                </span>
              </div>
            </div>
          </div>
        </aside>
      )}
    </div>
  );
}
