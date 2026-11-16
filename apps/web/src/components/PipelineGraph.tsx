import {
  Background,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";
import type { StageStatus } from "../lib/api";
import { STAGES, STATUS_LABEL, STATUS_TONE, stageDepth } from "../lib/stages";
import { useTheme } from "../lib/theme";

export type GraphStage = {
  key: string;
  status: StageStatus;
  version: number | null;
  placeholder: boolean;
  step?: string | undefined;
};

type StageNodeData = GraphStage & {
  code: string;
  label: string;
  onOpen?: ((key: string) => void) | undefined;
};

function StageNode({ data }: NodeProps<Node<StageNodeData>>) {
  const tone = STATUS_TONE[data.status];
  const text = `${data.code} ${data.label}: ${STATUS_LABEL[data.status]}${data.version ? `, version ${data.version}` : ""}${data.placeholder ? ", placeholder" : ""}`;
  const inner = (
    <>
      <span className="flow-node__code">{data.code}</span>
      <span className="flow-node__label">{data.label}</span>
      <span className="flow-node__status">
        {STATUS_LABEL[data.status]}
        {data.version ? ` · v${data.version}` : ""}
      </span>
    </>
  );
  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      {data.onOpen ? (
        <button
          type="button"
          className="flow-node"
          data-tone={tone}
          data-placeholder={data.placeholder}
          aria-label={text}
          onClick={() => data.onOpen?.(data.key)}
        >
          {inner}
        </button>
      ) : (
        <div className="flow-node" data-tone={tone} role="img" aria-label={text}>
          {inner}
        </div>
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </>
  );
}

const nodeTypes = { stage: StageNode };
const COL_W = 200;
const ROW_H = 92;

/** DAG of the 13 stages laid out by dependency depth (docs/02 §1). */
export function PipelineGraph({
  stages,
  onOpen,
  label,
}: {
  stages: GraphStage[];
  onOpen?: (key: string) => void;
  label: string;
}) {
  const [theme] = useTheme();
  const { nodes, edges } = useMemo(() => {
    const depth = stageDepth();
    const byKey = Object.fromEntries(stages.map((s) => [s.key, s]));
    const rowInCol: Record<number, number> = {};
    const nodes: Node<StageNodeData>[] = STAGES.map((s) => {
      const d = depth[s.key] ?? 0;
      const row = (rowInCol[d] = (rowInCol[d] ?? -1) + 1);
      const st = byKey[s.key] ?? { key: s.key, status: "pending" as const, version: null, placeholder: false };
      return {
        id: s.key,
        type: "stage",
        position: { x: d * COL_W, y: row * ROW_H + (d % 2 ? 24 : 0) },
        data: { ...st, code: s.code, label: s.label, onOpen },
        draggable: false,
        selectable: false,
        focusable: false,
      };
    });
    const edges: Edge[] = STAGES.flatMap((s) =>
      s.deps.map((d) => {
        const active = byKey[d]?.status === "approved";
        return {
          id: `${d}-${s.key}`,
          source: d,
          target: s.key,
          animated: byKey[s.key]?.status === "running",
          className: active ? "flow-edge flow-edge--done" : "flow-edge",
        };
      }),
    );
    return { nodes, edges };
  }, [stages, onOpen]);

  return (
    <div className="flow-wrap" role="group" aria-label={label}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        colorMode={theme}
        fitView
        fitViewOptions={{ padding: 0.08 }}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        panOnScroll={false}
        preventScrolling={false}
        minZoom={0.3}
      >
        <Background gap={24} size={1} />
      </ReactFlow>
    </div>
  );
}
