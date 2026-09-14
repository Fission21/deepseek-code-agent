#!/usr/bin/env node
import { DeepSeekController } from "../src/controller.mjs";

const controller = new DeepSeekController();
try {
  const result = await controller.check();
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  process.exitCode = result.ok ? 0 : 1;
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
} finally {
  controller.shutdown();
}
