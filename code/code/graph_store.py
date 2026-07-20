from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence

from .utils import MemoryNode, Turn, jaccard, tokenize


NODE_MEMORY = "MemoryNode"
NODE_TURN = "TurnNode"
NODE_SEGMENT = "SegmentNode"
NODE_ENTITY = "EntityNode"
NODE_INTENT = "IntentNode"
NODE_FACT = "FactNode"

EDGE_DERIVED_FROM = "derived_from"
EDGE_BELONGS_TO = "belongs_to"
EDGE_MENTIONS = "mentions"
EDGE_SIMILAR_TO = "similar_to"
EDGE_TEMPORAL_NEXT = "temporal_next"
EDGE_INTENT_ACTIVATES = "intent_activates"
EDGE_TARGETS_FACT = "targets_fact"
EDGE_EXPRESSES_FACT = "expresses_fact"


@dataclass
class Edge:
    source: str
    target: str
    edge_type: str

    def to_dict(self) -> Dict[str, str]:
        return {"source": self.source, "target": self.target, "type": self.edge_type}


class GraphStore:
    """Small JSON-friendly heterogeneous graph store.

    The shape is deliberately simple so the first demo can run without a graph
    database while keeping a clear migration path to NetworkX, Neo4j, or SQLite.
    """

    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Edge] = []

    def add_node(self, node_id: str, node_type: str, **data: Any) -> None:
        if node_id not in self.nodes:
            self.nodes[node_id] = {"id": node_id, "node_type": node_type, **data}
            return
        self.nodes[node_id].update(data)
        self.nodes[node_id]["node_type"] = node_type

    def add_edge(self, source: str, target: str, edge_type: str) -> None:
        self.edges.append(Edge(source=source, target=target, edge_type=edge_type))

    def clone(self) -> "GraphStore":
        cloned = GraphStore()
        cloned.nodes = deepcopy(self.nodes)
        cloned.edges = [Edge(edge.source, edge.target, edge.edge_type) for edge in self.edges]
        return cloned

    def neighbors(self, node_id: str, edge_types: Iterable[str] | None = None) -> List[str]:
        allowed = set(edge_types) if edge_types else None
        result: List[str] = []
        for edge in self.edges:
            if allowed is not None and edge.edge_type not in allowed:
                continue
            if edge.source == node_id:
                result.append(edge.target)
            elif edge.target == node_id:
                result.append(edge.source)
        return result

    def local_subgraph(self, memory_ids: Sequence[str]) -> Dict[str, Any]:
        keep_nodes = set(memory_ids)
        keep_edges: List[Edge] = []
        selected = set(memory_ids)
        for edge in self.edges:
            if edge.source in selected or edge.target in selected:
                keep_edges.append(edge)
                keep_nodes.add(edge.source)
                keep_nodes.add(edge.target)
        return {
            "nodes": [self.nodes[node_id] for node_id in keep_nodes if node_id in self.nodes],
            "edges": [edge.to_dict() for edge in keep_edges],
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": [edge.to_dict() for edge in self.edges],
        }


def build_memory_graph(
    memory_nodes: Sequence[MemoryNode],
    history: Sequence[Turn],
    similarity_threshold: float = 0.28,
) -> GraphStore:
    graph = GraphStore()

    for turn in history:
        graph.add_node(
            turn.id,
            NODE_TURN,
            speaker=turn.speaker,
            text=turn.text,
            timestamp=turn.timestamp,
            segment_id=turn.segment_id,
        )
        graph.add_node(turn.segment_id, NODE_SEGMENT, summary=turn.segment_summary)
        graph.add_edge(turn.id, turn.segment_id, EDGE_BELONGS_TO)

    for memory in memory_nodes:
        memory_data = memory.to_dict()
        memory_data.pop("id", None)
        memory_data.pop("node_type", None)
        graph.add_node(memory.id, NODE_MEMORY, **memory_data)
        graph.add_edge(memory.id, memory.source_turn_id, EDGE_DERIVED_FROM)
        graph.add_edge(memory.id, memory.segment_id, EDGE_BELONGS_TO)
        for entity in memory.entities:
            entity_id = entity_node_id(entity)
            graph.add_node(entity_id, NODE_ENTITY, name=entity)
            graph.add_edge(memory.id, entity_id, EDGE_MENTIONS)

    ordered = sorted(memory_nodes, key=lambda item: item.timestamp)
    for left, right in zip(ordered, ordered[1:]):
        graph.add_edge(left.id, right.id, EDGE_TEMPORAL_NEXT)

    for left_index, left in enumerate(memory_nodes):
        left_tokens = tokenize(left.summary + " " + " ".join(left.keywords))
        for right in memory_nodes[left_index + 1 :]:
            right_tokens = tokenize(right.summary + " " + " ".join(right.keywords))
            if jaccard(left_tokens, right_tokens) >= similarity_threshold:
                graph.add_edge(left.id, right.id, EDGE_SIMILAR_TO)

    return graph


def entity_node_id(entity: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(entity).lower()).strip("_")
    return "e_" + (cleaned or "unknown")


def summarize_graph(graph: GraphStore) -> Dict[str, Any]:
    memory_ids = [
        node_id
        for node_id, node in graph.nodes.items()
        if node.get("node_type") == NODE_MEMORY
    ]
    return {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "memory_ids": memory_ids,
    }


def memory_catalog(memory_nodes: Sequence[MemoryNode], limit: int | None = None) -> List[Dict[str, Any]]:
    rows = [
        {
            "id": memory.id,
            "type": memory.memory_type,
            "summary": memory.summary,
            "keywords": memory.keywords,
            "entities": memory.entities,
            "timestamp": memory.timestamp,
            "importance": memory.importance,
            "segment_id": memory.segment_id,
        }
        for memory in memory_nodes
    ]
    if limit is None or limit <= 0:
        return rows
    return rows[:limit]
