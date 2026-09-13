'use client';

import { useState, useEffect } from 'react';
import { Search, Star, Download, Plus } from 'lucide-react';

interface MarketplaceComponent {
  id: string;
  name: string;
  description: string;
  category: string;
  rating: number;
  downloads: number;
  preview: string;
}

const categories = ['All', 'Navigation', 'Forms', 'Charts', 'Maps', 'Payments', 'Social', 'Media', 'Layout'];

interface MarketplaceBrowserProps {
  projectId: string;
  onComponentAdded?: (componentId: string) => void;
}

export function MarketplaceBrowser({ projectId, onComponentAdded }: MarketplaceBrowserProps) {
  const [components, setComponents] = useState<MarketplaceComponent[]>([]);
  const [search, setSearch] = useState('');
  const [activeCategory, setActiveCategory] = useState('All');
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const params = new URLSearchParams();
        if (search) params.set('search', search);
        if (activeCategory !== 'All') params.set('category', activeCategory.toLowerCase());

        const res = await fetch(`/api/v1/marketplace/components?${params}`);
        if (res.ok) {
          const data = await res.json();
          setComponents(data.components || []);
        }
      } catch {
        // Mock data for dev
        setComponents([
          { id: '1', name: 'Navbar', description: 'Responsive navigation bar with mobile menu', category: 'Navigation', rating: 4.8, downloads: 1250, preview: '' },
          { id: '2', name: 'Contact Form', description: 'Form with validation and email sending', category: 'Forms', rating: 4.6, downloads: 890, preview: '' },
          { id: '3', name: 'Bar Chart', description: 'Interactive bar chart with animations', category: 'Charts', rating: 4.5, downloads: 720, preview: '' },
          { id: '4', name: 'Hero Section', description: 'Landing page hero with CTA buttons', category: 'Layout', rating: 4.9, downloads: 2100, preview: '' },
          { id: '5', name: 'Pricing Table', description: 'Pricing plans with toggle monthly/yearly', category: 'Payments', rating: 4.7, downloads: 650, preview: '' },
          { id: '6', name: 'Image Gallery', description: 'Lightbox gallery with lazy loading', category: 'Media', rating: 4.4, downloads: 530, preview: '' },
        ]);
      }
      setIsLoading(false);
    }
    load();
  }, [search, activeCategory]);

  const addComponent = async (componentId: string) => {
    try {
      await fetch('/api/v1/marketplace/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId, component_id: componentId }),
      });
      onComponentAdded?.(componentId);
    } catch { /* ignore */ }
  };

  const filtered = components.filter((c) => {
    if (activeCategory !== 'All' && c.category !== activeCategory) return false;
    if (search && !c.name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <div style={{ padding: '24px' }}>
      <h2 style={{ margin: '0 0 20px', fontSize: '20px', fontWeight: 600 }}>Component Marketplace</h2>

      {/* Search */}
      <div style={{ position: 'relative', marginBottom: '16px' }}>
        <Search size={16} style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)' }} />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search components..."
          style={{
            width: '100%', padding: '10px 12px 10px 36px',
            borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-light)',
            fontSize: '14px', outline: 'none',
          }}
        />
      </div>

      {/* Categories */}
      <div style={{ display: 'flex', gap: '6px', marginBottom: '20px', flexWrap: 'wrap' }}>
        {categories.map((cat) => (
          <button
            key={cat}
            onClick={() => setActiveCategory(cat)}
            style={{
              padding: '6px 14px',
              borderRadius: 'var(--radius-full)',
              fontSize: '12px',
              fontWeight: 500,
              backgroundColor: activeCategory === cat ? 'var(--accent)' : 'var(--bg-tertiary)',
              color: activeCategory === cat ? 'white' : 'var(--text-secondary)',
            }}
          >
            {cat}
          </button>
        ))}
      </div>

      {/* Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: '16px' }}>
        {filtered.map((comp) => (
          <div key={comp.id} style={{
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-light)',
            overflow: 'hidden',
            backgroundColor: 'var(--bg-secondary)',
          }}>
            {/* Preview */}
            <div style={{
              height: '120px',
              backgroundColor: 'var(--bg-tertiary)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--text-tertiary)',
              fontSize: '13px',
            }}>
              Preview
            </div>

            {/* Info */}
            <div style={{ padding: '14px' }}>
              <div style={{ fontWeight: 600, fontSize: '14px', marginBottom: '4px' }}>{comp.name}</div>
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.4, marginBottom: '10px' }}>
                {comp.description}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: 'var(--text-tertiary)' }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '2px' }}>
                    <Star size={12} style={{ fill: '#f59e0b', color: '#f59e0b' }} />
                    {comp.rating}
                  </span>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '2px' }}>
                    <Download size={12} />
                    {comp.downloads}
                  </span>
                </div>
                <button
                  onClick={() => addComponent(comp.id)}
                  style={{
                    padding: '6px 14px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: 'var(--accent)',
                    color: 'white',
                    fontSize: '12px',
                    fontWeight: 500,
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                  }}
                >
                  <Plus size={12} />
                  Add
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
