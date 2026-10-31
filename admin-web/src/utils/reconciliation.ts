export type SenderType = "visitor" | "staff" | "assistant" | "system";

export interface MessageMetadata {
  cited_chunk_ids?: string[];
  confidence?: number;
  handoff_reason?: string;
  model?: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  client_message_id?: string | null;
  sender_type: SenderType;
  sender_user_id?: string | null;
  content: string;
  metadata?: MessageMetadata;
  created_at: string;
}

/**
 * Reconciles incoming messages with existing messages:
 * 1. Deduplicates by immutable message ID.
 * 2. Matches pending optimistic messages by client_message_id if present.
 * 3. Preserves deterministic chronological order by (created_at, id).
 */
export function reconcileMessages(
  existingMessages: Message[],
  incomingMessages: Message[]
): Message[] {
  const messageMap = new Map<string, Message>();
  const clientMsgIdToId = new Map<string, string>();

  for (const msg of existingMessages) {
    messageMap.set(msg.id, msg);
    if (msg.client_message_id) {
      clientMsgIdToId.set(msg.client_message_id, msg.id);
    }
  }

  for (const msg of incomingMessages) {
    if (msg.client_message_id && clientMsgIdToId.has(msg.client_message_id)) {
      const existingId = clientMsgIdToId.get(msg.client_message_id)!;
      if (existingId !== msg.id) {
        messageMap.delete(existingId);
      }
    }
    messageMap.set(msg.id, msg);
    if (msg.client_message_id) {
      clientMsgIdToId.set(msg.client_message_id, msg.id);
    }
  }

  const result = Array.from(messageMap.values());
  result.sort((a, b) => {
    const timeA = new Date(a.created_at).getTime();
    const timeB = new Date(b.created_at).getTime();
    if (timeA !== timeB) {
      return timeA - timeB;
    }
    return a.id.localeCompare(b.id);
  });

  return result;
}

export interface PollingOptions {
  intervalMs?: number;
  onPoll: (signal: AbortSignal) => Promise<void>;
  onError?: (error: unknown) => void;
  isActive?: () => boolean;
}

/**
 * Polling controller conforming to Phase 2 contract:
 * - Polls every 3000ms while visible and active
 * - Halts on document hidden, resumes immediately on visible
 * - Cancels stale/overlapping requests with AbortController
 * - Cleans up on stop()
 */
export class PollingController {
  private intervalMs: number;
  private onPoll: (signal: AbortSignal) => Promise<void>;
  private onError?: (error: unknown) => void;
  private isActive: () => boolean;

  private timerId: ReturnType<typeof setTimeout> | null = null;
  private abortController: AbortController | null = null;
  private isPolling = false;
  private isStopped = false;

  constructor(options: PollingOptions) {
    this.intervalMs = options.intervalMs ?? 3000;
    this.onPoll = options.onPoll;
    this.onError = options.onError;
    this.isActive = options.isActive ?? (() => true);

    this.handleVisibilityChange = this.handleVisibilityChange.bind(this);
  }

  public start(): void {
    if (this.isStopped) return;
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", this.handleVisibilityChange);
    }
    this.scheduleNext(0);
  }

  public stop(): void {
    this.isStopped = true;
    this.clearTimer();
    this.cancelInFlight();
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", this.handleVisibilityChange);
    }
  }

  private handleVisibilityChange(): void {
    if (typeof document === "undefined" || this.isStopped) return;

    if (document.visibilityState === "visible") {
      if (this.isActive()) {
        this.clearTimer();
        this.cancelInFlight();
        this.executePoll();
      }
    } else {
      this.clearTimer();
      this.cancelInFlight();
    }
  }

  private scheduleNext(delay: number): void {
    this.clearTimer();
    if (this.isStopped) return;

    this.timerId = setTimeout(() => {
      this.executePoll();
    }, delay);
  }

  private clearTimer(): void {
    if (this.timerId !== null) {
      clearTimeout(this.timerId);
      this.timerId = null;
    }
  }

  private cancelInFlight(): void {
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }
    this.isPolling = false;
  }

  private async executePoll(): Promise<void> {
    if (this.isStopped) return;

    if (typeof document !== "undefined" && document.visibilityState !== "visible") {
      return;
    }

    if (!this.isActive()) {
      this.scheduleNext(this.intervalMs);
      return;
    }

    if (this.isPolling) {
      return;
    }

    this.cancelInFlight();
    this.abortController = new AbortController();
    const signal = this.abortController.signal;
    this.isPolling = true;

    try {
      await this.onPoll(signal);
    } catch (err) {
      if (!signal.aborted && this.onError) {
        this.onError(err);
      }
    } finally {
      this.isPolling = false;
      if (!this.isStopped) {
        this.scheduleNext(this.intervalMs);
      }
    }
  }
}

