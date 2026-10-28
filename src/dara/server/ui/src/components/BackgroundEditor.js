import React from 'react';
import { Button, Checkbox, InputNumber, Popover, Space, Typography } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { backgroundRequest } from './versions.mjs';

const { Text } = Typography;

export { backgroundRequest };

function EditorForm({ busy, getView, onSubmit, baseDegree }) {
  // A fit with an explicit degree keeps it unless the user switches to Auto (which resets it).
  const hasBase = baseDegree != null && Number.isFinite(Number(baseDegree));
  const [auto, setAuto] = React.useState(!hasBase);
  const [degree, setDegree] = React.useState(hasBase ? Number(baseDegree) : 8);
  const [ranges, setRanges] = React.useState([]);
  const setRange = (index, position, value) => setRanges(previous => previous.map((range, i) => (i === index
    ? (position === 0 ? [value, range[1]] : [range[0], value]) : range)));
  const useCurrentView = () => {
    const view = getView?.();
    if (!view) return;
    const [lo, hi] = [Math.min(...view), Math.max(...view)];
    setRanges(previous => [...previous, [Number(lo.toFixed(2)), Number(hi.toFixed(2))]]);
  };
  const request = backgroundRequest({ auto, degree, ranges, baseDegree: hasBase ? Number(baseDegree) : null });
  const invalid = ranges.some(([lo, hi]) => !(Number.isFinite(lo) && Number.isFinite(hi) && lo < hi));
  return <div style={{ width: 360 }}>
    <Space direction="vertical" size="small" style={{ width: '100%' }}>
      <div>
        <label htmlFor="bg-terms"><strong>Background polynomial terms</strong></label>
        <div style={{ marginTop: 4 }}>
          <Space>
            <Checkbox checked={auto} onChange={event => setAuto(event.target.checked)}>Auto</Checkbox>
            <InputNumber id="bg-terms" aria-label="Background polynomial terms" min={2} max={20} precision={0}
              disabled={auto} value={degree} onChange={value => setDegree(value ?? 8)} />
          </Space>
        </div>
      </div>
      <div>
        <strong>Exclude a hump</strong>
        <div><Text type="secondary">2θ intervals left out of the fit, for example an amorphous hump.</Text></div>
        {ranges.map((range, index) => <Space key={index} style={{ marginTop: 6 }}>
          <InputNumber aria-label={`Excluded interval ${index + 1} from`} value={range[0]} step={0.5} onChange={value => setRange(index, 0, value)} />
          <span>–</span>
          <InputNumber aria-label={`Excluded interval ${index + 1} to`} value={range[1]} step={0.5} onChange={value => setRange(index, 1, value)} />
          <Button size="small" icon={<DeleteOutlined />} aria-label={`Remove interval ${index + 1}`}
            onClick={() => setRanges(previous => previous.filter((_, i) => i !== index))} />
        </Space>)}
        <div style={{ marginTop: 6 }}>
          <Space wrap>
            <Button size="small" icon={<PlusOutlined />} onClick={() => setRanges(previous => [...previous, [null, null]])}>Add interval</Button>
            <Button size="small" onClick={useCurrentView} disabled={!getView}>Use current view</Button>
          </Space>
        </div>
      </div>
      <Text type="secondary">Changing the background or excluded ranges changes Rwp; compare fits made with the same settings.</Text>
      <Button type="primary" disabled={!request || invalid || busy} loading={busy}
        onClick={() => onSubmit(request.background, request.label)}>Re-refine</Button>
    </Space>
  </div>;
}

/** "Background…" popover of the fit view. `onSubmit(background, label)` starts a quick re-refinement. */
export default function BackgroundEditor({ busy, disabled, getView, onSubmit, baseDegree = null }) {
  const [open, setOpen] = React.useState(false);
  return <Popover trigger="click" open={open} onOpenChange={setOpen} placement="bottomLeft" title="Background"
    content={<EditorForm busy={busy} getView={getView} baseDegree={baseDegree} onSubmit={(background, label) => { setOpen(false); onSubmit(background, label); }} />}>
    <Button disabled={disabled}>Background…</Button>
  </Popover>;
}
