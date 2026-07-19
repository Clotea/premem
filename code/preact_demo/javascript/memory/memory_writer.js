function memoryWriter(history) {
  const memories = [];

  for (const turn of history) {
    for (const raw of turn.memories || []) {
      const id = `m_${String(memories.length + 1).padStart(3, "0")}`;
      memories.push({
        id,
        node_type: "MemoryNode",
        type: raw.type,
        content: raw.content,
        summary: raw.summary,
        keywords: raw.keywords || [],
        entities: raw.entities || [],
        segment_id: turn.segment_id,
        segment_summary: turn.segment_summary,
        source_turn_id: turn.id,
        timestamp: turn.timestamp,
        importance: raw.importance || 0.5
      });
    }
  }

  return memories;
}

module.exports = {
  memoryWriter
};
