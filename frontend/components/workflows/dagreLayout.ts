import Dagre from '@dagrejs/dagre';
import type { Edge, Node } from '@xyflow/react';

const NODE_WIDTH = 220;
const NODE_HEIGHT = 64;

export function layoutWithDagre<TNode extends Node, TEdge extends Edge>(
  nodes: TNode[],
  edges: TEdge[],
  direction: 'LR' | 'TB' = 'LR',
): TNode[] {
  if (nodes.length === 0) return nodes;

  const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep: 60, ranksep: 100, marginx: 30, marginy: 30 });

  for (const n of nodes) {
    g.setNode(n.id, {
      width: n.width ?? NODE_WIDTH,
      height: n.height ?? NODE_HEIGHT,
    });
  }
  for (const e of edges) {
    g.setEdge(e.source, e.target);
  }

  Dagre.layout(g);

  return nodes.map((n) => {
    const pos = g.node(n.id);
    if (!pos) return n;
    return {
      ...n,
      position: {
        x: pos.x - (n.width ?? NODE_WIDTH) / 2,
        y: pos.y - (n.height ?? NODE_HEIGHT) / 2,
      },
    };
  });
}
