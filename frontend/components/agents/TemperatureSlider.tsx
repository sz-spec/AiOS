'use client';

import React from 'react';

interface TemperatureSliderProps {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
}

const PRESETS = [
  { value: 0, label: 'Precise', description: 'Deterministic, focused' },
  { value: 0.7, label: 'Balanced', description: 'Default, versatile' },
  { value: 1.0, label: 'Creative', description: 'More varied' },
  { value: 2.0, label: 'Wild', description: 'Maximum randomness' },
];

export function TemperatureSlider({
  value,
  onChange,
  min = 0,
  max = 2,
  step = 0.1,
  disabled = false,
}: TemperatureSliderProps) {
  const percentage = ((value - min) / (max - min)) * 100;

  // Determine the temperature category
  const getCategory = () => {
    if (value <= 0.3) return { label: 'Precise', color: 'var(--success)' };
    if (value <= 0.8) return { label: 'Balanced', color: 'var(--accent)' };
    if (value <= 1.2) return { label: 'Creative', color: 'var(--warning)' };
    return { label: 'Wild', color: 'var(--error)' };
  };

  const category = getCategory();

  return (
    <div>
      {/* Header with current value */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <span
          style={{
            fontSize: '12px',
            fontWeight: 500,
            color: category.color,
            padding: '2px 8px',
            backgroundColor: `${category.color}15`,
            borderRadius: 'var(--radius-full)',
          }}
        >
          {category.label}
        </span>
        <span
          style={{
            fontSize: '14px',
            fontWeight: 600,
            color: 'var(--text-primary)',
            fontFamily: 'monospace',
          }}
        >
          {value.toFixed(1)}
        </span>
      </div>

      {/* Slider */}
      <div style={{ position: 'relative', marginBottom: '16px' }}>
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          disabled={disabled}
          style={{
            width: '100%',
            height: '6px',
            appearance: 'none',
            backgroundColor: 'var(--bg-tertiary)',
            borderRadius: '3px',
            outline: 'none',
            cursor: disabled ? 'not-allowed' : 'pointer',
            opacity: disabled ? 0.5 : 1,
          }}
        />
        {/* Progress fill */}
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            height: '6px',
            width: `${percentage}%`,
            background: `linear-gradient(90deg, var(--success) 0%, var(--accent) 35%, var(--warning) 60%, var(--error) 100%)`,
            borderRadius: '3px',
            pointerEvents: 'none',
          }}
        />
      </div>

      {/* Labels */}
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: 'var(--text-tertiary)' }}>
        <span>Precise</span>
        <span>Balanced</span>
        <span>Creative</span>
      </div>

      {/* Quick presets */}
      <div style={{ display: 'flex', gap: '6px', marginTop: '12px' }}>
        {PRESETS.map((preset) => (
          <button
            key={preset.value}
            type="button"
            onClick={() => onChange(preset.value)}
            disabled={disabled}
            title={preset.description}
            style={{
              flex: 1,
              padding: '6px 8px',
              fontSize: '11px',
              fontWeight: 500,
              backgroundColor: Math.abs(value - preset.value) < 0.05 ? 'var(--accent)' : 'var(--bg-tertiary)',
              color: Math.abs(value - preset.value) < 0.05 ? 'white' : 'var(--text-secondary)',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              cursor: disabled ? 'not-allowed' : 'pointer',
              opacity: disabled ? 0.5 : 1,
              transition: 'all 0.15s ease',
            }}
          >
            {preset.value.toFixed(1)}
          </button>
        ))}
      </div>

      {/* Slider input styling */}
      <style>{`
        input[type="range"]::-webkit-slider-thumb {
          appearance: none;
          width: 18px;
          height: 18px;
          background: white;
          border: 2px solid var(--accent);
          border-radius: 50%;
          cursor: pointer;
          box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
          position: relative;
          z-index: 10;
        }

        input[type="range"]::-moz-range-thumb {
          width: 18px;
          height: 18px;
          background: white;
          border: 2px solid var(--accent);
          border-radius: 50%;
          cursor: pointer;
          box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }

        input[type="range"]:focus::-webkit-slider-thumb {
          box-shadow: 0 0 0 4px rgba(217, 119, 6, 0.2);
        }
      `}</style>
    </div>
  );
}

export default TemperatureSlider;
