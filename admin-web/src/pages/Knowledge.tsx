import React, { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "../hooks/useTranslation";
import { useAuth } from "../hooks/useAuth";
import {
  knowledgeApi,
  type DocumentStatus,
  type IngestionStatus,
  type SourceType,
  type KnowledgeDocument,
  type KnowledgeDocumentDetail,
  type RetrievalDebugResponse,
} from "../api/client";
import { toSafeErrorMessage } from "../utils/errors";
import { LoadingState, EmptyState, ErrorState } from "../components/common/UIStates";

type ActiveTab = "documents" | "testbed";
type StatusFilter = "all" | DocumentStatus;
type IngestionFilter = "all" | IngestionStatus;
type CreateTab = "upload" | "write";

const MAX_UPLOAD_BYTES = 5 * 1024 * 1024; // 5 MB

export default function Knowledge() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  // Tab State
  const [activeTab, setActiveTab] = useState<ActiveTab>("documents");

  // Document List State
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [ingestionFilter, setIngestionFilter] = useState<IngestionFilter>("all");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  // Detail Modal State
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [detailDoc, setDetailDoc] = useState<KnowledgeDocumentDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  // Create/Upload Modal State
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createTab, setCreateTab] = useState<CreateTab>("upload");
  const [createTitle, setCreateTitle] = useState("");
  const [createSourceType, setCreateSourceType] = useState<SourceType>("text");
  const [createContent, setCreateContent] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Edit Draft Modal State
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editingDoc, setEditingDoc] = useState<KnowledgeDocumentDetail | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editContent, setEditContent] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  // Action Confirmation Dialog States
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeDocument | null>(null);
  const [publishTarget, setPublishTarget] = useState<KnowledgeDocument | null>(null);
  const [archiveTarget, setArchiveTarget] = useState<KnowledgeDocument | null>(null);
  const [isActionLoading, setIsActionLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Retrieval Testbed State
  const [testbedQuery, setTestbedQuery] = useState("");
  const [testbedLimit, setTestbedLimit] = useState(5);
  const [testbedMinScore, setTestbedMinScore] = useState(0.72);
  const [testbedLoading, setTestbedLoading] = useState(false);
  const [testbedError, setTestbedError] = useState<string | null>(null);
  const [testbedResponse, setTestbedResponse] = useState<RetrievalDebugResponse | null>(null);

  // Load document list
  const loadDocuments = useCallback(
    async (isInitial = true, cursor?: string | null) => {
      if (isInitial) {
        setListLoading(true);
        setListError(null);
      } else {
        setLoadingMore(true);
      }

      try {
        const params: {
          status?: string;
          ingestion_status?: string;
          cursor?: string | null;
          limit?: number;
        } = { limit: 20 };

        if (statusFilter !== "all") params.status = statusFilter;
        if (ingestionFilter !== "all") params.ingestion_status = ingestionFilter;
        if (cursor) params.cursor = cursor;

        const data = await knowledgeApi.list(params);

        if (isInitial) {
          setDocuments(data.items);
        } else {
          setDocuments((prev) => [...prev, ...data.items]);
        }
        setNextCursor(data.next_cursor);
        setHasMore(data.has_more);
      } catch (err) {
        setListError(toSafeErrorMessage(err, "Failed to load knowledge documents."));
      } finally {
        setListLoading(false);
        setLoadingMore(false);
      }
    },
    [statusFilter, ingestionFilter]
  );

  useEffect(() => {
    loadDocuments(true);
  }, [loadDocuments]);

  // Polling for processing items
  useEffect(() => {
    const hasProcessing = documents.some(
      (doc) => doc.ingestion_status === "processing" || doc.ingestion_status === "pending"
    );
    if (!hasProcessing) return;

    const interval = setInterval(() => {
      loadDocuments(true);
    }, 3000);

    return () => clearInterval(interval);
  }, [documents, loadDocuments]);

  // Open Document Detail
  const openDetail = async (docId: string) => {
    setSelectedDocId(docId);
    setDetailLoading(true);
    setDetailError(null);
    try {
      const data = await knowledgeApi.get(docId);
      setDetailDoc(data);
    } catch (err) {
      setDetailError(toSafeErrorMessage(err, "Failed to fetch document details."));
    } finally {
      setDetailLoading(false);
    }
  };

  const closeDetail = () => {
    setSelectedDocId(null);
    setDetailDoc(null);
    setDetailError(null);
  };

  // Open Edit Draft Modal
  const openEditModal = async (doc: KnowledgeDocument) => {
    setIsEditing(false);
    setEditError(null);
    try {
      const fullDoc = await knowledgeApi.get(doc.id);
      setEditingDoc(fullDoc);
      setEditTitle(fullDoc.title);
      setEditContent(fullDoc.content || "");
      setEditModalOpen(true);
    } catch (err) {
      setActionError(toSafeErrorMessage(err, "Failed to load document for editing."));
    }
  };

  // Handle Save Edit
  const handleSaveEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingDoc) return;
    setIsEditing(true);
    setEditError(null);

    try {
      await knowledgeApi.update(editingDoc.id, {
        title: editTitle.trim(),
        content: editContent,
      });
      setEditModalOpen(false);
      setEditingDoc(null);
      await loadDocuments(true);
      if (selectedDocId === editingDoc.id) {
        openDetail(editingDoc.id);
      }
    } catch (err) {
      setEditError(toSafeErrorMessage(err, "Failed to update draft document."));
    } finally {
      setIsEditing(false);
    }
  };

  // Handle Create or Upload
  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setSubmitError(null);

    try {
      if (createTab === "upload") {
        if (!uploadFile) {
          setSubmitError(t("knowledge.selectFileError"));
          setIsSubmitting(false);
          return;
        }
        if (uploadFile.size > MAX_UPLOAD_BYTES) {
          setSubmitError(t("knowledge.fileOversizedError"));
          setIsSubmitting(false);
          return;
        }

        const formData = new FormData();
        formData.append("file", uploadFile);
        if (createTitle.trim()) {
          formData.append("title", createTitle.trim());
        }

        await knowledgeApi.upload(formData);
      } else {
        if (!createTitle.trim()) {
          setSubmitError(t("knowledge.titleRequiredError"));
          setIsSubmitting(false);
          return;
        }
        if (!createContent.trim()) {
          setSubmitError(t("knowledge.contentRequiredError"));
          setIsSubmitting(false);
          return;
        }

        await knowledgeApi.create({
          title: createTitle.trim(),
          source_type: createSourceType,
          content: createContent,
        });
      }

      setCreateModalOpen(false);
      setCreateTitle("");
      setCreateContent("");
      setUploadFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await loadDocuments(true);
    } catch (err) {
      setSubmitError(toSafeErrorMessage(err, "Failed to create knowledge document."));
    } finally {
      setIsSubmitting(false);
    }
  };

  // Action Handlers
  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setIsActionLoading(true);
    setActionError(null);
    try {
      await knowledgeApi.delete(deleteTarget.id);
      setDeleteTarget(null);
      if (selectedDocId === deleteTarget.id) closeDetail();
      await loadDocuments(true);
    } catch (err) {
      setActionError(toSafeErrorMessage(err, "Failed to delete document."));
    } finally {
      setIsActionLoading(false);
    }
  };

  const handlePublishConfirm = async () => {
    if (!publishTarget) return;
    setIsActionLoading(true);
    setActionError(null);
    try {
      await knowledgeApi.publish(publishTarget.id);
      setPublishTarget(null);
      await loadDocuments(true);
      if (selectedDocId === publishTarget.id) openDetail(publishTarget.id);
    } catch (err) {
      setActionError(toSafeErrorMessage(err, "Failed to publish document."));
    } finally {
      setIsActionLoading(false);
    }
  };

  const handleArchiveConfirm = async () => {
    if (!archiveTarget) return;
    setIsActionLoading(true);
    setActionError(null);
    try {
      await knowledgeApi.archive(archiveTarget.id);
      setArchiveTarget(null);
      await loadDocuments(true);
      if (selectedDocId === archiveTarget.id) openDetail(archiveTarget.id);
    } catch (err) {
      setActionError(toSafeErrorMessage(err, "Failed to archive document."));
    } finally {
      setIsActionLoading(false);
    }
  };

  const handleRetryIngestion = async (docId: string) => {
    setActionError(null);
    try {
      await knowledgeApi.retry(docId);
      await loadDocuments(true);
      if (selectedDocId === docId) openDetail(docId);
    } catch (err) {
      setActionError(toSafeErrorMessage(err, "Failed to retry document ingestion."));
    }
  };

  // Handle Retrieval Testbed Search
  const handleTestbedSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!testbedQuery.trim()) return;

    setTestbedLoading(true);
    setTestbedError(null);

    try {
      const res = await knowledgeApi.debugRetrieve({
        query: testbedQuery.trim(),
        limit: testbedLimit,
        min_score: testbedMinScore,
      });
      setTestbedResponse(res);
    } catch (err) {
      setTestbedError(toSafeErrorMessage(err, "Failed to execute retrieval diagnostics."));
    } finally {
      setTestbedLoading(false);
    }
  };

  // Helper renderers for status badges
  const renderPublicationBadge = (status: DocumentStatus) => {
    switch (status) {
      case "published":
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-100 text-emerald-800 border border-emerald-200">
            {t("knowledge.statusPublished")}
          </span>
        );
      case "draft":
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-amber-100 text-amber-800 border border-amber-200">
            {t("knowledge.statusDraft")}
          </span>
        );
      case "archived":
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-700 border border-gray-200">
            {t("knowledge.statusArchived")}
          </span>
        );
    }
  };

  const renderIngestionBadge = (
    status: IngestionStatus,
    errorCode?: string | null,
    docId?: string,
    isDraft?: boolean
  ) => {
    switch (status) {
      case "ready":
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-teal-50 text-teal-700 border border-teal-200">
            <span className="w-1.5 h-1.5 mr-1.5 rounded-full bg-teal-500" />
            {t("knowledge.ingestionReady")}
          </span>
        );
      case "processing":
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-50 text-blue-700 border border-blue-200">
            <svg
              className="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-blue-600"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
              />
            </svg>
            {t("knowledge.ingestionProcessing")}
          </span>
        );
      case "pending":
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-700 border border-gray-200">
            {t("knowledge.ingestionPending")}
          </span>
        );
      case "failed":
        return (
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-rose-50 text-rose-700 border border-rose-200"
              title={errorCode ? `Error: ${errorCode}` : "Ingestion Failed"}
            >
              <span className="w-1.5 h-1.5 mr-1.5 rounded-full bg-rose-500" />
              {t("knowledge.ingestionFailed")}
              {errorCode && <span className="ml-1 font-mono text-[10px]">({errorCode})</span>}
            </span>
            {isAdmin && isDraft && docId && (
              <button
                type="button"
                onClick={() => handleRetryIngestion(docId)}
                className="text-xs text-blue-600 hover:text-blue-800 underline font-medium"
              >
                {t("knowledge.retryIngestion")}
              </button>
            )}
          </span>
        );
    }
  };

  return (
    <div className="space-y-6">
      {/* Top Header & Navigation Tabs */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-2 border-b border-gray-200">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t("knowledge.title")}</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {isAdmin
              ? "Manage documents, safe ingestion pipelines, and retrieval diagnostics."
              : t("knowledge.permissionsNotice")}
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex bg-gray-100 p-1 rounded-lg border border-gray-200">
            <button
              type="button"
              onClick={() => setActiveTab("documents")}
              className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                activeTab === "documents"
                  ? "bg-white text-gray-900 shadow-sm"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              {t("knowledge.documentsTab")}
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("testbed")}
              className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                activeTab === "testbed"
                  ? "bg-white text-gray-900 shadow-sm"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              {t("knowledge.testbedTab")}
            </button>
          </div>

          {isAdmin && activeTab === "documents" && (
            <button
              type="button"
              onClick={() => {
                setSubmitError(null);
                setCreateModalOpen(true);
              }}
              className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 text-sm font-medium shadow-sm"
            >
              + {t("knowledge.addDocument")}
            </button>
          )}
        </div>
      </div>

      {actionError && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-700 flex justify-between items-center">
          <span>{actionError}</span>
          <button
            type="button"
            onClick={() => setActionError(null)}
            className="text-red-500 hover:text-red-700 text-xs font-bold ml-2"
          >
            ✕
          </button>
        </div>
      )}

      {/* TAB 1: DOCUMENTS MANAGEMENT */}
      {activeTab === "documents" && (
        <div className="space-y-4">
          {/* Filters Bar */}
          <div className="flex flex-wrap items-center justify-between gap-4 bg-white p-4 rounded-lg border border-gray-200">
            <div className="flex flex-wrap items-center gap-3">
              {/* Publication Status Filter */}
              <div>
                <label htmlFor="status-filter" className="sr-only">
                  {t("knowledge.status")}
                </label>
                <select
                  id="status-filter"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
                  className="text-sm border border-gray-300 rounded-md px-3 py-1.5 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="all">{t("knowledge.statusAll")}</option>
                  <option value="draft">{t("knowledge.statusDraft")}</option>
                  <option value="published">{t("knowledge.statusPublished")}</option>
                  <option value="archived">{t("knowledge.statusArchived")}</option>
                </select>
              </div>

              {/* Ingestion Status Filter */}
              <div>
                <label htmlFor="ingestion-filter" className="sr-only">
                  {t("knowledge.ingestionStatus")}
                </label>
                <select
                  id="ingestion-filter"
                  value={ingestionFilter}
                  onChange={(e) => setIngestionFilter(e.target.value as IngestionFilter)}
                  className="text-sm border border-gray-300 rounded-md px-3 py-1.5 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="all">{t("knowledge.ingestionAll")}</option>
                  <option value="ready">{t("knowledge.ingestionReady")}</option>
                  <option value="processing">{t("knowledge.ingestionProcessing")}</option>
                  <option value="pending">{t("knowledge.ingestionPending")}</option>
                  <option value="failed">{t("knowledge.ingestionFailed")}</option>
                </select>
              </div>
            </div>

            <button
              type="button"
              onClick={() => loadDocuments(true)}
              className="text-sm text-gray-600 hover:text-gray-900 border border-gray-300 rounded-md px-3 py-1.5 bg-gray-50 hover:bg-gray-100"
            >
              ↻ Refresh
            </button>
          </div>

          {/* Document List Table */}
          {listLoading ? (
            <LoadingState message="Loading knowledge documents..." />
          ) : listError ? (
            <ErrorState message={listError} onRetry={() => loadDocuments(true)} />
          ) : documents.length === 0 ? (
            <EmptyState
              title={t("knowledge.noDocuments")}
              description="No documents match the selected filters."
              action={
                isAdmin ? (
                  <button
                    type="button"
                    onClick={() => setCreateModalOpen(true)}
                    className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 text-sm"
                  >
                    {t("knowledge.addDocument")}
                  </button>
                ) : undefined
              }
            />
          ) : (
            <div className="bg-white rounded-lg border border-gray-200 overflow-hidden shadow-sm">
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
                  <thead className="bg-gray-50 text-gray-500 uppercase tracking-wider text-xs">
                    <tr>
                      <th scope="col" className="px-6 py-3 font-semibold">
                        {t("knowledge.docTitle")}
                      </th>
                      <th scope="col" className="px-6 py-3 font-semibold">
                        {t("knowledge.sourceType")}
                      </th>
                      <th scope="col" className="px-6 py-3 font-semibold">
                        {t("knowledge.status")}
                      </th>
                      <th scope="col" className="px-6 py-3 font-semibold">
                        {t("knowledge.ingestionStatus")}
                      </th>
                      <th scope="col" className="px-6 py-3 font-semibold">
                        {t("knowledge.chunks")}
                      </th>
                      <th scope="col" className="px-6 py-3 font-semibold">
                        {t("knowledge.updatedAt")}
                      </th>
                      <th scope="col" className="px-6 py-3 font-semibold text-right">
                        {t("common.actions")}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 bg-white">
                    {documents.map((doc) => {
                      const canPublish =
                        doc.status === "draft" &&
                        doc.ingestion_status === "ready" &&
                        doc.chunk_count > 0;
                      const canArchive = doc.status === "published";
                      const canDelete = doc.status === "draft";
                      const canEdit = doc.status === "draft";

                      return (
                        <tr key={doc.id} className="hover:bg-gray-50 transition-colors">
                          <td className="px-6 py-4">
                            <button
                              type="button"
                              onClick={() => openDetail(doc.id)}
                              className="font-medium text-gray-900 hover:text-blue-600 text-left truncate max-w-xs block"
                              title={doc.title}
                            >
                              {doc.title}
                            </button>
                            {doc.source_filename && (
                              <span className="text-xs text-gray-400 block truncate max-w-xs">
                                📎 {doc.source_filename}
                              </span>
                            )}
                          </td>
                          <td className="px-6 py-4">
                            <span className="capitalize text-xs font-mono px-2 py-0.5 rounded bg-gray-100 text-gray-700">
                              {doc.source_type}
                            </span>
                          </td>
                          <td className="px-6 py-4">{renderPublicationBadge(doc.status)}</td>
                          <td className="px-6 py-4">
                            {renderIngestionBadge(
                              doc.ingestion_status,
                              doc.ingestion_error_code,
                              doc.id,
                              doc.status === "draft"
                            )}
                          </td>
                          <td className="px-6 py-4 text-gray-600 font-mono text-xs">
                            {doc.chunk_count}
                          </td>
                          <td className="px-6 py-4 text-gray-500 text-xs">
                            {new Date(doc.updated_at).toLocaleDateString(undefined, {
                              year: "numeric",
                              month: "short",
                              day: "numeric",
                            })}
                          </td>
                          <td className="px-6 py-4 text-right">
                            <div className="flex items-center justify-end gap-2">
                              {/* View Details */}
                              <button
                                type="button"
                                onClick={() => openDetail(doc.id)}
                                className="text-xs text-gray-600 hover:text-gray-900 px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
                              >
                                View
                              </button>

                              {/* Admin Actions */}
                              {isAdmin && (
                                <>
                                  {canEdit && (
                                    <button
                                      type="button"
                                      onClick={() => openEditModal(doc)}
                                      className="text-xs text-blue-600 hover:text-blue-800 px-2 py-1 rounded border border-blue-200 hover:bg-blue-50"
                                    >
                                      {t("common.edit")}
                                    </button>
                                  )}

                                  {canPublish && (
                                    <button
                                      type="button"
                                      onClick={() => setPublishTarget(doc)}
                                      className="text-xs text-emerald-700 hover:text-emerald-900 px-2 py-1 rounded border border-emerald-300 hover:bg-emerald-50 font-medium"
                                    >
                                      {t("knowledge.publish")}
                                    </button>
                                  )}

                                  {canArchive && (
                                    <button
                                      type="button"
                                      onClick={() => setArchiveTarget(doc)}
                                      className="text-xs text-amber-700 hover:text-amber-900 px-2 py-1 rounded border border-amber-300 hover:bg-amber-50"
                                    >
                                      {t("knowledge.archive")}
                                    </button>
                                  )}

                                  {canDelete && (
                                    <button
                                      type="button"
                                      onClick={() => setDeleteTarget(doc)}
                                      className="text-xs text-rose-600 hover:text-rose-800 px-2 py-1 rounded border border-rose-200 hover:bg-rose-50"
                                    >
                                      {t("common.delete")}
                                    </button>
                                  )}
                                </>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Load More Pagination */}
              {hasMore && (
                <div className="p-4 border-t border-gray-200 text-center bg-gray-50">
                  <button
                    type="button"
                    onClick={() => loadDocuments(false, nextCursor)}
                    disabled={loadingMore}
                    className="px-4 py-2 text-sm text-blue-600 hover:text-blue-800 font-medium disabled:opacity-50"
                  >
                    {loadingMore ? t("common.loading") : "Load More Documents"}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* TAB 2: RETRIEVAL TESTBED & DIAGNOSTICS */}
      {activeTab === "testbed" && (
        <div className="space-y-6">
          <div className="bg-white p-6 rounded-lg border border-gray-200 shadow-sm space-y-4">
            <h2 className="text-lg font-semibold text-gray-900">
              {t("knowledge.testbedTab")}
            </h2>
            <p className="text-sm text-gray-600">
              Run similarity queries against published documents. Draft and archived documents are
              strictly excluded from answers and query results.
            </p>

            <form onSubmit={handleTestbedSearch} className="space-y-4">
              <div>
                <label
                  htmlFor="testbed-query"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  {t("knowledge.testbedQuery")}
                </label>
                <textarea
                  id="testbed-query"
                  rows={3}
                  value={testbedQuery}
                  onChange={(e) => setTestbedQuery(e.target.value)}
                  placeholder={t("knowledge.testbedQueryPlaceholder")}
                  className="w-full text-sm border border-gray-300 rounded-md p-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  required
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label
                    htmlFor="testbed-min-score"
                    className="block text-sm font-medium text-gray-700 mb-1"
                  >
                    {t("knowledge.testbedMinScore")} (Current: {testbedMinScore})
                  </label>
                  <div className="flex items-center gap-3">
                    <input
                      id="testbed-min-score"
                      type="range"
                      min="0.0"
                      max="1.0"
                      step="0.01"
                      value={testbedMinScore}
                      onChange={(e) => setTestbedMinScore(parseFloat(e.target.value))}
                      className="w-full accent-blue-600"
                    />
                    <span className="text-sm font-mono text-gray-700 w-12 text-right">
                      {testbedMinScore.toFixed(2)}
                    </span>
                  </div>
                  <span className="text-xs text-gray-400">
                    Default production threshold is 0.72.
                  </span>
                </div>

                <div>
                  <label
                    htmlFor="testbed-limit"
                    className="block text-sm font-medium text-gray-700 mb-1"
                  >
                    {t("knowledge.testbedLimit")} (1 to 5)
                  </label>
                  <input
                    id="testbed-limit"
                    type="number"
                    min="1"
                    max="5"
                    value={testbedLimit}
                    onChange={(e) =>
                      setTestbedLimit(Math.min(5, Math.max(1, parseInt(e.target.value) || 1)))
                    }
                    className="text-sm border border-gray-300 rounded-md px-3 py-1.5 w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <span className="text-xs text-gray-400">
                    Top-K chunks returned (maximum 5 chunks).
                  </span>
                </div>
              </div>

              <div className="pt-2">
                <button
                  type="submit"
                  disabled={testbedLoading || !testbedQuery.trim()}
                  className="px-5 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 text-sm font-medium shadow-sm disabled:opacity-50"
                >
                  {testbedLoading
                    ? t("knowledge.testbedSearching")
                    : t("knowledge.testbedSearch")}
                </button>
              </div>
            </form>
          </div>

          {testbedError && <ErrorState message={testbedError} />}

          {/* Testbed Diagnostic Metrics */}
          {testbedResponse && (
            <div className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="bg-white p-4 rounded-lg border border-gray-200">
                  <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider block">
                    {t("knowledge.testbedTotalScanned")}
                  </span>
                  <span className="text-2xl font-bold text-gray-900 mt-1 block">
                    {testbedResponse.total_chunks_scanned}
                  </span>
                </div>

                <div className="bg-white p-4 rounded-lg border border-gray-200">
                  <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider block">
                    {t("knowledge.testbedResults")}
                  </span>
                  <span className="text-2xl font-bold text-blue-600 mt-1 block">
                    {testbedResponse.total_found}
                  </span>
                </div>

                <div className="bg-white p-4 rounded-lg border border-gray-200">
                  <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider block">
                    {t("knowledge.testbedExcludedUnpublished")}
                  </span>
                  <span className="text-2xl font-bold text-amber-600 mt-1 block">
                    {testbedResponse.excluded_unpublished_matches}
                  </span>
                </div>
              </div>

              {testbedResponse.excluded_unpublished_matches > 0 && (
                <div className="p-3 bg-amber-50 border border-amber-200 rounded-md text-sm text-amber-800">
                  ℹ️ <strong>Notice:</strong>{" "}
                  {t("knowledge.excludedNotice", {
                    count: testbedResponse.excluded_unpublished_matches,
                  })}
                </div>
              )}

              {/* Retrieval Results List */}
              {testbedResponse.results.length === 0 ? (
                <EmptyState
                  title={t("knowledge.testbedNoResults")}
                  description="No published chunks satisfied the current score threshold."
                />
              ) : (
                <div className="space-y-3">
                  <h3 className="text-md font-semibold text-gray-900">
                    Top Ranked Chunks ({testbedResponse.results.length})
                  </h3>
                  {testbedResponse.results.map((item) => (
                    <div
                      key={item.chunk_id}
                      className="bg-white p-5 rounded-lg border border-gray-200 shadow-sm space-y-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 pb-2">
                        <div className="flex items-center gap-2">
                          <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-blue-100 text-blue-800 text-xs font-bold">
                            #{item.rank}
                          </span>
                          <span className="font-semibold text-gray-900 text-sm">
                            {item.document_title}
                          </span>
                        </div>
                        <div className="flex items-center gap-3 text-xs">
                          <span className="bg-emerald-50 text-emerald-700 px-2.5 py-0.5 rounded-full font-mono font-medium border border-emerald-200">
                            Score: {item.score.toFixed(4)}
                          </span>
                          <span className="text-gray-400 font-mono">
                            Offset: {item.start_char}..{item.end_char}
                          </span>
                          <span className="text-gray-400 font-mono">
                            ~{item.token_estimate} tokens
                          </span>
                        </div>
                      </div>

                      <div className="bg-gray-50 p-3 rounded border border-gray-200 text-sm text-gray-800 whitespace-pre-wrap font-sans">
                        {item.content}
                      </div>

                      <div className="text-[11px] text-gray-400 font-mono">
                        Chunk ID: {item.chunk_id}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* MODAL: CREATE / UPLOAD DOCUMENT (Admin Only) */}
      {createModalOpen && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-black/50 flex items-center justify-center p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-modal-title"
            className="bg-white rounded-lg shadow-xl max-w-2xl w-full p-6 space-y-4"
          >
            <div className="flex items-center justify-between border-b border-gray-200 pb-3">
              <h3 id="create-modal-title" className="text-lg font-bold text-gray-900">
                {t("knowledge.newDocument")}
              </h3>
              <button
                type="button"
                onClick={() => setCreateModalOpen(false)}
                className="text-gray-400 hover:text-gray-600 text-lg font-bold"
              >
                ✕
              </button>
            </div>

            {submitError && (
              <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
                {submitError}
              </div>
            )}

            {/* Modal Tabs */}
            <div className="flex border-b border-gray-200">
              <button
                type="button"
                onClick={() => {
                  setCreateTab("upload");
                  setSubmitError(null);
                }}
                className={`py-2 px-4 text-sm font-medium border-b-2 transition-colors ${
                  createTab === "upload"
                    ? "border-blue-600 text-blue-600"
                    : "border-transparent text-gray-500 hover:text-gray-700"
                }`}
              >
                {t("knowledge.uploadTab")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setCreateTab("write");
                  setSubmitError(null);
                }}
                className={`py-2 px-4 text-sm font-medium border-b-2 transition-colors ${
                  createTab === "write"
                    ? "border-blue-600 text-blue-600"
                    : "border-transparent text-gray-500 hover:text-gray-700"
                }`}
              >
                {t("knowledge.writeTab")}
              </button>
            </div>

            <form onSubmit={handleCreateSubmit} className="space-y-4">
              {/* Optional Custom Title */}
              <div>
                <label
                  htmlFor="create-title"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  {t("knowledge.docTitle")}{" "}
                  {createTab === "upload" && (
                    <span className="text-xs text-gray-400 font-normal">
                      (Optional — defaults to filename)
                    </span>
                  )}
                </label>
                <input
                  id="create-title"
                  type="text"
                  value={createTitle}
                  onChange={(e) => setCreateTitle(e.target.value)}
                  placeholder={
                    createTab === "upload" ? "e.g. Acme Return Policy" : "e.g. Shipping Guidelines"
                  }
                  required={createTab === "write"}
                  maxLength={255}
                  className="w-full text-sm border border-gray-300 rounded-md p-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              {/* Upload Tab Form */}
              {createTab === "upload" && (
                <div className="space-y-3">
                  <div className="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center hover:border-blue-400 transition-colors bg-gray-50">
                    <input
                      ref={fileInputRef}
                      type="file"
                      id="file-upload"
                      accept=".txt,.md,.pdf"
                      onChange={(e) => {
                        if (e.target.files && e.target.files[0]) {
                          setUploadFile(e.target.files[0]);
                          if (!createTitle.trim()) {
                            setCreateTitle(e.target.files[0].name.replace(/\.[^/.]+$/, ""));
                          }
                        }
                      }}
                      className="hidden"
                    />
                    <label
                      htmlFor="file-upload"
                      className="cursor-pointer block space-y-2 text-sm text-gray-600"
                    >
                      <span className="text-3xl block">📄</span>
                      <span className="text-blue-600 font-medium hover:underline">
                        {uploadFile ? uploadFile.name : t("knowledge.selectFile")}
                      </span>
                      <p className="text-xs text-gray-500">{t("knowledge.dropzoneHint")}</p>
                      <p className="text-[11px] text-gray-400">{t("knowledge.maxFileSizeHint")}</p>
                    </label>
                  </div>
                  {uploadFile && (
                    <p className="text-xs text-gray-600">
                      Selected: <strong>{uploadFile.name}</strong> (
                      {(uploadFile.size / 1024).toFixed(1)} KB)
                    </p>
                  )}
                </div>
              )}

              {/* Write Tab Form */}
              {createTab === "write" && (
                <div className="space-y-4">
                  <div>
                    <label
                      htmlFor="create-source-type"
                      className="block text-sm font-medium text-gray-700 mb-1"
                    >
                      {t("knowledge.sourceType")}
                    </label>
                    <select
                      id="create-source-type"
                      value={createSourceType}
                      onChange={(e) => setCreateSourceType(e.target.value as SourceType)}
                      className="text-sm border border-gray-300 rounded-md px-3 py-1.5 bg-white text-gray-700"
                    >
                      <option value="text">{t("knowledge.sourceTypeText")}</option>
                      <option value="markdown">{t("knowledge.sourceTypeMarkdown")}</option>
                    </select>
                  </div>

                  <div>
                    <label
                      htmlFor="create-content"
                      className="block text-sm font-medium text-gray-700 mb-1"
                    >
                      {t("knowledge.docContent")}
                    </label>
                    <textarea
                      id="create-content"
                      rows={8}
                      value={createContent}
                      onChange={(e) => setCreateContent(e.target.value)}
                      placeholder="Write or paste your knowledge document content here..."
                      required
                      className="w-full text-sm border border-gray-300 rounded-md p-3 font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                </div>
              )}

              <div className="flex justify-end gap-3 pt-3 border-t border-gray-200">
                <button
                  type="button"
                  onClick={() => setCreateModalOpen(false)}
                  className="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded-md hover:bg-gray-50"
                >
                  {t("common.cancel")}
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 font-medium disabled:opacity-50"
                >
                  {isSubmitting ? t("common.loading") : t("common.create")}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: EDIT DRAFT (Admin Only) */}
      {editModalOpen && editingDoc && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-black/50 flex items-center justify-center p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="edit-modal-title"
            className="bg-white rounded-lg shadow-xl max-w-2xl w-full p-6 space-y-4"
          >
            <div className="flex items-center justify-between border-b border-gray-200 pb-3">
              <h3 id="edit-modal-title" className="text-lg font-bold text-gray-900">
                {t("knowledge.editDocument")}
              </h3>
              <button
                type="button"
                onClick={() => setEditModalOpen(false)}
                className="text-gray-400 hover:text-gray-600 text-lg font-bold"
              >
                ✕
              </button>
            </div>

            {editError && (
              <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
                {editError}
              </div>
            )}

            <form onSubmit={handleSaveEdit} className="space-y-4">
              <div>
                <label
                  htmlFor="edit-title"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  {t("knowledge.docTitle")}
                </label>
                <input
                  id="edit-title"
                  type="text"
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  required
                  maxLength={255}
                  className="w-full text-sm border border-gray-300 rounded-md p-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label
                  htmlFor="edit-content"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  {t("knowledge.docContent")}
                </label>
                <textarea
                  id="edit-content"
                  rows={10}
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  required
                  className="w-full text-sm border border-gray-300 rounded-md p-3 font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <span className="text-xs text-gray-400 mt-1 block">
                  Editing a draft automatically re-runs chunking and embedding generation.
                </span>
              </div>

              <div className="flex justify-end gap-3 pt-3 border-t border-gray-200">
                <button
                  type="button"
                  onClick={() => setEditModalOpen(false)}
                  className="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded-md hover:bg-gray-50"
                >
                  {t("common.cancel")}
                </button>
                <button
                  type="submit"
                  disabled={isEditing}
                  className="px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 font-medium disabled:opacity-50"
                >
                  {isEditing ? t("common.loading") : t("common.save")}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: DOCUMENT DETAIL / CHUNKS PREVIEW */}
      {selectedDocId && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-black/50 flex items-center justify-center p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="detail-modal-title"
            className="bg-white rounded-lg shadow-xl max-w-3xl w-full p-6 space-y-4 max-h-[90vh] flex flex-col"
          >
            <div className="flex items-center justify-between border-b border-gray-200 pb-3 flex-shrink-0">
              <h3 id="detail-modal-title" className="text-lg font-bold text-gray-900 truncate pr-4">
                {detailDoc ? detailDoc.title : t("knowledge.viewDocument")}
              </h3>
              <button
                type="button"
                onClick={closeDetail}
                className="text-gray-400 hover:text-gray-600 text-lg font-bold"
              >
                ✕
              </button>
            </div>

            {detailLoading ? (
              <LoadingState message="Loading document details..." />
            ) : detailError ? (
              <ErrorState message={detailError} onRetry={() => openDetail(selectedDocId)} />
            ) : detailDoc ? (
              <div className="space-y-5 overflow-y-auto flex-1 pr-1">
                {/* Document Metadata Badges */}
                <div className="flex flex-wrap items-center gap-3 bg-gray-50 p-3 rounded-lg border border-gray-200 text-xs">
                  <div>
                    <span className="text-gray-400 block mb-0.5">{t("knowledge.status")}:</span>
                    {renderPublicationBadge(detailDoc.status)}
                  </div>
                  <div>
                    <span className="text-gray-400 block mb-0.5">
                      {t("knowledge.ingestionStatus")}:
                    </span>
                    {renderIngestionBadge(
                      detailDoc.ingestion_status,
                      detailDoc.ingestion_error_code,
                      detailDoc.id,
                      detailDoc.status === "draft"
                    )}
                  </div>
                  <div>
                    <span className="text-gray-400 block mb-0.5">
                      {t("knowledge.sourceType")}:
                    </span>
                    <span className="font-mono bg-white px-2 py-0.5 rounded border border-gray-200">
                      {detailDoc.source_type}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-400 block mb-0.5">{t("knowledge.chunks")}:</span>
                    <span className="font-mono font-bold text-gray-800">
                      {detailDoc.chunks?.length || detailDoc.chunk_count}
                    </span>
                  </div>
                  {detailDoc.published_at && (
                    <div>
                      <span className="text-gray-400 block mb-0.5">
                        {t("knowledge.publishedAt")}:
                      </span>
                      <span className="text-gray-700">
                        {new Date(detailDoc.published_at).toLocaleString()}
                      </span>
                    </div>
                  )}
                </div>

                {/* Content Section */}
                <div>
                  <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
                    Document Text
                  </h4>
                  <div className="bg-gray-50 p-4 rounded-lg border border-gray-200 text-sm font-mono text-gray-800 max-h-48 overflow-y-auto whitespace-pre-wrap">
                    {detailDoc.content || "(No text content)"}
                  </div>
                </div>

                {/* Chunks Listing Section */}
                <div>
                  <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
                    Ingested Chunks ({detailDoc.chunks?.length || 0})
                  </h4>
                  {(!detailDoc.chunks || detailDoc.chunks.length === 0) ? (
                    <p className="text-sm text-gray-400 italic">{t("knowledge.noChunksYet")}</p>
                  ) : (
                    <div className="space-y-3">
                      {detailDoc.chunks.map((chunk) => (
                        <div
                          key={chunk.id}
                          className="bg-white p-3 rounded border border-gray-200 shadow-2xs space-y-1.5"
                        >
                          <div className="flex items-center justify-between text-xs text-gray-500">
                            <span className="font-bold text-gray-700">
                              Chunk #{chunk.ordinal + 1}
                            </span>
                            <span className="font-mono text-gray-400">
                              ~{chunk.token_estimate} tokens
                            </span>
                          </div>
                          <p className="text-xs text-gray-800 bg-gray-50 p-2 rounded whitespace-pre-wrap font-mono">
                            {chunk.content}
                          </p>
                          <span className="text-[10px] text-gray-400 font-mono block">
                            ID: {chunk.id}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : null}

            <div className="flex justify-end pt-3 border-t border-gray-200 flex-shrink-0">
              <button
                type="button"
                onClick={closeDetail}
                className="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded-md hover:bg-gray-50"
              >
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* CONFIRMATION MODAL: DELETE (Admin Only, Draft Only) */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-black/50 flex items-center justify-center p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-dialog-title"
            className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 space-y-4"
          >
            <h3 id="delete-dialog-title" className="text-lg font-bold text-gray-900">
              {t("knowledge.delete")} Document
            </h3>
            <p className="text-sm text-gray-600">
              {t("knowledge.confirmDelete")}
            </p>
            <div className="bg-gray-50 p-3 rounded text-sm font-medium text-gray-800 border border-gray-200">
              {deleteTarget.title}
            </div>
            <div className="flex justify-end gap-3 pt-3 border-t border-gray-200">
              <button
                type="button"
                disabled={isActionLoading}
                onClick={() => setDeleteTarget(null)}
                className="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded-md hover:bg-gray-50"
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                disabled={isActionLoading}
                onClick={handleDeleteConfirm}
                className="px-4 py-2 text-sm bg-rose-600 text-white rounded-md hover:bg-rose-700 font-medium disabled:opacity-50"
              >
                {isActionLoading ? t("common.loading") : t("common.delete")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* CONFIRMATION MODAL: PUBLISH (Admin Only, Ready Draft Only) */}
      {publishTarget && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-black/50 flex items-center justify-center p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="publish-dialog-title"
            className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 space-y-4"
          >
            <h3 id="publish-dialog-title" className="text-lg font-bold text-gray-900">
              {t("knowledge.publish")} Document
            </h3>
            <p className="text-sm text-gray-600">
              {t("knowledge.confirmPublish")}
            </p>
            <div className="p-3 bg-amber-50 border border-amber-200 rounded text-xs text-amber-800 font-medium">
              ⚠️ {t("knowledge.publishWarning")}
            </div>
            <div className="bg-gray-50 p-3 rounded text-sm font-medium text-gray-800 border border-gray-200">
              {publishTarget.title} ({publishTarget.chunk_count} chunks)
            </div>
            <div className="flex justify-end gap-3 pt-3 border-t border-gray-200">
              <button
                type="button"
                disabled={isActionLoading}
                onClick={() => setPublishTarget(null)}
                className="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded-md hover:bg-gray-50"
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                disabled={isActionLoading}
                onClick={handlePublishConfirm}
                className="px-4 py-2 text-sm bg-emerald-600 text-white rounded-md hover:bg-emerald-700 font-medium disabled:opacity-50"
              >
                {isActionLoading ? t("common.loading") : t("knowledge.publish")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* CONFIRMATION MODAL: ARCHIVE (Admin Only, Published Only) */}
      {archiveTarget && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-black/50 flex items-center justify-center p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="archive-dialog-title"
            className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 space-y-4"
          >
            <h3 id="archive-dialog-title" className="text-lg font-bold text-gray-900">
              {t("knowledge.archive")} Document
            </h3>
            <p className="text-sm text-gray-600">
              {t("knowledge.confirmArchive")}
            </p>
            <div className="bg-gray-50 p-3 rounded text-sm font-medium text-gray-800 border border-gray-200">
              {archiveTarget.title}
            </div>
            <div className="flex justify-end gap-3 pt-3 border-t border-gray-200">
              <button
                type="button"
                disabled={isActionLoading}
                onClick={() => setArchiveTarget(null)}
                className="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded-md hover:bg-gray-50"
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                disabled={isActionLoading}
                onClick={handleArchiveConfirm}
                className="px-4 py-2 text-sm bg-amber-600 text-white rounded-md hover:bg-amber-700 font-medium disabled:opacity-50"
              >
                {isActionLoading ? t("common.loading") : t("knowledge.archive")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
