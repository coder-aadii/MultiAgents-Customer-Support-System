import React, { useState, useEffect, useRef, useCallback } from "react";
import { useTranslation } from "../hooks/useTranslation";
import { useAuth } from "../hooks/useAuth";
import {
  conversationsApi,
  messagesApi,
  operationsApi,
  type Conversation,
  type ConversationNote,
  type Message,
  type AgentRosterItem,
} from "../api/client";
import { useNotification } from "../context/NotificationContext";
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
  const [isInternalNote, setIsInternalNote] = useState(false);
  const [isWhisper, setIsWhisper] = useState(false);
  const [notes, setNotes] = useState<ConversationNote[]>([]);

  // Take-over / Close action state
  const [isTakingOver, setIsTakingOver] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const [showCloseConfirmModal, setShowCloseConfirmModal] = useState(false);

  // Reassign state
  const notify = useNotification();
  const [availableAgents, setAvailableAgents] = useState<AgentRosterItem[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(false);
  const [agentsHasMore, setAgentsHasMore] = useState(true);
  const [isReassigning, setIsReassigning] = useState(false);
  const [showAssignDropdown, setShowAssignDropdown] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

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

        // Fetch detail, messages, and internal notes concurrently
        const [convDetail, msgResp, notesResp] = await Promise.all([
          conversationsApi.get(convId, signal),
          messagesApi.list(convId, { cursor: currentCursor, limit: 50 }, signal),
          operationsApi.listNotes(convId).catch(() => [] as ConversationNote[]),
        ]);

        setActiveConversation(convDetail);
        setNotes(notesResp);

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
    setNotes([]);
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

    setIsClosing(true);
    try {
      const updated = await conversationsApi.close(selectedConvId);
      setActiveConversation(updated);
      setConversations((prev) =>
        prev.map((c) => (c.id === updated.id ? { ...c, ...updated } : c))
      );
      setShowCloseConfirmModal(false);
      notify.success("Conversation closed successfully");
    } catch (err: unknown) {
      setMessagesError(toSafeErrorMessage(err));
      notify.error(toSafeErrorMessage(err));
    } finally {
      setIsClosing(false);
    }
  };

  // ---------------------------------------------------------------------------
  // 5. Assignment & Lazy-loaded Agents
  // ---------------------------------------------------------------------------
  const fetchAgentsPage = useCallback(async (offset: number) => {
    if (agentsLoading) return;
    setAgentsLoading(true);
    try {
      const resp = await operationsApi.listAgents({ limit: 15, offset });
      let items: AgentRosterItem[] = [];
      let hasMore = false;
      if (Array.isArray(resp)) {
        items = resp;
        hasMore = false;
      } else {
        items = resp.items;
        hasMore = resp.has_more;
      }
      setAvailableAgents((prev) => {
        const existingIds = new Set(prev.map((a) => a.id));
        const newItems = items.filter((a) => !existingIds.has(a.id));
        return offset === 0 ? items : [...prev, ...newItems];
      });
      setAgentsHasMore(hasMore);
    } catch {
      // ignore
    } finally {
      setAgentsLoading(false);
    }
  }, [agentsLoading]);

  const toggleAssignDropdown = () => {
    if (!showAssignDropdown) {
      // Lazy load first page when opening
      setShowAssignDropdown(true);
      setAvailableAgents([]);
      setAgentsHasMore(true);
      fetchAgentsPage(0);
    } else {
      setShowAssignDropdown(false);
    }
  };

  const handleDropdownScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
    if (scrollHeight - scrollTop <= clientHeight + 20 && agentsHasMore && !agentsLoading) {
      fetchAgentsPage(availableAgents.length);
    }
  };

  const handleReassign = async (agentId?: string) => {
    if (!selectedConvId || isReassigning) return;
    setIsReassigning(true);
    setShowAssignDropdown(false);
    try {
      if (!agentId || agentId === user?.id) {
        // Take over / Assign to me
        const updated = await conversationsApi.claim(selectedConvId);
        setActiveConversation(updated);
        setConversations((prev) =>
          prev.map((c) => (c.id === updated.id ? { ...c, ...updated } : c))
        );
        notify.success("Conversation assigned to you");
      } else {
        // Reassign to specific agent
        await operationsApi.reassignConversation(selectedConvId, { agent_id: agentId });
        const agent = availableAgents.find((a) => a.id === agentId);
        notify.success(`Conversation assigned to ${agent?.display_name || "agent"}`);
        await fetchMessagesForActive(selectedConvId, undefined, true);
      }
    } catch (err: unknown) {
      notify.error(err instanceof Error ? err.message : "Failed to assign conversation");
    } finally {
      setIsReassigning(false);
    }
  };

  // Close assign dropdown on outside click
  useEffect(() => {
    if (!showAssignDropdown) return;
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowAssignDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showAssignDropdown]);

  // ---------------------------------------------------------------------------
  // 6. Send message
  // ---------------------------------------------------------------------------
  const handleSendMessage = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!selectedConvId || isSending) return;

    const trimmed = replyText.trim();
    if (!trimmed) return;

    if (isInternalNote) {
      setIsSending(true);
      setSendError(null);
      try {
        const note = await operationsApi.addNote(selectedConvId, trimmed, isWhisper);
        setNotes((prev) => [...prev, note]);
        setReplyText("");
        replyInputRef.current?.focus();
      } catch (err: unknown) {
        setSendError(toSafeErrorMessage(err));
      } finally {
        setIsSending(false);
      }
      return;
    }

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
                    <div className="flex items-center space-x-1.5">
                      {conv.priority && conv.priority !== "normal" && (
                        <span
                          className={`px-1.5 py-0.5 text-[10px] font-bold rounded uppercase ${
                            conv.priority === "urgent"
                              ? "bg-red-100 text-red-800"
                              : "bg-orange-100 text-orange-800"
                          }`}
                        >
                          {conv.priority}
                        </span>
                      )}
                      {getStatusBadge(conv.status)}
                    </div>
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
            <header className="px-5 py-3 border-b border-gray-200 flex items-center justify-between bg-white z-10 shadow-xs gap-4">
              <div className="flex items-center space-x-3 min-w-0 flex-1">
                <button
                  type="button"
                  onClick={() => setShowMobileDetail(false)}
                  className="md:hidden text-sm text-blue-600 hover:text-blue-800 font-medium p-1 flex-shrink-0"
                >
                  ← {t("inbox.backToList")}
                </button>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap sm:flex-nowrap">
                    <h2
                      className="text-sm sm:text-base font-semibold text-gray-900 truncate max-w-xs sm:max-w-md"
                      title={activeConversation.visitor?.email || activeConversation.visitor?.name || undefined}
                    >
                      {activeConversation.visitor?.name ||
                        activeConversation.visitor?.email ||
                        t("inbox.anonymousVisitor")}
                    </h2>
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      {getStatusBadge(activeConversation.status)}
                      <span
                        className={`px-1.5 py-0.5 rounded text-[11px] font-semibold uppercase ${
                          activeConversation.priority === "urgent"
                            ? "bg-red-100 text-red-800"
                            : activeConversation.priority === "high"
                            ? "bg-orange-100 text-orange-800"
                            : "bg-gray-100 text-gray-700"
                        }`}
                      >
                        {activeConversation.priority || "normal"}
                      </span>
                      {activeConversation.sla_breached && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-600 text-white">
                          SLA Breached
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-gray-500 mt-0.5">
                    <span className="font-mono text-[11px] text-gray-400">
                      #{activeConversation.id.substring(0, 8)}
                    </span>
                    <span>•</span>
                    <span>
                      {activeConversation.assignee
                        ? `Assigned: ${activeConversation.assignee.display_name}`
                        : t("inbox.unassigned")}
                    </span>
                  </div>
                </div>
              </div>

              {/* Take-over & Close controls */}
              <div className="flex items-center space-x-2 flex-shrink-0">
                {activeConversation.status !== "closed" && (
                  <>
                    {/* Combined Assign / Reassign dropdown */}
                    <div className="relative" ref={dropdownRef}>
                      <button
                        type="button"
                        onClick={toggleAssignDropdown}
                        disabled={isReassigning}
                        className="px-3 py-1.5 text-xs font-semibold rounded-md bg-blue-50 text-blue-700 hover:bg-blue-100 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 transition-colors whitespace-nowrap flex items-center gap-1.5"
                      >
                        <span>
                          {isReassigning
                            ? "Assigning…"
                            : activeConversation.assigned_user_id === user?.id
                            ? "Assigned to You"
                            : activeConversation.assignee
                            ? `Assigned: ${activeConversation.assignee.display_name}`
                            : "Assign Agent"}
                        </span>
                        <span className="text-[10px]">▾</span>
                      </button>

                      {showAssignDropdown && (
                        <div
                          className="absolute right-0 top-full mt-1 w-60 bg-white border border-gray-200 rounded-lg shadow-lg z-50 overflow-hidden flex flex-col"
                          style={{ maxHeight: "280px" }}
                        >
                          <div className="px-3 py-2 text-[11px] font-semibold text-gray-500 uppercase border-b border-gray-100 tracking-wide bg-gray-50 flex items-center justify-between">
                            <span>Assign Conversation</span>
                            {agentsLoading && (
                              <span className="text-[10px] text-blue-600 lowercase font-normal">loading…</span>
                            )}
                          </div>

                          <div
                            className="overflow-y-auto flex-1 divide-y divide-gray-100"
                            onScroll={handleDropdownScroll}
                          >
                            {/* Option 1: Assign to Me */}
                            {activeConversation.assigned_user_id !== user?.id && (
                              <button
                                type="button"
                                onClick={() => handleReassign(user?.id)}
                                className="w-full text-left px-3 py-2 text-xs font-medium text-blue-700 hover:bg-blue-50 flex items-center gap-2 transition-colors bg-blue-50/30"
                              >
                                <span className="w-2 h-2 rounded-full bg-blue-500 flex-shrink-0" />
                                <div>
                                  <div className="font-semibold">Assign to Me</div>
                                  <div className="text-[10px] text-blue-600/80">Take over this conversation</div>
                                </div>
                              </button>
                            )}

                            {/* Option 2..N: Dynamic Online / Busy Agents */}
                            {availableAgents
                              .filter((a) => a.id !== activeConversation.assigned_user_id && a.id !== user?.id)
                              .map((agent) => (
                                <button
                                  key={agent.id}
                                  type="button"
                                  onClick={() => handleReassign(agent.id)}
                                  className="w-full text-left px-3 py-2 text-xs hover:bg-gray-50 flex items-center gap-2 transition-colors"
                                >
                                  <span
                                    className={`w-2 h-2 rounded-full flex-shrink-0 ${
                                      agent.presence === "online"
                                        ? "bg-green-500"
                                        : agent.presence === "busy"
                                        ? "bg-yellow-400"
                                        : "bg-gray-300"
                                    }`}
                                  />
                                  <div className="min-w-0 flex-1">
                                    <div className="font-medium text-gray-900 truncate">
                                      {agent.display_name}
                                    </div>
                                    <div className="text-[10px] text-gray-500 flex items-center gap-1.5">
                                      <span className="capitalize">{agent.presence}</span>
                                      <span>•</span>
                                      <span>{agent.active_chats} active</span>
                                    </div>
                                  </div>
                                </button>
                              ))}

                            {/* Empty or loading states */}
                            {!agentsLoading &&
                              availableAgents.filter(
                                (a) => a.id !== activeConversation.assigned_user_id && a.id !== user?.id
                              ).length === 0 &&
                              activeConversation.assigned_user_id === user?.id && (
                                <div className="px-3 py-3 text-xs text-gray-500 italic text-center">
                                  No other agents available
                                </div>
                              )}

                            {agentsLoading && (
                              <div className="px-3 py-2 text-center text-xs text-gray-400">
                                Loading more agents…
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => setShowCloseConfirmModal(true)}
                      disabled={isClosing}
                      className="px-3 py-1.5 text-xs font-semibold rounded-md bg-red-50 text-red-700 hover:bg-red-100 disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 transition-colors whitespace-nowrap"
                    >
                      {isClosing ? t("inbox.closing") : "Close Ticket"}
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
                [
                  ...messages.map((m) => ({
                    kind: "message" as const,
                    id: m.id,
                    time: new Date(m.created_at).getTime(),
                    message: m,
                  })),
                  ...notes.map((n) => ({
                    kind: "note" as const,
                    id: `note-${n.id}`,
                    time: new Date(n.created_at).getTime(),
                    note: n,
                  })),
                ]
                  .sort((a, b) => a.time - b.time)
                  .map((item) => {
                    if (item.kind === "note") {
                      const n = item.note;
                      return (
                        <div key={item.id} className="flex flex-col items-center my-3">
                          <div
                            className={`max-w-lg w-full p-3 rounded-lg border text-sm shadow-xs ${
                              n.is_whisper
                                ? "bg-amber-50 border-amber-300 text-amber-950"
                                : "bg-yellow-50 border-yellow-300 text-yellow-950"
                            }`}
                          >
                            <div className="flex items-center justify-between text-xs font-semibold mb-1">
                              <span className="flex items-center gap-1.5 text-amber-900">
                                {n.is_whisper ? "🔒 Supervisor Whisper" : "📝 Internal Note"}
                                <span className="text-gray-500 font-normal">
                                  {n.author_user_id === user?.id ? "(You)" : "• Staff"}
                                </span>
                              </span>
                              <time className="text-gray-400 font-normal" dateTime={n.created_at}>
                                {new Date(n.created_at).toLocaleTimeString([], {
                                  hour: "2-digit",
                                  minute: "2-digit",
                                })}
                              </time>
                            </div>
                            <div className="whitespace-pre-wrap break-words">{n.note_text}</div>
                          </div>
                        </div>
                      );
                    }

                    const msg = item.message;
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
                                msg.metadata.cited_chunk_ids.map((chunkId, idx) => {
                                  const matching = ((msg.metadata as any)?.sources as any[])?.find(
                                    (s: any) => s.chunk_id === chunkId
                                  );
                                  const chipLabel = matching?.doc_title || `Source ${idx + 1}`;
                                  return (
                                    <span
                                      key={chunkId || idx}
                                      className="px-2 py-0.5 text-xs bg-purple-200 text-purple-800 rounded font-medium"
                                      title={matching?.doc_title ? `${matching.doc_title} (${chunkId})` : undefined}
                                    >
                                      {chipLabel}
                                    </span>
                                  );
                                })}
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

              {activeConversation.status === "closed" && !isInternalNote ? (
                <div className="p-3 text-center bg-gray-100 rounded-md text-sm text-gray-600 font-medium flex items-center justify-between">
                  <span>{t("inbox.conversationClosedNotice")}</span>
                  <button
                    type="button"
                    onClick={() => setIsInternalNote(true)}
                    className="px-2.5 py-1 text-xs bg-amber-100 text-amber-800 rounded font-medium hover:bg-amber-200"
                  >
                    + Add Internal Note
                  </button>
                </div>
              ) : (
                <form onSubmit={handleSendMessage} className="space-y-2">
                  <div className="flex items-center justify-between pb-1 border-b border-gray-100">
                    <div className="flex items-center space-x-2">
                      <button
                        type="button"
                        onClick={() => {
                          setIsInternalNote(false);
                          setIsWhisper(false);
                        }}
                        className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                          !isInternalNote
                            ? "bg-blue-600 text-white"
                            : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                        }`}
                      >
                        💬 Customer Reply
                      </button>
                      <button
                        type="button"
                        onClick={() => setIsInternalNote(true)}
                        className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                          isInternalNote
                            ? "bg-amber-500 text-white"
                            : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                        }`}
                      >
                        📝 Internal Note
                      </button>
                    </div>

                    {isInternalNote && (
                      <label className="flex items-center space-x-1.5 text-xs text-amber-800 cursor-pointer font-medium select-none">
                        <input
                          type="checkbox"
                          checked={isWhisper}
                          onChange={(e) => setIsWhisper(e.target.checked)}
                          className="rounded text-amber-600 focus:ring-amber-500 h-3.5 w-3.5"
                        />
                        <span>🔒 Supervisor Whisper</span>
                      </label>
                    )}
                  </div>

                  <label htmlFor="reply-composer" className="sr-only">
                    {isInternalNote ? "Internal Note" : t("inbox.replyPlaceholder")}
                  </label>
                  <textarea
                    id="reply-composer"
                    ref={replyInputRef}
                    value={replyText}
                    onChange={(e) => setReplyText(e.target.value)}
                    onKeyDown={handleKeyDown}
                    disabled={isSending}
                    placeholder={
                      isInternalNote
                        ? isWhisper
                          ? "Write a private supervisor whisper (visible only to team)..."
                          : "Write an internal note (hidden from customer)..."
                        : t("inbox.replyPlaceholder")
                    }
                    rows={2}
                    maxLength={2000}
                    className={`w-full px-3 py-2 border rounded-md text-sm focus-visible:outline-none focus-visible:ring-2 resize-none disabled:bg-gray-100 disabled:cursor-not-allowed ${
                      isInternalNote
                        ? "border-amber-400 focus-visible:ring-amber-500 bg-amber-50/20"
                        : "border-gray-300 focus-visible:ring-blue-500"
                    }`}
                  />
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-400">
                      {replyText.length}/2000
                    </span>
                    <button
                      type="submit"
                      disabled={isSending || !replyText.trim()}
                      className={`px-4 py-1.5 text-white text-sm font-medium rounded-md focus-visible:outline-none focus-visible:ring-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors ${
                        isInternalNote
                          ? "bg-amber-600 hover:bg-amber-700 focus-visible:ring-amber-500"
                          : "bg-blue-600 hover:bg-blue-700 focus-visible:ring-blue-500"
                      }`}
                    >
                      {isSending
                        ? isInternalNote
                          ? "Saving Note..."
                          : t("inbox.sendingReply")
                        : isInternalNote
                        ? isWhisper
                          ? "Post Whisper"
                          : "Save Note"
                        : t("inbox.sendReply")}
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
                <span className="text-gray-500">Priority: </span>
                <span
                  className={`px-1.5 py-0.5 rounded text-[11px] font-semibold uppercase ${
                    activeConversation.priority === "urgent"
                      ? "bg-red-100 text-red-800"
                      : activeConversation.priority === "high"
                      ? "bg-orange-100 text-orange-800"
                      : "bg-gray-100 text-gray-700"
                  }`}
                >
                  {activeConversation.priority || "normal"}
                </span>
              </div>
              {activeConversation.sla_breached && (
                <div>
                  <span className="px-1.5 py-0.5 rounded text-[11px] font-bold bg-red-600 text-white">
                    ⚠️ SLA Breached
                  </span>
                </div>
              )}
              {activeConversation.channel_type && (
                <div>
                  <span className="text-gray-500">Channel: </span>
                  <span className="font-medium uppercase text-gray-800">
                    {activeConversation.channel_type}
                  </span>
                </div>
              )}
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

      {/* Custom Confirmation Modal for Closing Ticket */}
      {showCloseConfirmModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-xs">
          <div
            className="w-full max-w-sm rounded-xl bg-white p-5 shadow-2xl border border-gray-100 flex flex-col gap-3 animate-in fade-in zoom-in-95 duration-150"
            role="dialog"
            aria-modal="true"
            aria-labelledby="close-modal-title"
          >
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full bg-red-100 text-red-600">
                <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <div className="flex-1">
                <h3 id="close-modal-title" className="text-sm font-semibold text-gray-900">
                  Close Ticket
                </h3>
                <p className="mt-1 text-xs text-gray-500">
                  Are you sure you want to close this conversation? Once closed, further replies may not be sent unless reopened.
                </p>
              </div>
            </div>

            <div className="mt-2 flex items-center justify-end gap-2 pt-2 border-t border-gray-100">
              <button
                type="button"
                onClick={() => setShowCloseConfirmModal(false)}
                disabled={isClosing}
                className="px-3 py-1.5 text-xs font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors focus:outline-none focus:ring-2 focus:ring-gray-300 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleClose}
                disabled={isClosing}
                className="px-3.5 py-1.5 text-xs font-semibold text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors shadow-xs focus:outline-none focus:ring-2 focus:ring-red-500 disabled:opacity-50 flex items-center gap-1.5"
              >
                {isClosing && (
                  <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                )}
                {isClosing ? "Closing…" : "Confirm & Close"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
