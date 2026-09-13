"use client";
import { useState } from "react";

interface Component {
  id: string;
  name: string;
  description: string;
  category: string;
  rating: number;
  downloads: number;
  code: string;
  dependencies: string[];
}

export default function ComponentDetail({
  component,
  onInstall,
  onClose,
}: {
  component: Component;
  onInstall?: (id: string) => void;
  onClose: () => void;
}) {
  const [installing, setInstalling] = useState(false);

  const handleInstall = async () => {
    setInstalling(true);
    try {
      const res = await fetch(`/api/v1/marketplace/install/${component.id}`, { method: "POST" });
      if (res.ok) onInstall?.(component.id);
    } finally {
      setInstalling(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg w-full max-w-2xl max-h-[80vh] overflow-y-auto">
        <div className="p-6 space-y-4">
          <div className="flex justify-between items-start">
            <div>
              <h2 className="text-xl font-semibold">{component.name}</h2>
              <p className="text-gray-600 text-sm">{component.description}</p>
            </div>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">
              &times;
            </button>
          </div>

          <div className="flex gap-4 text-sm text-gray-500">
            <span>Category: {component.category}</span>
            <span>Rating: {component.rating}/5</span>
            <span>Downloads: {component.downloads.toLocaleString()}</span>
          </div>

          {component.dependencies.length > 0 && (
            <div>
              <h3 className="text-sm font-medium mb-1">Dependencies</h3>
              <div className="flex gap-1">
                {component.dependencies.map((dep) => (
                  <span key={dep} className="text-xs bg-gray-100 px-2 py-1 rounded">{dep}</span>
                ))}
              </div>
            </div>
          )}

          <div>
            <h3 className="text-sm font-medium mb-1">Preview</h3>
            <pre className="bg-gray-50 p-4 rounded-lg text-xs overflow-x-auto max-h-60">
              {component.code}
            </pre>
          </div>

          <button
            onClick={handleInstall}
            disabled={installing}
            className="w-full py-2 bg-blue-600 text-white rounded-lg font-medium disabled:opacity-50"
          >
            {installing ? "Installing..." : "Install Component"}
          </button>
        </div>
      </div>
    </div>
  );
}
