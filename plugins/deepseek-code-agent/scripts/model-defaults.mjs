#!/usr/bin/env node
import { DeepSeekController } from "../src/controller.mjs";

const usage =
  "Usage: model-defaults.mjs get|set|reset [--provider opencode-go|deepseek] [--model ID] [--variant NAME|null] [--workspace PATH]";

const controller = new DeepSeekController();
try {
  const args = process.argv.slice(2);
  const [action, ...rest] = args;
  if (!action || !["get", "set", "reset"].includes(action)) throw new Error(usage);
  const options = { action };
  for (let index = 0; index < rest.length; index += 2) {
    const key = (rest[index] ?? "").replace(/^--/, "");
    if (
      !rest[index]?.startsWith("--") ||
      !["provider", "model", "variant", "workspace"].includes(key) ||
      rest[index + 1] === undefined
    ) {
      throw new Error(usage);
    }
    if (action !== "set") throw new Error(`${action} takes no options`);
    options[key] = key === "variant" && rest[index + 1] === "null" ? null : rest[index + 1];
  }
  const result = await controller.modelDefaults(options);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  process.exitCode = 0;
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
} finally {
  controller.shutdown();
}
