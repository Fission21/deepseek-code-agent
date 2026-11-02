import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { createLink } from "../skills/dsh-delegate/runtime/controller_link.mjs";

function mailbox() {
  const base = mkdtempSync(path.join(tmpdir(), "dsh-link-"));
  for (const dir of ["inbox", "delivered", "outbox", "questions", "replies"]) {
    mkdirSync(path.join(base, "mailbox", dir), { recursive: true });
  }
  return { base, mailbox: path.join(base, "mailbox") };
}

function link(mailboxPath) {
  const tools = {};
  const ctx = { on() {}, tools: { register(tool) { tools[tool.name] = tool; } } };
  createLink(ctx, { mailbox: mailboxPath, invocation: "/tmp/result.json" }, {
    createUserMessage: (value) => value,
    defineTool: (value) => value,
  });
  return tools;
}

function messages(mailboxPath) {
  return readdirSync(path.join(mailboxPath, "outbox"))
    .filter((file) => file.endsWith(".json"))
    .map((file) => JSON.parse(readFileSync(path.join(mailboxPath, "outbox", file), "utf8")))
    .sort((left, right) => left.seq - right.seq);
}

test("controller messages use a persistent monotonic sequence across restarts", async () => {
  const { base, mailbox: box } = mailbox();
  try {
    let tools = link(box);
    await tools.message_parent.execute({ message: "first" });
    await tools.message_parent.execute({ message: "second" });
    const firstBatch = messages(box);
    assert.deepEqual(firstBatch.map((message) => message.seq), [1, 2]);
    assert.deepEqual(firstBatch.map((message) => message.message), ["first", "second"]);
    for (const message of firstBatch) {
      assert.match(message.id, /^m-\d{12}-/);
    }

    // A fresh controller link reads outbox.seq instead of restarting at 1.
    tools = link(box);
    await tools.message_parent.execute({ message: "third" });
    const secondBatch = messages(box);
    assert.deepEqual(secondBatch.map((message) => message.seq), [1, 2, 3]);
    assert.equal(secondBatch[2].message, "third");
  } finally {
    rmSync(base, { recursive: true, force: true });
  }
});

test("legacy unsequenced outbox records do not reset the new sequence", async () => {
  const { base, mailbox: box } = mailbox();
  try {
    const legacy = { id: "m-9999999999999-legacy", message: "old" };
    writeFileSync(path.join(box, "outbox", `${legacy.id}.json`), JSON.stringify(legacy));
    const tools = link(box);
    await tools.message_parent.execute({ message: "new" });
    const all = messages(box);
    const added = all.find((message) => message.message === "new");
    assert.equal(added.seq, 1);
    assert.match(added.id, /^m-000000000001-/);
  } finally {
    rmSync(base, { recursive: true, force: true });
  }
});
