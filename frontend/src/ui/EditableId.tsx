// The inspector id field for library entities (#3). An id is derived from the
// name but editable; committing a change renames the entity everywhere it is
// referenced (the host does the reference rewrite). Ids are slugified and kept
// unique, so the field never produces a colliding or malformed id.

import { useEffect, useState } from "react";
import type { EntityKind, World } from "../bridge";
import { idsOfKind, slugify, uniqueId } from "./ids";

export function EditableId({
  id,
  name,
  kind,
  world,
  onRename,
}: {
  id: string;
  name: string | undefined;
  kind: EntityKind;
  world: World;
  onRename: (newId: string) => void;
}) {
  const [draft, setDraft] = useState(id);
  useEffect(() => setDraft(id), [id]);

  const commit = (raw: string) => {
    const slug = slugify(raw);
    if (!slug || slug === id) {
      setDraft(id);
      return;
    }
    const taken = idsOfKind(world, kind);
    taken.delete(id);
    onRename(uniqueId(slug, taken));
  };

  const syncFromName = () => {
    if (name) commit(name);
  };

  return (
    <div className="editable-id">
      <input
        className="field-input mono id-input"
        value={draft}
        spellCheck={false}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => commit(draft)}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") setDraft(id);
        }}
      />
      <button
        type="button"
        className="id-sync"
        title="Derive id from name"
        disabled={!name}
        onClick={syncFromName}
      >
        ↻ name
      </button>
    </div>
  );
}
