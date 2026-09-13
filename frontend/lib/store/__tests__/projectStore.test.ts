import { describe, it, expect, beforeEach } from 'vitest';
import { useProjectStore, Project } from '../projectStore';

const makeProject = (overrides: Partial<Project> = {}): Project => ({
  id: 'p1',
  name: 'Test Project',
  description: 'A test project',
  category: 'website',
  status: 'draft',
  files: {},
  settings: {},
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  ...overrides,
});

describe('useProjectStore', () => {
  beforeEach(() => {
    // Merge reset — preserves action functions
    useProjectStore.setState({
      projects: [],
      currentProject: null,
      isLoading: false,
      searchQuery: '',
      sortBy: 'updatedAt',
    });
  });

  it('addProject prepends to the list', () => {
    const { addProject } = useProjectStore.getState();
    const p1 = makeProject({ id: 'p1', name: 'First' });
    const p2 = makeProject({ id: 'p2', name: 'Second' });
    addProject(p1);
    addProject(p2);
    const { projects } = useProjectStore.getState();
    expect(projects[0].id).toBe('p2');
    expect(projects[1].id).toBe('p1');
  });

  it('updateProject changes fields on matching project', () => {
    const { addProject, updateProject } = useProjectStore.getState();
    addProject(makeProject({ id: 'p1', name: 'Old Name' }));
    updateProject('p1', { name: 'New Name', status: 'ready' });
    const { projects } = useProjectStore.getState();
    expect(projects[0].name).toBe('New Name');
    expect(projects[0].status).toBe('ready');
  });

  it('updateProject syncs currentProject when ids match', () => {
    const { addProject, updateProject, setCurrentProject } = useProjectStore.getState();
    const proj = makeProject({ id: 'p1', name: 'Original' });
    addProject(proj);
    setCurrentProject(proj);
    updateProject('p1', { name: 'Updated' });
    expect(useProjectStore.getState().currentProject?.name).toBe('Updated');
  });

  it('updateProject does not change currentProject when ids differ', () => {
    const { addProject, updateProject, setCurrentProject } = useProjectStore.getState();
    addProject(makeProject({ id: 'p1', name: 'P1' }));
    addProject(makeProject({ id: 'p2', name: 'P2' }));
    setCurrentProject(makeProject({ id: 'p2', name: 'P2' }));
    updateProject('p1', { name: 'P1 updated' });
    expect(useProjectStore.getState().currentProject?.name).toBe('P2');
  });

  it('deleteProject removes project from list', () => {
    const { addProject, deleteProject } = useProjectStore.getState();
    addProject(makeProject({ id: 'p1' }));
    addProject(makeProject({ id: 'p2' }));
    deleteProject('p1');
    const { projects } = useProjectStore.getState();
    expect(projects).toHaveLength(1);
    expect(projects[0].id).toBe('p2');
  });

  it('deleteProject clears currentProject when deleted id matches', () => {
    const { addProject, deleteProject, setCurrentProject } = useProjectStore.getState();
    const proj = makeProject({ id: 'p1' });
    addProject(proj);
    setCurrentProject(proj);
    deleteProject('p1');
    expect(useProjectStore.getState().currentProject).toBeNull();
  });

  it('filteredProjects filters by name search query', () => {
    const { addProject, setSearchQuery } = useProjectStore.getState();
    addProject(makeProject({ id: 'p1', name: 'Alpha Project', description: '' }));
    addProject(makeProject({ id: 'p2', name: 'Beta Project', description: '' }));
    setSearchQuery('alpha');
    const filtered = useProjectStore.getState().filteredProjects();
    expect(filtered).toHaveLength(1);
    expect(filtered[0].id).toBe('p1');
  });

  it('filteredProjects filters by description', () => {
    const { addProject, setSearchQuery } = useProjectStore.getState();
    addProject(makeProject({ id: 'p1', name: 'X', description: 'ecommerce store' }));
    addProject(makeProject({ id: 'p2', name: 'Y', description: 'blog site' }));
    setSearchQuery('ecommerce');
    const filtered = useProjectStore.getState().filteredProjects();
    expect(filtered).toHaveLength(1);
    expect(filtered[0].id).toBe('p1');
  });

  it('filteredProjects sorts by name', () => {
    const { addProject, setSortBy } = useProjectStore.getState();
    addProject(makeProject({ id: 'p1', name: 'Zebra' }));
    addProject(makeProject({ id: 'p2', name: 'Apple' }));
    setSortBy('name');
    const sorted = useProjectStore.getState().filteredProjects();
    expect(sorted[0].name).toBe('Apple');
    expect(sorted[1].name).toBe('Zebra');
  });

  it('filteredProjects sorts by updatedAt descending', () => {
    const { addProject, setSortBy } = useProjectStore.getState();
    addProject(makeProject({ id: 'p1', updatedAt: '2026-01-01T00:00:00Z' }));
    addProject(makeProject({ id: 'p2', updatedAt: '2026-06-01T00:00:00Z' }));
    setSortBy('updatedAt');
    const sorted = useProjectStore.getState().filteredProjects();
    expect(sorted[0].id).toBe('p2');
  });
});
