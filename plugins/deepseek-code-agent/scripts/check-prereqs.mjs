#!/usr/bin/env node
import { DeepSeekController } from "../src/controller.mjs";

const controller = new DeepSeekController();
try {
  const options = {};
  const args = process.argv.slice(2);
  for (let index = 0; index < args.length; index += 2) {
    const key = args[index].replace(/^--/, "");
    if (!args[index].startsWith("--") || !["provider", "model", "variant", "workspace"].includes(key) || args[index + 1] === undefined) {
      throw new Error("Usage: check-prereqs.mjs [--provider opencode-go|deepseek] [--model ID] [--variant NAME|null] [--workspace PATH]");
    }
    options[key] = key === "variant" && args[index + 1] === "null" ? null : args[index + 1];
  }
  const result = await controller.check(options);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  process.exitCode = result.ok ? 0 : 1;
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
} finally {
  controller.shutdown();
}
