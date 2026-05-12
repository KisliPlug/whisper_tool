import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUpRight,
  Circle,
  Clipboard,
  Eraser,
  Grid3X3,
  MousePointer2,
  PenLine,
  Plus,
  Save,
  Settings,
  Square,
  StickyNote,
  Triangle,
  Type,
  X
} from "lucide-react";
import "./styles.css";

const COLORS = [
  ["red", "#ff2a2a"],
  ["orange", "#f97316"],
  ["yellow", "#facc15"],
  ["green", "#22c55e"],
  ["cyan", "#06b6d4"],
  ["blue", "#1e90ff"],
  ["purple", "#a855f7"],
  ["white", "#ffffff"],
  ["black", "#111111"]
];

const TOOL_LABELS = {
  select: "Select",
  pen: "Pen",
  line: "Line",
  arrow: "Arrow",
  rect: "Rect",
  triangle: "Triangle",
  ellipse: "Circle",
  text: "Text",
  clear: "Clear"
};

const TOOL_SHORTCUTS = {
  select: "S",
  pen: "P",
  line: "L",
  arrow: "A",
  rect: "R",
  triangle: "G",
  ellipse: "C",
  text: "T"
};

const TOOL_BY_SHORTCUT = Object.fromEntries(
  Object.entries(TOOL_SHORTCUTS).map(([toolName, key]) => [key.toLowerCase(), toolName])
);

const TOOL_BY_CODE = Object.fromEntries(
  Object.entries(TOOL_SHORTCUTS).map(([toolName, key]) => [`Key${key}`, toolName])
);

const RADIAL_TOOLS = [
  ["select", MousePointer2],
  ["pen", PenLine],
  ["line", MinusIcon],
  ["arrow", ArrowUpRight],
  ["rect", Square],
  ["triangle", Triangle],
  ["ellipse", Circle],
  ["text", Type],
  ["clear", Eraser]
];

const MIN_BOX = 8;
const DEFAULT_WHEEL_ANCHOR = { x: 136, y: 196 };

function uid(prefix = "id") {
  return `${prefix}_${Math.random().toString(36).slice(2)}_${Date.now().toString(36)}`;
}

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

function normalizeBox(box) {
  return {
    x: Math.min(box.x, box.x + box.w),
    y: Math.min(box.y, box.y + box.h),
    w: Math.abs(box.w),
    h: Math.abs(box.h)
  };
}

function boxFromPoints(a, b) {
  return normalizeBox({ x: a.x, y: a.y, w: b.x - a.x, h: b.y - a.y });
}

function itemBox(item) {
  if (["rect", "triangle", "ellipse", "text"].includes(item.type)) {
    return item.box;
  }
  const pts = item.points || [item.start, item.end].filter(Boolean);
  if (!pts.length) return null;
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  return {
    x: Math.min(...xs),
    y: Math.min(...ys),
    w: Math.max(...xs) - Math.min(...xs),
    h: Math.max(...ys) - Math.min(...ys)
  };
}

function pointInBox(point, box, pad = 5) {
  return (
    point.x >= box.x - pad &&
    point.x <= box.x + box.w + pad &&
    point.y >= box.y - pad &&
    point.y <= box.y + box.h + pad
  );
}

function boxesIntersect(a, b) {
  if (!a || !b) return false;
  return !(
    a.x + a.w < b.x ||
    b.x + b.w < a.x ||
    a.y + a.h < b.y ||
    b.y + b.h < a.y
  );
}

function combinedBox(items) {
  const boxes = items.map(itemBox).filter(Boolean);
  if (!boxes.length) return null;
  const x1 = Math.min(...boxes.map((box) => box.x));
  const y1 = Math.min(...boxes.map((box) => box.y));
  const x2 = Math.max(...boxes.map((box) => box.x + box.w));
  const y2 = Math.max(...boxes.map((box) => box.y + box.h));
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

function distanceToSegment(p, a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (dx === 0 && dy === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  const t = clamp(((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy), 0, 1);
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

function hitTest(item, point) {
  if (item.type === "pen") {
    for (let i = 1; i < item.points.length; i += 1) {
      if (distanceToSegment(point, item.points[i - 1], item.points[i]) <= item.width + 5) return true;
    }
    return false;
  }
  if (item.type === "line" || item.type === "arrow") {
    return distanceToSegment(point, item.start, item.end) <= item.width + 6;
  }
  const box = itemBox(item);
  return box ? pointInBox(point, box, 5) : false;
}

function preserveLiveText(target, current) {
  const next = structuredClone(target);
  next.notes = next.notes.map((note) => {
    const live = current.notes.find((n) => n.id === note.id);
    return live ? { ...note, text: live.text, name: live.name } : note;
  });
  next.items = next.items.map((item) => {
    if (item.type !== "text") return item;
    const live = current.items.find((i) => i.id === item.id);
    return live ? { ...item, text: live.text } : item;
  });
  return next;
}

function useHistory(initial) {
  const [state, setState] = useState(initial);
  const [undoStack, setUndoStack] = useState([]);
  const [redoStack, setRedoStack] = useState([]);

  const pushSnapshot = (snapshot) => {
    setUndoStack((stack) => [...stack.slice(-79), structuredClone(snapshot)]);
    setRedoStack([]);
  };

  const commit = (updater) => {
    setState((current) => {
      pushSnapshot(current);
      return typeof updater === "function" ? updater(structuredClone(current)) : updater;
    });
  };

  const undo = () => {
    if (!undoStack.length) return;
    const target = preserveLiveText(undoStack[undoStack.length - 1], state);
    setRedoStack((redo) => [structuredClone(state), ...redo]);
    setUndoStack((stack) => stack.slice(0, -1));
    setState(target);
  };

  const redo = () => {
    if (!redoStack.length) return;
    const target = preserveLiveText(redoStack[0], state);
    setUndoStack((undoItems) => [...undoItems, structuredClone(state)]);
    setRedoStack((stack) => stack.slice(1));
    setState(target);
  };

  return { state, setState, commit, undo, redo, pushSnapshot, canUndo: undoStack.length > 0, canRedo: redoStack.length > 0 };
}

export default function App() {
  const [request, setRequest] = useState(null);
  const [tool, setTool] = useState("select");
  const [colorName, setColorName] = useState("red");
  const [width, setWidth] = useState(4);
  const [fontSize, setFontSize] = useState(20);
  const [snap, setSnap] = useState(true);
  const [gridSize, setGridSize] = useState(20);
  const [selectedIds, setSelectedIds] = useState([]);
  const [draft, setDraft] = useState(null);
  const [drag, setDrag] = useState(null);
  const [clipboardItems, setClipboardItems] = useState([]);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState(null);
  const [wheel, setWheel] = useState(null);
  const [wheelAnchor, setWheelAnchor] = useState(DEFAULT_WHEEL_ANCHOR);
  const [renamingNote, setRenamingNote] = useState(false);
  const scrollerRef = useRef(null);
  const stageRef = useRef(null);
  const imgRef = useRef(null);
  const dragMovedRef = useRef(false);

  const {
    state,
    setState,
    commit,
    undo,
    redo,
    pushSnapshot,
    canUndo,
    canRedo
  } = useHistory({ notes: [{ id: 1, text: "" }], activeNoteId: 1, items: [] });

  const activeNote = state.notes.find((n) => n.id === state.activeNoteId) || state.notes[0];
  const color = Object.fromEntries(COLORS)[colorName] || "#ff2a2a";
  const activeItems = state.items.filter((item) => item.noteId === state.activeNoteId);
  const selectedItems = state.items.filter((item) => selectedIds.includes(item.id));
  const selected = selectedItems.length === 1 ? selectedItems[0] : null;

  const clampAnchor = (point) => ({
    x: clamp(point.x, 96, window.innerWidth - 96),
    y: clamp(point.y, 112, window.innerHeight - 96)
  });

  const moveWheelAnchor = (point) => {
    const next = clampAnchor(point);
    setWheelAnchor(next);
    setWheel((current) => current ? { ...current, x: next.x, y: next.y } : current);
  };

  const openWheel = () => {
    const anchor = clampAnchor(wheelAnchor);
    setWheel({
      x: anchor.x,
      y: anchor.y,
      hover: tool,
      mode: "tools"
    });
  };

  const toggleWheel = () => {
    const anchor = clampAnchor(wheelAnchor);
    setWheel((current) => current ? null : {
      x: anchor.x,
      y: anchor.y,
      hover: tool,
      mode: "tools"
    });
  };

  useEffect(() => {
    window.annotatorApi.load().then(setRequest);
  }, []);

  useEffect(() => {
    const id = setInterval(async () => {
      const signal = await window.annotatorApi.checkSignal();
      if (signal === "commit") save();
      if (signal === "cancel") window.annotatorApi.cancel();
    }, 180);
    return () => clearInterval(id);
  });

  useEffect(() => {
    const onKey = (e) => {
      const activeElement = document.activeElement;
      const isTextField = activeElement?.classList?.contains("inlineText") || ["INPUT", "TEXTAREA"].includes(activeElement?.tagName);
      const key = e.key.toLowerCase();
      const shortcut = e.ctrlKey || e.metaKey;
      const textSelectionLength = isTextField && activeElement.selectionStart != null
        ? Math.abs((activeElement.selectionEnd || 0) - (activeElement.selectionStart || 0))
        : 0;
      if (shortcut && (key === "z" || key === "y")) {
        if (isTextField) return;
        e.preventDefault();
        if (key === "z") undo(); else redo();
      } else if (shortcut && key === "a") {
        if (isTextField) return;
        e.preventDefault();
      } else if (shortcut && key === "c") {
        if (isTextField && textSelectionLength > 0) return;
        if (!selectedIds.length) return;
        e.preventDefault();
        copySelected();
      } else if (shortcut && key === "x") {
        if (isTextField && textSelectionLength > 0) return;
        if (!selectedIds.length) return;
        e.preventDefault();
        cutSelected();
      } else if (shortcut && key === "v") {
        if (isTextField) return;
        if (!clipboardItems.length) return;
        e.preventDefault();
        pasteClipboard();
      } else if (shortcut && (e.key === "+" || e.key === "=")) {
        e.preventDefault();
        zoomBy(1.2);
      } else if (shortcut && e.key === "-") {
        e.preventDefault();
        zoomBy(1 / 1.2);
      } else if (shortcut && e.key === "0") {
        e.preventDefault();
        setZoom(1);
      } else if (isTextField) {
        return;
      } else if (e.key === "Delete" || e.key === "Backspace") {
        deleteSelected();
      } else if (e.key === "Escape") {
        setDraft(null);
        setSelectedIds([]);
      } else if (!e.ctrlKey && !e.metaKey && !e.altKey) {
        if (e.key === " ") {
          e.preventDefault();
          toggleWheel();
          return;
        }
        const nextTool = TOOL_BY_SHORTCUT[e.key.toLowerCase()] || TOOL_BY_CODE[e.code];
        if (nextTool) {
          e.preventDefault();
          setTool(nextTool);
          setWheel((current) => current ? { ...current, hover: nextTool, mode: "tools" } : current);
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  });

  useEffect(() => {
    const closeContext = (e) => e.preventDefault();
    window.addEventListener("contextmenu", closeContext);
    return () => window.removeEventListener("contextmenu", closeContext);
  }, []);

  useEffect(() => {
    if (!pan) return undefined;
    const onMove = (e) => {
      if (!scrollerRef.current) return;
      scrollerRef.current.scrollLeft = pan.left - (e.clientX - pan.x);
      scrollerRef.current.scrollTop = pan.top - (e.clientY - pan.y);
    };
    const onUp = () => setPan(null);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [pan]);

  const toImagePoint = (event, options = {}) => {
    const rect = stageRef.current.getBoundingClientRect();
    const raw = {
      x: ((event.clientX - rect.left) / rect.width) * request.size.width,
      y: ((event.clientY - rect.top) / rect.height) * request.size.height
    };
    if (!snap || options.noSnap) return raw;
    return {
      x: Math.round(raw.x / gridSize) * gridSize,
      y: Math.round(raw.y / gridSize) * gridSize
    };
  };

  const zoomBy = (factor) => {
    setZoom((z) => clamp(Number((z * factor).toFixed(3)), 0.2, 5));
  };

  const fitZoom = () => {
    if (!request || !scrollerRef.current) return;
    const rect = scrollerRef.current.getBoundingClientRect();
    const next = Math.min(
      rect.width / request.size.width,
      rect.height / request.size.height,
      1
    );
    setZoom(clamp(Number(next.toFixed(3)), 0.2, 5));
    requestAnimationFrame(() => {
      if (!scrollerRef.current) return;
      scrollerRef.current.scrollLeft = 0;
      scrollerRef.current.scrollTop = 0;
    });
  };

  const nextNote = () => {
    commit((s) => {
      const id = Math.max(...s.notes.map((n) => n.id)) + 1;
      s.notes.push({ id, name: `Note ${id}`, text: "" });
      s.activeNoteId = id;
      return s;
    });
    setSelectedIds([]);
    setRenamingNote(true);
  };

  const updateActiveNoteText = (text) => {
    setState((s) => {
      const copy = structuredClone(s);
      const note = copy.notes.find((n) => n.id === copy.activeNoteId);
      if (note) note.text = text;
      return copy;
    });
  };

  const updateActiveNoteName = (name) => {
    setState((s) => {
      const copy = structuredClone(s);
      const note = copy.notes.find((n) => n.id === copy.activeNoteId);
      if (note) note.name = name || `Note ${note.id}`;
      return copy;
    });
  };

  const selectNote = (id) => {
    setState((s) => ({ ...s, activeNoteId: id }));
    setSelectedIds([]);
  };

  const updateItem = (id, patch) => {
    const isTextOnly = Object.keys(patch).length === 1 && "text" in patch;
    if (isTextOnly) {
      setState((s) => {
        const copy = structuredClone(s);
        const item = copy.items.find((i) => i.id === id);
        if (item) item.text = patch.text;
        return copy;
      });
      return;
    }
    commit((s) => {
      const item = s.items.find((i) => i.id === id);
      if (item) Object.assign(item, patch);
      return s;
    });
  };

  const deleteSelected = () => {
    if (!selectedIds.length) return;
    const ids = new Set(selectedIds);
    commit((s) => {
      s.items = s.items.filter((item) => !ids.has(item.id));
      return s;
    });
    setSelectedIds([]);
  };

  const copySelected = () => {
    if (!selectedItems.length) return;
    setClipboardItems(structuredClone(selectedItems));
  };

  const cutSelected = () => {
    if (!selectedItems.length) return;
    setClipboardItems(structuredClone(selectedItems));
    deleteSelected();
  };

  const pasteClipboard = () => {
    if (!clipboardItems.length) return;
    const copies = clipboardItems.map((item) => {
      const copy = offsetItem(structuredClone(item), 28, 28);
      copy.id = uid("item");
      copy.noteId = state.activeNoteId;
      return copy;
    });
    commit((s) => {
      s.items.push(...copies);
      return s;
    });
    setSelectedIds(copies.map((item) => item.id));
    setTool("select");
  };

  const duplicateSelected = () => {
    if (!selectedItems.length) return;
    const copies = selectedItems.map((item) => {
      const copy = offsetItem(structuredClone(item), 24, 24);
      copy.id = uid("item");
      return copy;
    });
    commit((s) => {
      s.items.push(...copies);
      return s;
    });
    setSelectedIds(copies.map((item) => item.id));
  };

  const applyColor = (name, value) => {
    setColorName(name);
    if (!selectedItems.length) return;
    const ids = new Set(selectedIds);
    commit((s) => {
      for (const item of s.items) {
        if (ids.has(item.id)) Object.assign(item, { color: value, colorName: name });
      }
      return s;
    });
  };

  const onPointerDown = (e) => {
    if (!request) return;
    stageRef.current?.focus({ preventScroll: true });
    if (e.button === 2) {
      e.preventDefault();
      return;
    }
    if (e.button === 1) {
      e.preventDefault();
      e.currentTarget.setPointerCapture?.(e.pointerId);
      setPan({
        x: e.clientX,
        y: e.clientY,
        left: scrollerRef.current?.scrollLeft || 0,
        top: scrollerRef.current?.scrollTop || 0
      });
      return;
    }
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    const point = toImagePoint(e, { noSnap: tool === "pen" });
    if (tool === "select") {
      const handle = selected ? handleAt(selected, point) : null;
      if (handle) {
        dragMovedRef.current = false;
        setDrag({
          mode: "resize",
          id: selected.id,
          handle,
          start: point,
          original: structuredClone(selected),
          preSnapshot: structuredClone(state)
        });
        return;
      }
      const selectionBox = combinedBox(selectedItems);
      if (!e.shiftKey && selectedItems.length && selectionBox && pointInBox(point, selectionBox, 8)) {
        dragMovedRef.current = false;
        setDrag({
          mode: "move",
          ids: [...selectedIds],
          start: point,
          originals: structuredClone(selectedItems),
          preSnapshot: structuredClone(state)
        });
        return;
      }
      const hit = [...activeItems].reverse().find((item) => hitTest(item, point));
      if (hit) {
        let nextIds;
        if (e.shiftKey) {
          nextIds = selectedIds.includes(hit.id)
            ? selectedIds.filter((id) => id !== hit.id)
            : [...selectedIds, hit.id];
        } else {
          nextIds = selectedIds.includes(hit.id) ? selectedIds : [hit.id];
        }
        setSelectedIds(nextIds);
        dragMovedRef.current = false;
        setDrag({
          mode: "move",
          ids: nextIds,
          start: point,
          originals: structuredClone(state.items.filter((item) => nextIds.includes(item.id))),
          preSnapshot: structuredClone(state)
        });
      } else {
        if (!e.shiftKey) setSelectedIds([]);
        setDrag({ mode: "marquee", start: point, end: point });
      }
      return;
    }
    setSelectedIds([]);
    if (tool === "pen") {
      setDraft({ id: uid("draft"), type: "pen", points: [point], color, colorName, width });
      return;
    }
    setDraft({ id: uid("draft"), type: tool, start: point, end: point, color, colorName, width, fontSize });
  };

  const onPointerMove = (e) => {
    if (!request) return;
    if (pan) {
      if (scrollerRef.current) {
        scrollerRef.current.scrollLeft = pan.left - (e.clientX - pan.x);
        scrollerRef.current.scrollTop = pan.top - (e.clientY - pan.y);
      }
      return;
    }
    const point = toImagePoint(e, { noSnap: draft?.type === "pen" });
    if (drag?.mode === "resize") {
      dragMovedRef.current = true;
      const resized = resizeItem(drag.original, drag.handle, point);
      setState((s) => ({
        ...s,
        items: s.items.map((item) => (item.id === drag.id ? resized : item))
      }));
      return;
    }
    if (drag?.mode === "move") {
      const dx = point.x - drag.start.x;
      const dy = point.y - drag.start.y;
      if (dx !== 0 || dy !== 0) dragMovedRef.current = true;
      const moved = new Map(
        drag.originals.map((item) => [item.id, offsetItem(structuredClone(item), dx, dy)])
      );
      setState((s) => ({
        ...s,
        items: s.items.map((item) => (moved.has(item.id) ? moved.get(item.id) : item))
      }));
      return;
    }
    if (drag?.mode === "marquee") {
      setDrag({ ...drag, end: point });
      return;
    }
    if (!draft) return;
    if (draft.type === "pen") {
      setDraft({ ...draft, points: [...draft.points, point] });
    } else {
      setDraft({ ...draft, end: point });
    }
  };

  const onPointerUp = (e) => {
    if (pan) {
      setPan(null);
      return;
    }
    if (drag?.mode === "resize") {
      if (dragMovedRef.current && drag.preSnapshot) pushSnapshot(drag.preSnapshot);
      dragMovedRef.current = false;
      setDrag(null);
      return;
    }
    if (drag?.mode === "move") {
      if (dragMovedRef.current && drag.preSnapshot) pushSnapshot(drag.preSnapshot);
      dragMovedRef.current = false;
      setDrag(null);
      return;
    }
    if (drag?.mode === "marquee") {
      const box = boxFromPoints(drag.start, drag.end);
      const ids = activeItems
        .filter((item) => boxesIntersect(itemBox(item), box))
        .map((item) => item.id);
      setSelectedIds(ids);
      setDrag(null);
      return;
    }
    if (!draft) return;
    const item = finalizeDraft(draft, state.activeNoteId);
    setDraft(null);
    if (!item) return;
    commit((s) => {
      s.items.push(item);
      return s;
    });
    setSelectedIds([item.id]);
    if (item.type === "text") {
      setTool("select");
      setTimeout(() => {
        const el = document.querySelector(`[data-text-id="${item.id}"]`);
        el?.focus();
      }, 0);
    }
  };

  const save = async ({ copyImageToClipboard = false } = {}) => {
    if (!request || !imgRef.current) return;
    const canvas = document.createElement("canvas");
    canvas.width = request.size.width;
    canvas.height = request.size.height;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(imgRef.current, 0, 0, canvas.width, canvas.height);
    drawItems(ctx, state.items);
    const imageBase64 = canvas.toDataURL("image/png").split(",")[1];
    await window.annotatorApi.save({
      imageBase64,
      metadata: buildMetadata(state, request.size),
      copyImageToClipboard
    });
  };

  if (!request) {
    return <div className="loading">Loading annotator...</div>;
  }

  return (
    <div className="shell">
      <main className="workspace">
        <header className="topbar">
          <div className="brandCompact">
            <div className="brandMark"><StickyNote size={18} /></div>
            <div>
              <div className="brandTitle">Annotator</div>
              <div className="brandSub">Tool dot opens the wheel</div>
            </div>
          </div>
          <div className="noteDock" aria-label="Notes">
            {state.notes.map((note) => (
              <button
                key={note.id}
                className={`noteIcon ${note.id === state.activeNoteId ? "active" : ""}`}
                title={note.name || `Note ${note.id}`}
                onClick={() => selectNote(note.id)}
              >
                {note.id}
              </button>
            ))}
            <button className="noteAdd" onClick={nextNote} title="New note"><Plus size={16} /></button>
          </div>
          <div className="noteMeta">
            {renamingNote ? (
              <input
                className="noteNameInput"
                autoFocus
                value={activeNote?.name || `Note ${activeNote?.id || 1}`}
                onChange={(e) => updateActiveNoteName(e.target.value)}
                onBlur={() => setRenamingNote(false)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") setRenamingNote(false);
                  if (e.key === "Escape") setRenamingNote(false);
                }}
              />
            ) : (
              <button className="noteNameButton" onClick={() => setRenamingNote(true)}>
                {activeNote?.name || `Note ${activeNote?.id || 1}`}
              </button>
            )}
          </div>
          <textarea
            className="noteText"
            value={activeNote?.text || ""}
            placeholder="Note text for the model..."
            onChange={(e) => updateActiveNoteText(e.target.value)}
          />
          <div className="topActions">
            <button
              className="toolPill"
              onClick={toggleWheel}
              title="Open tool wheel"
            >
              {toolIcon(tool)}
              {TOOL_LABELS[tool]}
            </button>
            <button className="ghostButton" onClick={() => window.annotatorApi.cancel()}><X size={16} /> Cancel</button>
            <button className="clipboardButton" onClick={() => save({ copyImageToClipboard: true })}>
              <Clipboard size={16} /> Save to Clipboard
            </button>
            <button className="saveButton" onClick={() => save()}><Save size={16} /> Save</button>
          </div>
        </header>

        <div
          ref={scrollerRef}
          className={`stageScroller ${pan ? "panning" : ""}`}
          onWheel={(e) => {
            if (!e.ctrlKey && !e.metaKey) return;
            e.preventDefault();
            zoomBy(e.deltaY < 0 ? 1.12 : 1 / 1.12);
          }}
        >
          <div
            ref={stageRef}
            className={`stage tool-${tool}`}
            style={{
              width: request.size.width * zoom,
              height: request.size.height * zoom
            }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerLeave={() => {
              if (!wheel) onPointerUp();
            }}
            tabIndex={0}
          >
            <img ref={imgRef} className="baseImage" src={request.imageUrl} draggable={false} />
            {snap && <GridOverlay width={request.size.width} height={request.size.height} size={gridSize} />}
            <svg className="overlay" viewBox={`0 0 ${request.size.width} ${request.size.height}`}>
              <defs>
                <marker id="arrowHead" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
                  <path d="M0,0 L0,6 L9,3 z" fill="context-stroke" />
                </marker>
              </defs>
              {activeItems.map((item) => (
                <ItemSvg
                  key={item.id}
                  item={item}
                  selected={selectedIds.includes(item.id)}
                  onTextChange={(text) => updateItem(item.id, { text })}
                  onTextFocus={() => setSelectedIds([item.id])}
                />
              ))}
              {draft && <ItemSvg item={draftToPreview(draft)} selected={false} preview />}
              {selectedItems.map((item) => <Selection key={item.id} item={item} />)}
              {selectedItems.length > 1 && <GroupSelection items={selectedItems} />}
              {drag?.mode === "marquee" && <Marquee start={drag.start} end={drag.end} />}
            </svg>
          </div>
        </div>
      </main>
      {wheel && (
        <RadialWheel
          x={wheel.x}
          y={wheel.y}
          mode={wheel.mode}
          active={tool}
          hover={wheel.hover}
          colorName={colorName}
          width={selected?.width || width}
          fontSize={selected?.fontSize || fontSize}
          gridSize={gridSize}
          snap={snap}
          onHover={(nextTool) => setWheel((w) => w ? { ...w, hover: nextTool } : w)}
          onPick={(nextTool) => {
            setTool(nextTool);
            setWheel(null);
          }}
          onMode={(mode) => setWheel((w) => w ? { ...w, mode } : w)}
          onBack={() => setWheel((w) => w ? { ...w, mode: "tools", hover: tool } : w)}
          onClose={() => setWheel(null)}
          onColor={applyColor}
          onStroke={(v) => {
            setWidth(v);
            if (selected && selected.type !== "text") updateItem(selected.id, { width: v });
          }}
          onTextSize={(v) => {
            setFontSize(v);
            if (selected?.type === "text") updateItem(selected.id, { fontSize: v });
          }}
          onGridSize={setGridSize}
          onSnap={() => setSnap((value) => !value)}
          onStrokeStep={(delta) => {
            const next = clamp((selected?.width || width) + delta, 1, 40);
            setWidth(next);
            if (selected && selected.type !== "text") updateItem(selected.id, { width: next });
          }}
          onTextStep={(delta) => {
            const next = clamp((selected?.fontSize || fontSize) + delta, 10, 96);
            setFontSize(next);
            if (selected?.type === "text") updateItem(selected.id, { fontSize: next });
          }}
          onGridStep={(delta) => setGridSize((value) => clamp(value + delta, 5, 80))}
          onAnchorMove={moveWheelAnchor}
          onClear={() => {
            commit((s) => ({ ...s, items: s.items.filter((item) => item.noteId !== s.activeNoteId) }));
            setSelectedIds([]);
          }}
        />
      )}
      {!wheel && <ToolAnchor anchor={wheelAnchor} onOpen={openWheel} onMove={moveWheelAnchor} />}
    </div>
  );
}

function ToolAnchor({ anchor, onOpen, onMove }) {
  return (
    <ToolDot
      className="toolAnchor"
      style={{
        left: clamp(anchor.x, 96, window.innerWidth - 96),
        top: clamp(anchor.y, 112, window.innerHeight - 96)
      }}
      title="Open tools"
      onClick={onOpen}
      onMove={onMove}
    >
      <PenLine size={15} />
    </ToolDot>
  );
}

function ToolDot({ className, style, title, onClick, onMove, children }) {
  const dragRef = useRef(null);
  return (
    <button
      type="button"
      className={className}
      style={style}
      title={title}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        e.currentTarget.setPointerCapture?.(e.pointerId);
        dragRef.current = { pointerId: e.pointerId, startX: e.clientX, startY: e.clientY, moved: false };
      }}
      onPointerMove={(e) => {
        const drag = dragRef.current;
        if (!drag || drag.pointerId !== e.pointerId) return;
        const dx = e.clientX - drag.startX;
        const dy = e.clientY - drag.startY;
        if (!drag.moved && Math.hypot(dx, dy) < 4) return;
        drag.moved = true;
        onMove({ x: e.clientX, y: e.clientY });
      }}
      onPointerUp={(e) => {
        const drag = dragRef.current;
        if (!drag || drag.pointerId !== e.pointerId) return;
        e.preventDefault();
        e.stopPropagation();
        dragRef.current = null;
        e.currentTarget.releasePointerCapture?.(e.pointerId);
        if (!drag.moved) onClick();
      }}
      onPointerCancel={(e) => {
        const drag = dragRef.current;
        if (drag?.pointerId === e.pointerId) dragRef.current = null;
      }}
    >
      <span className="toolDotIcon">{children}</span>
    </button>
  );
}

function IconButton({ icon, title, disabled, onClick }) {
  return (
    <button className="iconButton" disabled={disabled} onClick={onClick} title={title}>
      {React.cloneElement(icon, { size: 17 })}
    </button>
  );
}

function MinusIcon() {
  return <span className="minusIcon" />;
}

function toolIcon(toolName, size = 18) {
  const found = RADIAL_TOOLS.find(([name]) => name === toolName);
  if (!found) return <MousePointer2 size={size} />;
  const Icon = found[1];
  return <Icon size={size} />;
}

function RadialWheel({
  x,
  y,
  mode,
  active,
  hover,
  colorName,
  width,
  fontSize,
  gridSize,
  snap,
  onHover,
  onPick,
  onMode,
  onBack,
  onClose,
  onColor,
  onStroke,
  onTextSize,
  onGridSize,
  onSnap,
  onStrokeStep,
  onTextStep,
  onGridStep,
  onAnchorMove,
  onClear
}) {
  const radius = 82;
  const center = 122;
  const currentMode = mode || "tools";
  const ringItems = currentMode === "colors"
    ? COLORS.map(([name, value]) => ({ id: name, label: name, color: value, active: colorName === name, action: () => onColor(name, value) }))
    : RADIAL_TOOLS.map(([name, Icon]) => ({
      id: name,
      label: TOOL_LABELS[name],
      shortcut: TOOL_SHORTCUTS[name],
      icon: Icon,
      active: name === active,
      hover: name === hover,
      action: () => onPick(name),
      onEnter: () => onHover(name)
    }));

  return (
    <div
      className={`radialHost mode-${currentMode}`}
      style={{ left: x - center, top: y - center }}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <ToolDot
        className="radialCollapseDot"
        title="Close tools"
        onClick={onClose}
        onMove={onAnchorMove}
      >
        <PenLine size={20} />
      </ToolDot>
      <div className="radialCenterMenu" aria-label="Radial categories">
        <button
          className={`radialSegment segmentTools ${currentMode === "tools" ? "active" : ""}`}
          onPointerDown={(e) => {
            e.stopPropagation();
            onMode("tools");
          }}
          onPointerUp={(e) => e.stopPropagation()}
          title="Drawing tools"
        >
          <PenLine size={15} />
          <span>Draw</span>
        </button>
        <button
          className={`radialSegment segmentColors ${currentMode === "colors" ? "active" : ""}`}
          onPointerDown={(e) => {
            e.stopPropagation();
            onMode("colors");
          }}
          onPointerUp={(e) => e.stopPropagation()}
          title="Colors"
        >
          <span className="activeColorDot" style={{ background: Object.fromEntries(COLORS)[colorName] }} />
          <span>Color</span>
        </button>
        <button
          className={`radialSegment segmentSettings ${currentMode === "settings" ? "active" : ""}`}
          onPointerDown={(e) => {
            e.stopPropagation();
            if (currentMode === "settings") {
              onClose();
            } else {
              onMode("settings");
            }
          }}
          onPointerUp={(e) => e.stopPropagation()}
          title="Settings"
        >
          <Settings size={15} />
          <span>Settings</span>
        </button>
      </div>
      {ringItems.map((item, index) => {
        const angle = -Math.PI / 2 + (index / ringItems.length) * Math.PI * 2;
        const Icon = item.icon;
        return (
          <button
            key={item.id}
            className={`radialItem ${item.color ? "colorWheelSwatch" : ""} ${item.active ? "active" : ""} ${item.hover ? "hover" : ""}`}
            style={{
              left: center + Math.cos(angle) * radius,
              top: center + Math.sin(angle) * radius,
              background: item.color || undefined
            }}
            onPointerEnter={item.onEnter}
            onPointerUp={(e) => {
              e.stopPropagation();
              item.action();
            }}
            title={item.shortcut ? `${item.label} (${item.shortcut})` : item.label}
          >
            {Icon ? <Icon size={17} /> : null}
            {item.shortcut ? <kbd className="toolShortcut">{item.shortcut}</kbd> : null}
          </button>
        );
      })}
      {currentMode === "settings" ? (
        <WheelSettingsPanel
          width={width}
          fontSize={fontSize}
          gridSize={gridSize}
          snap={snap}
          onStroke={onStroke}
          onTextSize={onTextSize}
          onGridSize={onGridSize}
          onSnap={onSnap}
          onStrokeStep={onStrokeStep}
          onTextStep={onTextStep}
          onGridStep={onGridStep}
          onClear={onClear}
        />
      ) : null}
    </div>
  );
}

function WheelSettingsPanel({
  width,
  fontSize,
  gridSize,
  snap,
  onStroke,
  onTextSize,
  onGridSize,
  onSnap,
  onStrokeStep,
  onTextStep,
  onGridStep,
  onClear
}) {
  return (
    <div
      className="wheelSettingsPanel"
      onPointerDown={(e) => e.stopPropagation()}
      onPointerUp={(e) => e.stopPropagation()}
    >
      <WheelSettingSlider
        label="Stroke"
        value={width}
        valueLabel={`${width}px`}
        min={1}
        max={40}
        step={1}
        marks={[1, 4, 12, 24, 40]}
        onChange={onStroke}
        onStep={onStrokeStep}
      />
      <WheelSettingSlider
        label="Text"
        value={fontSize}
        valueLabel={`${fontSize}px`}
        min={10}
        max={96}
        step={2}
        marks={[10, 20, 36, 64, 96]}
        onChange={onTextSize}
        onStep={onTextStep}
      />
      <WheelSettingSlider
        label="Pixel Grid"
        value={gridSize}
        valueLabel={`${gridSize}px`}
        min={5}
        max={80}
        step={5}
        marks={[5, 20, 40, 60, 80]}
        onChange={onGridSize}
        onStep={onGridStep}
      />
      <div className="wheelSettingsActions">
        <button type="button" className={`wheelToggle ${snap ? "active" : ""}`} onClick={onSnap}>
          <Grid3X3 size={15} />
          <span>{snap ? "Snap on" : "Snap off"}</span>
        </button>
        <button type="button" className="wheelDanger" onClick={onClear}>
          <Eraser size={15} />
          <span>Clear</span>
        </button>
      </div>
    </div>
  );
}

function WheelSettingSlider({ label, value, valueLabel, min, max, step, marks, onChange, onStep }) {
  return (
    <div className="wheelSettingRow">
      <div className="wheelSettingTop">
        <span>{label}</span>
        <output>{valueLabel}</output>
      </div>
      <div className="wheelSettingControl">
        <button type="button" onClick={() => onStep(-Number(step))}>-</button>
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
        />
        <button type="button" onClick={() => onStep(Number(step))}>+</button>
      </div>
      <div className="wheelSettingMarks">
        {marks.map((mark) => <span key={mark}>{mark}</span>)}
      </div>
    </div>
  );
}

function RangeControl({ label, min, max, step = "1", value, onChange }) {
  return (
    <label className="settingsRange">
      <span>{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

function ActionButton({ icon, label, active, disabled, onClick }) {
  return (
    <button
      className={`settingsAction ${active ? "active" : ""}`}
      disabled={disabled}
      onClick={onClick}
      title={label}
    >
      {icon ? React.cloneElement(icon, { size: 15 }) : null}
      <span>{label}</span>
    </button>
  );
}

function GridOverlay({ width, height, size }) {
  const lines = [];
  for (let x = size; x < width; x += size) lines.push(<line key={`x${x}`} x1={x} y1={0} x2={x} y2={height} />);
  for (let y = size; y < height; y += size) lines.push(<line key={`y${y}`} x1={0} y1={y} x2={width} y2={y} />);
  return <svg className="gridOverlay" viewBox={`0 0 ${width} ${height}`}>{lines}</svg>;
}

function draftToPreview(draft) {
  if (draft.type === "pen") return draft;
  if (draft.type === "line" || draft.type === "arrow") return { ...draft, start: draft.start, end: draft.end };
  return {
    ...draft,
    box: boxFromPoints(draft.start, draft.end),
    text: ""
  };
}

function finalizeDraft(draft, noteId) {
  const base = {
    id: uid("item"),
    noteId,
    type: draft.type,
    color: draft.color,
    colorName: draft.colorName,
    width: draft.width,
    fontSize: draft.fontSize
  };
  if (draft.type === "pen") {
    if (draft.points.length < 2) return null;
    return { ...base, points: draft.points };
  }
  if (draft.type === "line" || draft.type === "arrow") {
    if (Math.hypot(draft.end.x - draft.start.x, draft.end.y - draft.start.y) < 4) return null;
    return { ...base, start: draft.start, end: draft.end };
  }
  const box = boxFromPoints(draft.start, draft.end);
  if (draft.type === "text") {
    const finalBox = box.w < 20 || box.h < 20 ? { x: draft.start.x, y: draft.start.y, w: 280, h: 120 } : box;
    return { ...base, box: finalBox, text: "" };
  }
  if (box.w < MIN_BOX || box.h < MIN_BOX) return null;
  return { ...base, box };
}

function ItemSvg({ item, selected, preview, onTextChange, onTextFocus }) {
  const strokeDasharray = preview ? "8 6" : undefined;
  if (item.type === "pen") {
    return <polyline points={item.points.map((p) => `${p.x},${p.y}`).join(" ")} fill="none" stroke={item.color} strokeWidth={item.width} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={strokeDasharray} />;
  }
  if (item.type === "line" || item.type === "arrow") {
    return <line x1={item.start.x} y1={item.start.y} x2={item.end.x} y2={item.end.y} stroke={item.color} strokeWidth={item.width} strokeLinecap="round" markerEnd={item.type === "arrow" ? "url(#arrowHead)" : undefined} strokeDasharray={strokeDasharray} />;
  }
  if (item.type === "rect") {
    return <rect x={item.box.x} y={item.box.y} width={item.box.w} height={item.box.h} fill="none" stroke={item.color} strokeWidth={item.width} strokeDasharray={strokeDasharray} />;
  }
  if (item.type === "triangle") {
    const points = trianglePoints(item.box);
    return <polygon points={points} fill="none" stroke={item.color} strokeWidth={item.width} strokeLinejoin="round" strokeDasharray={strokeDasharray} />;
  }
  if (item.type === "ellipse") {
    return <ellipse cx={item.box.x + item.box.w / 2} cy={item.box.y + item.box.h / 2} rx={item.box.w / 2} ry={item.box.h / 2} fill="none" stroke={item.color} strokeWidth={item.width} strokeDasharray={strokeDasharray} />;
  }
  if (item.type === "text") {
    return (
      <foreignObject x={item.box.x} y={item.box.y} width={Math.max(20, item.box.w)} height={Math.max(20, item.box.h)}>
        <textarea
          data-text-id={item.id}
          className={`inlineText ${selected ? "selected" : ""}`}
          style={{ color: item.color, fontSize: item.fontSize }}
          value={item.text}
          onFocus={onTextFocus}
          onPointerDown={(e) => e.stopPropagation()}
          onChange={(e) => onTextChange(e.target.value)}
          placeholder="Type..."
        />
      </foreignObject>
    );
  }
  return null;
}

function Selection({ item }) {
  const box = itemBox(item);
  if (!box) return null;
  const handles = [
    ["nw", box.x, box.y],
    ["ne", box.x + box.w, box.y],
    ["sw", box.x, box.y + box.h],
    ["se", box.x + box.w, box.y + box.h]
  ];
  return (
    <g className="selection">
      <rect x={box.x - 4} y={box.y - 4} width={box.w + 8} height={box.h + 8} />
      {["rect", "triangle", "ellipse", "text"].includes(item.type) &&
        handles.map(([name, x, y]) => <rect key={name} data-handle={name} x={x - 5} y={y - 5} width={10} height={10} />)}
    </g>
  );
}

function GroupSelection({ items }) {
  const box = combinedBox(items);
  if (!box) return null;
  return (
    <rect
      className="groupSelection"
      x={box.x - 8}
      y={box.y - 8}
      width={box.w + 16}
      height={box.h + 16}
    />
  );
}

function Marquee({ start, end }) {
  const box = boxFromPoints(start, end);
  return (
    <rect
      className="marquee"
      x={box.x}
      y={box.y}
      width={box.w}
      height={box.h}
    />
  );
}

function trianglePointArray(box) {
  return [
    { x: box.x + box.w / 2, y: box.y },
    { x: box.x + box.w, y: box.y + box.h },
    { x: box.x, y: box.y + box.h }
  ];
}

function trianglePoints(box) {
  return trianglePointArray(box).map((p) => `${p.x},${p.y}`).join(" ");
}

function handleAt(item, point) {
  const box = itemBox(item);
  if (!box || !["rect", "triangle", "ellipse", "text"].includes(item.type)) return null;
  const handles = {
    nw: { x: box.x, y: box.y },
    ne: { x: box.x + box.w, y: box.y },
    sw: { x: box.x, y: box.y + box.h },
    se: { x: box.x + box.w, y: box.y + box.h }
  };
  for (const [name, p] of Object.entries(handles)) {
    if (Math.abs(point.x - p.x) <= 10 && Math.abs(point.y - p.y) <= 10) return name;
  }
  return null;
}

function resizeItem(item, handle, point) {
  const copy = structuredClone(item);
  const box = { ...copy.box };
  if (handle.includes("n")) {
    box.h += box.y - point.y;
    box.y = point.y;
  }
  if (handle.includes("s")) box.h = point.y - box.y;
  if (handle.includes("w")) {
    box.w += box.x - point.x;
    box.x = point.x;
  }
  if (handle.includes("e")) box.w = point.x - box.x;
  copy.box = normalizeBox(box);
  copy.box.w = Math.max(20, copy.box.w);
  copy.box.h = Math.max(20, copy.box.h);
  return copy;
}

function offsetItem(item, dx, dy) {
  if (item.box) {
    item.box = { ...item.box, x: item.box.x + dx, y: item.box.y + dy };
  }
  if (item.points) {
    item.points = item.points.map((p) => ({ x: p.x + dx, y: p.y + dy }));
  }
  if (item.start) {
    item.start = { x: item.start.x + dx, y: item.start.y + dy };
  }
  if (item.end) {
    item.end = { x: item.end.x + dx, y: item.end.y + dy };
  }
  return item;
}

function drawItems(ctx, items) {
  for (const item of items) {
    ctx.save();
    ctx.strokeStyle = item.color;
    ctx.fillStyle = item.color;
    ctx.lineWidth = item.width || 1;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    if (item.type === "pen") {
      ctx.beginPath();
      item.points.forEach((p, index) => (index ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.stroke();
    } else if (item.type === "line" || item.type === "arrow") {
      ctx.beginPath();
      ctx.moveTo(item.start.x, item.start.y);
      ctx.lineTo(item.end.x, item.end.y);
      ctx.stroke();
      if (item.type === "arrow") drawArrowHead(ctx, item);
    } else if (item.type === "rect") {
      ctx.strokeRect(item.box.x, item.box.y, item.box.w, item.box.h);
    } else if (item.type === "triangle") {
      const points = trianglePointArray(item.box);
      ctx.beginPath();
      points.forEach((p, index) => (index ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.closePath();
      ctx.stroke();
    } else if (item.type === "ellipse") {
      ctx.beginPath();
      ctx.ellipse(item.box.x + item.box.w / 2, item.box.y + item.box.h / 2, item.box.w / 2, item.box.h / 2, 0, 0, Math.PI * 2);
      ctx.stroke();
    } else if (item.type === "text") {
      ctx.font = `${item.fontSize}px Segoe UI, Arial, sans-serif`;
      drawWrappedText(ctx, item.text, item.box.x, item.box.y, item.box.w, item.fontSize * 1.25);
    }
    ctx.restore();
  }
}

function drawArrowHead(ctx, item) {
  const angle = Math.atan2(item.end.y - item.start.y, item.end.x - item.start.x);
  const size = Math.max(10, item.width * 3);
  ctx.beginPath();
  ctx.moveTo(item.end.x, item.end.y);
  ctx.lineTo(item.end.x - size * Math.cos(angle - Math.PI / 6), item.end.y - size * Math.sin(angle - Math.PI / 6));
  ctx.lineTo(item.end.x - size * Math.cos(angle + Math.PI / 6), item.end.y - size * Math.sin(angle + Math.PI / 6));
  ctx.closePath();
  ctx.fill();
}

function drawWrappedText(ctx, text, x, y, maxWidth, lineHeight) {
  let line = "";
  let yy = y;
  for (const paragraph of text.split("\n")) {
    const words = paragraph.split(" ");
    for (const word of words) {
      const test = line ? `${line} ${word}` : word;
      if (ctx.measureText(test).width <= maxWidth) {
        line = test;
      } else {
        if (line) {
          ctx.fillText(line, x, yy);
          yy += lineHeight;
        }
        line = word;
      }
    }
    if (line) {
      ctx.fillText(line, x, yy);
      yy += lineHeight;
      line = "";
    } else {
      yy += lineHeight;
    }
  }
}

function buildMetadata(state, size) {
  return {
    version: 1,
    image: "screenshot.png",
    size,
    coordinate_space: "screenshot_pixels",
    notes: state.notes
      .map((note) => ({
        id: note.id,
        name: note.name || `Note ${note.id}`,
        text: note.text,
        items: state.items
          .filter((item) => item.noteId === note.id)
          .map((item, index) => metadataItem(item, index + 1))
      }))
      .filter((note) => note.text.trim() || note.items.length)
  };
}

function metadataItem(item, id) {
  const base = { id, color: item.color, color_name: item.colorName };
  if (item.type === "pen") {
    const box = itemBox(item);
    return { ...base, type: "freehand", points: item.points.map((p) => [Math.round(p.x), Math.round(p.y)]), width: item.width, bbox: boxToArray(box) };
  }
  if (item.type === "line" || item.type === "arrow") {
    return { ...base, type: item.type, points: [[Math.round(item.start.x), Math.round(item.start.y)], [Math.round(item.end.x), Math.round(item.end.y)]], width: item.width, bbox: boxToArray(itemBox(item)) };
  }
  if (item.type === "rect") return { ...base, type: "rectangle", bbox: boxToArray(item.box), width: item.width };
  if (item.type === "triangle") return { ...base, type: "triangle", points: trianglePointArray(item.box).map((p) => [Math.round(p.x), Math.round(p.y)]), bbox: boxToArray(item.box), width: item.width };
  if (item.type === "ellipse") return { ...base, type: "ellipse", bbox: boxToArray(item.box), width: item.width };
  if (item.type === "text") return { ...base, type: "text", xy: [Math.round(item.box.x), Math.round(item.box.y)], bbox: boxToArray(item.box), text: item.text, font_size: item.fontSize };
  return base;
}

function boxToArray(box) {
  return [Math.round(box.x), Math.round(box.y), Math.round(box.x + box.w), Math.round(box.y + box.h)];
}
