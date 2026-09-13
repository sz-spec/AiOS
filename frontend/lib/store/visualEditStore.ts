import { create } from 'zustand';

export interface SelectedElement {
  id: string;
  tagName: string;
  text: string;
  styles: {
    color: string;
    backgroundColor: string;
    fontSize: string;
    fontFamily: string;
    padding: string;
    margin: string;
    borderRadius: string;
  };
  rect: { top: number; left: number; width: number; height: number };
}

interface VisualEditState {
  isActive: boolean;
  selectedElement: SelectedElement | null;
  hoveredElement: string | null;
  pendingChanges: Map<string, Record<string, string>>;

  setActive: (active: boolean) => void;
  selectElement: (element: SelectedElement | null) => void;
  setHoveredElement: (id: string | null) => void;
  applyStyleChange: (property: string, value: string) => void;
  applyTextChange: (text: string) => void;
  clearChanges: () => void;
}

export const useVisualEditStore = create<VisualEditState>((set, get) => ({
  isActive: false,
  selectedElement: null,
  hoveredElement: null,
  pendingChanges: new Map(),

  setActive: (active) => set({ isActive: active, selectedElement: null }),
  selectElement: (element) => set({ selectedElement: element }),
  setHoveredElement: (id) => set({ hoveredElement: id }),
  applyStyleChange: (property, value) => {
    const { selectedElement } = get();
    if (!selectedElement) return;
    set((s) => ({
      selectedElement: s.selectedElement ? {
        ...s.selectedElement,
        styles: { ...s.selectedElement.styles, [property]: value },
      } : null,
    }));
  },
  applyTextChange: (text) => {
    set((s) => ({
      selectedElement: s.selectedElement ? { ...s.selectedElement, text } : null,
    }));
  },
  clearChanges: () => set({ pendingChanges: new Map(), selectedElement: null }),
}));
