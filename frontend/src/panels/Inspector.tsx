// Inspector panel (WORKFLOW.md P7) — the docked editor for the current
// selection. It resolves the selected entity from the World, renders the
// matching inspector (showing only that entity's present/applicable fields),
// and routes every change back to the host as an `apply_edit` upsert that
// persists and recomputes. A delete button removes the entity; a close button
// clears the selection.

import type {
  EntityKind,
  Item,
  MachineDef,
  MachineNode,
  Pipe,
  Recipe,
  System,
  World,
} from "../bridge";
import { ItemInspector } from "./ItemInspector";
import { RecipeInspector } from "./RecipeInspector";
import { MachineDefInspector } from "./MachineDefInspector";
import { NodeInspector } from "./NodeInspector";
import { PipeInspector } from "./PipeInspector";
import { SystemInspector } from "./SystemInspector";

export interface InspectorSelection {
  kind: EntityKind;
  id: string;
}

const KIND_LABEL: Record<EntityKind, string> = {
  item: "Item",
  recipe: "Recipe",
  machineDef: "Machine def",
  node: "Machine node",
  pipe: "Pipe",
  system: "System",
};

const KIND_FIELD: Record<EntityKind, keyof World> = {
  item: "items",
  recipe: "recipes",
  machineDef: "machineDefs",
  node: "nodes",
  pipe: "pipes",
  system: "systems",
};

function findEntity(world: World, sel: InspectorSelection): { id: string } | undefined {
  const list = (world[KIND_FIELD[sel.kind]] ?? []) as { id: string }[];
  return list.find((e) => e.id === sel.id);
}

export interface InspectorProps {
  world: World;
  selection: InspectorSelection;
  onChange: (kind: EntityKind, entity: object) => void;
  onDelete: (kind: EntityKind, id: string) => void;
  onClose: () => void;
}

export function Inspector({ world, selection, onChange, onDelete, onClose }: InspectorProps) {
  const entity = findEntity(world, selection);

  return (
    <aside className="inspector panel-glass">
      <header className="inspector-head">
        <div>
          <p className="eyebrow">{KIND_LABEL[selection.kind]}</p>
          <div className="inspector-id mono">{selection.id}</div>
        </div>
        <button type="button" className="icon-btn" title="Close" onClick={onClose}>
          ×
        </button>
      </header>

      {!entity ? (
        <div className="inspector-body">
          <p className="lede mono">This entity is no longer in the World.</p>
        </div>
      ) : (
        <Body world={world} selection={selection} entity={entity} onChange={onChange} />
      )}

      {entity && (
        <footer className="inspector-foot">
          <button
            type="button"
            className="tool-btn danger"
            onClick={() => onDelete(selection.kind, selection.id)}
          >
            Delete {KIND_LABEL[selection.kind].toLowerCase()}
          </button>
        </footer>
      )}
    </aside>
  );
}

function Body({
  world,
  selection,
  entity,
  onChange,
}: {
  world: World;
  selection: InspectorSelection;
  entity: { id: string };
  onChange: (kind: EntityKind, entity: object) => void;
}) {
  switch (selection.kind) {
    case "item":
      return <ItemInspector item={entity as Item} onChange={(n) => onChange("item", n)} />;
    case "recipe":
      return (
        <RecipeInspector
          recipe={entity as Recipe}
          world={world}
          onChange={(n) => onChange("recipe", n)}
        />
      );
    case "machineDef":
      return (
        <MachineDefInspector
          def={entity as MachineDef}
          world={world}
          onChange={(n) => onChange("machineDef", n)}
        />
      );
    case "node":
      return (
        <NodeInspector
          node={entity as MachineNode}
          world={world}
          onChange={(n) => onChange("node", n)}
        />
      );
    case "pipe":
      return (
        <PipeInspector pipe={entity as Pipe} world={world} onChange={(n) => onChange("pipe", n)} />
      );
    case "system":
      return (
        <SystemInspector
          system={entity as System}
          world={world}
          onChange={(n) => onChange("system", n)}
        />
      );
    default:
      return (
        <div className="inspector-body">
          <p className="lede mono">No inspector for this kind yet.</p>
        </div>
      );
  }
}
