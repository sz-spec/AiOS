import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ViewModeSelector } from './ViewModeSelector';

describe('ViewModeSelector', () => {
  it('preserves focused controls across parent rerenders and uses current callbacks', () => {
    const select = vi.fn();
    const back = vi.fn();
    const { rerender } = render(
      <ViewModeSelector viewMode="wizard" onModeChange={select} onBack={back} />,
    );
    const expert = screen.getByRole('button', { name: /Expert/ });
    expert.focus();
    const updated = vi.fn();
    rerender(<ViewModeSelector viewMode="expert" onModeChange={updated} onBack={back} />);
    expect(screen.getByRole('button', { name: /Expert/ })).toBe(expert);
    expect(document.activeElement).toBe(expert);
    fireEvent.click(screen.getByRole('button', { name: /Party/ }));
    expect(updated).toHaveBeenCalledWith('party');
    expect(select).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTitle('Back to projects'));
    expect(back).toHaveBeenCalledOnce();
  });

  it('keeps all three mode choices available', () => {
    const select = vi.fn();
    render(<ViewModeSelector viewMode="auto" onModeChange={select} onBack={vi.fn()} />);
    for (const [label, mode] of [['Guided', 'wizard'], ['Expert', 'expert'], ['Party', 'party']]) {
      fireEvent.click(screen.getByRole('button', { name: new RegExp(label) }));
      expect(select).toHaveBeenLastCalledWith(mode);
    }
  });
});
