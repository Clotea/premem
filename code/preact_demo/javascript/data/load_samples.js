const fs = require("node:fs");
const path = require("node:path");
const { extractKeywords } = require("../common/text");

const LOCOMO_URL =
  "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json";

async function loadSamples(options) {
  const dataset = options.dataset || "demo";
  if (dataset === "demo") {
    const samples = JSON.parse(fs.readFileSync(options.demoPath, "utf8"));
    return applyLimit(samples, options.limit);
  }

  if (dataset === "locomo") {
    if (!fs.existsSync(options.locomoPath)) {
      if (!options.downloadLocomo) {
        throw new Error(
          `LoCoMo file not found: ${options.locomoPath}\n` +
            `Run with --download-locomo, or download it from ${LOCOMO_URL}`
        );
      }
      await downloadFile(LOCOMO_URL, options.locomoPath);
    }

    const raw = JSON.parse(fs.readFileSync(options.locomoPath, "utf8"));
    return applyLimit(convertLocomo(raw), options.limit);
  }

  throw new Error(`Unknown dataset: ${dataset}`);
}

function applyLimit(samples, limit) {
  if (limit === undefined || limit === null) return samples;
  if (limit === 0) return samples;
  if (limit < 0) throw new Error("--limit must be >= 0");
  return samples.slice(0, limit);
}

function convertLocomo(records) {
  const samples = [];

  records.forEach((record, conversationIndex) => {
    const conversationId = `locomo_c${String(conversationIndex + 1).padStart(2, "0")}`;
    const history = convertConversation(record.conversation || {}, conversationId);

    (record.qa || []).forEach((qa, qaIndex) => {
      samples.push({
        id: `${conversationId}_qa_${String(qaIndex + 1).padStart(3, "0")}`,
        history_cache_key: conversationId,
        history,
        question: String(qa.question || ""),
        answer: String(qa.answer || qa.adversarial_answer || ""),
        evidence_terms: extractKeywords(
          `${qa.question || ""} ${qa.answer || qa.adversarial_answer || ""}`,
          12
        ),
        gold_evidence_turn_ids: Array.isArray(qa.evidence) ? qa.evidence : [],
        metadata: {
          dataset: "locomo",
          conversation_id: conversationId,
          category: qa.category
        }
      });
    });
  });

  return samples;
}

function convertConversation(conversation, conversationId) {
  const turns = [];
  let timestamp = 0;
  const sessionNumbers = Object.keys(conversation)
    .map((key) => {
      const match = key.match(/^session_(\d+)$/);
      return match && Array.isArray(conversation[key]) ? Number(match[1]) : null;
    })
    .filter((value) => value !== null)
    .sort((a, b) => a - b);

  for (const sessionNumber of sessionNumbers) {
    const sessionId = `seg_${conversationId}_${sessionNumber}`;
    const dateTime = conversation[`session_${sessionNumber}_date_time`] || "";
    const sessionSummary = conversation[`session_${sessionNumber}_summary`] || dateTime;

    for (const rawTurn of conversation[`session_${sessionNumber}`]) {
      timestamp += 1;
      const text = [rawTurn.text, rawTurn.blip_caption].filter(Boolean).join(" ");
      const turnId = rawTurn.dia_id || `D${sessionNumber}:${timestamp}`;

      turns.push({
        id: turnId,
        timestamp,
        speaker: rawTurn.speaker || "unknown",
        segment_id: sessionId,
        segment_summary: sessionSummary,
        text,
        memories: [
          {
            type: inferMemoryType(text),
            content: `${rawTurn.speaker || "unknown"}: ${text}`,
            summary: truncate(`${rawTurn.speaker || "unknown"}: ${text}`, 180),
            keywords: extractKeywords(text, 10),
            entities: extractEntities(text),
            importance: estimateImportance(text)
          }
        ]
      });
    }
  }

  return turns;
}

function inferMemoryType(text) {
  const lower = String(text || "").toLowerCase();
  if (lower.includes("i like") || lower.includes("i love") || lower.includes("favorite")) {
    return "preference";
  }
  if (lower.includes("will") || lower.includes("plan") || lower.includes("going to")) {
    return "task";
  }
  if (/\b(yesterday|today|tomorrow|week|month|year|202\d)\b/i.test(text)) {
    return "event";
  }
  return "fact";
}

function estimateImportance(text) {
  const tokens = extractKeywords(text, 20).length;
  return Math.min(0.95, 0.35 + tokens * 0.03);
}

function extractEntities(text) {
  const matches = String(text || "").match(/\b[A-Z][a-z]{2,}\b/g) || [];
  return [...new Set(matches)].slice(0, 8);
}

function truncate(text, maxLength) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  return value.length <= maxLength ? value : `${value.slice(0, maxLength - 3)}...`;
}

async function downloadFile(url, destination) {
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to download ${url}: HTTP ${response.status}`);
  }
  fs.writeFileSync(destination, await response.text(), "utf8");
}

module.exports = {
  LOCOMO_URL,
  loadSamples
};
