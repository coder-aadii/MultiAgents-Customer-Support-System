import test from "node:test";
import assert from "node:assert";
import { isAbortError, toSafeErrorMessage } from "./errors.ts";
import { lookupLocaleKey } from "./i18n.ts";
import { tokenManager } from "../api/client.ts";
import { reconcileMessages, type Message } from "./reconciliation.ts";

test("Sub-phase 2.2: expired-auth reset clears token and fires callback", () => {
  let callbackFired = false;
  tokenManager.setAccessToken("test-sample-token");
  assert.strictEqual(tokenManager.getAccessToken(), "test-sample-token");

  tokenManager.setOnAuthExpired(() => {
    callbackFired = true;
  });

  tokenManager.triggerAuthExpired();
  assert.strictEqual(tokenManager.getAccessToken(), null);
  assert.strictEqual(callbackFired, true);

  tokenManager.setOnAuthExpired(null);
});

test("Sub-phase 2.2: request cancellation detection and safe mapping", () => {
  const domAbort = new DOMException("The operation was aborted.", "AbortError");
  assert.strictEqual(isAbortError(domAbort), true);
  assert.strictEqual(toSafeErrorMessage(domAbort), "Request canceled");

  const canceledError = { name: "CanceledError", message: "canceled" };
  assert.strictEqual(isAbortError(canceledError), true);
  assert.strictEqual(toSafeErrorMessage(canceledError), "Request canceled");

  const normalError = new Error("Regular failure");
  assert.strictEqual(isAbortError(normalError), false);
  assert.strictEqual(toSafeErrorMessage(normalError), "An unexpected error occurred.");
});

test("Sub-phase 2.2: safe error mapping never exposes raw server traces", () => {
  const axiosLike401 = {
    isAxiosError: true,
    response: {
      status: 401,
      data: { error: { code: "UNAUTHORIZED", message: "Internal server stack trace: line 42" } },
    },
  };
  const msg401 = toSafeErrorMessage(axiosLike401);
  assert.strictEqual(msg401, "Your session has expired. Please sign in again.");
  assert.ok(!msg401.includes("stack trace"));

  const axiosLike409 = {
    isAxiosError: true,
    response: {
      status: 409,
      data: { error: { code: "CONVERSATION_CLOSED", message: "Private DB exception" } },
    },
  };
  const msg409 = toSafeErrorMessage(axiosLike409);
  assert.strictEqual(msg409, "This conversation is closed.");
  assert.ok(!msg409.includes("Private DB"));

  const axiosNetworkErr = {
    isAxiosError: true,
    request: {},
  };
  const netMsg = toSafeErrorMessage(axiosNetworkErr);
  assert.strictEqual(netMsg, "Network error. Please check your internet connection.");
});

test("Sub-phase 2.2: locale lookup safely handles missing keys and parameter replacement", () => {
  const dictionary = {
    common: {
      save: "Save",
      welcome: "Welcome, {name}!",
    },
    nav: {
      inbox: "Inbox",
    },
  };

  // Valid key
  assert.strictEqual(lookupLocaleKey(dictionary, "common.save"), "Save");

  // Valid key with interpolation
  assert.strictEqual(
    lookupLocaleKey(dictionary, "common.welcome", { name: "Alice" }),
    "Welcome, Alice!"
  );

  // Missing nested key safe fallback
  assert.strictEqual(lookupLocaleKey(dictionary, "common.nonexistent"), "common.nonexistent");
  assert.strictEqual(lookupLocaleKey(dictionary, "totally.invalid.key"), "totally.invalid.key");
});

test("Sub-phase 2.2: message reconciliation deduplicates and sorts stably by (created_at, id)", () => {
  const existing: Message[] = [
    {
      id: "msg-1",
      conversation_id: "conv-1",
      client_message_id: "client-uuid-1",
      sender_type: "visitor",
      content: "Hello",
      created_at: "2026-10-30T10:00:00Z",
    },
    {
      id: "msg-2",
      conversation_id: "conv-1",
      client_message_id: "client-uuid-2",
      sender_type: "staff",
      content: "Hi there",
      created_at: "2026-10-30T10:01:00Z",
    },
  ];

  const incoming: Message[] = [
    {
      id: "msg-2", // Duplicate id (server replay)
      conversation_id: "conv-1",
      client_message_id: "client-uuid-2",
      sender_type: "staff",
      content: "Hi there",
      created_at: "2026-10-30T10:01:00Z",
    },
    {
      id: "msg-3", // New message
      conversation_id: "conv-1",
      client_message_id: "client-uuid-3",
      sender_type: "visitor",
      content: "How are you?",
      created_at: "2026-10-30T10:02:00Z",
    },
  ];

  const reconciled = reconcileMessages(existing, incoming);
  assert.strictEqual(reconciled.length, 3);
  assert.strictEqual(reconciled[0].id, "msg-1");
  assert.strictEqual(reconciled[1].id, "msg-2");
  assert.strictEqual(reconciled[2].id, "msg-3");
});
