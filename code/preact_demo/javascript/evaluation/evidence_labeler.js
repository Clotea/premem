const { tokenize } = require("../common/text");

function labelGoldEvidence({ evidenceTerms, evidenceTurnIds, memoryNodes }) {
  const turnIds = new Set(evidenceTurnIds || []);
  if (turnIds.size > 0) {
    return memoryNodes
      .filter((memory) => turnIds.has(memory.source_turn_id))
      .map((memory) => memory.id);
  }

  const terms = new Set(tokenize((evidenceTerms || []).join(" ")));

  return memoryNodes
    .filter((memory) => {
      const memoryTerms = tokenize([
        memory.content,
        memory.summary,
        ...(memory.keywords || []),
        ...(memory.entities || [])
      ].join(" "));
      const hits = memoryTerms.filter((term) => terms.has(term)).length;
      return hits >= 2;
    })
    .map((memory) => memory.id);
}

module.exports = {
  labelGoldEvidence
};
