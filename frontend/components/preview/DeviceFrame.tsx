'use client';

import { DeviceType, DEVICE_SIZES } from '@/hooks/usePreview';

interface DeviceFrameProps {
  device: DeviceType;
  zoom: number;
  children: React.ReactNode;
}

export function DeviceFrame({ device, zoom, children }: DeviceFrameProps) {
  const size = DEVICE_SIZES[device];
  const scale = zoom / 100;

  return (
    <div style={{
      flex: 1,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: 'var(--bg-tertiary)',
      overflow: 'auto',
      padding: '24px',
    }}>
      <div style={{
        width: `${size.width}px`,
        height: `${size.height}px`,
        transform: `scale(${scale})`,
        transformOrigin: 'top center',
        borderRadius: device === 'mobile' ? '24px' : device === 'tablet' ? '16px' : '8px',
        border: device === 'desktop' ? '1px solid var(--border-light)' : `${device === 'mobile' ? '12px' : '8px'} solid #1a1a1a`,
        overflow: 'hidden',
        backgroundColor: 'white',
        boxShadow: 'var(--shadow-lg)',
      }}>
        {children}
      </div>
    </div>
  );
}
