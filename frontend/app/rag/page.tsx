'use client';

import { BrainIcon } from '@/components/shared/EmptyState';

export default function RAGPage() {
  return (
    <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-secondary)' }}>
      {/* Header */}
      <header
        style={{
          padding: '16px 24px',
          borderBottom: '1px solid var(--border-light)',
          backgroundColor: 'var(--bg-primary)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div
            style={{
              width: '36px',
              height: '36px',
              borderRadius: '10px',
              background: 'linear-gradient(135deg, var(--accent), #ea580c)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
            }}
          >
            <BrainIcon size={20} />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>Knowledge Base</h1>
            <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-secondary)' }}>
              Retrieval-Augmented Generation - Not Yet Implemented
            </p>
          </div>
        </div>
      </header>

      {/* Development Instructions */}
      <main style={{ maxWidth: '900px', margin: '0 auto', padding: '32px 24px' }}>
        {/* Overview Card */}
        <div
          style={{
            backgroundColor: 'var(--bg-primary)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-lg)',
            padding: '24px',
            marginBottom: '24px',
          }}
        >
          <h2 style={{ margin: '0 0 12px', fontSize: '20px', fontWeight: 600 }}>
            What is RAG?
          </h2>
          <p style={{ margin: 0, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            RAG (Retrieval-Augmented Generation) allows the AI to search through your uploaded documents
            and use relevant information to provide more accurate, context-aware responses. Users can
            upload PDFs, text files, and markdown documents to create searchable knowledge bases.
          </p>
        </div>

        {/* Two Column Layout */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px', marginBottom: '24px' }}>
          {/* Frontend Files to Create */}
          <div
            style={{
              backgroundColor: 'var(--bg-primary)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-lg)',
              padding: '24px',
            }}
          >
            <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600, color: 'var(--accent)' }}>
              Frontend Files to Create
            </h3>
            <ul style={{ margin: 0, padding: '0 0 0 20px', color: 'var(--text-primary)', lineHeight: 2 }}>
              <li><code style={{ backgroundColor: 'var(--bg-tertiary)', padding: '2px 6px', borderRadius: '4px', fontSize: '13px' }}>hooks/useRAG.ts</code> - API hook</li>
              <li><code style={{ backgroundColor: 'var(--bg-tertiary)', padding: '2px 6px', borderRadius: '4px', fontSize: '13px' }}>components/rag/FileUpload.tsx</code></li>
              <li><code style={{ backgroundColor: 'var(--bg-tertiary)', padding: '2px 6px', borderRadius: '4px', fontSize: '13px' }}>components/rag/KnowledgeBaseList.tsx</code></li>
              <li><code style={{ backgroundColor: 'var(--bg-tertiary)', padding: '2px 6px', borderRadius: '4px', fontSize: '13px' }}>components/rag/DocumentList.tsx</code></li>
              <li><code style={{ backgroundColor: 'var(--bg-tertiary)', padding: '2px 6px', borderRadius: '4px', fontSize: '13px' }}>components/rag/SearchInterface.tsx</code></li>
              <li><code style={{ backgroundColor: 'var(--bg-tertiary)', padding: '2px 6px', borderRadius: '4px', fontSize: '13px' }}>components/rag/ChunkingSettings.tsx</code></li>
            </ul>
          </div>

          {/* Backend Endpoints */}
          <div
            style={{
              backgroundColor: 'var(--bg-primary)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-lg)',
              padding: '24px',
            }}
          >
            <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600, color: 'var(--success)' }}>
              Backend Endpoints (to create)
            </h3>
            <ul style={{ margin: 0, padding: '0 0 0 20px', color: 'var(--text-primary)', lineHeight: 2, fontSize: '14px' }}>
              <li><code style={{ color: 'var(--accent)' }}>POST</code> /api/rag/knowledge-bases</li>
              <li><code style={{ color: 'var(--accent)' }}>GET</code> /api/rag/knowledge-bases</li>
              <li><code style={{ color: 'var(--accent)' }}>DELETE</code> /api/rag/knowledge-bases/:id</li>
              <li><code style={{ color: 'var(--accent)' }}>POST</code> /api/rag/documents/upload</li>
              <li><code style={{ color: 'var(--accent)' }}>GET</code> /api/rag/documents/:kbId</li>
              <li><code style={{ color: 'var(--accent)' }}>POST</code> /api/rag/search</li>
            </ul>
          </div>
        </div>

        {/* UI Layout Specification */}
        <div
          style={{
            backgroundColor: 'var(--bg-primary)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-lg)',
            padding: '24px',
            marginBottom: '24px',
          }}
        >
          <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600 }}>
            Expected UI Layout
          </h3>
          <pre
            style={{
              margin: 0,
              padding: '16px',
              backgroundColor: 'var(--bg-tertiary)',
              borderRadius: 'var(--radius-md)',
              fontSize: '12px',
              lineHeight: 1.5,
              overflow: 'auto',
              fontFamily: "'SF Mono', 'Fira Code', Consolas, monospace",
            }}
          >
{`┌─────────────────────────────────────────────────────────────────┐
│  Knowledge Base                              [+ New KB]     │
├──────────────────────┬──────────────────────────────────────────┤
│                      │                                          │
│  Knowledge Bases     │  Documents in "Product Docs"             │
│  ─────────────────   │  ────────────────────────────            │
│                      │                                          │
│  ▶ Product Docs (5)  │  [Upload Files]  [Drag & drop zone]     │
│    Customer FAQ (12) │                                          │
│    API Reference (8) │  ┌─────────────────────────────────┐    │
│                      │  │ 📄 product-guide.pdf    2.3 MB  │    │
│                      │  │ 📄 faq.md               45 KB   │    │
│  [+ New Knowledge    │  │ 📄 api-docs.txt         120 KB  │    │
│     Base]            │  │ 📄 changelog.md         12 KB   │    │
│                      │  └─────────────────────────────────┘    │
│                      │                                          │
│  Settings            │  Search this knowledge base:             │
│  ─────────────────   │  ┌─────────────────────────────────┐    │
│  Chunk Size: 512     │  │ How do I configure webhooks?    │    │
│  Overlap: 50         │  └─────────────────────────────────┘    │
│  Embedding: ada-002  │                                          │
│                      │  Results:                                │
│                      │  • Found in api-docs.txt (92% match)    │
│                      │  • Found in faq.md (78% match)          │
│                      │                                          │
└──────────────────────┴──────────────────────────────────────────┘`}
          </pre>
        </div>

        {/* Implementation Steps */}
        <div
          style={{
            backgroundColor: 'var(--bg-primary)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-lg)',
            padding: '24px',
          }}
        >
          <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600 }}>
            Step-by-Step Implementation Guide
          </h3>
          <ol style={{ margin: 0, padding: '0 0 0 20px', color: 'var(--text-primary)', lineHeight: 2 }}>
            <li><strong>Create useRAG hook</strong> - State management for knowledge bases, documents, and search</li>
            <li><strong>Build FileUpload component</strong> - Drag-and-drop with progress indicator, accepts PDF/TXT/MD/DOCX</li>
            <li><strong>Build KnowledgeBaseList</strong> - Sidebar list with create/delete, shows document count</li>
            <li><strong>Build DocumentList</strong> - Table showing uploaded files with size, date, delete option</li>
            <li><strong>Build SearchInterface</strong> - Query input with results showing source documents and match scores</li>
            <li><strong>Build ChunkingSettings</strong> - Configure chunk size (256-2048), overlap (0-200), embedding model</li>
            <li><strong>Wire up to backend</strong> - Backend already has ChromaDB/FAISS in <code>backend/ai/rag/</code></li>
            <li><strong>Integrate with Chat</strong> - Add option to select knowledge base when chatting</li>
          </ol>
        </div>
      </main>
    </div>
  );
}
