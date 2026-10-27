import React from 'react';
import { Alert, Typography } from 'antd';
import { sortCautions } from './fitCautions.mjs';

const { Text } = Typography;

/** Advisory flags from the automated fit checks; renders nothing when there are none. */
export default function FitCautions({ cautions, style }) {
  const items = sortCautions(cautions);
  if (!items.length) return null;
  return (
    <div style={{ marginTop: 8, ...style }}>
      <Text strong style={{ display: 'block', marginBottom: 4 }}>
        Review cautions (automated checks, not a verdict)
      </Text>
      {items.map((item, index) => (
        <Alert
          key={`${item.code}-${item.phase || ''}-${index}`}
          type={item.level === 'warning' ? 'warning' : 'info'}
          showIcon
          message={item.message}
          style={{ marginBottom: 4, padding: '4px 12px' }}
        />
      ))}
    </div>
  );
}
