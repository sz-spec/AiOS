import { create } from 'zustand';

export type ProjectStatus = 'draft' | 'building' | 'ready' | 'deployed' | 'error';

export interface Project {
  id: string;
  name: string;
  description: string;
  category: string;
  status: ProjectStatus;
  templateId?: string;
  files: Record<string, string>;
  settings: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

interface ProjectState {
  projects: Project[];
  currentProject: Project | null;
  isLoading: boolean;
  searchQuery: string;
  sortBy: 'updatedAt' | 'name' | 'createdAt';

  setProjects: (projects: Project[]) => void;
  addProject: (project: Project) => void;
  updateProject: (id: string, updates: Partial<Project>) => void;
  deleteProject: (id: string) => void;
  setCurrentProject: (project: Project | null) => void;
  setIsLoading: (loading: boolean) => void;
  setSearchQuery: (query: string) => void;
  setSortBy: (sort: 'updatedAt' | 'name' | 'createdAt') => void;
  filteredProjects: () => Project[];
}

export const useProjectStore = create<ProjectState>((set, get) => ({
  projects: [],
  currentProject: null,
  isLoading: false,
  searchQuery: '',
  sortBy: 'updatedAt',

  setProjects: (projects) => set({ projects }),
  addProject: (project) => set((s) => ({ projects: [project, ...s.projects] })),
  updateProject: (id, updates) => set((s) => ({
    projects: s.projects.map((p) => p.id === id ? { ...p, ...updates } : p),
    currentProject: s.currentProject?.id === id ? { ...s.currentProject, ...updates } : s.currentProject,
  })),
  deleteProject: (id) => set((s) => ({
    projects: s.projects.filter((p) => p.id !== id),
    currentProject: s.currentProject?.id === id ? null : s.currentProject,
  })),
  setCurrentProject: (project) => set({ currentProject: project }),
  setIsLoading: (loading) => set({ isLoading: loading }),
  setSearchQuery: (query) => set({ searchQuery: query }),
  setSortBy: (sort) => set({ sortBy: sort }),
  filteredProjects: () => {
    const { projects, searchQuery, sortBy } = get();
    let filtered = projects;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      filtered = filtered.filter((p) =>
        p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q)
      );
    }
    return [...filtered].sort((a, b) => {
      if (sortBy === 'name') return a.name.localeCompare(b.name);
      if (sortBy === 'createdAt') return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
      return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
    });
  },
}));
