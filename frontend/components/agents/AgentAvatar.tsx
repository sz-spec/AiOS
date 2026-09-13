'use client';

import React from 'react';

interface AgentAvatarProps {
  name: string;
  color: string;
  size?: 'sm' | 'md' | 'lg';
}

const SIZES = {
  sm: { box: 24, font: 11, weight: 600 },
  md: { box: 32, font: 13, weight: 600 },
  lg: { box: 44, font: 18, weight: 700 },
} as const;

export function AgentAvatar({ name, color, size = 'md' }: AgentAvatarProps) {
  const s = SIZES[size];
  const initial = name.charAt(0).toUpperCase();

  return (
    <div
      style={{
        width: s.box,
        height: s.box,
        borderRadius: '50%',
        backgroundColor: color,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        color: 'white',
        fontSize: s.font,
        fontWeight: s.weight,
        lineHeight: 1,
        userSelect: 'none',
      }}
    >
      {initial}
    </div>
  );
}

export default AgentAvatar;
