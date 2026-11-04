import React from 'react';
import { Alert, Tooltip, Typography } from 'antd';
import { cautionsBesideBanner } from './fitCautions.mjs';

const { Text } = Typography;

/**
 * Advisory flags from the automated fit checks; renders nothing when there are none.
 * `besideBanner` leaves out what the confidence banner above already states.
 */
export default function FitCautions({ cautions, style, besideBanner = false }) {
  const items = cautionsBesideBanner(cautions, besideBanner);
  if (!items.length) return null;
  return (
    <div style={{ marginTop: 8, ...style }}>
      <Tooltip title="Automated checks, not a verdict">
        <Text strong style={{ display: 'inline-block', marginBottom: 4 }}>Cautions</Text>
      </Tooltip>
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
