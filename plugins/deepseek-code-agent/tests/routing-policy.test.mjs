import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const PILOT_URL = new URL("../../../docs/task-routing-pilot-2026-09-15.json", import.meta.url);
const BASELINE_COMMIT = "90d84be225fe2751ed0583917e7391ff29245559";
const SIZES = ["small", "medium", "large"];
const EXPECTED = {
  direct_proxy_total: 202201,
  worker_proxy_total: 174338,
  controller_proxy_total: 90157,
  worker_proxy_reduction_percent: 13.78,
  controller_proxy_reduction_percent: 55.41,
  direct_total_sum: 3088345,
  worker_total_sum: 6155472,
};

const pilot = JSON.parse(readFileSync(PILOT_URL, "utf8"));

function round2(value) {
  return Math.round(value * 100) / 100;
}

function assertNumber(value, label) {
  assert.equal(typeof value, "number", `${label} must be a number`);
  assert.ok(Number.isFinite(value), `${label} must be finite`);
}

test("pilot JSON declares one non-alternating pair per size", () => {
  assert.equal(pilot.schema_version, 1);
  assert.equal(pilot.baseline_commit, BASELINE_COMMIT);
  assert.equal(pilot.date, "2026-09-15");
  assert.deepEqual(pilot.task_sizes, SIZES);
  assert.deepEqual(pilot.design.arms, ["direct_codex", "delegated_deepseek"]);
  assert.equal(pilot.design.repetitions_per_cell, 1);
  assert.equal(pilot.design.execution, "parallel");
  assert.equal(pilot.design.order_alternated, false);
  assert.equal(pilot.design.matches_queen_protocol, false);
  assert.equal(pilot.design.wall_time_used_for_token_reduction, false);
});

test("every execution is pinned to the same baseline commit", () => {
  for (const size of SIZES) {
    assert.equal(pilot.direct_codex[size].commit, BASELINE_COMMIT, `direct ${size} commit`);
    assert.equal(
      pilot.delegated_deepseek[size].commit,
      BASELINE_COMMIT,
      `delegated ${size} commit`,
    );
  }
});

test("raw totals recompute from reported counters", () => {
  for (const size of SIZES) {
    const direct = pilot.direct_codex[size];
    for (const field of ["input", "cached_input", "output", "reasoning", "total"]) {
      assertNumber(direct[field], `direct_codex.${size}.${field}`);
    }
    assert.equal(direct.total, direct.input + direct.output, `direct ${size} total`);
    assert.equal(direct.acceptance, true, `direct ${size} acceptance`);
    assertNumber(direct.corrections, `direct_codex.${size}.corrections`);
    assert.ok(!("cost_usd" in direct), "unknown direct cost must not be fabricated as a number");

    const worker = pilot.delegated_deepseek[size];
    for (const field of ["input", "cache_read", "output", "reasoning", "total", "cost_usd"]) {
      assertNumber(worker[field], `delegated_deepseek.${size}.${field}`);
    }
    assert.equal(
      worker.total,
      worker.input + worker.cache_read + worker.output + worker.reasoning,
      `worker ${size} total`,
    );
    assert.equal(worker.acceptance, true, `delegated ${size} acceptance`);
    assertNumber(worker.corrections, `delegated_deepseek.${size}.corrections`);
    assert.equal(typeof worker.usage_complete, "boolean", `worker ${size} usage_complete`);
  }

  const controller = pilot.controller_interval;
  for (const field of ["input", "cached_input", "output", "reasoning", "total", "proxy"]) {
    assertNumber(controller[field], `controller_interval.${field}`);
  }
  assert.equal(controller.total, controller.input + controller.output, "controller total");
  assert.equal(controller.noncached_input, controller.input - controller.cached_input);
  assert.equal(controller.proxy, controller.noncached_input + controller.output);
  assert.equal(controller.pure_delegation_bill, false);
});

test("proxy aggregates recompute from raw fields", () => {
  const directProxy = SIZES.reduce((sum, size) => {
    const record = pilot.direct_codex[size];
    return sum + (record.input - record.cached_input) + record.output;
  }, 0);
  const workerProxy = SIZES.reduce((sum, size) => {
    const record = pilot.delegated_deepseek[size];
    return sum + record.input + record.output;
  }, 0);

  assert.equal(directProxy, EXPECTED.direct_proxy_total);
  assert.equal(workerProxy, EXPECTED.worker_proxy_total);
  assert.equal(pilot.controller_interval.proxy, EXPECTED.controller_proxy_total);

  assert.equal(pilot.derived.direct_proxy_total, EXPECTED.direct_proxy_total);
  assert.equal(pilot.derived.worker_proxy_total, EXPECTED.worker_proxy_total);
  assert.equal(pilot.derived.controller_proxy_total, EXPECTED.controller_proxy_total);
});

test("reduction percentages recompute to the published values", () => {
  const directProxy = pilot.derived.direct_proxy_total;
  const workerReduction = round2(
    ((directProxy - pilot.derived.worker_proxy_total) / directProxy) * 100,
  );
  const controllerReduction = round2(
    ((directProxy - pilot.derived.controller_proxy_total) / directProxy) * 100,
  );

  assert.equal(workerReduction, EXPECTED.worker_proxy_reduction_percent);
  assert.equal(controllerReduction, EXPECTED.controller_proxy_reduction_percent);
  assert.equal(
    pilot.derived.worker_proxy_reduction_vs_direct_percent,
    EXPECTED.worker_proxy_reduction_percent,
  );
  assert.equal(
    pilot.derived.controller_proxy_reduction_vs_direct_percent,
    EXPECTED.controller_proxy_reduction_percent,
  );
});

test("published totals, acceptance and corrections are preserved", () => {
  const directTotal = SIZES.reduce((sum, size) => sum + pilot.direct_codex[size].total, 0);
  const workerTotal = SIZES.reduce(
    (sum, size) => sum + pilot.delegated_deepseek[size].total,
    0,
  );
  assert.equal(directTotal, EXPECTED.direct_total_sum);
  assert.equal(workerTotal, EXPECTED.worker_total_sum);
  assert.equal(pilot.derived.direct_total_sum, EXPECTED.direct_total_sum);
  assert.equal(pilot.derived.worker_total_sum, EXPECTED.worker_total_sum);
  assert.ok(workerTotal > directTotal, "cache-inclusive worker total must remain visible");

  assert.deepEqual(
    SIZES.map((size) => pilot.direct_codex[size].corrections),
    [0, 0, 0],
  );
  assert.deepEqual(
    SIZES.map((size) => pilot.delegated_deepseek[size].corrections),
    [0, 0, 1],
  );
  assert.equal(pilot.delegated_deepseek.small.usage_complete, true);
  assert.equal(pilot.delegated_deepseek.medium.usage_complete, false);
  assert.equal(pilot.delegated_deepseek.large.usage_complete, false);

  assert.equal(pilot.review_corrections.length, 1);
  assert.equal(pilot.review_corrections[0].task_size, "large");
  assert.equal(pilot.review_corrections[0].rounds, 1);
  assert.match(pilot.review_corrections[0].finding, /stale lease/);
  assert.match(pilot.review_corrections[0].finding, /cooldown/);
});

test("task definitions cover all three sizes with stable non-empty IDs", () => {
  const ids = new Set();
  for (const size of SIZES) {
    const task = pilot.tasks[size];
    assert.ok(task, `tasks.${size} is required`);
    assert.equal(typeof task.task_id, "string", `tasks.${size}.task_id`);
    assert.ok(task.task_id.trim().length > 0, `tasks.${size}.task_id must not be empty`);
    assert.ok(!ids.has(task.task_id), `task_id ${task.task_id} must be unique`);
    ids.add(task.task_id);
    assert.equal(typeof task.definition, "string", `tasks.${size}.definition`);
    assert.ok(task.definition.trim().length > 0, `tasks.${size}.definition must not be empty`);
    assert.ok(Array.isArray(task.scope) && task.scope.length > 0, `tasks.${size}.scope`);
    for (const entry of task.scope) {
      assert.equal(typeof entry, "string", `tasks.${size}.scope entry`);
      assert.ok(entry.trim().length > 0, `tasks.${size}.scope entry must not be empty`);
    }
  }
});

test("wall times are positive observations with recorded sources", () => {
  const durations = [];
  for (const size of SIZES) {
    for (const arm of ["direct_codex", "delegated_deepseek"]) {
      const record = pilot[arm][size];
      assertNumber(record.wall_time_seconds, `${arm}.${size}.wall_time_seconds`);
      assert.ok(record.wall_time_seconds > 0, `${arm}.${size}.wall_time_seconds must be positive`);
      assert.equal(typeof record.wall_time_source, "string", `${arm}.${size}.wall_time_source`);
      assert.ok(
        record.wall_time_source.trim().length > 0,
        `${arm}.${size}.wall_time_source must not be empty`,
      );
      durations.push({ arm, size, seconds: record.wall_time_seconds });
    }
  }
  for (const size of SIZES) {
    assert.ok(
      pilot.delegated_deepseek[size].wall_time_seconds >
        pilot.direct_codex[size].wall_time_seconds,
      `delegated ${size} wall time should exceed direct`,
    );
  }
  const longest = durations.reduce((max, entry) => (entry.seconds > max.seconds ? entry : max));
  assert.equal(longest.arm, "delegated_deepseek");
  assert.equal(longest.size, "large");
});

test("large delegated wall time includes its single correction round", () => {
  const large = pilot.delegated_deepseek.large;
  assert.equal(large.corrections, 1);
  assert.equal(large.wall_time_includes_correction_rounds, 1);
  assert.ok(large.wall_time_seconds > pilot.delegated_deepseek.medium.wall_time_seconds);
});

test("controller acceptance counts and quality checks are recorded", () => {
  const expected = {
    direct_codex: {
      small: { tests_total: 32 },
      medium: { tests_total: 51 },
      large: { tests_service: 21, tests_api: 49 },
    },
    delegated_deepseek: {
      small: { tests_total: 41 },
      medium: { tests_total: 120 },
      large: { tests_service: 25, tests_api: 53 },
    },
  };
  for (const arm of Object.keys(expected)) {
    for (const size of SIZES) {
      const acceptance = pilot[arm][size].controller_acceptance;
      assert.ok(acceptance, `${arm}.${size}.controller_acceptance is required`);
      assert.equal(acceptance.ruff, "passed", `${arm}.${size} ruff`);
      assert.equal(acceptance.format, "passed", `${arm}.${size} format`);
      assert.equal(acceptance.diff, "passed", `${arm}.${size} diff`);
      for (const [field, value] of Object.entries(expected[arm][size])) {
        assertNumber(acceptance[field], `${arm}.${size}.${field}`);
        assert.ok(acceptance[field] > 0, `${arm}.${size}.${field} must be positive`);
        assert.equal(acceptance[field], value, `${arm}.${size}.${field}`);
      }
    }
  }
});
