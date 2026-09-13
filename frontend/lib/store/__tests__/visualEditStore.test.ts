import { describe, it, expect, beforeEach } from 'vitest';
import { useVisualEditStore, SelectedElement } from '../visualEditStore';

const makeElement = (overrides: Partial<SelectedElement> = {}): SelectedElement => ({
  id: 'el-1',
  tagName: 'DIV',
  text: 'Hello',
  styles: {
    color: '#000',
    backgroundColor: '#fff',
    fontSize: '16px',
    fontFamily: 'sans-serif',
    padding: '0',
    margin: '0',
    borderRadius: '0',
  },
  rect: { top: 0, left: 0, width: 100, height: 50 },
  ...overrides,
});

describe('useVisualEditStore', () => {
  beforeEach(() => {
    // Merge reset — preserves action functions
    useVisualEditStore.setState({
      isActive: false,
      selectedElement: null,
      hoveredElement: null,
      pendingChanges: new Map(),
    });
  });

  it('setActive true activates edit mode and clears selection', () => {
    useVisualEditStore.setState({ selectedElement: makeElement(), isActive: false });
    useVisualEditStore.getState().setActive(true);
    const state = useVisualEditStore.getState();
    expect(state.isActive).toBe(true);
    expect(state.selectedElement).toBeNull();
  });

  it('setActive false deactivates edit mode', () => {
    useVisualEditStore.setState({ isActive: true });
    useVisualEditStore.getState().setActive(false);
    expect(useVisualEditStore.getState().isActive).toBe(false);
  });

  it('selectElement stores the element', () => {
    const el = makeElement({ id: 'el-2', tagName: 'P' });
    useVisualEditStore.getState().selectElement(el);
    expect(useVisualEditStore.getState().selectedElement?.id).toBe('el-2');
  });

  it('applyStyleChange updates style property on selected element', () => {
    useVisualEditStore.setState({ selectedElement: makeElement() });
    useVisualEditStore.getState().applyStyleChange('color', '#ff0000');
    expect(useVisualEditStore.getState().selectedElement?.styles.color).toBe('#ff0000');
  });

  it('applyStyleChange is no-op when no element is selected', () => {
    useVisualEditStore.setState({ selectedElement: null });
    // Should not throw
    useVisualEditStore.getState().applyStyleChange('color', '#ff0000');
    expect(useVisualEditStore.getState().selectedElement).toBeNull();
  });

  it('applyStyleChange does not affect other style properties', () => {
    useVisualEditStore.setState({ selectedElement: makeElement() });
    useVisualEditStore.getState().applyStyleChange('fontSize', '24px');
    const styles = useVisualEditStore.getState().selectedElement!.styles;
    expect(styles.fontSize).toBe('24px');
    expect(styles.color).toBe('#000');
    expect(styles.padding).toBe('0');
  });

  it('applyTextChange updates text on selected element', () => {
    useVisualEditStore.setState({ selectedElement: makeElement({ text: 'original' }) });
    useVisualEditStore.getState().applyTextChange('updated text');
    expect(useVisualEditStore.getState().selectedElement?.text).toBe('updated text');
  });

  it('applyTextChange is no-op when no element is selected', () => {
    useVisualEditStore.setState({ selectedElement: null });
    useVisualEditStore.getState().applyTextChange('text');
    expect(useVisualEditStore.getState().selectedElement).toBeNull();
  });

  it('clearChanges resets pendingChanges and selectedElement', () => {
    useVisualEditStore.setState({
      selectedElement: makeElement(),
      pendingChanges: new Map([['el-1', { color: 'red' }]]),
    });
    useVisualEditStore.getState().clearChanges();
    const state = useVisualEditStore.getState();
    expect(state.selectedElement).toBeNull();
    expect(state.pendingChanges.size).toBe(0);
  });

  it('setHoveredElement stores the hovered element id', () => {
    useVisualEditStore.getState().setHoveredElement('el-5');
    expect(useVisualEditStore.getState().hoveredElement).toBe('el-5');
  });
});
