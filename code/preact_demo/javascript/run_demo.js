const fs = require("node:fs");
const path = require("node:path");
const { runEvaluation } = require("./evaluation/run_eval");
const { LOCOMO_URL, loadSamples } = require("./data/load_samples");

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});

async function main() {
  const root = path.resolve(__dirname);
  const projectRoot = path.resolve(root, "..");
  const args = parseArgs(process.argv.slice(2));
  const config = JSON.parse(fs.readFileSync(path.join(root, "configs", "demo.json"), "utf8"));
  const limit = args.limit === undefined ? undefined : Number(args.limit);
  const dataset = args.dataset || "demo";
  const locomoPath = path.resolve(
    args.locomoPath || path.join(projectRoot, "data", "locomo", "locomo10.json")
  );
  const samples = await loadSamples({
    dataset,
    demoPath: path.join(projectRoot, "data", "samples.json"),
    locomoPath,
    downloadLocomo: Boolean(args.downloadLocomo),
    limit
  });

  if (samples.length === 0) {
    throw new Error("No samples loaded.");
  }

  const summary = runEvaluation(samples, config);

  printSection("PreAct-Memory Lite Demo");
  console.log(`Dataset: ${dataset}`);
  console.log(`Samples: ${samples.length}`);
  console.log(`Limit: ${limit === 0 ? "all" : limit ?? "default/all loaded"}`);
  if (dataset === "locomo") {
    console.log(`LoCoMo path: ${locomoPath}`);
    console.log(`LoCoMo source: ${LOCOMO_URL}`);
  }
  console.log(`Cache budget: ${config.cacheBudget}`);
  console.log(`Verifier threshold: ${config.verifierThreshold}`);

  printSection("Activation Quality");
  printTable(summary, ["method", "budget", "precision", "recall", "hit_rate", "wasted_rate"]);

  printSection("Answer Quality");
  printTable(summary, ["method", "f1", "rouge_l", "llm_judge", "faithfulness"]);

  printSection("Efficiency");
  printTable(summary, [
    "method",
    "query_time_latency_ms",
    "idle_time_cost",
    "total_tokens",
    "hit_rate",
    "fallback_rate"
  ]);

  if (args.details || samples.length <= 20) {
    printSection("Per-Sample Selected Memories");
    for (const row of summary) {
      console.log(`\n${row.method}`);
      for (const sample of row.samples) {
        console.log(`  ${sample.sample_id}: ${sample.selected_memory_ids.join(", ") || "(none)"}`);
      }
    }
  } else {
    console.log("\nPer-sample details hidden for large runs. Add --details to print them.");
  }
}

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--dataset") args.dataset = argv[++i];
    else if (arg === "--limit") args.limit = argv[++i];
    else if (arg === "--locomo-path") args.locomoPath = argv[++i];
    else if (arg === "--download-locomo") args.downloadLocomo = true;
    else if (arg === "--details") args.details = true;
    else if (arg === "--help" || arg === "-h") {
      printHelp();
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return args;
}

function printHelp() {
  console.log(`Usage:
  node preact_demo/javascript/run_demo.js
  node preact_demo/javascript/run_demo.js --dataset locomo --limit 10 --download-locomo
  node preact_demo/javascript/run_demo.js --dataset locomo --limit 0

Options:
  --dataset demo|locomo       Dataset to run. Default: demo
  --limit N                   Number of QA samples. Use 0 for all loaded samples.
  --locomo-path PATH          Path to locomo10.json.
  --download-locomo           Download LoCoMo if --locomo-path is missing.
  --details                   Print per-sample selected memories for large runs.
`);
}

function printSection(title) {
  console.log(`\n=== ${title} ===`);
}

function printTable(rows, columns) {
  const formatted = rows.map((row) => {
    const item = {};
    for (const column of columns) {
      const value = row[column];
      item[column] = typeof value === "number" ? Number(value.toFixed(3)) : value;
    }
    return item;
  });
  console.table(formatted);
}
