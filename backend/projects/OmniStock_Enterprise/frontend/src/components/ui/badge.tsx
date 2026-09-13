// OmniStock Enterprise - Badge Component

import { StockStatus } from '@/types/inventory';

interface BadgeProps {
  children: React.ReactNode;
  variant?: 'default' | 'success' | 'warning' | 'error' | 'info';
  size?: 'sm' | 'md';
}

const variantStyles = {
  default: 'bg-gray-100 text-gray-700',
  success: 'bg-emerald-100 text-emerald-700',
  warning: 'bg-amber-100 text-amber-700',
  error: 'bg-red-100 text-red-700',
  info: 'bg-blue-100 text-blue-700'
};

const sizeStyles = {
  sm: 'px-2 py-0.5 text-xs',
  md: 'px-2.5 py-1 text-sm'
};

export function Badge({ children, variant = 'default', size = 'sm' }: BadgeProps) {
  return (
    <span
      className={`
        inline-flex items-center font-medium rounded-full
        ${variantStyles[variant]}
        ${sizeStyles[size]}
      `}
    >
      {children}
    </span>
  );
}

export function StockStatusBadge({ status }: { status: StockStatus }) {
  const config: Record<StockStatus, { label: string; variant: BadgeProps['variant'] }> = {
    in_stock: { label: 'In Stock', variant: 'success' },
    low_stock: { label: 'Low Stock', variant: 'warning' },
    out_of_stock: { label: 'Out of Stock', variant: 'error' },
    overstock: { label: 'Overstock', variant: 'info' }
  };

  const { label, variant } = config[status];

  return <Badge variant={variant}>{label}</Badge>;
}
